# AOC server, part 18: people (invites, roles, who did what, who's here).
# monitor.py exec()s server/NN_*.py in number order into ONE shared
# namespace (its own) -- not an importable module: names from earlier
# parts are plain globals here, and __file__ is monitor.py.
# ── People: several people on one AOC ─────────────────────────────────────
# This AOC is the host; others join through its tunnel with a personal
# invite link (one use, 7 days) and get their own token -- the page they
# load carries THEIR token, never the owner's. Roles:
#   owner  -- this PC (localhost) and the main token: everything
#   member -- everything but people and AOC's own settings (tunnel, license,
#             updates, restart, hooks, notifications, billing, memory
#             files) and actions on the owner's PC (open a console, focus a
#             window); may approve, start runs and entities, type in shared
#             sessions
#   viewer -- reads; may ask the voice assistant (it answers, doesn't act)
# Each request runs as one person (_req_ctx.user): orchestration records
# who approved / merged / skipped, and /status says who's here.
import hashlib, secrets as _secrets
USERS_FILE = os.path.join(AOC_DATA_DIR, "users.json")
_USER_ROLES = ("owner", "member", "viewer")
_INVITE_TTL_S = 7 * 86400
_JOIN_FAIL_MAX = 20                 # bad invite codes per hour from one address, then /join refuses it
_JOIN_FAIL_ALL = 300                # ... and from everyone together (one bad guesser used to lock all out)
_users_lock = threading.Lock()
_req_ctx = threading.local()
_users_seen = {}                    # user id -> {"name", "role", "last_seen", "view"}
_join_fails = []                    # (client address, time)
_OWNER_ONLY_EXACT = {
    "/auth/token", "/auth/info", "/budget_caps", "/danger_guard", "/license", "/notify_center/set", "/notify_settings",
    "/permission/settings", "/push/settings", "/push/test", "/recap/settings", "/restart", "/restore_backup",
    "/snitch_ping_now", "/orch/settings", "/voice/settings", "/launch", "/focus_session", "/memory_archive",
    # a shell on this PC (found in review: a GET websocket, so it was open to every role)
    "/terminal/ws",
    "/shared/start", "/shared/stop",   # a shared session runs on this PC: the owner starts / ends it
    # found in review 11-09: these act on the owner's PC / money / settings too
    "/update",                          # agent status rows -- the hooks post it from this PC; names reach every page
    "/queue", "/limit_stop",            # start Claude Code sessions here, like /launch
    "/budget_caps/continue", "/context_save", "/clients", "/notify_center/test", "/notify_center/clear",
}
# GETs that write: not for viewers. The poll ones hand a held permission / reply to whoever asks first.
_GET_CHANGES = {"/history/save_current", "/permission/poll", "/reply/poll"}
_OWNER_ONLY_PREFIX = ("/users", "/tunnel/", "/hooks_health/", "/release/", "/selfupdate/", "/webhook_", "/memory_file",
                      "/fixes/")
_VIEWER_POST = {"/voice/ask", "/whats_new/dismiss", "/me"}


def _user_hash(token):
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _users_load(path=None):
    d = _load_json_file(path or USERS_FILE, {})
    d = d if isinstance(d, dict) else {}
    return {"users": [u for u in d.get("users") or [] if isinstance(u, dict)],
            "invites": [i for i in d.get("invites") or [] if isinstance(i, dict)], "owner_name": d.get("owner_name") or "Owner"}


def _users_save(d, path=None):
    path = path or USERS_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _owner_user(d=None):
    return {"id": "owner", "name": (d or _users_load()).get("owner_name") or "Owner", "role": "owner"}


def _user_for_token(token, master, d=None):
    """Pure-ish: a request's token -> its person (None if nobody)."""
    import hmac
    token = str(token or "")
    if not token:
        return None
    d = d or _users_load()
    if master and hmac.compare_digest(token, master):
        return _owner_user(d)
    h = _user_hash(token)
    for u in d["users"]:
        if not u.get("disabled") and hmac.compare_digest(str(u.get("token_sha256") or ""), h):
            return {"id": u["id"], "name": u.get("name") or u["id"], "role": u.get("role") if u.get("role") in _USER_ROLES else "viewer"}
    return None


