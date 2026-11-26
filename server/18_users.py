# AOC server, part 18: people (invites, roles, who did what, who's here).
# monitor.py exec()s server/NN_*.py in number order into ONE shared
# namespace (its own) -- not an importable module: names from earlier
# parts are plain globals here, and __file__ is monitor.py.
# ── People: several people on one AOC ─────────────────────────────────────
# This AOC is the host; others join through its tunnel with a personal
# invite link (one use, 7 days) and get their own token -- the page they
# load carries THEIR token, never the owner's. Roles:
#   owner  -- this PC (localhost) and the main token: everything
#   member -- reads everything but people and the owner's token; changes
#             only what _MEMBER_POST lists (an allowlist: a new route is the
#             owner's until it's added there). Approves plans, starts runs,
#             entities and schedules in workspaces with agents that use no
#             connected service (_orch_member_refusal), answers permission
#             prompts, types in shared sessions. Never: people, AOC's own
#             settings, actions on the owner's PC, merge / discard / gates.
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
_VIEWER_POST = {"/voice/ask", "/whats_new/dismiss"}
# What a member may change. An allowlist (audit FIND-002): with the old
# denylist every route someone forgot to list was open to members -- found
# twice in review. Left out on purpose: whatever acts on this PC or its
# settings, the hooks' own posts (/permission/request, /reply/request: a member
# could plant prompts on the owner's dashboard), /context_save/send (types
# into the owner's sessions), and orchestration's merge / discard / gate /
# settings (the owner's decisions). tests/python/users.test.py lists every
# POST route with what each role gets: a new route fails it until decided.
_MEMBER_POST = _VIEWER_POST | {
    "/ask", "/clear_done", "/danger_guard/test", "/history_tags", "/kill_session", "/notes", "/remove", "/reset",
    "/session_note", "/prompts", "/prompts/delete", "/prompts/used", "/push/subscribe", "/push/unsubscribe",
    "/permission/decide", "/reply/send", "/queue/cancel", "/voice/confirm",
    "/orch/create", "/orch/plan", "/orch/approve", "/orch/cancel", "/orch/baseline", "/orch/judge",
    "/orch/agent_save", "/orch/agent_delete", "/orch/entity_save", "/orch/entity_run", "/orch/entity_pause",
    "/orch/entity_forget", "/orch/entity_delete", "/orch/start", "/orch/schedule_save", "/orch/schedule_pause",
    "/orch/schedule_delete", "/orch/schedule_run",
}
_MEMBER_DELETE_PREFIX = ("/session/",)   # dismiss a closed session card
# files a non-owner never gets through /diff, tracked or not (audit FIND-006)
_DIFF_SECRET_RE = re.compile(r"(^|[\\/])(\.env[^\\/]*|[^\\/]*\.(pem|key|p12|pfx)|id_(rsa|ed25519|ecdsa)[^\\/]*"
                             r"|[^\\/]*(secret|credential)[^\\/]*)$", re.I)


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
        return method == "POST" and path in _VIEWER_POST
    if role == "member":
        return (method == "POST" and path in _MEMBER_POST) or \
               (method == "DELETE" and path.startswith(_MEMBER_DELETE_PREFIX))
    return False


def _diff_refusal(role, rel, tracked):
    """Pure: why /diff won't show this file to this role ('' = it will). A
    non-owner sees only files git tracks (an untracked file can be anything:
    a local config, a key) and never one named like a secret."""
    if role == "owner":
        return ""
    if _DIFF_SECRET_RE.search(str(rel or "")):
        return f"(not shown: {rel} may hold secrets -- only the owner sees it)"
    if not tracked:
        return f"(not shown: {rel} isn't tracked by git -- only the owner sees untracked files)"
    return ""


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


# ── A Claude Code agent is not the owner (audit FIND-016) ──
# Found in the audit: with no token, any program on this PC was the owner --
# so an agent's `curl -X POST localhost:5151/danger_guard -d '{"mode":"off"}'`
# switched off the very guard that holds its commands, and /auth/info handed
# it the owner's token. Browser headers can't tell an agent apart (curl sends
# any header it likes), its process can: a request whose program runs under
# Claude Code's CLI is the agent -- it may read like a viewer, and changes
# need the token (AOC's hooks send it; an agent reading aoc_token.txt is held
# by the danger guard's aoc_self rule). The live AOC only: previews are
# driven by tests and smoke runs from Claude sessions.
_AGENT_USER = {"id": "agent", "name": "Claude Code agent (no token)", "role": "viewer", "background": True}
# AOC's hooks run under Claude Code too, and send no token while the owner
# has none set (it is optional): what they post stays open. Planting a status
# row or a prompt is all an agent gets from these.
_AGENT_POST = {"/update", "/permission/request", "/reply/request"}
_AGENT_GET = {"/permission/poll", "/reply/poll"}


