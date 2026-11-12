# AOC server, part 06: remote approve, reply from the dashboard, context save, Web Push.
# monitor.py exec()s server/NN_*.py in number order into ONE shared
# namespace (its own) -- not an importable module: names from earlier
# parts are plain globals here, and __file__ is monitor.py.
# ── Remote approve: answer permission prompts from the dashboard ──────────
# hooks/aoc_permission.py (a PermissionRequest hook) asks POST
# /permission/request whether to hold a prompt for the dashboard. Claude
# Code shows its own terminal dialog only after that hook returns, so the
# hook holds only while you're away from this PC (mode "away": no keyboard
# or mouse input for idle_min minutes) or in mode "always"; otherwise it
# returns at once and the terminal dialog works exactly as before. While it
# holds, it polls GET /permission/poll -- touching the PC again releases the
# prompt back to the terminal dialog.
REMOTE_APPROVE_FILE = os.path.join(AOC_DATA_DIR, "remote_approve.json")
_PERM_MODES = ("off", "away", "always")
_REPLY_MODES = ("off", "away")
_PERM_POLL_STALE_S = 15   # no poll for this long = the hook is gone (Esc, session closed)
_PERM_BACK_IDLE_S = 5     # input this recent while held in "away" mode = you're back
_perm_lock = threading.Lock()
_perm_requests = {}       # id -> request dict (see _perm_open)


def _sanitize_remote_approve(data) -> dict:
    data = data if isinstance(data, dict) else {}
    mode = data.get("mode") if data.get("mode") in _PERM_MODES else "away"
    try:
        idle_min = int(data.get("idle_min", 5))
    except (TypeError, ValueError):
        idle_min = 5
    reply = data.get("reply") if data.get("reply") in _REPLY_MODES else "away"
    return {"mode": mode, "idle_min": max(1, min(idle_min, 120)), "reply": reply}


def _load_remote_approve() -> dict:
    return _sanitize_remote_approve(_load_json_file(REMOTE_APPROVE_FILE, {}))


def _save_remote_approve(data) -> dict:
    """Saves the fields present in `data`; the rest keep their stored value."""
    merged = _load_remote_approve()
    if isinstance(data, dict):
        merged.update({k: v for k, v in data.items() if k in ("mode", "idle_min", "reply")})
    cfg = _sanitize_remote_approve(merged)
    os.makedirs(os.path.dirname(REMOTE_APPROVE_FILE), exist_ok=True)
    with open(REMOTE_APPROVE_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    return cfg


def _perm_should_hold(cfg, idle_s) -> bool:
    """Pure: hold a new permission prompt for the dashboard?"""
    if cfg["mode"] == "always":
        return True
    if cfg["mode"] == "off" or idle_s is None:
        return False
    return idle_s >= cfg["idle_min"] * 60


def _perm_summary(tool_name, tool_input):
    """Pure: (one-line summary, longer detail) of what a tool wants to do."""
    ti = tool_input if isinstance(tool_input, dict) else {}
    tool_name = str(tool_name or "?")
    if tool_name == "Bash":
        summary = str(ti.get("command") or "")
        detail = str(ti.get("description") or "")
    elif tool_name in ("Edit", "MultiEdit", "Write", "Read", "NotebookEdit"):
        summary = str(ti.get("file_path") or ti.get("notebook_path") or "")
        if tool_name == "Edit":
            detail = "- " + str(ti.get("old_string") or "") + "\n+ " + str(ti.get("new_string") or "")
        elif tool_name == "Write":
            detail = str(ti.get("content") or "")
        else:
            detail = ""
    elif tool_name in ("WebFetch", "WebSearch"):
        summary = str(ti.get("url") or ti.get("query") or "")
        detail = str(ti.get("prompt") or "")
    else:
        summary = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)[:60]}" for k, v in list(ti.items())[:3])
        detail = json.dumps(ti, ensure_ascii=False, indent=1)
    return f"{tool_name}: {summary}".strip()[:300], detail[:2000]


def _perm_expire(now):
    """Drop requests whose hook stopped polling. Caller holds _perm_lock."""
    for rid in [r for r, q in _perm_requests.items() if now - q["last_poll"] > _PERM_POLL_STALE_S]:
        del _perm_requests[rid]


def _perm_open(data, cfg, now) -> dict:
    """POST /permission/request body from the hook -> its reply:
    {"hold": false} (show the terminal dialog now) or {"hold": true, "id"}."""
    if not _perm_should_hold(cfg, data.get("idle_s")):
        return {"hold": False}
    summary, detail = _perm_summary(data.get("tool_name"), data.get("tool_input"))
    rid = _secrets.token_hex(8)
    with _perm_lock:
        _perm_expire(now)
        _perm_requests[rid] = {
            "id": rid, "session_id": str(data.get("session_id") or ""),
            "tool_name": str(data.get("tool_name") or ""), "summary": summary, "detail": detail,
            "created": now, "last_poll": now, "state": "pending", "message": "",
        }
    try:
        if _notify_gate("permission", "push")[0]:
            title = f"🔐 {_push_session_name(data.get('session_id'))} needs your OK"
            if _push_notify(title, summary, tag="perm-" + rid, urgent=True):
                _notify_log_add("permission", "push", title, summary)
    except Exception as e:
        _log_bg_error("push", e)
    return {"hold": True, "id": rid, "summary": summary}