def _user_allowed(role, method, path):
    """Pure: may this role do this request? GETs are reads (the owner's
    token behind /auth/info excepted); POST / DELETE are changes."""
    if role == "owner":
        return True
    owner_only = path in _OWNER_ONLY_EXACT or path.startswith(_OWNER_ONLY_PREFIX)
    if method == "GET":
        if role == "viewer" and path in _GET_CHANGES:
            return False
        return not owner_only or path == "/users/me"
    if role == "viewer":
        return path in _VIEWER_POST
    return role == "member" and not owner_only


def _tokenless_owner_ok(host, origin, fetch_site, fetch_mode, method, path, port):
    """Pure: may a request from this PC with no token run as the owner?
    Not when a web page made it: any site the owner visits can reach
    127.0.0.1:5151 from their browser (found in review: a page could open
    /terminal/ws or read the token from /auth/info). So the Host must be a
    loopback name (DNS rebinding sends the attacker's), an Origin must be this
    very AOC, and Sec-Fetch-Site (sent by browsers even where Origin isn't,
    e.g. <img src>) must say the request didn't come from another site -- a
    cross-site link may still open the dashboard itself. Hooks, the watchdog
    and curl send none of these headers."""
    from urllib.parse import urlsplit
    loop = ("localhost", "127.0.0.1", "::1")
    try:
        if host and (urlsplit("//" + host).hostname or "") not in loop:
            return False
        if origin is not None:
            o = urlsplit(origin)
            if o.scheme not in ("http", "https") or (o.hostname or "") not in loop or (o.port or 80) != port:
                return False
    except ValueError:
        return False
    if fetch_site and fetch_site not in ("same-origin", "none"):
        return method == "GET" and fetch_mode == "navigate" and path in ("/", "/index.html")
    return True


def _current_user():
    """The person this request runs as (the owner for background work)."""
    return getattr(_req_ctx, "user", None) or {"id": "owner", "name": "Owner", "role": "owner", "background": True}


def _user_seen(user, view="", now=None):
    """Who's here: the last request of each person (shown for 2 minutes)."""
    if not user or user.get("background"):
        return
    _users_seen[user["id"]] = {"name": user["name"], "role": user["role"], "last_seen": now or time.time(),
                               "view": view or (_users_seen.get(user["id"]) or {}).get("view", "")}


def _users_present(now=None, within_s=120):
    """Pure-ish: [{id, name, role, view, ago_s}] seen in the last 2 minutes."""
    now = now or time.time()
    return [{"id": k, "name": v["name"], "role": v["role"], "view": v.get("view", ""), "ago_s": int(now - v["last_seen"])}
            for k, v in sorted(_users_seen.items(), key=lambda kv: -kv[1]["last_seen"]) if now - v["last_seen"] <= within_s]


def _user_invite(data, by, base_url, now=None, path=None):
    """POST /users/invite {role, name?} -> {ok, link, code, expires}."""
    now = now or time.time()
    role = (data or {}).get("role")
    if role not in ("member", "viewer"):
        return {"ok": False, "error": "invite as member or viewer"}
    code = _secrets.token_urlsafe(18)
    with _users_lock:
        d = _users_load(path)
        d["invites"] = [i for i in d["invites"] if not i.get("used_by") and i.get("expires", 0) > now]
        d["invites"].append({"code_sha256": _user_hash(code), "role": role, "name": str((data or {}).get("name") or "")[:60],
                             "created": now, "expires": now + _INVITE_TTL_S, "by": by.get("name", "")})
        _users_save(d, path)
    base = (base_url or "").rstrip("/")
    return {"ok": True, "code": code, "expires": now + _INVITE_TTL_S, "link": f"{base}/join?code={code}" if base else "",
            "path": f"/join?code={code}"}


def _user_join(data, now=None, path=None, client=""):
    """POST /join {code, name} (no token: the invite is the key) -> {ok, token, user}."""
    now = now or time.time()
    _join_fails[:] = [f for f in _join_fails if now - f[1] < 3600]
    if len(_join_fails) >= _JOIN_FAIL_ALL or sum(1 for f in _join_fails if f[0] == client) >= _JOIN_FAIL_MAX:
        return {"ok": False, "error": "too many wrong invite codes -- try again in an hour"}
    code, name = str((data or {}).get("code") or ""), re.sub(r"\s+", " ", str((data or {}).get("name") or "")).strip()[:40]
    if len(name) < 2:
        return {"ok": False, "error": "tell us your name"}
    with _users_lock:
        d = _users_load(path)
        inv = next((i for i in d["invites"] if i.get("code_sha256") == _user_hash(code)), None)
        if not inv or inv.get("used_by") or inv.get("expires", 0) < now:
            _join_fails.append((client, now))
            return {"ok": False, "error": "this invite isn't valid (used, expired or mistyped) -- ask for a new one"}
        uid = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")[:24] or "user"
        taken = {u["id"] for u in d["users"]} | {"owner"}
        base_id, n = uid, 2
        while uid in taken:
            uid, n = f"{base_id}-{n}", n + 1
        token = _secrets.token_urlsafe(24)
        d["users"].append({"id": uid, "name": name, "role": inv["role"], "token_sha256": _user_hash(token), "created": now,
                           "invited_by": inv.get("by", ""), "disabled": False})
        inv["used_by"], inv["used_at"] = uid, now
        _users_save(d, path)
    return {"ok": True, "token": token, "user": {"id": uid, "name": name, "role": inv["role"]}}


