# AOC server, part 16: WebSocket + terminal, dashboard page, /status payload.
# monitor.py exec()s server/NN_*.py in number order into ONE shared
# namespace (its own) -- not an importable module: names from earlier
# parts are plain globals here, and __file__ is monitor.py.
# ── WebSocket + Terminal ──────────────────────────────────────────────────────
import hashlib as _hashlib, base64 as _b64, struct as _struct, queue as _queue

def _ws_key(k):
    return _b64.b64encode(
        _hashlib.sha1((k+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()
    ).decode()

_WS_MAX_FRAME = 4 * 1024 * 1024

def _ws_recv(sock, limit=None):
    """One frame -> (opcode, payload), or (None, None) when the client is gone
    or sends a frame over `limit` bytes. The length comes from the client: an
    uncapped one let any signed-in viewer make recv() allocate gigabytes."""
    limit=_WS_MAX_FRAME if limit is None else limit
    def rx(n):
        buf=bytearray()
        while len(buf)<n:
            c=sock.recv(min(n-len(buf),65536))
            if not c: raise ConnectionError
            buf+=c
        return bytes(buf)
    try:
        h=rx(2); op=h[0]&0xf; msk=bool(h[1]&0x80); ln=h[1]&0x7f
        if ln==126: ln=_struct.unpack('>H',rx(2))[0]
        elif ln==127: ln=_struct.unpack('>Q',rx(8))[0]
        if ln>limit: return None,None
        mk=rx(4) if msk else b''; data=rx(ln)
        if msk and ln:
            key=(mk*(ln//4+1))[:ln]
            data=(int.from_bytes(data,'big')^int.from_bytes(key,'big')).to_bytes(ln,'big')
        return op,data
    except: return None,None

def _ws_send(sock, data):
    if isinstance(data,str): data=data.encode('utf-8','replace'); op=0x81
    else: op=0x82
    ln=len(data)
    if ln<126: hdr=bytes([0x80|op,ln])
    elif ln<65536: hdr=bytes([0x80|op,126])+_struct.pack('>H',ln)
    else: hdr=bytes([0x80|op,127])+_struct.pack('>Q',ln)
    try: sock.sendall(hdr+data)
    except: pass

def _ws_send_or_raise(sock, text: str):
    """Same framing as _ws_send, but propagates send failures — needed by loops
    that must notice a dead client and stop (unlike the terminal's fire-and-forget
    output, where silently dropping a frame after disconnect is fine)."""
    data = text.encode('utf-8', 'replace')
    ln = len(data)
    if ln < 126: hdr = bytes([0x81, ln])
    elif ln < 65536: hdr = bytes([0x81, 126]) + _struct.pack('>H', ln)
    else: hdr = bytes([0x81, 127]) + _struct.pack('>Q', ln)
    sock.sendall(hdr + data)

_term_procs={}
_term_lock=threading.Lock()

def _hard_kill_pty(pty):
    """winpty equivalent of _hard_kill_proc (see there for why this exists) —
    plus a second, distinct problem found while verifying this one: pty.pid is
    the winpty *agent* process, not the shell it launches. TerminateProcess
    (what terminate()/os.kill both use on Windows) does not cascade to child
    processes, so killing just the agent leaves its bash.exe child orphaned.
    Confirmed live with Get-CimInstance Win32_Process: the surviving bash.exe's
    ParentProcessId was exactly the agent PID that had just been "successfully"
    terminated.

    `taskkill /T` (tree-kill) is what actually takes the shell down with it —
    but ORDER MATTERS: calling pty.terminate() first (as an earlier version of
    this function did) kills the agent immediately, and once that parent PID
    no longer exists Windows can no longer enumerate "processes whose parent
    was X" for `/T` to act on — confirmed live, the child survived every time
    terminate() ran first. So taskkill /T has to run FIRST, while the whole
    tree is still alive and queryable; pty.terminate() is only a fallback for
    the (non-Windows) case taskkill isn't available at all."""
    if pty is None:
        return
    try:
        pid = pty.pid
    except Exception:
        pid = None
    if pid and os.name == "nt":
        try:
            import subprocess as _sp
            _sp.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True, timeout=5, creationflags=_sp.CREATE_NO_WINDOW)
        except Exception:
            pass
    else:
        try:
            if pty.isalive():
                pty.terminate(force=True)
        except Exception:
            pass

def _handle_terminal_ws(handler):
    key=handler.headers.get('Sec-WebSocket-Key','')
    handler.send_response(101)
    handler.send_header('Upgrade','websocket')
    handler.send_header('Connection','Upgrade')
    handler.send_header('Sec-WebSocket-Accept',_ws_key(key))
    handler.end_headers()
    handler.wfile.flush()
    sock=handler.connection; sock.settimeout(None)

    try:
        import winpty as _winpty
        env=os.environ.copy(); env['TERM']='xterm-256color'
        # Add claude-code dir to PATH so `claude` works in bash
        import glob as _glob
        _claude_dirs = _glob.glob(os.path.join(
            os.environ.get('LOCALAPPDATA',''), 'Packages', 'Claude_pzs8sxrjxfjjc',
            'LocalCache', 'Roaming', 'Claude', 'claude-code', '*'))
        if _claude_dirs:
            _claude_bin = max(_claude_dirs)  # latest version
            env['PATH'] = _claude_bin + os.pathsep + env.get('PATH', '')
        _BASH = r'C:\Program Files\Git\bin\bash.exe'
        try:
            pty=_winpty.PtyProcess.spawn(
                [_BASH,'--login','-i'],
                dimensions=(24,120), env=env,
                cwd=os.path.expanduser('~'))
        except Exception as _e:
            _ws_send(sock, f'\r\n\x1b[31m[winpty error: {_e}]\x1b[0m\r\n'.encode())
            raise
        tid=str(id(sock))
        with _term_lock: _term_procs[tid]=pty

        def _reader():
            try:
                while pty.isalive():
                    try: chunk=pty.read(4096)
                    except EOFError: break
                    if chunk: _ws_send(sock, chunk.encode('utf-8','replace') if isinstance(chunk,str) else chunk)
            except: pass
            finally:
                try: sock.sendall(bytes([0x88,0x00]))
                except: pass

        threading.Thread(target=_reader,daemon=True).start()
        try:
            while True:
                op,data=_ws_recv(sock)
                if op is None or op==8: break
                if op in(1,2) and data:
                    try:
                        text=data.decode('utf-8','replace') if isinstance(data,bytes) else data
                        # resize protocol: \x1b[resize:rows:cols
                        if text.startswith('\x00resize:'):
                            parts=text[8:].split(':')
                            if len(parts)==2:
                                rows,cols=int(parts[0]),int(parts[1])
                                pty.setwinsize(rows,cols)
                            continue
                        pty.write(text)
                    except: break
        except: pass
        finally:
            _hard_kill_pty(pty)
            with _term_lock: _term_procs.pop(tid,None)

    except ImportError:
        # fallback without PTY -- limited interactivity
        env=os.environ.copy(); env['TERM']='xterm-256color'; env['PYTHONUNBUFFERED']='1'
        proc=subprocess.Popen(
            [r'C:\Program Files\Git\bin\bash.exe','--login','-i'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW, bufsize=0, env=env,
            cwd=os.path.expanduser('~'))
        tid=str(id(sock))
        with _term_lock: _term_procs[tid]=proc
        def _reader2():
            try:
                while True:
                    chunk=proc.stdout.read(4096)
                    if not chunk: break
                    _ws_send(sock,chunk)
            except: pass
            finally:
                try: sock.sendall(bytes([0x88,0x00]))
                except: pass
        threading.Thread(target=_reader2,daemon=True).start()
        try:
            while True:
                op,data=_ws_recv(sock)
                if op is None or op==8: break
                if op in(1,2) and data:
                    try: proc.stdin.write(data); proc.stdin.flush()
                    except: break
        except: pass
        finally:
            _hard_kill_proc(proc)
            with _term_lock: _term_procs.pop(tid,None)

# ── Shared sessions: one Claude Code terminal, several people ─────────────
# The owner starts a shared session: an interactive `claude` (or a shell)
# in a folder, in a pty AOC owns. Anyone signed in to this AOC can open it
# (/shared/ws?id=): everyone sees the same screen (a newcomer gets the
# recent output first), the owner and members type into it, viewers watch.
# Text frames carry who's there / who's typing ("\x00people:" / "\x00who:");
# terminal output goes as binary frames. It runs until the owner stops it,
# or 4 hours after the last person left.
_shared = {}
_shared_lock = threading.Lock()
_SHARED_SCROLLBACK = 256 * 1024
_SHARED_IDLE_S = 4 * 3600


def _shared_trim(buf, limit=_SHARED_SCROLLBACK):
    """Pure: keep the newest `limit` bytes of the replay buffer."""
    return buf[-limit:] if len(buf) > limit else buf


def _shared_people(entry):
    """Pure: who's connected, one line per person (first role seen)."""
    seen = {}
    for u in list(entry["clients"].values()):
        seen.setdefault(u["id"], {"name": u["name"], "role": u["role"]})
    return list(seen.values())


class _SharedOut:
    """One writer thread per shared-session client. Every send to it goes
    through this queue, so a slow or stalled client (a phone on bad Wi-Fi, a
    stuck tunnel) can't block the pty reader -- everyone else's output, and
    Claude Code itself once the pty buffer fills -- and two threads never
    interleave frames on one socket. A client that falls LIMIT frames behind
    is dropped."""
    LIMIT = 1024

    def __init__(self, entry, sock):
        self.entry, self.sock, self.dead = entry, sock, False
        self.q = _queue.Queue(self.LIMIT)
        threading.Thread(target=self._run, daemon=True, name="shared-out").start()

    def put(self, item):
        if self.dead:
            return
        try:
            self.q.put_nowait(item)
        except _queue.Full:
            self.drop()

    def stop(self):
        self.dead = True
        try:
            self.q.put_nowait(None)     # wakes the writer if it waits for work
        except _queue.Full:
            pass                        # it's busy sending; it sees `dead` next

    def drop(self):
        self.entry["clients"].pop(self.sock, None)
        self.entry.get("out", {}).pop(self.sock, None)
        try:
            self.sock.shutdown(2)       # wakes its handler, blocked in recv
        except Exception:
            pass
        self.stop()

    def _run(self):
        while True:
            item = self.q.get()
            if item is None or self.dead:
                return
            try:
                if isinstance(item, tuple):     # ("raw", bytes): a close frame
                    self.sock.sendall(item[1])
                else:
                    _ws_send_raise(self.sock, item)
            except Exception:
                self.drop()
                return


_shared_out_lock = threading.Lock()


def _shared_out(entry, sock):
    with _shared_out_lock:
        outs = entry.setdefault("out", {})
        o = outs.get(sock)
        if o is None:
            o = outs[sock] = _SharedOut(entry, sock)
        return o


def _shared_broadcast(entry, data, skip=None):
    for sock in list(entry["clients"]):
        if sock is not skip:
            _shared_out(entry, sock).put(data)


def _ws_send_raise(sock, data):
    """_ws_send that tells a dead client (shared sessions drop it)."""
    if isinstance(data, str):
        data, op = data.encode("utf-8", "replace"), 0x81
    else:
        op = 0x82
    ln = len(data)
    hdr = bytes([op, ln]) if ln < 126 else (bytes([op, 126]) + _struct.pack(">H", ln) if ln < 65536
                                             else bytes([op, 127]) + _struct.pack(">Q", ln))
    sock.sendall(hdr + data)


def _shared_spawn(cwd, kind):
    import winpty as _winpty
    env = os.environ.copy()
    env["TERM"] = "xterm-256color"
    if kind == "claude":
        exe = _find_claude_exe()
        if not exe:
            raise RuntimeError("Claude Code (claude) not found")
        argv = [exe]
    else:
        argv = [r"C:\Program Files\Git\bin\bash.exe", "--login", "-i"]
    return _winpty.PtyProcess.spawn(argv, dimensions=(30, 120), env=env, cwd=cwd)


def _shared_start(data, user, spawn=None, now=None):
    """POST /shared/start {name?, cwd, kind: claude | shell} (owner)."""
    now = now or time.time()
    data = data if isinstance(data, dict) else {}
    cwd = str(data.get("cwd") or "").strip().strip('"')
    if not cwd or not os.path.isabs(cwd) or not os.path.isdir(cwd):
        return {"ok": False, "error": "pick an existing folder (full path)"}
    kind = "shell" if data.get("kind") == "shell" else "claude"
    name = re.sub(r"\s+", " ", str(data.get("name") or "")).strip()[:60] or os.path.basename(cwd.rstrip("\\/")) or cwd
    try:
        pty = (spawn or _shared_spawn)(cwd, kind)
    except Exception as e:
        return {"ok": False, "error": f"couldn't start it: {e}"[:300]}
    sid = os.urandom(4).hex()
    entry = {"id": sid, "name": name, "cwd": cwd, "kind": kind, "by": user.get("name", ""), "created": now, "pty": pty,
             "clients": {}, "buf": b"", "last_client": now, "typed": {},
             "lock": threading.Lock()}   # buf + who gets it: a newcomer neither misses nor doubles output
    with _shared_lock:
        _shared[sid] = entry

    def _reader():
        try:
            while pty.isalive():
                try:
                    chunk = pty.read(4096)
                except EOFError:
                    break
                if not chunk:
                    continue
                b = chunk.encode("utf-8", "replace") if isinstance(chunk, str) else chunk
                with entry["lock"]:
                    entry["buf"] = _shared_trim(entry["buf"] + b)
                    _shared_broadcast(entry, b)
        except Exception:
            pass
        finally:
            _shared_broadcast(entry, "\x00ended")
    threading.Thread(target=_reader, daemon=True, name=f"shared-{sid}").start()
    return {"ok": True, "id": sid}


def _shared_list(now=None):
    """GET /shared: the shared sessions running now."""
    now = now or time.time()
    with _shared_lock:
        items = list(_shared.values())
    out = []
    for e in items:
        try:
            alive = bool(e["pty"].isalive())
        except Exception:
            alive = False
        out.append({"id": e["id"], "name": e["name"], "cwd": e["cwd"], "kind": e["kind"], "by": e["by"],
                    "created": e["created"], "alive": alive, "people": _shared_people(e)})
    return {"ok": True, "sessions": sorted(out, key=lambda x: -x["created"])}


def _shared_stop(data):
    """POST /shared/stop {id} (owner): end it for everyone."""
    with _shared_lock:
        e = _shared.pop(str((data or {}).get("id") or ""), None)
    if not e:
        return {"ok": False, "error": "unknown shared session"}
    _shared_broadcast(e, "\x00ended")
    _hard_kill_pty(e["pty"])
    for sock in list(e["clients"]):
        _shared_out(e, sock).put(("raw", bytes([0x88, 0x00])))
    return {"ok": True}


def _shared_sweep(now=None):
    """Drop ended sessions; end those nobody has had open for 4 hours."""
    now = now or time.time()
    kill = []
    with _shared_lock:
        for sid, e in list(_shared.items()):
            try:
                alive = e["pty"].isalive()
            except Exception:
                alive = False
            if not alive or (not e["clients"] and now - e["last_client"] > _SHARED_IDLE_S):
                _shared.pop(sid, None)
                if alive:
                    kill.append(e)
    for e in kill:      # outside the lock: taskkill can take seconds, and joins / starts wait on it
        _hard_kill_pty(e["pty"])


def _shared_sweeper():
    """The sweep every 5 minutes -- it used to run only when someone opened the
    shared list, so an idle session's `claude` could live forever."""
    while True:
        time.sleep(300)
        try:
            _shared_sweep()
        except Exception as e:
            _log_bg_error("shared sweep", e)


def _shared_input_allowed(role):
    """Pure: who may type into a shared session."""
    return role in ("owner", "member")


def _handle_shared_ws(handler, sid, user):
    key = handler.headers.get("Sec-WebSocket-Key", "")
    with _shared_lock:
        entry = _shared.get(sid)
    if not entry:
        handler._serve(404, "text/plain", b"No such shared session")
        return
    handler.send_response(101)
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", _ws_key(key))
    handler.end_headers()
    handler.wfile.flush()
    sock = handler.connection
    sock.settimeout(None)
    out = _shared_out(entry, sock)
    with entry["lock"]:     # replay + join in one step: the reader can't slip output in between
        if entry["buf"]:
            out.put(entry["buf"])
        out.put("\x00me:" + json.dumps({"name": user["name"], "role": user["role"],
                                         "can_type": _shared_input_allowed(user["role"])}))
        entry["clients"][sock] = user
    entry["last_client"] = time.time()
    _shared_broadcast(entry, "\x00people:" + json.dumps(_shared_people(entry)))
    try:
        while True:
            op, data = _ws_recv(sock)
            if op is None or op == 8:
                break
            if op not in (1, 2) or not data:
                continue
            text = data.decode("utf-8", "replace")
            if text.startswith("\x00resize:"):
                if user["role"] == "owner":
                    try:
                        rows, cols = (int(x) for x in text[8:].split(":")[:2])
                        entry["pty"].setwinsize(rows, cols)
                    except Exception:
                        pass
                continue
            if not _shared_input_allowed(user["role"]):
                continue
            try:
                entry["pty"].write(text)
            except Exception:
                break
            now = time.time()
            if now - entry["typed"].get(user["id"], 0) > 1.0:
                entry["typed"][user["id"]] = now
                _shared_broadcast(entry, "\x00who:" + user["name"], skip=sock)
    finally:
        entry["clients"].pop(sock, None)
        o = entry.get("out", {}).pop(sock, None)
        if o:
            o.stop()
        entry["last_client"] = time.time()
        _shared_broadcast(entry, "\x00people:" + json.dumps(_shared_people(entry)))


def _handle_events_ws(handler):
    """WebSocket push of the dashboard's live status — replaces the old 500ms
    client poll. SSE was tried first: it works fine directly, but never gets
    forwarded at all through a Cloudflare quick tunnel (chunked responses
    just never arrive on the other end, confirmed with 0 bytes over 35s) —
    whereas WebSocket already reliably survives the same tunnel, same as the
    terminal channel below, so that's what carries this too."""
    key = handler.headers.get('Sec-WebSocket-Key', '')
    handler.send_response(101)
    handler.send_header('Upgrade', 'websocket')
    handler.send_header('Connection', 'Upgrade')
    handler.send_header('Sec-WebSocket-Accept', _ws_key(key))
    handler.end_headers()
    handler.wfile.flush()
    sock = handler.connection
    sock.settimeout(None)
    try:
        sock.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_NODELAY, 1)
    except Exception:
        pass
    last_version = -1
    try:
        while True:
            with _status_cond:
                _status_cond.wait_for(lambda: _status_version[0] != last_version, timeout=2)
            last_version = _status_version[0]
            payload = _build_status_payload()
            _ws_send_or_raise(sock, json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass  # client disconnected (or a genuine send error) — end this thread

WEB_DIR = os.path.join(AOC_DIR, "web")


def _build_dashboard_html(index: str, css: str, js: str) -> str:
    """Pure: web/index.html with web/app.css and web/app.js inlined in place
    of their <link>/<script src> lines -- one self-contained page, exactly as
    when it was a string in this file (the page is fetched with the token,
    separate sub-resources wouldn't be). Raises if a placeholder is missing,
    so a broken web/index.html fails startup instead of serving half a page."""
    link, script = '<link rel="stylesheet" href="app.css">\n', '<script src="app.js"></script>\n'
    if index.count(link) != 1 or index.count(script) != 1:
        raise RuntimeError("web/index.html must reference app.css and app.js exactly once")
    # ending at "</html>" without the file's final newline, like the old string
    return (index.replace(link, "<style>\n" + css + "</style>\n")
                 .replace(script, "<script>\n" + js + "</script>\n").rstrip("\n"))


def _read_web(name: str) -> str:
    with open(os.path.join(WEB_DIR, name), encoding="utf-8") as f:
        return f.read()


def _web_js_parts(js_dir: str) -> list:
    """web/js/NN_name.js in page order. Raises unless they are numbered
    01..N without a gap -- a missing part would leave the page with
    undefined functions, so it fails startup instead (like server/ parts)."""
    names = sorted(n for n in os.listdir(js_dir) if n.endswith(".js"))
    nums = [int(m.group(1)) if (m := re.match(r"(\d\d)_\w+\.js$", n)) else 0 for n in names]
    if not names or nums != list(range(1, len(names) + 1)):
        raise RuntimeError(f"web/js/ must hold 01_*.js .. NN_*.js with no gap, found {names}")
    return [os.path.join(js_dir, n) for n in names]


def _read_web_js() -> str:
    """The dashboard script: web/js/ parts joined in order, byte for byte --
    one <script>, one global scope, exactly as when it was web/app.js
    (index.html still names it app.js; that line is where it goes)."""
    out = []
    for p in _web_js_parts(os.path.join(WEB_DIR, "js")):
        with open(p, encoding="utf-8") as f:
            out.append(f.read())
    return "".join(out)


HTML = _build_dashboard_html(_read_web("index.html"), _read_web("app.css"), _read_web_js())


def _build_status_payload() -> dict:
    """Shared by GET /status and the /events SSE stream — one place computing
    the dashboard's live data so the two paths can never drift out of shape.

    Wrapped in a version-gated cache (see _status_payload_cache above): the
    whole body runs while holding _status_payload_cache_lock, so concurrent
    callers (watchdog's poll, a manual GET, and every open /events WS client
    all waking on the same _status_version bump) block briefly and share one
    rebuilt payload instead of each independently redoing the full disk-read +
    multi-MB json parse/rebuild -- that pile-up was the confirmed cause of
    watchdog's recurring "Monitor not responding" restarts."""
    with _status_payload_cache_lock:
        _now = time.time()
        _cache = _status_payload_cache
        if _status_cache_is_fresh(_cache["version"], _cache["built_at"], _status_version[0], _now):
            return _cache["payload"]
        data = _build_status_payload_uncached()
        _status_payload_cache["version"] = _status_version[0]
        _status_payload_cache["built_at"] = time.time()
        _status_payload_cache["payload"] = data
        return data


def _build_status_payload_uncached() -> dict:
    """The actual rebuild -- only ever called from inside _build_status_payload's
    cache lock above, never directly."""
    data = _load_status()
    _check_session_start(data)
    _log.poll()
    now_epoch = time.time()
    # active sessions: keep for 30min; inactive: disappear after 5min
    sessions_from_dict = {}
    for _sk, _sv in data.get("sessions", {}).items():
        if _sk.startswith(("functest_", "stress_", "browser_test", "autosave_test")):
            continue
        if _sv.get("dismissed"):
            continue
        _ls = _sv.get("last_seen_epoch", 0)
        if _sv.get("session_active", False) and (_ls > now_epoch - 1800 or _is_claude_pid_alive(_sv.get("host_pid"))):
            sessions_from_dict[_sk] = _sv
        elif not _sv.get("session_active", False) and _ls > now_epoch - 300:
            sessions_from_dict[_sk] = _sv
    with _pr_link_lock:
        _pr_link_cache_snapshot = dict(_pr_link_cache)
    active_sessions = [
        {"id": k,
         "project": v.get("project", ""),
         "display_name": v.get("display_name", ""),
         "cwd": v.get("cwd", ""),
         "last_seen": v.get("last_seen", ""),
         "session_active": _session_really_active(v, now_epoch),
         "model": v.get("model", ""),
         "cc_version": v.get("cc_version", ""),
         "git_branch": v.get("git_branch", ""),
         "input_tokens": v.get("input_tokens", 0),
         "output_tokens": v.get("output_tokens", 0),
         "cache_read_tokens": v.get("cache_read_tokens", 0),
         "cache_write_tokens": v.get("cache_write_tokens", 0),
         "estimated_cost": v.get("estimated_cost", 0.0),
         "msg_count": v.get("msg_count", 0),
         "first_ts": v.get("first_ts", ""),
         "last_ts": v.get("last_ts", ""),
         "host_pid": v.get("host_pid"),
         "waiting_on_you": v.get("waiting_on_you", False),
         "waiting_kind": (v.get("waiting_kind") or "turn") if v.get("waiting_on_you") else "",
         "waiting_message": v.get("waiting_message", "") if v.get("waiting_on_you") else "",
         "last_message": v.get("last_message", ""),
         "loop": v.get("loop") if _session_really_active(v, now_epoch) else None,
         "away": _activity_view(v.get("activity_raw"), now_epoch),
         "edited_count": v.get("edited_count", 0),
         "note": v.get("note", ""),
         "pr_url": _pr_link_cache_snapshot.get(v.get("git_branch", ""), {}).get("url"),
         "waiting_secs": _compute_waiting_secs(v, now_epoch)}
        for k, v in sessions_from_dict.items()
    ]
    _ctx = _context_snapshot([s["id"] for s in active_sessions])
    _perms = _perm_pending_by_session(time.time())
    _replies = _reply_pending_by_session(time.time())
    with _budget_lock:
        _budget_snap = {"sessions": dict(_budget_state["sessions"]), "day": _budget_state["day"]}
    _cs_cfg = _ctxsave_settings()
    with _ctxsave_lock:
        _cs_snap = {k: dict(v) for k, v in _ctxsave_state.items()}
    for s in active_sessions:
        s["ctx_save"] = _ctxsave_view(_cs_snap.get(s["id"]), _cs_cfg)
        s["context"] = _ctx.get(s["id"])
        if s["context"] and s["context"].get("cache_ttl"):
            cold, warm = _recache_cost(s["context"].get("recache_tokens") or s["context"].get("tokens"),
                                       s["context"]["cache_ttl"], s.get("model"))
            # a copy: the view itself is cached in _context_state
            s["context"] = {**s["context"], "recache_usd": round(cold, 2), "warm_usd": round(warm, 2)}
        s["perm_requests"] = _perms.get(s["id"], [])
        _dg = [q for q in s["perm_requests"] if q.get("danger")]
        if _dg:
            # a held dangerous command blocks the turn mid-tool-call: the hook
            # still reports the session as running, so mark it waiting here
            s["waiting_on_you"] = True
            s["waiting_kind"] = "permission"
            s["waiting_message"] = "⚠ " + _dg[0]["summary"]
            s["waiting_secs"] = max(s.get("waiting_secs") or 0, _dg[0]["age_s"])
        s["reply_request"] = _replies.get(s["id"])
        s["budget"] = _budget_snap["sessions"].get(s["id"]) or _budget_snap["day"]
    # sessions_count: floor at the real claude.exe process count (background
    # scanner, _claude_proc_worker, counts claude.exe processes every 2s). Hook-tracked
    # sessions can under-count — a CLI sitting idle with no fresh heartbeat,
    # or one whose entry got dismissed/stale — so trusting the dict alone
    # made AOC report fewer CLIs than are actually running.
    data["sessions_count"] = max(len(sessions_from_dict), _claude_proc_count[0])
    data["sessions_list"] = active_sessions
    data["last_autosave"] = _autosave_time[0] or None
    # Expose claude binary path so frontend can launch it in bash (cached)
    if _claude_bin_cache[0] is None:
        import glob as _g
        _cdirs = _g.glob(os.path.join(
            os.environ.get('LOCALAPPDATA',''),
            'Packages','Claude_pzs8sxrjxfjjc',
            'LocalCache','Roaming','Claude','claude-code','*'))
        if _cdirs:
            _claude_bin_cache[0] = max(_cdirs).replace('\\','/') + '/claude.exe'
    if _claude_bin_cache[0]:
        data["claude_bin"] = _claude_bin_cache[0]
    data["tunnel"] = {
        "status": _tunnel_status[0],
        "url": _tunnel_url[0],
        "dl_pct": _tunnel_dl_pct[0],
        "provider": _tunnel_provider[0],
        "cf_found": bool(_find_cloudflared()),
        "ngrok_found": bool(_find_ngrok()),
        "ngrok_authtoken_ok": _ngrok_authtoken_configured(),
        "error_detail": _tunnel_error_detail[0],
    }
    with _self_update_lock:
        data["self_update"] = dict(_self_update_status)
    with _update_job_lock:
        data["self_update"]["job"] = dict(_update_job)
    data["infra_health"] = _infra_health_snapshot()
    data["rate_limits"] = _rate_limits_snapshot()
    data["queue"] = _queue_view(data["rate_limits"], time.time())
    data["orch_attention"] = list(_orch_attention_state["items"])
    data["presence"] = _users_present()
    with _limit_stops_lock:
        _stops = _limit_stops_load()
    data["limit_stops"] = _limit_stops_view(_stops, {s.get("id") for s in active_sessions if s.get("session_active") is not False},
                                            data["queue"], data["rate_limits"], time.time())
    return data