def _perm_poll(rid, idle_s, cfg, now) -> dict:
    """GET /permission/poll from the holding hook -> {"state": pending|allow|
    deny|release, "message"}. A decided request is handed out once, then gone."""
    with _perm_lock:
        req = _perm_requests.get(rid)
        if not req:
            return {"state": "release"}
        req["last_poll"] = now
        if req["state"] == "pending":
            if cfg["mode"] == "off" or (cfg["mode"] == "away" and idle_s is not None and idle_s < _PERM_BACK_IDLE_S):
                req["state"] = "release"
            else:
                return {"state": "pending"}
        del _perm_requests[rid]
        return {"state": req["state"], "message": req["message"]}


def _perm_decide(rid, decision, message="") -> bool:
    """POST /permission/decide from the dashboard: allow | deny | terminal."""
    state = {"allow": "allow", "deny": "deny", "terminal": "release"}.get(decision)
    with _perm_lock:
        req = _perm_requests.get(rid)
        if not state or not req or req["state"] != "pending":
            return False
        req["state"] = state
        req["message"] = str(message or "")[:500]
        return True


def _perm_pending_by_session(now) -> dict:
    """{session_id: [pending requests, oldest first]} for the sessions list."""
    out = {}
    with _perm_lock:
        _perm_expire(now)
        for q in sorted(_perm_requests.values(), key=lambda q: q["created"]):
            if q["state"] == "pending":
                out.setdefault(q["session_id"], []).append(
                    {"id": q["id"], "tool_name": q["tool_name"], "summary": q["summary"],
                     "detail": q["detail"], "age_s": int(now - q["created"]),
                     "danger": q.get("danger") or "", "cwd": q.get("cwd") or ""})
    return out


# ── Reply from the dashboard: answer a finished turn while you're away ────
# The same hook script also runs on Stop (aoc_permission.py, mode "reply").
# When a turn ends while you're away it asks POST /reply/request whether to
# hold the end of the turn; while it holds, the dashboard shows the last
# message with a reply box. Your reply goes back as the Stop hook's
# {"decision": "block", "reason": ...} and Claude carries on with it.
# Claude Code ends the turn anyway after CLAUDE_CODE_STOP_HOOK_BLOCK_CAP (8)
# blocks in a row, so AOC stops offering a reply one short of that.
_REPLY_MAX_STREAK = 7
_reply_lock = threading.Lock()
_reply_requests = {}      # id -> request dict (see _reply_open)
_reply_streak = {}        # session_id -> replies sent in a row without you typing


def _reply_should_hold(cfg, idle_s, streak) -> bool:
    """Pure: hold the end of a turn for a reply from the dashboard?"""
    if cfg.get("reply") != "away" or idle_s is None or streak >= _REPLY_MAX_STREAK:
        return False
    return idle_s >= cfg["idle_min"] * 60


def _reply_expire(now):
    """Drop requests whose hook stopped polling. Caller holds _reply_lock."""
    for rid in [r for r, q in _reply_requests.items() if now - q["last_poll"] > _PERM_POLL_STALE_S]:
        del _reply_requests[rid]


def _reply_open(data, cfg, now) -> dict:
    """POST /reply/request from the Stop hook -> {"hold": false} or
    {"hold": true, "id"}. stop_hook_active false = this turn started from you
    typing, so the run of dashboard replies starts again from zero."""
    sid = str(data.get("session_id") or "")
    with _reply_lock:
        if not data.get("stop_hook_active"):
            _reply_streak.pop(sid, None)
        if sid and _reply_streak.get(sid, 0) < _REPLY_MAX_STREAK and _ctxsave_take(sid, now):
            # an armed "Save state": answer this turn end with it right away
            _reply_expire(now)
            for rid in [r for r, q in _reply_requests.items() if q["session_id"] == sid]:
                del _reply_requests[rid]
            rid = _secrets.token_hex(8)
            _reply_requests[rid] = {
                "id": rid, "session_id": sid, "message": str(data.get("last_assistant_message") or "")[-6000:],
                "created": now, "last_poll": now, "state": "reply", "reply": _ctxsave_settings()["prompt"],
                "kind": "ctx_save",   # the Stop hook words its preamble by this
            }
            return {"hold": True, "id": rid}
        if not sid or not _reply_should_hold(cfg, data.get("idle_s"), _reply_streak.get(sid, 0)):
            return {"hold": False}
        _reply_expire(now)
        for rid in [r for r, q in _reply_requests.items() if q["session_id"] == sid]:
            del _reply_requests[rid]   # a newer turn end replaces an older one
        rid = _secrets.token_hex(8)
        _reply_requests[rid] = {
            "id": rid, "session_id": sid,
            "message": str(data.get("last_assistant_message") or "")[-6000:],
            "created": now, "last_poll": now, "state": "pending", "reply": "",
        }
    try:
        msg = str(data.get("last_assistant_message") or "").strip()
        if _notify_gate("reply", "push")[0]:
            title = f"💬 {_push_session_name(sid)} " + ("asks" if msg.endswith("?") else "finished its turn")
            body = ("…" + msg[-240:]) if len(msg) > 240 else (msg or "Reply from AOC to carry on.")
            if _push_notify(title, body, tag="reply-" + rid, urgent=True):
                _notify_log_add("reply", "push", title, body)
    except Exception as e:
        _log_bg_error("push", e)
    return {"hold": True, "id": rid}