def _users_view(now=None, path=None):
    """GET /users (owner): people, open invites, who's here."""
    now = now or time.time()
    d = _users_load(path)
    seen = {p["id"]: p for p in _users_present(now, within_s=10 ** 9)}
    return {"ok": True, "owner_name": d["owner_name"],
            "users": [{"id": u["id"], "name": u.get("name"), "role": u.get("role"), "created": u.get("created"),
                       "invited_by": u.get("invited_by", ""), "disabled": bool(u.get("disabled")),
                       "last_seen": (now - seen[u["id"]]["ago_s"]) if u["id"] in seen else None} for u in d["users"]],
            "invites": [{"role": i["role"], "name": i.get("name", ""), "expires": i["expires"], "by": i.get("by", "")}
                        for i in d["invites"] if not i.get("used_by") and i.get("expires", 0) > now],
            "present": _users_present(now)}


def _users_update(data, path=None):
    """POST /users/update {id, role?, disabled?, remove?, owner_name?}."""
    data = data or {}
    with _users_lock:
        d = _users_load(path)
        if "owner_name" in data:
            d["owner_name"] = re.sub(r"\s+", " ", str(data["owner_name"] or "")).strip()[:40] or "Owner"
            _users_save(d, path)
            return {"ok": True}
        u = next((x for x in d["users"] if x["id"] == str(data.get("id") or "")), None)
        if not u:
            return {"ok": False, "error": "unknown person"}
        if data.get("remove") is True:
            d["users"] = [x for x in d["users"] if x is not u]
            _users_seen.pop(u["id"], None)
        else:
            if data.get("role") in ("member", "viewer"):
                u["role"] = data["role"]
            if "disabled" in data:
                u["disabled"] = data["disabled"] is True
        _users_save(d, path)
    return {"ok": True}


def _users_cancel_invites(path=None):
    """POST /users/cancel_invites: every open invite stops working."""
    with _users_lock:
        d = _users_load(path)
        d["invites"] = [i for i in d["invites"] if i.get("used_by")]
        _users_save(d, path)
    return {"ok": True}


def _join_page(code):
    """Pure: the page an invite link opens (no dashboard, no token yet). The
    code comes from the link: as a JSON string with '<' escaped, so a
    crafted link can't close the <script> (found in review)."""
    code_js = json.dumps(str(code)).replace("<", "\\u003c")
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Join AOC</title><style>body{{font:15px system-ui,sans-serif;background:#050c18;color:#dff1ff;display:flex;min-height:100vh;
align-items:center;justify-content:center;margin:0}}form{{background:#0c2036;padding:24px;border-radius:10px;width:min(360px,90vw);
display:flex;flex-direction:column;gap:12px}}input,button{{font:inherit;padding:10px;border-radius:6px;border:1px solid #2a4a66;
background:#08182a;color:inherit}}button{{background:#1e88d8;border:0;cursor:pointer}}#e{{color:#ef6a57;min-height:1em}}</style></head>
<body><form onsubmit="event.preventDefault();j()"><b>Join this AOC</b><span>You were invited to see and work on its sessions,
agent teams and entities.</span><input id="n" placeholder="Your name" autofocus required minlength="2"><button>Join</button>
<span id="e"></span></form><script>
async function j(){{const r=await fetch('/join',{{method:'POST',headers:{{'Content-Type':'application/json'}},
body:JSON.stringify({{code:{code_js},name:document.getElementById('n').value}})}}).then(r=>r.json()).catch(e=>({{ok:false,error:String(e)}}));
if(!r.ok){{document.getElementById('e').textContent=r.error;return;}}location.href='/?token='+encodeURIComponent(r.token);}}
</script></body></html>"""