def _agent_allowed(method, path):
    """Pure: may a Claude Code agent without the token do this? Read like a
    viewer, plus what AOC's own hooks call."""
    if method == "GET" and path in _AGENT_GET:
        return True
    if method == "POST" and path in _AGENT_POST:
        return True
    return _user_allowed("viewer", method, path)


def _agent_guard_on():
    return not AOC_PREVIEW or os.environ.get("AOC_AGENT_GUARD") in ("1", "all")


def _agent_guard_all():
    """A preview with AOC_AGENT_GUARD=all takes every tokenless caller for an
    agent (tests check the refusals end to end). It only takes rights away,
    and never in the live AOC."""
    return AOC_PREVIEW and os.environ.get("AOC_AGENT_GUARD") == "all"


def _is_agent_proc(name, exe, cmdline=""):
    """Pure: is this process Claude Code (the CLI, which runs agents and their
    shells) rather than the Claude desktop app (whose built-in browser may
    show this dashboard)?"""
    name, exe = str(name or "").lower(), str(exe or "").lower().replace("/", "\\")
    if name == "claude.exe":
        desktop = "\\anthropicclaude\\" in exe or ("\\windowsapps\\" in exe and "claude-code" not in exe)
        return not desktop
    if name in ("node.exe", "node", "bun.exe"):
        return "claude-code" in str(cmdline or "").lower().replace("/", "\\")
    return False


def _loopback_caller_pid(client_port, server_port, conns=None):
    """The pid of the program on this PC whose TCP connection this is (its
    side: local port = client_port, remote port = ours). None if not found."""
    if conns is None:
        import psutil
        conns = psutil.net_connections("tcp")
    for c in conns:
        if c.laddr and c.raddr and c.laddr.port == client_port and c.raddr.port == server_port and c.pid:
            return c.pid
    return None


def _agent_caller(client_port, server_port=None, conns=None, proc_fn=None) -> str:
    """'' or the Claude Code process (name + pid) the caller runs under.
    Fails open ('' -- the owner, as before) when the caller can't be found:
    psutil missing or the program already gone."""
    try:
        pid = _loopback_caller_pid(client_port, server_port or PORT, conns)
        if not pid:
            return ""
        if proc_fn is None:
            import psutil
            proc_fn = psutil.Process
        p = proc_fn(pid)
        chain = [p] + list(p.parents())
    except Exception:
        return ""
    for q in chain[:40]:
        try:
            name, exe = q.name(), q.exe()
            cmd = " ".join(q.cmdline()) if name.lower() in ("node.exe", "node", "bun.exe") else ""
        except Exception:
            continue
        if _is_agent_proc(name, exe, cmd):
            return f"{name} (pid {q.pid})"
    return ""


# ── Who on the network may even connect (audit FIND-005) ──
# The server listens on every interface (phone, Remote Machines and invited
# people reach it over the LAN), but without the token or a person nobody
# from another device can get in -- they only got a 401. Now such a
# connection is closed before AOC reads a byte of it, as if it listened on
# 127.0.0.1 only, and it opens by itself once there is a token or a person.
_lan_cache = {"t": 0.0, "open": False}


def _lan_open(token, users_doc, now=None) -> bool:
    """Pure: may other devices connect -- a token, an active person, or an
    invite still waiting to be used?"""
    now = now or time.time()
    d = users_doc or {}
    return bool(token) or any(not u.get("disabled") for u in d.get("users") or []) or \
        any(not i.get("used_by") and i.get("expires", 0) > now for i in d.get("invites") or [])


def _connection_allowed(client_ip, now=None, ttl=5.0) -> bool:
    """A new connection: from this PC always; from elsewhere only while
    _lan_open (people.json read at most every `ttl` s)."""
    ip = str(client_ip or "")
    if ip.startswith("127.") or ip in ("::1", "localhost"):
        return True
    now = now or time.time()
    if now - _lan_cache["t"] > ttl:
        _lan_cache.update(t=now, open=_lan_open(_auth_token, _users_load(), now))
    return _lan_cache["open"]


def _relogin_page() -> str:
    """Pure: what `/` without a token gets. The dashboard drops ?token= from
    the address bar and keeps it in this browser (localStorage, this origin
    only) -- so a refresh on the phone comes back here and goes on with it.
    A token that no longer works is forgotten, not retried."""
    return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            "<title>AOC</title><body style='font:15px system-ui;padding:24px'>"
            "<p id=m>Unauthorized - token required</p><script>"
            "try{const t=localStorage.getItem('aoc_token');"
            "if(t&&!new URLSearchParams(location.search).has('token')){location.replace('/?token='+encodeURIComponent(t));}"
            "else if(t){localStorage.removeItem('aoc_token');"
            "document.getElementById('m').textContent='This link no longer works -- open a new one from AOC on your PC.';}"
            "}catch(e){}</script>")


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