def _reply_poll(rid, idle_s, cfg, now) -> dict:
    """GET /reply/poll from the holding hook -> {"state": pending|reply|
    release, "reply"}. Handed out once, then gone."""
    with _reply_lock:
        req = _reply_requests.get(rid)
        if not req:
            return {"state": "release"}
        req["last_poll"] = now
        if req["state"] == "pending":
            if cfg.get("reply") != "away" or (idle_s is not None and idle_s < _PERM_BACK_IDLE_S):
                req["state"] = "release"
            else:
                return {"state": "pending"}
        del _reply_requests[rid]
        if req["state"] == "reply":
            _reply_streak[req["session_id"]] = _reply_streak.get(req["session_id"], 0) + 1
        return {"state": req["state"], "reply": req["reply"], "kind": req.get("kind", "")}


def _reply_send(rid, action, text="", kind="") -> tuple:
    """POST /reply/send from the dashboard: action "reply" (needs text) or
    "done" (let the turn end; the session waits in the terminal as usual).
    kind="ctx_save" marks AOC's own Save state prompt (hook preamble).
    Returns (ok, error)."""
    text = str(text or "").strip()
    if action not in ("reply", "done"):
        return False, "unknown action"
    if action == "reply" and not text:
        return False, "empty reply"
    with _reply_lock:
        req = _reply_requests.get(rid)
        if not req or req["state"] != "pending":
            return False, "no longer waiting"
        req["state"] = "reply" if action == "reply" else "release"
        req["reply"] = text[:20000]
        req["kind"] = kind
    return True, ""


def _reply_pending_by_session(now) -> dict:
    """{session_id: {id, message, age_s}} for turns held for a reply."""
    out = {}
    with _reply_lock:
        _reply_expire(now)
        for q in _reply_requests.values():
            if q["state"] == "pending":
                out[q["session_id"]] = {"id": q["id"], "message": q["message"],
                                        "age_s": int(now - q["created"])}
    return out


# ── Save state when the context fills up ──────────────────────────────────
# When a session's context window crosses the threshold (Settings -> Notify,
# default 90 %), AOC offers "Save state": a prompt asking Claude to write
# what's done / where to resume / next steps to memory before Claude Code
# compacts the conversation. It goes in through the Stop hook (reply from the
# dashboard): straight away if the turn is already held for a reply, else
# at the end of the current turn. Mode "auto" sends it by itself, once per
# crossing. A big drop in fill = the session compacted: it re-arms and stays
# quiet for a while; a crossing already in auto-compact territory is skipped.
CTXSAVE_FILE = os.path.join(AOC_DATA_DIR, "context_save.json")
CTXSAVE_STATE_FILE = os.path.join(AOC_DATA_DIR, "context_save_state.json")
_CTXSAVE_MODES = ("offer", "auto", "off")
_CTXSAVE_DEFAULT_PROMPT = ("Ulož stav projektu do pamäte: čo je hotové, kde skončiť (RESUME HERE), next steps.")
_CTXSAVE_MIN_PCT = 50
_CTXSAVE_MAX_PCT = 95
_CTXSAVE_REARM_BELOW = 10        # points under the threshold that re-arm a crossing
_CTXSAVE_COMPACT_DROP = 20       # a fall this big between two readings = compacted
_CTXSAVE_COMPACT_QUIET_S = 900   # no offer this soon after a compaction
_CTXSAVE_COMPACTING_PCT = 97     # at/over this Claude Code is about to compact on its own
_CTXSAVE_KEEP_S = 3 * 86400
_ctxsave_lock = threading.Lock()
_ctxsave_state = {}              # sid -> {pct, crossed, crossed_at, compacted_at, armed, sent_at, t}


def _ctxsave_sanitize(raw) -> dict:
    """Pure: settings file -> {mode, threshold, prompt} with defaults."""
    raw = raw if isinstance(raw, dict) else {}
    mode = raw.get("mode") if raw.get("mode") in _CTXSAVE_MODES else "offer"
    try:
        thr = int(raw.get("threshold", 90))
    except (TypeError, ValueError):
        thr = 90
    prompt = str(raw.get("prompt") or "").strip()[:2000] or _CTXSAVE_DEFAULT_PROMPT
    return {"mode": mode, "threshold": max(_CTXSAVE_MIN_PCT, min(_CTXSAVE_MAX_PCT, thr)), "prompt": prompt}


