# AOC server, part 05: Force Stop core, AOC Cloud, auth token, license, auto-save, tunnel.
# monitor.py exec()s server/NN_*.py in number order into ONE shared
# namespace (its own) -- not an importable module: names from earlier
# parts are plain globals here, and __file__ is monitor.py.
# ── Force Stop core (shared by POST /kill_session and the cloud poller) ─────

_KILL_OUTCOME_RESPONSES = {
    "killed": (200, b'{"ok":true}'),
    "refused_no_host_pid": (400, b'{"ok":false,"error":"no host_pid recorded for this session"}'),
    "refused_pid_mismatch": (409, b'{"ok":false,"error":"PID no longer belongs to claude.exe -- refusing to kill"}'),
}

def _kill_session_core(sid: str, remote_addr: str = "") -> str:
    """Kill one CLI session's claude.exe and return the outcome string
    (a key of _KILL_OUTCOME_RESPONSES, also what kill_audit.log records).
    Extracted from POST /kill_session so the cloud command poller
    (_cloud_command_worker) runs the exact same checks and audit trail
    instead of a second copy that could drift."""
    with _status_lock:
        status = _load_status()
        sess = status.get("sessions", {}).get(sid)
    host_pid = sess.get("host_pid") if sess else None
    if not host_pid:
        _log_kill_attempt(sid, sess, host_pid, "refused_no_host_pid", remote_addr)
        return "refused_no_host_pid"
    # PID-reuse guard: Windows recycles PIDs, and by the time a user clicks
    # Force Stop the original claude.exe may be long gone with that PID
    # reassigned to something completely unrelated -- verify identity right
    # before killing, same pattern as _reap_stray_tunnel_process's "only
    # ever touch a PID after confirming what it actually is" rule, never a
    # blanket kill-by-name.
    r = _run(["tasklist", "/FI", f"PID eq {host_pid}"], timeout=5)
    if "claude.exe" not in (r.stdout or "").lower():
        _log_kill_attempt(sid, sess, host_pid, "refused_pid_mismatch", remote_addr)
        return "refused_pid_mismatch"
    _run(["taskkill", "/F", "/PID", str(host_pid)], timeout=5)
    with _status_lock:
        status = _load_status()
        if sid in status.get("sessions", {}):
            status["sessions"][sid]["session_active"] = False
            status["sessions"][sid]["dismissed"] = True
        _save_status(status)
    _log_kill_attempt(sid, sess, host_pid, "killed", remote_addr)
    return "killed"


# ── AOC Cloud: remote Force Stop poller + offline spool drain ──────────────
# Only does anything when this machine was set up with
# `setup.py --cloud-url ... --cloud-key ...` (the same two files the hook
# reads). Otherwise it's a cheap file-exists check every tick.
#
# Force Stop from the cloud dashboard can't reach this PC directly, so the
# cloud queues it and this polls (saas-backend/app/services/commands.py).
# Commands for sessions this machine doesn't know are left alone: another
# machine on the same account may own them, and the cloud expires any
# command nobody picks up within 5 minutes.
#
# The hook spools cloud updates it couldn't deliver (offline laptop, cloud
# down) to aoc_cloud_spool.jsonl; this drains them in order. Done here and
# not in the hook, because the hook runs as many short-lived processes that
# would race each other, and this is the one long-running process.

CLOUD_HOOKS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "hooks")
CLOUD_SPOOL_FILE = os.path.join(CLOUD_HOOKS_DIR, "aoc_cloud_spool.jsonl")
_CLOUD_POLL_INTERVAL_S = 10

def _read_cloud_config():
    """Same files and rules as hooks/aoc_hook.py's _read_cloud_config."""
    try:
        with open(os.path.join(CLOUD_HOOKS_DIR, "aoc_cloud_url.txt"), encoding="utf-8") as f:
            url = f.read().strip()
        with open(os.path.join(CLOUD_HOOKS_DIR, "aoc_api_key.txt"), encoding="utf-8") as f:
            key = f.read().strip()
        if url and key:
            return url.rstrip("/"), key
    except Exception:
        pass
    return None, None

def _cloud_request(url: str, key: str, method: str, path: str, data=None, timeout: float = 8):
    import urllib.request as _ur
    body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
    req = _ur.Request(f"{url}{path}", data=body, method=method, headers={
        "Content-Type": "application/json; charset=utf-8", "Authorization": f"Bearer {key}",
    })
    with _ur.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None