def _ctxsave_settings() -> dict:
    return _ctxsave_sanitize(_load_json_file(CTXSAVE_FILE))


def _ctxsave_save_settings(data) -> dict:
    cfg = _ctxsave_sanitize({**_ctxsave_settings(), **(data if isinstance(data, dict) else {})})
    os.makedirs(AOC_DATA_DIR, exist_ok=True)
    tmp = CTXSAVE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)
    os.replace(tmp, CTXSAVE_FILE)
    return cfg


def _ctxsave_step(prev, pct, threshold, now):
    """Pure: (new state, event) for one context reading of one session.
    event: "crossed" (offer / auto-send now), "compacted" or "". Once per
    crossing; re-armed by compaction or falling well under the threshold."""
    st = dict(prev or {})
    last = st.get("pct")
    st["pct"], st["t"] = pct, now
    if isinstance(last, (int, float)) and last - pct >= _CTXSAVE_COMPACT_DROP:
        st.update(crossed=False, armed=False, compacted_at=now)
        return st, "compacted"
    if st.get("crossed"):
        if pct < threshold - _CTXSAVE_REARM_BELOW:
            st.update(crossed=False, armed=False)
        return st, ""
    if pct < threshold:
        return st, ""
    if now - (st.get("compacted_at") or 0) < _CTXSAVE_COMPACT_QUIET_S:
        return st, ""
    st.update(crossed=True, crossed_at=now, armed=False, sent_at=None)
    if pct >= _CTXSAVE_COMPACTING_PCT:
        return st, ""        # too late to ask: it compacts any moment
    return st, "crossed"


def _ctxsave_view(st, cfg) -> dict:
    """Pure: the /status 'ctx_save' block of a session, or None."""
    if cfg["mode"] == "off" or not st or not st.get("crossed"):
        return None
    state = "sent" if st.get("sent_at") else "armed" if st.get("armed") else "offer"
    return {"state": state, "mode": cfg["mode"], "threshold": cfg["threshold"], "sent_at": st.get("sent_at")}


def _ctxsave_persist():
    """Caller holds _ctxsave_lock."""
    try:
        os.makedirs(AOC_DATA_DIR, exist_ok=True)
        tmp = CTXSAVE_STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_ctxsave_state, f)
        os.replace(tmp, CTXSAVE_STATE_FILE)
    except Exception:
        pass


def _ctxsave_load():
    data = _load_json_file(CTXSAVE_STATE_FILE)
    if isinstance(data, dict):
        _ctxsave_state.update({k: v for k, v in data.items() if isinstance(v, dict)})


def _ctxsave_tick(sessions, cfg, now):
    """One worker pass over /status sessions_list: [(session, event)]."""
    out, changed = [], False
    with _ctxsave_lock:
        for s in sessions or ():
            sid, c = s.get("id"), s.get("context") or {}
            if not sid or not s.get("session_active") or not isinstance(c.get("pct"), (int, float)):
                continue
            prev = _ctxsave_state.get(sid)
            st, ev = _ctxsave_step(prev, float(c["pct"]), cfg["threshold"], now)
            if ev or not prev or bool(st.get("crossed")) != bool(prev.get("crossed")):
                changed = True
            _ctxsave_state[sid] = st
            if ev:
                out.append((s, ev))
        for sid in [k for k, v in _ctxsave_state.items() if now - (v.get("t") or 0) > _CTXSAVE_KEEP_S]:
            del _ctxsave_state[sid]
            changed = True
        if changed:
            _ctxsave_persist()
    return out


def _ctxsave_request(sid, now=None, prompt=None) -> dict:
    """Send the save-state prompt to a session: into a turn already held for
    a reply ("sent"), else armed for the end of its current turn ("armed")."""
    now = now or time.time()
    sid = str(sid or "")
    if not sid:
        return {"ok": False, "error": "no session"}
    prompt = prompt or _ctxsave_settings()["prompt"]
    held = _reply_pending_by_session(now).get(sid)
    if held and _reply_send(held["id"], "reply", prompt, kind="ctx_save")[0]:
        with _ctxsave_lock:
            st = _ctxsave_state.setdefault(sid, {"t": now})
            st.update(armed=False, sent_at=now)
            _ctxsave_persist()
        return {"ok": True, "state": "sent"}
    with _ctxsave_lock:
        st = _ctxsave_state.setdefault(sid, {"t": now})
        st.update(armed=True, armed_at=now, sent_at=None)
        _ctxsave_persist()
    return {"ok": True, "state": "armed"}


def _ctxsave_cancel(sid) -> bool:
    with _ctxsave_lock:
        st = _ctxsave_state.get(str(sid or ""))
        if not st or not st.get("armed"):
            return False
        st["armed"] = False
        _ctxsave_persist()
    return True


def _ctxsave_take(sid, now) -> bool:
    """The Stop hook for `sid` fired: hand over an armed save-state prompt
    (once). Called with _reply_lock held; never takes it itself."""
    with _ctxsave_lock:
        st = _ctxsave_state.get(sid)
        if not st or not st.get("armed"):
            return False
        st.update(armed=False, sent_at=now)
        _ctxsave_persist()
    return True


def _ctxsave_alert(s, cfg):
    """Pure: (title, body) for the notification of a crossing."""
    name = (s.get("display_name") or s.get("project") or "Session")[:40]
    pct = round((s.get("context") or {}).get("pct") or 0)
    if cfg["mode"] == "auto":
        return f"🧠 {name}: context {pct} % full", "Asking it to save its state to memory when this turn ends."
    return f"🧠 {name}: context {pct} % full", "Save its state to memory before it compacts -- one click in AOC."


try:
    _ctxsave_load()
except Exception:
    pass


# ── Phone push notifications (Web Push) ───────────────────────────────────
# A phone that opened AOC through Remote access (https) and pressed "Enable
# on this device" gets real push notifications through its browser's push
# service, even with the AOC page closed: every toast AOC shows on this PC
# (while you're away, or always), plus held permission prompts and turns
# waiting for a reply. Messages are encrypted for the device (RFC 8291,
# aes128gcm) and signed with AOC's own VAPID key (RFC 8292) -- no third-party
# account, just `cryptography`, which AOC already needs.
PUSH_VAPID_FILE = os.path.join(AOC_DATA_DIR, "push_vapid.json")
PUSH_SUBS_FILE = os.path.join(AOC_DATA_DIR, "push_subs.json")
PUSH_SETTINGS_FILE = os.path.join(AOC_DATA_DIR, "push.json")
_PUSH_MODES = ("away", "always", "off")
_PUSH_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com",
               ".notify.windows.com", ".push.apple.com")
_PUSH_MAX_SUBS = 10
_PUSH_TAG_GAP_S = 30
_push_lock = threading.Lock()
_push_last_tag = {}


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64u_dec(s: str) -> bytes:
    s = str(s or "").strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _webpush_encrypt(plaintext: bytes, ua_public: bytes, auth_secret: bytes, as_private=None, salt=None) -> bytes:
    """RFC 8291 aes128gcm body (one record) for one push subscription.
    as_private / salt are only passed by tests (RFC 8291 appendix A)."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    as_private = as_private or ec.generate_private_key(ec.SECP256R1())
    salt = salt or os.urandom(16)
    as_public = as_private.public_key().public_bytes(serialization.Encoding.X962,
                                                      serialization.PublicFormat.UncompressedPoint)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = as_private.exchange(ec.ECDH(), ua_key)
    ikm = HKDF(hashes.SHA256(), 32, auth_secret, b"WebPush: info\x00" + ua_public + as_public).derive(shared)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    ct = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    return salt + (4096).to_bytes(4, "big") + bytes([len(as_public)]) + as_public + ct


def _vapid_keys():
    """(private key, public key b64url) -- created once, kept in AOC's data dir."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    with _push_lock:
        data = _load_json_file(PUSH_VAPID_FILE, {})
        priv = None
        if isinstance(data, dict) and data.get("private_pem"):
            try:
                priv = serialization.load_pem_private_key(data["private_pem"].encode(), None)
            except Exception:
                priv = None
        if priv is None:
            priv = ec.generate_private_key(ec.SECP256R1())
            pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()).decode()
            os.makedirs(AOC_DATA_DIR, exist_ok=True)
            with open(PUSH_VAPID_FILE, "w", encoding="utf-8") as f:
                json.dump({"private_pem": pem}, f)
        pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        return priv, _b64u(pub)