def _drain_cloud_spool(url: str, key: str, spool_path: str = None) -> int:
    """Send spooled updates oldest-first; stop at the first failure and keep
    the rest for the next tick. The spool is first renamed to .draining
    (atomic), so the hook can keep appending to a fresh spool meanwhile and
    order is preserved: .draining is always fully sent before the next
    rename. Returns how many entries were delivered."""
    spool_path = spool_path or CLOUD_SPOOL_FILE
    draining = spool_path + ".draining"
    if not os.path.exists(draining):
        if not os.path.exists(spool_path):
            return 0
        try:
            os.replace(spool_path, draining)
        except OSError:
            return 0  # the hook has it open for an append right now; next tick
    with open(draining, encoding="utf-8") as f:
        lines = [ln for ln in f.read().splitlines() if ln.strip()]
    sent = 0
    for i, line in enumerate(lines):
        try:
            job = json.loads(line)
        except ValueError:
            sent += 1  # a torn/corrupt line can never succeed -- drop it
            continue
        try:
            _cloud_request(url, key, "POST", job["path"], job["data"], timeout=5)
        except Exception:
            with open(draining, "w", encoding="utf-8") as f:
                f.writelines(ln + "\n" for ln in lines[i:])
            return sent
        sent += 1
    os.remove(draining)
    return sent

def _process_cloud_commands(url: str, key: str, unreported: dict) -> None:
    """One poll: execute open kill commands for sessions this machine
    knows, then report each outcome. `unreported` ({command_id: outcome})
    survives between ticks, so a kill whose report failed is re-reported,
    never re-executed."""
    for cmd in _cloud_request(url, key, "GET", "/api/commands/pending") or []:
        cid = cmd.get("id")
        if cid in unreported or cmd.get("command") != "kill":
            continue
        sid = str(cmd.get("session_id", ""))
        with _status_lock:
            known = sid in _load_status().get("sessions", {})
        if not known:
            continue
        try:
            unreported[cid] = _kill_session_core(sid, "cloud")
        except Exception as e:
            _log_bg_error("_process_cloud_commands", e)
            unreported[cid] = "error"
    for cid, outcome in list(unreported.items()):
        try:
            _cloud_request(url, key, "POST", f"/api/commands/{cid}/result", {"outcome": outcome})
            unreported.pop(cid, None)
        except Exception as e:
            code = getattr(e, "code", None)
            if code in (404, 409, 422):
                unreported.pop(cid, None)  # closed/expired on the cloud side; retrying can't help
            else:
                _log_bg_error("_process_cloud_commands.report", e)

def _cloud_command_worker():
    unreported = {}
    while True:
        time.sleep(_CLOUD_POLL_INTERVAL_S)
        url, key = _read_cloud_config()
        if not url:
            continue
        try:
            _drain_cloud_spool(url, key)
        except Exception as e:
            _log_bg_error("_drain_cloud_spool", e)
        try:
            _process_cloud_commands(url, key, unreported)
        except Exception as e:
            _log_bg_error("_cloud_command_worker", e)

threading.Thread(target=_cloud_command_worker, daemon=True).start()

# ── Auth token ────────────────────────────────────────────────────────────────
import secrets as _secrets, socket as _socket

TOKEN_FILE = os.path.join(AOC_DIR, "aoc_token.txt")
_auth_token = None

def _load_auth_token():
    global _auth_token
    try:
        with open(TOKEN_FILE, "r") as f:
            t = f.read().strip()
            _auth_token = t if len(t) >= 16 else None
    except FileNotFoundError:
        _auth_token = None

def _save_auth_token(token: str):
    global _auth_token
    with open(TOKEN_FILE, "w") as f:
        f.write(token)
    _auth_token = token

def _delete_auth_token():
    global _auth_token
    _auth_token = None
    try:
        os.remove(TOKEN_FILE)
    except FileNotFoundError:
        pass

def _get_local_ip() -> str:
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        try: s.close()
        except: pass

def _auth_info() -> dict:
    ip = _get_local_ip()
    return {
        "enabled": bool(_auth_token),
        "token": _auth_token,
        "ip": ip,
        "url": f"http://{ip}:{PORT}/?token={_auth_token}" if _auth_token else None,
    }

_load_auth_token()