def _vapid_auth(endpoint, priv, pub_b64, now=None) -> str:
    """RFC 8292 Authorization header value for a push to `endpoint`."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from urllib.parse import urlparse
    u = urlparse(endpoint)
    header = _b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = _b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int((now or time.time()) + 12 * 3600),
                               "sub": "https://github.com/deezeed/aoc-agent-monitoring-public"},
                              separators=(",", ":")).encode())
    r, s = decode_dss_signature(priv.sign(f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256())))
    return f"vapid t={header}.{claims}.{_b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}, k={pub_b64}"


def _push_endpoint_ok(endpoint) -> bool:
    """Pure: an https URL on a known browser push service (no SSRF via a
    made-up 'subscription')."""
    from urllib.parse import urlparse
    try:
        u = urlparse(str(endpoint or ""))
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == h or (h.startswith(".") and host.endswith(h)) for h in _PUSH_HOSTS)


def _push_sanitize_sub(data):
    """Pure: POST /push/subscribe body -> subscription dict, or None."""
    sub = (data or {}).get("subscription") if isinstance(data, dict) else None
    if not isinstance(sub, dict) or not _push_endpoint_ok(sub.get("endpoint")):
        return None
    keys = sub.get("keys") or {}
    try:
        p256dh, auth = _b64u_dec(keys.get("p256dh")), _b64u_dec(keys.get("auth"))
    except Exception:
        return None
    if len(p256dh) != 65 or p256dh[0] != 4 or len(auth) != 16:
        return None
    return {"endpoint": sub["endpoint"], "keys": {"p256dh": _b64u(p256dh), "auth": _b64u(auth)},
            "label": str(data.get("label") or "")[:60] or "Phone", "created": int(time.time()),
            "user": str(_current_user().get("id") or "owner")}


def _push_subs():
    data = _load_json_file(PUSH_SUBS_FILE, [])
    return [s for s in data if isinstance(s, dict) and s.get("endpoint")] if isinstance(data, list) else []


def _push_save_subs(subs):
    os.makedirs(AOC_DATA_DIR, exist_ok=True)
    tmp = PUSH_SUBS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(subs[-_PUSH_MAX_SUBS:], f)
    os.replace(tmp, PUSH_SUBS_FILE)


def _push_subscribe(data) -> dict:
    sub = _push_sanitize_sub(data)
    if not sub:
        return {"ok": False, "error": "not a browser push subscription"}
    with _push_lock:
        subs = [s for s in _push_subs() if s["endpoint"] != sub["endpoint"]] + [sub]
        _push_save_subs(subs)
    return {"ok": True, "devices": len(subs)}


def _push_unsubscribe(endpoint, user=None) -> bool:
    """Drop a device. `user` (a person's id, from POST /push/unsubscribe): only
    their own -- a member can't switch off the owner's phones."""
    with _push_lock:
        subs = _push_subs()
        kept = [s for s in subs if s["endpoint"] != endpoint
                or (user not in (None, "owner") and s.get("user", "owner") != user)]
        if len(kept) != len(subs):
            _push_save_subs(kept)
            return True
    return False


def _load_push_settings() -> dict:
    d = _load_json_file(PUSH_SETTINGS_FILE, {})
    mode = d.get("mode") if isinstance(d, dict) and d.get("mode") in _PUSH_MODES else "away"
    return {"mode": mode}


def _save_push_settings(data) -> dict:
    cfg = {"mode": data.get("mode") if isinstance(data, dict) and data.get("mode") in _PUSH_MODES else "away"}
    os.makedirs(AOC_DATA_DIR, exist_ok=True)
    with open(PUSH_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    return cfg


def _pc_idle_seconds():
    """Seconds since this PC last had keyboard/mouse input (None if unknown)."""
    if os.name != "nt":
        return None
    try:
        import ctypes

        class _LII(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        lii = _LII()
        lii.cbSize = ctypes.sizeof(lii)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return None
        return ((ctypes.windll.kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return None


def _push_should_send(mode, idle_s, idle_min, urgent) -> bool:
    """Pure: send this alert to the phones? urgent = something is blocked on
    you right now (a held prompt / turn) -- those only happen while away."""
    if mode == "off":
        return False
    if mode == "always" or urgent:
        return True
    return idle_s is not None and idle_s >= idle_min * 60


def _push_send_one(sub, payload: bytes, urlopen=None) -> int:
    """POST one encrypted message; returns the HTTP status (0 = network error)."""
    if AOC_PREVIEW:
        return 0
    import urllib.request
    import urllib.error
    priv, pub = _vapid_keys()
    body = _webpush_encrypt(payload, _b64u_dec(sub["keys"]["p256dh"]), _b64u_dec(sub["keys"]["auth"]))
    req = urllib.request.Request(sub["endpoint"], data=body, method="POST", headers={
        "Authorization": _vapid_auth(sub["endpoint"], priv, pub), "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream", "TTL": "3600", "Urgency": "high"})
    try:
        with (urlopen or urllib.request.urlopen)(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def _push_notify(title, body, tag="aoc", urgent=False, force=False, now=None) -> int:
    """Send to every enabled device in a background thread. Returns how many
    devices it went to (0 when off / at the PC / throttled / none)."""
    now = now or time.time()
    if not force:
        cfg = _load_push_settings()
        if not _push_should_send(cfg["mode"], _pc_idle_seconds(), _load_remote_approve()["idle_min"], urgent):
            return 0
        with _push_lock:
            if now - _push_last_tag.get(tag, 0) < _PUSH_TAG_GAP_S:
                return 0
            _push_last_tag[tag] = now
    subs = _push_subs()
    if not subs:
        return 0
    # the payload is encrypted for the device, so the token may ride along --
    # but the owner's token only to the owner's devices (found in review: a
    # member who turned on push got the master token in every alert)
    url = ""
    if _tunnel_status[0] == "ready" and _tunnel_url[0]:
        url = _tunnel_url[0].rstrip("/") + "/"

    def _payload(s):
        u = url + (f"?token={_auth_token}" if url and _auth_token and s.get("user", "owner") == "owner" else "")
        return json.dumps({"title": str(title)[:120], "body": str(body)[:300], "tag": str(tag)[:60], "url": u},
                          ensure_ascii=False).encode("utf-8")

    def _go():
        gone = []
        for s in subs:
            code = _push_send_one(s, _payload(s))
            if code in (404, 410):
                gone.append(s["endpoint"])
            elif code not in (200, 201, 202):
                _log_bg_error("push", Exception(f"push service answered {code}"))
        for ep in gone:
            _push_unsubscribe(ep)
    threading.Thread(target=_go, daemon=True, name="push-send").start()
    return len(subs)


def _push_session_name(sid, cwd="") -> str:
    d = _own_status_payload() or {}
    for s in d.get("sessions_list") or []:
        if s.get("id") == sid:
            return s.get("display_name") or s.get("project") or "A session"
    # an Orchestrate agent (server/17_orchestration.py): named as one, not "A session"
    for w in d.get("orch_workers") or []:
        if w.get("id") == sid:
            return _orch_worker_label(w)
    slot = _orch_worker_path(cwd)
    return _orch_worker_label({"slot": slot}) if slot else "A session"


_SW_JS = r"""self.addEventListener('fetch', () => {});
self.addEventListener('push', (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = { body: e.data ? e.data.text() : '' }; }
  e.waitUntil(self.registration.showNotification(d.title || 'AOC', {
    body: d.body || '', tag: d.tag || 'aoc', renotify: true, icon: '/icon.svg', badge: '/icon.svg',
    data: { url: d.url || '' },
  }));
});
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const want = (e.notification.data && e.notification.data.url) || self.registration.scope;
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
    for (const c of list) { if ('focus' in c) return c.focus(); }
    return self.clients.openWindow(want);
  }));
});
"""


def _pid_exe_name(pid) -> str:
    """Return the lowercase exe filename for a live PID ("" if gone) --
    see _snapshot_processes. Runs on every /status computation, hence no
    tasklist."""
    if not pid:
        return ""
    for p, exe in _snapshot_processes() or ():
        if p == pid:
            return exe
    return ""

def _is_claude_pid_alive(pid) -> bool:
    """Fast, subprocess-free check that `pid` is still a live claude.exe
    process -- see _pid_exe_name's docstring for the technique."""
    return _pid_exe_name(pid) == "claude.exe"

def _watchdog_pid_alive() -> bool:
    """Read watchdog.pid (written by watchdog.py's own _acquire_lock) and
    confirm that PID is still a live python/pythonw process -- the infra
    health panel's signal for "is watchdog.py actually running", not just
    "did the lock file exist at some point"."""
    try:
        with open(os.path.join(AOC_DIR, "watchdog.pid")) as f:
            pid = int(f.read().strip())
    except Exception:
        return False
    return _pid_exe_name(pid) in ("python.exe", "pythonw.exe")

_WATCHDOG_LOG = os.path.join(AOC_DIR, "watchdog.log")
_SENTINEL_LOG = os.path.join(AOC_DIR, "sentinel.log")

_TAIL_READ_BYTES = 64 * 1024

def _tail_log_lines(path: str, n: int = 20) -> list:
    """Return the last `n` non-empty, stripped lines of a log file. Reads
    only the last _TAIL_READ_BYTES: watchdog.py/sentinel.py cap their logs
    at 200KB, but reading all of that on every /status build added up, and
    on a dev checkout those logs sit in a OneDrive-synced folder where a
    full read can block for tens of seconds (status_stalls.log, 09-28/09-30)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(0, size - _TAIL_READ_BYTES)
            f.seek(start)
            data = f.read()
        if start > 0:
            # drop the (probably partial) first line
            nl = data.find(b"\n")
            data = data[nl + 1:] if nl >= 0 else b""
        text = data.decode("utf-8", errors="replace")
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        return lines[-n:]
    except Exception:
        return []

def _parse_log_line_ts(line: str):
    """Parse the leading "[YYYY-MM-DD HH:MM:SS]" timestamp both
    watchdog.py's and sentinel.py's _log() write, returning epoch seconds
    or None if the line doesn't match -- a malformed/truncated line
    shouldn't crash the whole health computation."""
    m = re.match(r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]', line)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
    except Exception:
        return None

_tail_json_log_cache: dict = {}  # (path, max_lines) -> (mtime, size, parsed_entries)
_tail_json_log_cache_lock = threading.Lock()

def _tail_json_log(path: str, max_lines: int = 2000) -> list:
    """Return parsed JSON objects from a JSON-lines audit log (kill_audit.log
    / alert_audit.log shape: one JSON object per line, each carrying a 'ts'
    field). Delegates the actual tail/read to _tail_log_lines (one read
    implementation instead of two) and skips any line that fails to parse
    rather than aborting the whole read.

    Unlike watchdog.py/sentinel.py's own logs (capped at 200KB, see
    _tail_log_lines' docstring), kill_audit.log/alert_audit.log grow
    unbounded by design -- they're meant to be a permanent record (see
    _log_kill_attempt's docstring). _build_infra_health() re-reads both via
    this function roughly once a second through the /status TTL cache, so
    the parsed result is cached by (mtime, size) to skip the read+reparse
    entirely on ticks where the file hasn't changed, instead of re-reading
    an ever-growing file on every tick forever."""
    try:
        st = os.stat(path)
    except Exception:
        return []
    key = (path, max_lines)
    with _tail_json_log_cache_lock:
        cached = _tail_json_log_cache.get(key)
        if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
            return cached[2]
    out = []
    for l in _tail_log_lines(path, max_lines):
        try:
            out.append(json.loads(l))
        except Exception:
            continue
    with _tail_json_log_cache_lock:
        _tail_json_log_cache[key] = (st.st_mtime, st.st_size, out)
    return out