# ── License (Free/Pro) ───────────────────────────────────────────────────────
import base64
# Ed25519 signature check, not a symmetric HMAC: the point isn't to stop
# someone from deleting the `if not _is_pro()` checks below (this ships as
# plain-text .py, so that's never actually preventable) -- it's to stop a
# valid-looking license key from being forged or shared, which a symmetric
# scheme couldn't do since verifying it would require embedding the same
# secret used to sign it. Verification only needs the public key below;
# minting a new key needs the private key, which never ships in this file.
_AOC_LICENSE_PUBKEY = bytes.fromhex(
    "b7ab17eeaa680648fd38ed412162cbf18856564f2f4b372359a38eaf937941ea"
)
LICENSE_FILE = os.path.join(AOC_DIR, "aoc_license.json")
_license_lock = threading.Lock()
_license = {"key": None}

def _load_license():
    global _license
    with _license_lock:
        _license = _load_json_file(LICENSE_FILE, {"key": None})

def _save_license(key: str):
    global _license
    with _license_lock:
        _license = {"key": key}
        try:
            with open(LICENSE_FILE, "w") as f:
                json.dump(_license, f)
        except Exception:
            pass

def _delete_license():
    global _license
    with _license_lock:
        _license = {"key": None}
        try:
            os.remove(LICENSE_FILE)
        except FileNotFoundError:
            pass