def _bucket_audit_events(entries: list, now_epoch: float, key: str, days: int = 7) -> dict:
    """Count JSON-lines audit log entries (each carrying a 'ts' field in
    '%Y-%m-%d %H:%M:%S' local-time form) from the last `days` days, grouped
    by whatever field the caller wants broken down by -- 'outcome' for
    kill_audit.log entries, 'event' for alert_audit.log ones. Both logs were
    write-only until now (nothing ever read them back for the dashboard),
    so this is what turns them into an actual glanceable metric."""
    cutoff = now_epoch - days * 86400
    counts = {}
    for e in entries:
        try:
            ts = datetime.strptime(e.get("ts", ""), "%Y-%m-%d %H:%M:%S").timestamp()
        except Exception:
            continue
        if ts < cutoff:
            continue
        k = e.get(key) or "(unknown)"
        counts[k] = counts.get(k, 0) + 1
    return counts

def _build_infra_health() -> dict:
    """Surface everything the dashboard's INFRA settings tab shows in one
    payload: watchdog.py/sentinel.py health (both separate OS processes with
    no shared Python state, so read from their log/pid files directly rather
    than anything in-process) plus the last-7-days kill_audit.log/
    alert_audit.log breakdowns -- a different kind of data (audit-log
    summaries, not process liveness), bundled here anyway since this is
    already the one function/fetch backing that whole tab."""
    now = time.time()
    wd_lines = _tail_log_lines(_WATCHDOG_LOG, 50)
    wd_last_ts = _parse_log_line_ts(wd_lines[-1]) if wd_lines else None
    wd_restarts = sum(1 for l in wd_lines if "Restarting monitor" in l)

    sn_lines = _tail_log_lines(_SENTINEL_LOG, 20)
    sn_last_ts = _parse_log_line_ts(sn_lines[-1]) if sn_lines else None

    kill_events = _bucket_audit_events(_tail_json_log(KILL_AUDIT_LOG), now, "outcome")
    alert_events = _bucket_audit_events(_tail_json_log(ALERT_AUDIT_LOG), now, "event")

    return {
        "watchdog": {
            "pid_alive": _watchdog_pid_alive(),
            "last_log_line": wd_lines[-1] if wd_lines else "",
            "last_log_ts": wd_last_ts,
            "restarts_recent": wd_restarts,
        },
        "sentinel": {
            "last_log_line": sn_lines[-1] if sn_lines else "",
            "last_log_ts": sn_last_ts,
            # sentinel's own Task Scheduler trigger fires every ~15min
            # (observed); 2400s (40min) gives real headroom before
            # flagging stale, so a single delayed tick doesn't false-alarm
            "stale": sn_last_ts is None or (now - sn_last_ts) > 2400,
        },
        "monitor_uptime_s": now - _PROCESS_START,
        "kill_audit_7d": kill_events,
        "alert_audit_7d": alert_events,
    }

_infra_health_cache = [None, 0.0]  # [last payload, built-at epoch]
_infra_health_refresh_lock = threading.Lock()  # held while a background refresh runs

def _infra_health_refresh_bg():
    try:
        _infra_health_cache[0] = _build_infra_health()
        _infra_health_cache[1] = time.time()
    except Exception:
        pass
    finally:
        _infra_health_refresh_lock.release()

def _infra_health_snapshot():
    """Non-blocking, for /status -- same pattern as
    _ngrok_authtoken_configured. _build_infra_health reads log/pid files next
    to monitor.py; built inline while holding _status_payload_cache_lock, one
    slow read (OneDrive sync) stalled every /status caller for up to 45 s and
    got monitor.py restarted by the watchdog (status_stalls.log 2026-09-28
    16:09). Returns the cached payload (None until the first refresh lands --
    the dashboard skips a missing infra_health) with a live uptime, and
    starts one background refresh when it's over 10 s old."""
    if (time.time() - _infra_health_cache[1] >= 10
            and _infra_health_refresh_lock.acquire(blocking=False)):
        threading.Thread(target=_infra_health_refresh_bg, daemon=True,
                         name="infra-health-refresh").start()
    cached = _infra_health_cache[0]
    if cached is None:
        return None
    return {**cached, "monitor_uptime_s": time.time() - _PROCESS_START,
            "hooks_bad": _hooks_health_bad(_hooks_health.get("checks"))}