def _verify_license(key_str: str):
    """Verify an 'AOC-PRO-<payload_b64>.<sig_b64>' key against the embedded
    Ed25519 public key. Returns the parsed payload dict on success, None on
    any failure (bad format, bad signature, tampered payload, or the
    optional `cryptography` package not being installed -- same
    graceful-degradation precedent as psutil elsewhere in this file)."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
    except ImportError:
        return None
    try:
        if not key_str.startswith("AOC-PRO-"):
            return None
        body = key_str[len("AOC-PRO-"):]
        payload_b64, sig_b64 = body.split(".", 1)
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
        sig_bytes = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
        Ed25519PublicKey.from_public_bytes(_AOC_LICENSE_PUBKEY).verify(sig_bytes, payload_bytes)
        payload = json.loads(payload_bytes)
        if payload.get("tier") != "pro":
            return None
        return payload
    except (InvalidSignature, ValueError, KeyError, UnicodeDecodeError):
        return None
    except Exception:
        return None

def _license_info() -> dict:
    key = _license.get("key")
    payload = _verify_license(key) if key else None
    return {
        "active": bool(payload),
        "tier": "pro" if payload else "free",
        "email": payload.get("email") if payload else None,
        "issued": payload.get("issued") if payload else None,
    }

def _is_pro() -> bool:
    return _license_info()["active"]

_load_license()

# ── Auto-save ──────────────────────────────────────────────────────────────────

_autosave_id   = [None]   # stable sid reused across auto-saves for the same session
_autosave_time = [0]      # epoch of last successful auto-save (0 = never)
# _autosave_id is in-memory only, per _autosave_worker's own comment on the
# line that sets it -- a watchdog restart (or a manual one) mid-session used
# to forget it, so the next autosave tick minted a brand-new sid for the same
# still-running live session, splitting one session's agents across two
# history.db rows instead of continuing the one that was already there. This
# small marker file (same TOKEN_FILE/DIGEST_MARKER_FILE convention as every
# other small persisted marker in this file) lets _autosave_worker's first
# tick after startup resume the previous sid instead, but only if the live
# session (status.json) still actually has agents at that point -- if a
# /reset happened while this process was down, agents would be empty and the
# stale id gets ignored rather than wrongly reattached to a new session.
AUTOSAVE_ID_FILE = os.path.join(AOC_DIR, "aoc_autosave_id.txt")

def _resolve_autosave_id(current_id, agents, persisted_id):
    """Pure decision logic for _autosave_worker's sid lifecycle, extracted
    on its own specifically so this restart-survival behavior -- easy to
    get subtly wrong -- is testable without needing to drive the actual
    infinite worker loop. Given the in-memory id carried from the previous
    tick (None on this process' very first tick, or after agents last
    cleared), whether the live session currently has agents, and whatever
    AUTOSAVE_ID_FILE persisted (from this process or a prior one), decides
    what the in-memory id should become this tick:
    - no live agents -> None (session's over, next one starts fresh)
    - already have an in-memory id -> keep it (the normal, most-common case)
    - no in-memory id yet, but a persisted one exists -> resume it (this is
      the restart-survival path: a prior process's sid for a session that's
      still actually live)
    - neither -> mint a brand-new one (genuinely the first save of a new
      session, nothing to resume)."""
    if not agents:
        return None
    if current_id:
        return current_id
    return persisted_id or ("auto_" + datetime.now().strftime("%Y%m%d_%H%M%S"))

_claude_bin_cache = [None]  # cached path to claude.exe (computed once)

# ── Tunnel (Cloudflare / ngrok) ──────────────────────────────────────────────────
_tunnel_url      = [None]   # public URL once established
_tunnel_proc     = [None]   # provider subprocess handle
_tunnel_status   = ["off"]  # "off" | "starting" | "downloading" | "ready" | "error"
_tunnel_dl_pct   = [0]      # download progress 0-100 (cloudflare only)
_tunnel_provider = [None]   # "cloudflare" | "ngrok" — which provider is/was active
_tunnel_error_detail = [None]  # last captured diagnostic line/message when status=="error", else None

def _hard_kill_proc(proc):
    """terminate() -> kill() -> taskkill /F, and actually verify the PID is gone.
    Popen.terminate()/kill() were observed (twice, on this machine) to return
    without error yet leave the child running — an orphaned cloudflared/ngrok
    keeps its public tunnel alive with no handle left to stop it. `taskkill /F`
    has been 100% reliable in the same situations, so it's the final word here,
    not a fallback we merely attempt."""
    if proc is None:
        return
    pid = proc.pid
    try:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)
    except Exception:
        pass
    try:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    except Exception:
        pass
    if proc.poll() is None and pid and os.name == "nt":
        try:
            import subprocess as _sp
            _sp.run(["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True, timeout=5, creationflags=_sp.CREATE_NO_WINDOW)
        except Exception as e:
            _log_bg_error("_hard_kill_proc:taskkill", e)
    if proc.poll() is None:
        # terminate() and kill() failing before falling through to taskkill
        # is routine (that's the documented fallback chain, not a bug) --
        # but the process still being alive after ALL of them, including
        # taskkill, is exactly the "orphaned cloudflared/ngrok" failure mode
        # this function's own docstring exists to prevent, and previously
        # had zero visibility when it still happened.
        _log_bg_error("_hard_kill_proc", RuntimeError(f"pid {pid} still alive after terminate/kill/taskkill"))
    _clear_tunnel_pid()

_TUNNEL_PID_FILE = os.path.join(AOC_DIR, "_tunnel_child.pid")

def _write_tunnel_pid(pid: int):
    try:
        with open(_TUNNEL_PID_FILE, "w") as f:
            f.write(str(pid))
    except Exception:
        pass

def _clear_tunnel_pid():
    try:
        os.remove(_TUNNEL_PID_FILE)
    except Exception:
        pass

def _reap_stray_tunnel_process():
    """A previous monitor.py instance's tunnel child can outlive it — e.g. if
    that instance was killed (by a watchdog/manual restart race) before its
    own _stop_tunnel cleanup finished running. This fresh instance starts with
    _tunnel_status="off" and no handle to that child, which would leave
    _check_auth's localhost bypass active while the orphan keeps routing real
    public traffic (this happened today). Only ever touches the one PID we
    ourselves recorded, and only after confirming it's still actually
    cloudflared/ngrok — never a blanket kill-by-image-name, since this
    machine may run those binaries for other, unrelated things."""
    try:
        with open(_TUNNEL_PID_FILE) as f:
            pid = int(f.read().strip())
    except Exception:
        return
    try:
        import subprocess as _sp
        r = _sp.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True,
                     timeout=5, creationflags=_sp.CREATE_NO_WINDOW, text=True)
        out = (r.stdout or "").lower()
        if "cloudflared.exe" in out or "ngrok.exe" in out:
            _sp.run(["taskkill", "/F", "/PID", str(pid)],
                     capture_output=True, timeout=5, creationflags=_sp.CREATE_NO_WINDOW)
    except Exception as e:
        _log_bg_error("_reap_stray_tunnel_process", e)
    _clear_tunnel_pid()

def _snapshot_processes(with_parent=False):
    """[(pid, lowercase exe name)] -- or (pid, exe, parent pid) with
    with_parent -- for every running process, from one
    CreateToolhelp32Snapshot walk (fast, subprocess-free -- the technique
    aoc_hook.py's own ancestor walk uses; ~7ms). None if the snapshot
    itself fails, so callers can tell "no such process" from "couldn't
    look". Shared by every process check in this file so the ctypes
    structure definition isn't duplicated per caller."""
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    TH32CS_SNAPPROCESS = 0x00000002
    try:
        kernel32 = ctypes.windll.kernel32
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snapshot or snapshot == -1:
            return None
        out = []
        try:
            entry = PROCESSENTRY32()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
            if kernel32.Process32First(snapshot, ctypes.byref(entry)):
                while True:
                    exe = entry.szExeFile.decode("mbcs", "ignore").lower()
                    out.append((entry.th32ProcessID, exe, entry.th32ParentProcessID) if with_parent
                               else (entry.th32ProcessID, exe))
                    if not kernel32.Process32Next(snapshot, ctypes.byref(entry)):
                        break
        finally:
            kernel32.CloseHandle(snapshot)
        return out
    except Exception:
        return None

def _norm_window_title(t):
    """Pure: lowercase, without the spinner/status glyphs Claude Code puts
    in front of the terminal title (e.g. '◑ AOC pokračování')."""
    t = (t or "").strip().lower()
    while t and not t[0].isalnum():
        t = t[1:].lstrip()
    return t


def _pick_session_window(windows, ancestors, title):
    """Pure: which top-level window shows a CLI session. windows =
    [(hwnd, pid, title)] (visible, titled), ancestors = the claude.exe's
    parent pids, nearest first. 1) the nearest ancestor that owns a window
    (Windows Terminal, VS Code, a console); 2) else a window whose title
    contains the session's name -- Claude Code titles its terminal with it,
    and Git Bash's mintty is not in the Windows parent chain. An exact
    title beats a substring. -> (hwnd, 'ancestor'|'title') or (None, None)."""
    by_pid = {}
    for hwnd, pid, _t in windows:
        by_pid.setdefault(pid, hwnd)
    for pid in ancestors:
        if pid in by_pid:
            return by_pid[pid], "ancestor"
    want = _norm_window_title(title)
    if len(want) < 3:
        return None, None
    exact = [h for h, _p, t in windows if _norm_window_title(t) == want]
    if exact:
        return exact[0], "title"
    part = [h for h, _p, t in windows if want in _norm_window_title(t)]
    return (part[0], "title") if len(part) == 1 else (None, None)


def _ancestor_pids(pid, procs, depth=12):
    """Pure: parent pids of `pid` (nearest first) from (pid, exe, ppid) rows."""
    parent = {p: pp for p, _e, pp in procs or ()}
    out, cur = [], parent.get(pid)
    while cur and cur not in out and len(out) < depth:
        out.append(cur)
        cur = parent.get(cur)
    return out


def _list_top_windows():
    """[(hwnd, pid, title)] of visible, titled, unowned top-level windows."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    out = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _lp):
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):  # GW_OWNER
            n = user32.GetWindowTextLengthW(hwnd)
            if n > 0:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                out.append((hwnd, pid.value, buf.value))
        return True
    user32.EnumWindows(proto(cb), 0)
    return out


def _focus_window(hwnd):
    """Restore + bring a window to the foreground. Windows only lets the
    foreground app do that; a synthetic Alt press (the usual workaround)
    lifts the lock for this background process. True if it worked."""
    import ctypes
    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.keybd_event(0x12, 0, 0, 0)            # Alt down
    ok = bool(user32.SetForegroundWindow(hwnd))
    user32.keybd_event(0x12, 0, 2, 0)            # Alt up
    if not ok:
        user32.SwitchToThisWindow(hwnd, True)
        ok = user32.GetForegroundWindow() == hwnd
    return ok


def _focus_session_terminal(session_id):
    """POST /focus_session: bring the terminal window running this local
    CLI session to the front. -> (http status, payload)."""
    with _status_lock:
        sess = dict((_load_status().get("sessions") or {}).get(session_id) or {})
    if not sess:
        return 404, {"ok": False, "error": "unknown session"}
    pid = sess.get("host_pid")
    procs = _snapshot_processes(with_parent=True) if pid else None
    hwnd, how = _pick_session_window(_list_top_windows(), _ancestor_pids(pid, procs) if pid else [],
                                     sess.get("display_name") or "")
    if not hwnd:
        return 404, {"ok": False, "error": "terminal window not found"}
    return 200, {"ok": _focus_window(hwnd), "matched": how}
