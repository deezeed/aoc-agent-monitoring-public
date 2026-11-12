# AOC server, part 07: install health, ngrok, updates, code reload, PR link, weekly digest.
# monitor.py exec()s server/NN_*.py in number order into ONE shared
# namespace (its own) -- not an importable module: names from earlier
# parts are plain globals here, and __file__ is monitor.py.
# ── install health: Claude Code hooks, statusLine, scripts, tasks ─────────
# setup.py is the one source of truth for what an install should look like:
# `setup.py --check` prints it as JSON (the same merge functions a real
# install runs, applied to a copy of settings.json), `--repair` re-applies
# just those steps. The danger guard's hook sat missing for two days once
# because nothing re-checked after an update; now a worker does, every
# _HOOKS_HEALTH_EVERY_S, and the Health view offers a one-click repair.
_HOOKS_HEALTH_EVERY_S = 600
_hooks_health = {"checks": None, "at": 0.0, "error": "", "repairing": False}
_hooks_health_lock = threading.Lock()  # one setup.py run at a time


def _parse_setup_health(stdout: str):
    """Pure: setup.py --check / --repair output -> the checks list (None if
    the AOC_HEALTH line is missing or malformed)."""
    for line in reversed((stdout or "").splitlines()):
        if line.startswith("AOC_HEALTH "):
            try:
                checks = json.loads(line[len("AOC_HEALTH "):]).get("checks")
            except Exception:
                return None
            return checks if isinstance(checks, list) else None
    return None


def _hooks_health_bad(checks) -> list:
    """Pure: labels of the failing checks ([] when unknown or all fine)."""
    return [str(c.get("label") or c.get("id")) for c in (checks or []) if isinstance(c, dict) and not c.get("ok")]


def _run_setup(flag: str) -> None:
    """Run setup.py --check / --repair with the python.exe next to this
    interpreter (the one setup.py was installed with: it derives the hook
    commands from its own interpreter) and store the result."""
    py = os.path.join(os.path.dirname(sys.executable), "python.exe")
    if not os.path.exists(py):
        py = sys.executable
    with _hooks_health_lock:
        _hooks_health["repairing"] = flag == "--repair"
        try:
            r = subprocess.run([py, os.path.join(AOC_DIR, "setup.py"), flag], cwd=AOC_DIR,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            checks = _parse_setup_health(r.stdout)
            err = "" if checks is not None else ((r.stderr or r.stdout).strip().splitlines() or ["setup.py gave no result"])[-1][:300]
        except Exception as e:
            checks, err = None, f"{type(e).__name__}: {e}"[:300]
        _hooks_health.update({"checks": checks if checks is not None else _hooks_health["checks"],
                              "at": time.time(), "error": err, "repairing": False})


def _repair_summary(before: list, after: list, error: str, at: float) -> dict:
    """Pure: what one Repair click did, kept until the next one so the
    Health card can say it plainly (a click once 'did nothing' and looked
    done). before/after = labels of the failing checks."""
    before, after = list(before or []), list(after or [])
    fixed = [b for b in before if b not in after]
    state = ("failed" if error else "fixed" if not after
             else "partial" if fixed else "unchanged")
    return {"at": at, "state": state, "fixed": fixed, "left": after, "error": error or ""}


def _repair_now(loopback: bool) -> dict:
    """POST /hooks_health/repair: run it (this PC only) and remember the result."""
    before = _hooks_health_bad(_hooks_health["checks"])
    if loopback:
        _run_setup("--repair")
        err = _hooks_health["error"]
    else:
        err = "repair can only be started on this PC"
    _hooks_health["last_repair"] = _repair_summary(before, _hooks_health_bad(_hooks_health["checks"]), err, time.time())
    return _hooks_health_view()


def _hooks_health_view() -> dict:
    last = _hooks_health.get("last_repair") or _auto_repair_load().get("last")
    return {"checks": _hooks_health["checks"], "at": _hooks_health["at"], "error": _hooks_health["error"],
            "bad": _hooks_health_bad(_hooks_health["checks"]), "last_repair": last,
            "auto_repair": _auto_repair_load().get("enabled", True) is not False,
            # deployed hooks from before the Orchestrate worker tag (server/17)
            "orch_hint": _orch_hooks_hint()}


# ── auto-repair after an update ────────────────────────────────────────────
# An update ships newer hook scripts / hook commands, but only setup.py puts
# them into ~/.claude -- on 11-02..11-05 an outdated aoc_permission.py sat
# there for days until a Repair click finally landed. Now, once per
# (version + hook sources) the worker repairs by itself, but only what an
# update made OUTDATED: a missing hook may have been removed on purpose and
# a foreign statusLine is the user's own -- those stay a click on Repair.
AUTO_REPAIR_FILE = os.path.join(AOC_DATA_DIR, "auto_repair.json")
_AUTO_REPAIR_IDS = ("activity", "held_", "budget", "danger", "statusline", "scripts")


def _auto_repair_targets(checks) -> list:
    """Pure: labels of the failing checks an automatic repair may fix."""
    return [str(c.get("label") or c.get("id")) for c in (checks or [])
            if isinstance(c, dict) and not c.get("ok") and str(c.get("id") or "").startswith(_AUTO_REPAIR_IDS)
            and "outdated" in str(c.get("detail") or "")]


def _hook_sources_key(version: str, aoc_dir: str) -> str:
    """What the installed hooks are made from: the running version plus
    setup.py and hooks/* as shipped. A new value = new hooks to deploy."""
    import hashlib
    h = hashlib.sha256((version or "").encode())
    names = ["setup.py"]
    try:
        names += sorted(os.path.join("hooks", n) for n in os.listdir(os.path.join(aoc_dir, "hooks")))
    except OSError:
        pass
    for n in names:
        h.update(b"\0" + n.replace("\\", "/").encode() + b"\0")
        try:
            with open(os.path.join(aoc_dir, n), "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(b"-")
    return h.hexdigest()[:16]


def _auto_repair_decide(state: dict, key: str, checks) -> str:
    """Pure: "off" (setting), "unknown" (no check result yet), "done" (this
    key already had its one try), "nothing" (no outdated part) or "repair"."""
    if (state or {}).get("enabled", True) is False:
        return "off"
    if not isinstance(checks, list):
        return "unknown"
    if (state or {}).get("key") == key:
        return "done"
    return "repair" if _auto_repair_targets(checks) else "nothing"


def _auto_repair_load(path=None) -> dict:
    try:
        with open(path or AUTO_REPAIR_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _auto_repair_save(state: dict, path=None) -> None:
    path = path or AUTO_REPAIR_FILE
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError as e:
        _log_bg_error("auto repair", e)


def _auto_repair_set(enabled) -> dict:
    """POST /hooks_health/auto_repair {enabled}: the Health card switch."""
    state = _auto_repair_load()
    state["enabled"] = bool(enabled)
    _auto_repair_save(state)
    return _hooks_health_view()


def _auto_repair_maybe(run=None, path=None, aoc_dir=None, history_add=None, notify=None, now=None) -> str:
    """After a check: repair once per hook-sources key if an update left AOC's
    hooks outdated. Returns the decision. Never in a preview unless
    AOC_AUTO_REPAIR=1 (a preview shares the real ~/.claude)."""
    if AOC_PREVIEW and os.environ.get("AOC_AUTO_REPAIR") != "1":
        return "preview"
    state = _auto_repair_load(path)
    key = _hook_sources_key(_read_installed_version(AOC_VERSION_FILE), aoc_dir or AOC_DIR)
    checks = _hooks_health["checks"]
    decision = _auto_repair_decide(state, key, checks)
    if decision in ("off", "unknown", "done"):
        return decision
    state["key"] = key
    if decision == "nothing":
        _auto_repair_save(state, path)
        return decision
    before = _hooks_health_bad(checks)
    _auto_repair_save(state, path)   # the one try is spent BEFORE it runs: a crash mid-repair never loops
    (run or _run_setup)("--repair")
    summary = _repair_summary(before, _hooks_health_bad(_hooks_health["checks"]), _hooks_health["error"], now or time.time())
    summary["source"] = "auto"
    state["last"] = summary
    _auto_repair_save(state, path)
    _hooks_health["last_repair"] = summary
    (history_add or _update_history_add)({"outcome": "repaired" if summary["state"] == "fixed" else "repair_failed",
                                          "state": summary["state"], "fixed": summary["fixed"],
                                          "left": summary["left"], "error": summary["error"], "via": "auto-repair"})
    if summary["state"] != "fixed":
        (notify or _show_native_toast)("AOC", "Automatic hook repair after the update didn't finish -- open Health -> Repair",
                                       kind="auto_repair")
    return decision


def _hooks_health_worker():
    time.sleep(_STARTUP_GRACE_S)
    while True:
        _run_setup("--check")
        try:
            _auto_repair_maybe()
        except Exception as e:
            _log_bg_error("auto repair", e)
        time.sleep(_HOOKS_HEALTH_EVERY_S)

threading.Thread(target=_hooks_health_worker, daemon=True, name="hooks-health").start()

def _session_really_active(sess: dict, now_epoch: float, heartbeat_cutoff_s: int = 300) -> bool:
    """A session counts as active if either its heartbeat is recent, OR its
    underlying claude.exe process is confirmed still running. Checking real
    process liveness (rather than just picking a longer timeout) is what
    actually closes the class of bug where a long tool call, a thinking
    pause, or a brief step-away wrongly looked like a closed session and
    got auto-dismissed -- no fixed timeout can fully solve that, since
    heartbeat gaps of arbitrary length are a normal part of real usage.
    The timeout remains the only signal for a session with no host_pid
    recorded yet (very early on, before the first heartbeat that carries
    one)."""
    if not sess.get("session_active"):
        return False
    if sess.get("last_seen_epoch", 0) > now_epoch - heartbeat_cutoff_s:
        return True
    return _is_claude_pid_alive(sess.get("host_pid"))

_CF_EXE = os.path.join(AOC_DIR, "cloudflared.exe")
_CF_DL_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"

def _find_cloudflared() -> str:
    """Return path to cloudflared.exe if found, else empty string."""
    candidates = [_CF_EXE]
    candidates.append(os.path.expandvars(r"%LOCALAPPDATA%\cloudflared\cloudflared.exe"))
    for d in os.environ.get("PATH", "").split(os.pathsep):
        candidates.append(os.path.join(d, "cloudflared.exe"))
    for c in candidates:
        if os.path.isfile(c):
            return c
    return ""

def _download_cloudflared_worker():
    """Download cloudflared.exe to AOC dir in a background thread."""
    import urllib.request as _ur
    _tunnel_status[0] = "downloading"
    _tunnel_dl_pct[0] = 0
    try:
        tmp = _CF_EXE + ".part"
        def _progress(count, block_size, total):
            if total > 0:
                _tunnel_dl_pct[0] = min(99, int(count * block_size / total * 100))
        _ur.urlretrieve(_CF_DL_URL, tmp, _progress)
        os.replace(tmp, _CF_EXE)
        _tunnel_dl_pct[0] = 100
        _tunnel_status[0] = "off"
        _start_tunnel_worker_thread()
    except Exception as exc:
        _tunnel_status[0] = "error"
        try:
            if os.path.exists(_CF_EXE + ".part"): os.remove(_CF_EXE + ".part")
        except Exception:
            pass

def _start_tunnel_worker():
    """Background thread: launch cloudflared and watch for the public URL."""
    import re, subprocess as _sp
    _tunnel_status[0] = "starting"
    _tunnel_url[0] = None
    _tunnel_error_detail[0] = None
    cf = _find_cloudflared()
    if not cf:
        _download_cloudflared_worker()
        return
    proc = None
    # Rolling tail of cloudflared's own stdout/stderr (redirected together) --
    # previously read line-by-line only to search for the URL and otherwise
    # discarded, so an "error" status carried zero information about why
    # (network issue, corrupted binary, port conflict, etc.). Capped small:
    # this is a diagnostic hint, not a log viewer.
    recent_lines = []
    try:
        flags = _sp.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = _sp.Popen(
            [cf, "tunnel", "--url", f"http://127.0.0.1:{PORT}", "--no-autoupdate"],
            stdout=_sp.PIPE, stderr=_sp.STDOUT,
            creationflags=flags, text=True, encoding="utf-8", errors="replace", bufsize=1
        )
        _tunnel_proc[0] = proc
        _write_tunnel_pid(proc.pid)
        url_re = re.compile(r'https://[a-z0-9\-]+\.trycloudflare\.com')
        found_url = False
        try:
            for line in proc.stdout:
                stripped = line.rstrip()
                if stripped:
                    recent_lines.append(stripped)
                    if len(recent_lines) > 20:
                        recent_lines.pop(0)
                m = url_re.search(line)
                if m:
                    _tunnel_url[0] = m.group(0)
                    _tunnel_status[0] = "ready"
                    found_url = True
                    break
        except Exception:
            pass  # still fall through to termination in finally below
        rc = proc.wait()
        if not found_url and rc != 0:
            _tunnel_status[0] = "error"
            _tunnel_error_detail[0] = (recent_lines[-1][:200] if recent_lines
                                        else f"cloudflared exited with code {rc}")
    except Exception as e:
        _tunnel_status[0] = "error"
        _tunnel_error_detail[0] = str(e)[:200]
    finally:
        # Any failure above must still kill the child — an orphaned cloudflared
        # keeps the public tunnel alive with no handle left to stop it (this
        # happened during testing: a stdout-decode exception left the process
        # running for good after _tunnel_proc[0] was cleared below).
        _hard_kill_proc(proc)
        if _tunnel_status[0] not in ("error",):
            _tunnel_status[0] = "off"
        _tunnel_url[0] = None
        _tunnel_proc[0] = None

def _start_tunnel_worker_thread():
    threading.Thread(target=_start_tunnel_worker, daemon=True).start()

# ── ngrok ──────────────────────────────────────────────────────────────────────
def _find_ngrok() -> str:
    """Return path to ngrok.exe if found, else empty string."""
    import glob as _g
    candidates = _g.glob(os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Ngrok.Ngrok_*\ngrok.exe"))
    for d in os.environ.get("PATH", "").split(os.pathsep):
        candidates.append(os.path.join(d, "ngrok.exe"))
    for c in candidates:
        if os.path.isfile(c):
            return c
    return ""

_ngrok_authtoken_cache = [False, 0.0]  # [last result, last-checked epoch]
_ngrok_authtoken_refresh_lock = threading.Lock()  # held while a background refresh runs

def _ngrok_authtoken_check_now() -> bool:
    """Blocking: shells out to `ngrok config check` (up to 5 s) and updates
    the cache. Never call this from the /status path -- see below."""
    ng = _find_ngrok()
    if not ng:
        return False
    import subprocess as _sp
    try:
        flags = _sp.CREATE_NO_WINDOW if os.name == "nt" else 0
        r = _sp.run([ng, "config", "check"], capture_output=True, timeout=5, creationflags=flags)
        ok = r.returncode == 0
    except Exception:
        ok = False
    _ngrok_authtoken_cache[0] = ok
    _ngrok_authtoken_cache[1] = time.time()
    return ok

def _ngrok_authtoken_refresh_bg():
    try:
        _ngrok_authtoken_check_now()
    finally:
        _ngrok_authtoken_refresh_lock.release()

def _ngrok_authtoken_configured() -> bool:
    """Non-blocking, for /status (polled every 500ms, and built while holding
    _status_payload_cache_lock). Returns the cached result and, if it is over
    10 s old, starts one background re-check. Doing the check inline used to
    stall every /status caller -- watchdog included -- whenever ngrok.exe was
    slow to start (status_stalls.log, 2026-09-26 17:34)."""
    if not _find_ngrok():
        return False
    if (time.time() - _ngrok_authtoken_cache[1] >= 10
            and _ngrok_authtoken_refresh_lock.acquire(blocking=False)):
        threading.Thread(target=_ngrok_authtoken_refresh_bg, daemon=True,
                         name="ngrok-authtoken-check").start()
    return _ngrok_authtoken_cache[0]

def _start_ngrok_worker():
    """Background thread: launch ngrok and poll its local API for the public URL."""
    import subprocess as _sp, urllib.request as _ur, json as _json
    _tunnel_status[0] = "starting"
    _tunnel_url[0] = None
    _tunnel_error_detail[0] = None
    ng = _find_ngrok()
    if not ng:
        _tunnel_status[0] = "error"
        _tunnel_error_detail[0] = "ngrok.exe not found"
        return
    if not _ngrok_authtoken_check_now():
        _tunnel_status[0] = "error"
        _tunnel_error_detail[0] = "ngrok authtoken not configured"
        return
    proc = None
    try:
        flags = _sp.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = _sp.Popen(
            [ng, "http", str(PORT), "--log", "stdout"],
            stdout=_sp.PIPE, stderr=_sp.STDOUT,
            creationflags=flags, text=True, encoding="utf-8", errors="replace", bufsize=1
        )
        _tunnel_proc[0] = proc
        _write_tunnel_pid(proc.pid)
        # Poll ngrok's local inspection API instead of parsing stdout — avoids the
        # decode/parsing failure modes that orphaned the cloudflared process earlier.
        found_url = False
        for _ in range(30):  # ~15s at 500ms
            if proc.poll() is not None:
                break
            try:
                with _ur.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=1) as resp:
                    data = _json.loads(resp.read().decode("utf-8", "replace"))
                for t in data.get("tunnels", []):
                    if t.get("public_url", "").startswith("https://"):
                        _tunnel_url[0] = t["public_url"]
                        _tunnel_status[0] = "ready"
                        found_url = True
                        break
                if found_url:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        if found_url:
            # Block here for as long as the tunnel is actually up — mirrors
            # cloudflared's rc = proc.wait(). Without this, falling straight
            # into `finally` would immediately terminate the process we just
            # got a working public_url for.
            proc.wait()
        else:
            _tunnel_status[0] = "error"
            # stdout was never drained during the polling loop above (only
            # the local API was polled), so whatever ngrok printed while
            # failing (auth error, ERR_NGROK_xxx, etc.) is still sitting in
            # the pipe buffer -- read it now instead of discarding it.
            try:
                remaining = proc.stdout.read(4000)
            except Exception:
                remaining = ""
            lines = [l for l in remaining.splitlines() if l.strip()]
            _tunnel_error_detail[0] = lines[-1][:200] if lines else f"ngrok exited with code {proc.poll()}"
    except Exception as e:
        _tunnel_status[0] = "error"
        _tunnel_error_detail[0] = str(e)[:200]
    finally:
        _hard_kill_proc(proc)
        if _tunnel_status[0] not in ("error",):
            _tunnel_status[0] = "off"
        _tunnel_url[0] = None
        _tunnel_proc[0] = None

def _start_ngrok_worker_thread():
    threading.Thread(target=_start_ngrok_worker, daemon=True).start()

def _stop_tunnel():
    _hard_kill_proc(_tunnel_proc[0])
    _tunnel_proc[0] = None
    _tunnel_url[0] = None
    _tunnel_status[0] = "off"
    _tunnel_provider[0] = None
_AUTOSAVE_INTERVAL = 300  # seconds between auto-saves

def _autosave_worker():
    """Background thread: snapshot current session to SQLite every 5 min.
    See AUTOSAVE_ID_FILE's own comment (and _resolve_autosave_id) above --
    the id assigned each tick can resume a still-live session's sid from a
    prior process instead of always minting a fresh one, so a watchdog
    restart mid-session doesn't fork it into two separate history.db rows."""
    import time as _t
    while True:
        _t.sleep(_AUTOSAVE_INTERVAL)
        try:
            status = _load_status()
            agents = [a for a in status.get("agents", [])
                      if not str(a.get("id", "")).startswith("hook_")]
            persisted = None
            if _autosave_id[0] is None:
                try:
                    with open(AUTOSAVE_ID_FILE, "r", encoding="utf-8") as f:
                        persisted = f.read().strip() or None
                except Exception:
                    pass
            new_id = _resolve_autosave_id(_autosave_id[0], agents, persisted)
            if new_id != _autosave_id[0]:
                _autosave_id[0] = new_id
                try:
                    if new_id is None:
                        os.remove(AUTOSAVE_ID_FILE)
                    else:
                        with open(AUTOSAVE_ID_FILE, "w", encoding="utf-8") as f:
                            f.write(new_id)
                except Exception:
                    pass
            if not agents:
                continue
            _db_save_session(status, _autosave_id[0])
            _autosave_time[0] = _t.time()
        except Exception as e:
            _log_bg_error("_autosave_worker", e)

import threading as _threading
_threading.Thread(target=_autosave_worker, daemon=True).start()


# ── self-update check ──────────────────────────────────────────────────────

_self_update_status = {"stale": False, "behind_by": 0, "checked_at": 0}
_self_update_lock = _threading.Lock()
_self_update_notified = [False]

# An installer build (installer/build.ps1) has no .git to fetch, so it can't
# use the git check below. build.ps1 writes its version to VERSION instead,
# and those installs check the public repo's latest GitHub release.
AOC_VERSION_FILE = os.path.join(AOC_DIR, "VERSION")

def _update_source(raw) -> str:
    """Pure: AOC_UPDATE_SOURCE -> the base URL of a stand-in release server,
    or "" (= GitHub, the default). Test-only, for CI's one-click update run
    (installer/one_click_update_e2e.py): read from the environment alone --
    no setting, file or HTTP route can change it -- and only a plain-http
    loopback address counts, so it can't send an install somewhere else.
    The SHA-256 check from the release notes still applies."""
    m = re.fullmatch(r"http://(127\.0\.0\.1|localhost):(\d{2,5})/?", str(raw or "").strip())
    return f"http://{m.group(1)}:{m.group(2)}/" if m else ""

_UPDATE_SOURCE = _update_source(os.environ.get("AOC_UPDATE_SOURCE"))
AOC_RELEASES_API = (_UPDATE_SOURCE + "releases/latest" if _UPDATE_SOURCE else
                    "https://api.github.com/repos/deezeed/aoc-agent-monitoring-public/releases/latest")
_RELEASE_CHECK_INTERVAL_S = 6 * 3600  # unauthenticated GitHub API: 60 req/h per IP

def _read_installed_version(path) -> str:
    """The installer build's version ("1.0.123"), or "" for a git checkout."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""

def _parse_version(s):
    """"v1.0.123" / "1.0.123" -> (1, 0, 123); None if not dotted integers."""
    parts = str(s or "").strip().lstrip("vV").split(".")
    if not parts or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)

def _release_update_status(installed: str, release: dict, now: float) -> dict:
    """Pure: turn a GitHub releases/latest response into _self_update_status.
    Only an https download URL on github.com is ever handed to the UI."""
    latest = str(release.get("tag_name") or "").strip()
    cur_v, new_v = _parse_version(installed), _parse_version(latest)
    url = ""
    for a in release.get("assets") or []:
        u = str(a.get("browser_download_url") or "")
        if u.lower().endswith(".exe"):
            url = u
            break
    url = url or str(release.get("html_url") or "")
    if not url.startswith("https://github.com/"):
        url = "https://github.com/deezeed/aoc-agent-monitoring-public/releases/latest"
    stale = bool(cur_v and new_v and new_v > cur_v)
    return {"stale": stale, "behind_by": 0, "checked_at": now, "mode": "release",
            "installed": installed, "latest": latest.lstrip("vV"), "url": url}

def _check_release_once(installed: str, keep=None) -> dict:
    """keep: optional one-item list that receives the raw release JSON (the
    one-click updater needs its assets and notes, not just the status)."""
    import urllib.request as _ur
    req = _ur.Request(AOC_RELEASES_API, headers={
        "Accept": "application/vnd.github+json", "User-Agent": f"AOC/{installed}"})
    with _ur.urlopen(req, timeout=15) as r:
        release = json.loads(r.read().decode("utf-8"))
    if keep is not None:
        keep[0] = release
    return _release_update_status(installed, release, time.time())

def _release_update_worker(installed: str):
    _whats_new_startup(installed)
    while True:
        try:
            status = _check_release_once(installed, keep=_latest_release)
            with _self_update_lock:
                _self_update_status.clear()
                _self_update_status.update(status)
            if status["stale"] and not _self_update_notified[0]:
                _self_update_notified[0] = True
                try:
                    _show_native_toast("AOC", f"AOC {status['latest']} is available (you have {installed}).", kind="update")
                except Exception:
                    pass
        except Exception as e:
            # offline, rate-limited, or no release published yet (404)
            _log_bg_error("_release_update_worker", e)
        time.sleep(_RELEASE_CHECK_INTERVAL_S)

# ── one-click update (installer builds only) ───────────────────────────────
# Clicking the UPDATE badge downloads the new AOC-Setup-x.y.z.exe, checks it
# against the "SHA-256: `...`" line publish_release.ps1 puts in every
# release's notes (no hash or a mismatch = refused), then runs it silently
# with the same flags as CI's upgrade job. The installer's PrepareToInstall
# stops this monitor (setup.py --stop) and its [Run] setup.py starts the new
# one, so the installer is started detached and outside our job object --
# it has to outlive us. The "What's new" list of that release is kept in
# WHATS_NEW_FILE and shown once when the new version first starts.
AOC_RELEASE_DL_PREFIX = (_UPDATE_SOURCE + "releases/download/" if _UPDATE_SOURCE else
                         "https://github.com/deezeed/aoc-agent-monitoring-public/releases/download/")
AOC_RELEASE_TAG_API = (_UPDATE_SOURCE + "releases/tags/" if _UPDATE_SOURCE else
                       "https://api.github.com/repos/deezeed/aoc-agent-monitoring-public/releases/tags/")
UPDATE_DIR = os.path.join(AOC_DATA_DIR, "update")
WHATS_NEW_FILE = os.path.join(AOC_DATA_DIR, "whats_new.json")
_INSTALLER_SILENT_ARGS = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]
_WHATS_NEW_MAX_ITEMS = 40
_latest_release = [None]  # last releases/latest JSON, set by _release_update_worker
_update_job = {"state": "idle", "pct": 0, "version": "", "error": ""}
_update_job_lock = _threading.Lock()
_whats_new_lock = _threading.Lock()

# (state, event) -> next state. Anything not listed leaves the job as it is,
# so a second click while downloading doesn't start a second download.
_UPDATE_TRANSITIONS = {
    ("idle", "start"): "downloading",
    ("error", "start"): "downloading",
    ("downloading", "progress"): "downloading",
    ("downloading", "downloaded"): "verifying",
    ("verifying", "verified"): "installing",
    ("downloading", "fail"): "error",
    ("verifying", "fail"): "error",
    ("installing", "fail"): "error",
}

def _update_job_next(job: dict, event: str, **kw) -> dict:
    """Pure: the update job after `event` (start / progress / downloaded /
    verified / fail). An event that doesn't fit the current state is ignored."""
    state = _UPDATE_TRANSITIONS.get((job.get("state", "idle"), event))
    if state is None:
        return dict(job)
    new = dict(job, state=state)
    if event == "start":
        new.update(pct=0, version=str(kw.get("version") or ""), error="")
    elif event == "progress":
        new["pct"] = max(0, min(99, int(kw.get("pct") or 0)))
    elif event in ("downloaded", "verified"):
        new["pct"] = 100
    elif event == "fail":
        new["error"] = str(kw.get("error") or "update failed")[:200]
    return new

def _release_sha256(body) -> str:
    """Pure: the installer hash from publish_release.ps1's notes line
    "SHA-256: `<64 hex>`", lowercased; "" if there isn't exactly one."""
    found = {m.lower() for m in re.findall(r"SHA-256:\s*`([0-9a-fA-F]{64})`", str(body or ""))}
    return found.pop() if len(found) == 1 else ""

def _release_whats_new(body) -> list:
    """Pure: the bullet list under the notes' "## What's new ..." heading."""
    items, inside = [], False
    for line in str(body or "").splitlines():
        s = line.strip()
        if s.startswith("#"):
            inside = s.lstrip("#").strip().lower().startswith("what's new")
            continue
        if inside and s[:2] in ("- ", "* "):
            text = s[2:].strip()
            if text:
                items.append(text[:300])
    return items[:_WHATS_NEW_MAX_ITEMS]

def _update_plan(installed: str, release) -> dict:
    """Pure: can this release be installed with one click? {"ok": True,
    version, url, name, sha256, notes} or {"ok": False, "error"}. Only an
    AOC-Setup-<version>.exe asset of the public repo's own release counts."""
    if not installed:
        return {"ok": False, "error": "one-click update is for installed copies only"}
    if not isinstance(release, dict):
        return {"ok": False, "error": "no release information yet -- try again in a minute"}
    version = str(release.get("tag_name") or "").strip().lstrip("vV")
    cur_v, new_v = _parse_version(installed), _parse_version(version)
    if not (cur_v and new_v and new_v > cur_v):
        return {"ok": False, "error": "already up to date"}
    name = f"AOC-Setup-{version}.exe"
    url = ""
    for a in release.get("assets") or []:
        if not isinstance(a, dict):
            continue
        u = str(a.get("browser_download_url") or "")
        if a.get("name") == name and u == f"{AOC_RELEASE_DL_PREFIX}v{version}/{name}":
            url = u
            break
    if not url:
        return {"ok": False, "error": f"release v{version} has no {name}"}
    sha = _release_sha256(release.get("body"))
    if not sha:
        return {"ok": False, "error": "release notes carry no SHA-256 -- download it manually"}
    return {"ok": True, "version": version, "url": url, "name": name, "sha256": sha,
            "notes": _release_whats_new(release.get("body"))}

def _whats_new_next(state, installed: str):
    """Pure: WHATS_NEW_FILE state at startup -> (new state, needs_fetch).
    State: {"seen": version last started, "show": {"version", "items"} or
    None}. A fresh install only records its version; a version change shows
    the list once -- the updater's saved one ("pending") if it matches,
    otherwise needs_fetch=True (installed by hand: fetch that release)."""
    state = dict(state) if isinstance(state, dict) else {}
    seen = str(state.get("seen") or "")
    if not installed or seen == installed:
        return state, bool(installed and isinstance(state.get("show"), dict)
                           and state["show"].get("version") == installed
                           and state["show"].get("items") is None)
    pending = state.pop("pending", None)
    state["seen"] = installed
    if not seen:
        state["show"] = None
        return state, False
    if isinstance(pending, dict) and pending.get("version") == installed and isinstance(pending.get("items"), list):
        state["show"] = {"version": installed, "items": pending["items"]}
        return state, False
    state["show"] = {"version": installed, "items": None}
    return state, True

def _whats_new_load(path=None) -> dict:
    try:
        with open(path or WHATS_NEW_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def _whats_new_save(state, path=None):
    path = path or WHATS_NEW_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, path)

def _whats_new_startup(installed: str, path=None, fetch_fn=None):
    """Run once when an installed copy starts (from the release worker)."""
    try:
        with _whats_new_lock:
            prev = _whats_new_load(path)
            state, needs_fetch = _whats_new_next(prev, installed)
            _whats_new_save(state, path)
        try:   # best effort, never in the way of the What's new list
            change = _version_change_entry(prev, installed)
            if change and path is None:   # tests pass their own path and keep the real history alone
                _update_history_add(change)
        except Exception:
            pass
        if not needs_fetch:
            return
        items = (fetch_fn or _fetch_release_notes)(installed)
        with _whats_new_lock:
            state = _whats_new_load(path)
            show = state.get("show")
            if isinstance(show, dict) and show.get("version") == installed:
                show["items"] = items
                _whats_new_save(state, path)
    except Exception as e:
        _log_bg_error("_whats_new_startup", e)

def _fetch_release_notes(version: str) -> list:
    import urllib.request as _ur
    req = _ur.Request(AOC_RELEASE_TAG_API + "v" + version, headers={
        "Accept": "application/vnd.github+json", "User-Agent": f"AOC/{version}"})
    with _ur.urlopen(req, timeout=15) as r:
        return _release_whats_new(json.loads(r.read().decode("utf-8")).get("body"))

def _whats_new_view(installed: str, path=None) -> dict:
    """GET /whats_new: the list to show once, or {} (dev tree / nothing new
    / not fetched yet)."""
    if not installed:
        return {}
    show = _whats_new_load(path).get("show")
    if isinstance(show, dict) and show.get("version") == installed and show.get("items"):
        return {"version": installed, "items": show["items"]}
    return {}

def _whats_new_dismiss(path=None):
    with _whats_new_lock:
        state = _whats_new_load(path)
        state["show"] = None
        _whats_new_save(state, path)

def _update_job_set(event: str, **kw):
    with _update_job_lock:
        new = _update_job_next(_update_job, event, **kw)
        _update_job.clear()
        _update_job.update(new)
    if event == "fail":
        try:   # best effort: the history must never break the job itself
            _update_history_add({"outcome": "failed", "from": _read_installed_version(AOC_VERSION_FILE),
                                 "to": new.get("version", ""), "error": str(new.get("error") or "")[:300]})
        except Exception:
            pass


# Health -> VERSION & UPDATES: what happened to past updates, across the
# restarts an update causes (_update_job is in memory only). A failure is
# written by the updater; a version change by the new version's first start.
UPDATE_HISTORY_FILE = os.path.join(AOC_DATA_DIR, "update_history.jsonl")
_UPDATE_HISTORY_KEEP = 50

def _update_history_add(entry, path=None, now=None):
    path = path or UPDATE_HISTORY_FILE
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": round(now or time.time(), 1), **entry}, ensure_ascii=False) + "\n")
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > 2 * _UPDATE_HISTORY_KEEP:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines[-_UPDATE_HISTORY_KEEP:])
    except OSError as e:
        _log_bg_error("update history", e)

def _update_history_read(limit=10, path=None) -> list:
    """Newest first; broken lines skipped."""
    try:
        with open(path or UPDATE_HISTORY_FILE, encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out

def _version_change_entry(prev_state, installed):
    """Pure: WHATS_NEW_FILE state before this start + the running version ->
    the history entry for a version change, None if nothing changed (or a
    first install). "updated" when the one-click updater staged it."""
    prev_state = prev_state if isinstance(prev_state, dict) else {}
    seen = str(prev_state.get("seen") or "")
    if not installed or not seen or seen == installed:
        return None
    pending = prev_state.get("pending")
    via = "one-click update" if isinstance(pending, dict) and pending.get("version") == installed else "installed by hand"
    return {"outcome": "updated", "from": seen, "to": installed, "via": via}

def _install_log_tail(path=None, lines=40) -> dict:
    """GET /update/log: the end of the last installer log ({} if none)."""
    path = path or os.path.join(UPDATE_DIR, "install.log")
    try:
        st = os.stat(path)
        with open(path, encoding="utf-8", errors="replace") as f:
            tail = f.readlines()[-lines:]
    except OSError:
        return {}
    return {"path": path, "mtime": st.st_mtime, "text": "".join(tail)[-8000:]}

def _update_status_view() -> dict:
    """GET /update/status for Health: version, what's available, the job
    now, past updates, whether an installer log exists."""
    installed = _read_installed_version(AOC_VERSION_FILE)
    with _self_update_lock:
        su = dict(_self_update_status)
    with _update_job_lock:
        job = dict(_update_job)
    log = _install_log_tail(lines=1)
    return {"installed": installed, "mode": "release" if installed else "git", "self_update": su, "job": job,
            "history": _update_history_read(), "log": {"path": log.get("path"), "mtime": log.get("mtime")} if log else None}

def _download_file(url, dest, progress_fn):
    """url -> dest (via dest.part); progress_fn(pct) while it runs."""
    import urllib.request as _ur
    tmp = dest + ".part"
    def _hook(count, block_size, total):
        if total > 0:
            progress_fn(count * block_size * 100 // total)
    try:
        _ur.urlretrieve(url, tmp, _hook)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

def _file_sha256(path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def _spawn_detached(args, cwd):
    """Start a process that must outlive this monitor (it is about to stop
    us): detached, own process group, and broken away from the Task
    Scheduler job the watchdog runs us in (when that job doesn't allow
    breakaway, plain detached is the best we can do)."""
    base = (getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    try:
        subprocess.Popen(args, creationflags=base | 0x01000000,  # CREATE_BREAKAWAY_FROM_JOB
                         close_fds=True, cwd=cwd)
    except OSError:
        subprocess.Popen(args, creationflags=base, close_fds=True, cwd=cwd)

def _launch_installer(exe, log_path):
    """Start the installer so it survives this monitor being stopped by it."""
    _spawn_detached([exe] + _INSTALLER_SILENT_ARGS + ["/LOG=" + log_path], os.path.dirname(exe))

def _restart_aoc(force=False, blockers=None, spawn=None) -> dict:
    """POST /restart: hand the restart to `setup.py --restart` (stops every
    AOC process, starts the watchdog task again). Held prompts / an open
    tunnel / an update on its way are named first unless force."""
    blockers = _restart_blockers(_perm_requests, _tunnel_status[0], _update_job.get("state"), _hooks_health["repairing"],
                                 _release_job.get("state")) if blockers is None else blockers
    if blockers and not force:
        return {"ok": False, "blockers": blockers}
    py = os.path.join(os.path.dirname(sys.executable), "python.exe")
    (spawn or _spawn_detached)([py if os.path.exists(py) else sys.executable,
                                os.path.join(AOC_DIR, "setup.py"), "--restart"], AOC_DIR)
    return {"ok": True, "blockers": blockers}

_UPDATE_INSTALL_WAIT_S = 600  # a working installer stops this process well before

def _update_install_worker(plan, installed, download_fn=None, launch_fn=None,
                           update_dir=None, whats_new_path=None, wait_s=None):
    """Download -> verify -> save the What's new list -> launch. Every
    failure lands in _update_job as an error the badge shows. A successful
    install stops this monitor; still being alive after wait_s means it
    didn't happen."""
    update_dir = update_dir or UPDATE_DIR
    exe = os.path.join(update_dir, plan["name"])
    try:
        os.makedirs(update_dir, exist_ok=True)
        (download_fn or _download_file)(plan["url"], exe, lambda pct: _update_job_set("progress", pct=pct))
        _update_job_set("downloaded")
        got = _file_sha256(exe)
        if got != plan["sha256"]:
            try:
                os.remove(exe)
            except OSError:
                pass
            _update_job_set("fail", error=f"checksum mismatch (got {got[:12]}..., expected {plan['sha256'][:12]}...) -- not installed")
            return
        with _whats_new_lock:
            state = _whats_new_load(whats_new_path)
            state.setdefault("seen", installed)
            state["pending"] = {"version": plan["version"], "items": plan.get("notes") or []}
            _whats_new_save(state, whats_new_path)
        _update_job_set("verified")
        log = os.path.join(update_dir, "install.log")
        (launch_fn or _launch_installer)(exe, log)
        time.sleep(_UPDATE_INSTALL_WAIT_S if wait_s is None else wait_s)
        _update_job_set("fail", error=f"the installer did not restart AOC -- see {log}")
    except Exception as e:
        _log_bg_error("_update_install_worker", e)
        _update_job_set("fail", error=str(e) or e.__class__.__name__)

def _update_start(installed: str, release) -> dict:
    """POST /selfupdate/install: validate, then run the worker in the
    background. The badge follows progress through /status."""
    plan = _update_plan(installed, release)
    if not plan["ok"]:
        return plan
    with _update_job_lock:
        if _update_job.get("state") in ("downloading", "verifying", "installing"):
            return {"ok": False, "error": "an update is already running"}
        new = _update_job_next(_update_job, "start", version=plan["version"])
        _update_job.clear()
        _update_job.update(new)
    _threading.Thread(target=_update_install_worker, args=(plan, installed), daemon=True).start()
    return {"ok": True, "version": plan["version"]}

_SELFUPDATE_FETCH_TIMEOUT_S = 30
_SELFUPDATE_QUIET_FETCH_TIMEOUTS = 2  # consecutive fetch timeouts not logged

def _selfupdate_worker():
    """Background thread: check every 15 min whether this machine's AOC
    checkout is behind origin/master, so a stale copy doesn't silently keep
    running old code across multiple machines. Read-only (fetch + rev-parse
    only, never pulls/merges) -- purely informational. Installer builds
    (VERSION file present) check GitHub releases instead."""
    installed = _read_installed_version(AOC_VERSION_FILE)
    if installed:
        _release_update_worker(installed)
        return
    import time as _t
    fetch_timeouts = 0
    while True:
        try:
            # The checkout lives in OneDrive, which now and then stalls git
            # for a while; a single slow fetch is not a problem worth an
            # error-log line (they made up most of background_errors.log
            # once the notify workers stopped timing out). Skip the round,
            # keep the last result, and only log once it keeps happening.
            try:
                _run(["git", "-C", AOC_DIR, "fetch", "--quiet"], timeout=_SELFUPDATE_FETCH_TIMEOUT_S)
                fetch_timeouts = 0
            except subprocess.TimeoutExpired as e:
                fetch_timeouts += 1
                if fetch_timeouts > _SELFUPDATE_QUIET_FETCH_TIMEOUTS:
                    _log_bg_error("_selfupdate_worker", e)
                _t.sleep(900)
                continue
            head = _run(["git", "-C", AOC_DIR, "rev-parse", "HEAD"], timeout=5).stdout.strip()
            remote = _run(["git", "-C", AOC_DIR, "rev-parse", "origin/master"], timeout=5).stdout.strip()
            count_out = _run(["git", "-C", AOC_DIR, "rev-list", "--count", "HEAD..origin/master"], timeout=5).stdout.strip()
            behind_by = int(count_out) if count_out.isdigit() else 0
            stale = bool(head) and bool(remote) and head != remote and behind_by > 0
            with _self_update_lock:
                _self_update_status["stale"] = stale
                _self_update_status["behind_by"] = behind_by
                _self_update_status["checked_at"] = _t.time()
            if stale and not _self_update_notified[0]:
                _self_update_notified[0] = True
                try:
                    _show_native_toast("AOC", f"Update available: {behind_by} commit(s) behind origin/master.", kind="update")
                except Exception:
                    pass
            elif not stale:
                _self_update_notified[0] = False  # allow a fresh notify if it falls behind again later
        except Exception as e:
            # no network, git not installed, not a git checkout, etc. are all
            # expected here and still stay silent-to-the-user -- but still
            # recorded (throttled) so a persistent failure is diagnosable
            _log_bg_error("_selfupdate_worker", e)
        _t.sleep(900)

_threading.Thread(target=_selfupdate_worker, daemon=True).start()


# ── code reload (watchdog) ─────────────────────────────────────────────────
# watchdog.py restarts this process once monitor.py changed on disk (edit,
# git pull, merge) and the new file has settled + compiles. It asks
# GET /restart_ok first: a held permission / danger prompt keeps a Claude
# Code hook request open, and a restart would drop it -- the hook then fails
# open and the command runs unapproved -- so those block the restart.

def _restart_blockers(perm_requests, tunnel_state, update_state=None, repairing=False, release_state=None) -> list:
    """Pure: reasons not to restart right now (empty = safe). An open remote
    tunnel blocks too: the restart would cut the phone off mid-use, and so
    does a one-click update on its way (the installer restarts AOC itself)
    and a hook repair (taskkill /F of this process takes setup.py with it,
    so Repair silently did nothing when a code reload landed mid-run). Same
    for Health's Publish release / Sync public mirror: killed half-way, a
    release could exist without its asset, or a mirror commit go unpushed."""
    out = []
    if repairing:
        out.append("hook repair running")
    if release_state == "running":
        out.append("release / mirror sync running")
    if update_state not in (None, "idle", "error"):
        out.append(f"update {update_state}")
    held = [r for r in (perm_requests or {}).values() if isinstance(r, dict) and r.get("state") == "pending"]
    if held:
        out.append(f"{len(held)} prompt(s) waiting for your OK")
    if tunnel_state in ("starting", "ready"):
        out.append("remote tunnel is open")
    return out


# ── Release tools: Publish release / Sync public mirror (dev checkout) ────
# The two last steps of a round used to wait on a terminal: a release is
# installer/publish_release.ps1 (dry run, read it, re-run with -Publish) and
# the public mirror is export_public_release.ps1 + commit + push in the
# mirror repo. Health -> RELEASE runs both from here, on this PC only, in two
# clicks each: "Check" does every read-only part and shows what would ship,
# "Publish"/"Commit & push" does the rest. Only in a git checkout: an
# installed copy (VERSION file) has neither the scripts' repo nor `gh`.
PUBLISH_SCRIPT = os.path.join(AOC_DIR, "installer", "publish_release.ps1")
EXPORT_SCRIPT = os.path.join(AOC_DIR, "export_public_release.ps1")
PUBLIC_REPO = "deezeed/aoc-agent-monitoring-public"
# where export_public_release.ps1 writes by default; AOC_PUBLIC_MIRROR points
# a preview at a scratch repo instead (env only, like AOC_UPDATE_SOURCE)
PUBLIC_MIRROR_DIR = (os.environ.get("AOC_PUBLIC_MIRROR")
                     or os.path.join(os.path.dirname(AOC_DIR), "aoc-agent-monitoring-public"))
_release_job = {"kind": "", "state": "idle", "step": "", "error": "", "plan": None, "result": None, "at": 0}
_release_lock = _threading.Lock()


def _release_tools_available(aoc_dir: str = AOC_DIR) -> bool:
    """A dev checkout (.git dir, or file in a worktree) that isn't an installed
    release (VERSION file next to monitor.py)."""
    return os.path.exists(os.path.join(aoc_dir, ".git")) and not os.path.exists(os.path.join(aoc_dir, "VERSION"))


def _ps_quote(s: str) -> str:
    """Pure: a PowerShell single-quoted string literal."""
    return "'" + str(s).replace("'", "''") + "'"


def _ps_script_args(script: str, params) -> list:
    """Pure: argv running a .ps1 with UTF-8 output (PowerShell 5.1 otherwise
    writes the OEM code page when piped, mangling any non-ASCII note line)
    and its exit code passed through (a `throw` in it exits 1)."""
    cmd = ("[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); & " + _ps_quote(script)
           + "".join(" " + (p if p.startswith("-") and p[1:].isalpha() else _ps_quote(p)) for p in params)
           + "; exit $LASTEXITCODE")
    return ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", cmd]


def _release_run(args, cwd=None, timeout=1800):
    """-> (returncode, stdout + stderr). Never raises. stdin closed and every
    prompt off: an inherited stdin left powershell waiting forever, and a gh
    or git credential prompt would do the same with no window to answer in."""
    env = dict(os.environ, GH_PROMPT_DISABLED="1", GIT_TERMINAL_PROMPT="0", GIT_PAGER="cat")
    try:
        r = subprocess.run(args, cwd=cwd or AOC_DIR, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, creationflags=_NO_WINDOW,
                           stdin=subprocess.DEVNULL, env=env)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, f"{type(e).__name__}: {e}"


def _ps_error_line(out: str) -> str:
    """Pure: the message of a failed script run -- PowerShell prints a thrown
    error as its text followed by "At line:..." / "+ ..." position lines;
    otherwise the last non-empty line."""
    lines = [l.rstrip() for l in str(out or "").splitlines() if l.strip()]
    for i, l in enumerate(lines):
        if i and re.match(r"\s*(At line:|At [A-Z]:\\|\+ )", l):
            return lines[i - 1].strip()[:300]
    return (lines[-1].strip() if lines else "no output")[:300]


def _parse_publish_dry_run(out: str) -> dict:
    """Pure: publish_release.ps1's dry-run output -> what the confirm step
    shows. ok=False if any of version / sha256 / CI run is missing."""
    out = str(out or "").replace("\r\n", "\n")

    def field(name):
        m = re.search(r"^" + re.escape(name) + r"\s*:\s*(.+)$", out, re.M)
        return m.group(1).strip() if m else ""
    ver = re.match(r"(\d+\.\d+\.\d+)", field("Version"))
    run = re.match(r"(\d+)\s*\(([0-9a-f]{7,40})\)", field("CI run"))
    sha = field("SHA-256").lower()
    chg = re.match(r"(\d+)\s+since\s+(.+)", field("Changes"))
    notes = ""
    m = re.search(r"----- release notes -----\n(.*?)\n-------------------------", out, re.S)
    if m:
        notes = m.group(1).strip()
    warnings, seen = [], False
    for l in out.split("\n"):
        if "read like developer notes" in l:
            seen = True
            continue
        if seen:
            w = re.match(r"\s+- (.+)", l)
            if not w:
                break
            warnings.append(w.group(1).strip())
    plan = {"version": ver.group(1) if ver else "", "sha256": sha if re.fullmatch(r"[0-9a-f]{64}", sha) else "",
            "run_id": run.group(1) if run else "", "commit": run.group(2) if run else "",
            "changes": int(chg.group(1)) if chg else 0, "since": chg.group(2).strip() if chg else "",
            "notes": notes, "warnings": warnings}
    plan["ok"] = bool(plan["version"] and plan["sha256"] and plan["run_id"])
    return plan


def _publish_dry_run(run_id: str, run) -> dict:
    rc, out = run(_ps_script_args(PUBLISH_SCRIPT, ["-RunId", run_id] if run_id else []), AOC_DIR)
    if rc != 0:
        raise RuntimeError(_ps_error_line(out))
    plan = _parse_publish_dry_run(out)
    if not plan["ok"]:
        raise RuntimeError("couldn't read version / SHA-256 / CI run from the dry run's output")
    return plan


def _release_asset_sha256(version: str, run) -> str:
    """Download the just-published installer back from GitHub and hash it --
    what a user's one-click update will actually get."""
    import hashlib, shutil, tempfile
    d = tempfile.mkdtemp(prefix="aoc-release-check-")
    try:
        rc, out = run(["gh", "release", "download", "v" + version, "--repo", PUBLIC_REPO,
                       "--pattern", f"AOC-Setup-{version}.exe", "--dir", d], d)
        exe = os.path.join(d, f"AOC-Setup-{version}.exe")
        if rc != 0 or not os.path.isfile(exe):
            raise RuntimeError("couldn't download the published installer: " + _ps_error_line(out))
        h = hashlib.sha256()
        with open(exe, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _publish_confirm(plan: dict, allow_internal: bool, run, sha_of_asset) -> dict:
    if plan.get("warnings") and not allow_internal:
        raise RuntimeError("the notes have developer-sounding lines -- tick 'publish anyway' (then reword them on GitHub)")
    params = ["-RunId", plan["run_id"], "-Publish"] + (["-AllowInternalNotes"] if allow_internal else [])
    rc, out = run(_ps_script_args(PUBLISH_SCRIPT, params), AOC_DIR)
    if rc != 0:
        raise RuntimeError(_ps_error_line(out))
    m = re.search(r"Published v\S+ -> (\S+)", out)
    # the release exists from here on: a failed check is reported, not raised
    try:
        got, verify_error = sha_of_asset(plan["version"]), ""
    except Exception as e:
        got, verify_error = "", (str(e) or type(e).__name__)[:300]
    return {"version": plan["version"], "url": m.group(1) if m else f"https://github.com/{PUBLIC_REPO}/releases/tag/v{plan['version']}",
            "sha256": plan["sha256"], "asset_sha256": got, "verified": bool(got) and got == plan["sha256"],
            "verify_error": verify_error}


def _rel_git(run, repo: str, *args) -> str:
    rc, out = run(["git", "-C", repo, *args], repo)
    if rc != 0:
        raise RuntimeError(f"git {args[0]} failed: " + _ps_error_line(out))
    return out


def _sync_prepare(run, mirror: str = None, now: float = None) -> dict:
    """Export into the mirror, stage everything there and say what a commit
    would contain. Nothing leaves this PC yet."""
    mirror = mirror or PUBLIC_MIRROR_DIR
    if not os.path.exists(os.path.join(mirror, ".git")):
        raise RuntimeError(f"the public mirror isn't a git checkout: {mirror}")
    rc, out = run(_ps_script_args(EXPORT_SCRIPT, ["-Dest", mirror]), AOC_DIR)
    if rc != 0:
        raise RuntimeError("export failed: " + _ps_error_line(out))
    _rel_git(run, mirror, "add", "-A")
    status = [l for l in _rel_git(run, mirror, "status", "--short").splitlines() if l.strip()]
    stat = [l for l in _rel_git(run, mirror, "diff", "--cached", "--stat").splitlines() if l.strip()]
    sha = _rel_git(run, AOC_DIR, "rev-parse", "--short", "HEAD").strip()
    dirty = [l for l in _rel_git(run, AOC_DIR, "status", "--porcelain", "--untracked-files=no").splitlines() if l.strip()]
    day = datetime.fromtimestamp(now if now is not None else time.time()).strftime("%Y-%m-%d")
    return {"mirror": mirror, "changed": len(status), "status": status[:40], "summary": stat[-1].strip() if stat else "",
            "private_sha": sha, "private_dirty": len(dirty), "message": f"AOC public mirror {day} @{sha}"}


def _sync_confirm(plan: dict, run) -> dict:
    mirror = plan["mirror"]
    if not plan.get("changed"):
        raise RuntimeError("nothing to commit -- the mirror already matches")
    _rel_git(run, mirror, "commit", "-q", "-m", plan["message"])
    commit = _rel_git(run, mirror, "rev-parse", "--short", "HEAD").strip()
    rc, out = run(["git", "-C", mirror, "push", "-q"], mirror)
    if rc != 0:
        raise RuntimeError(f"committed {commit} but the push failed: " + _ps_error_line(out))
    return {"commit": commit, "message": plan["message"], "pushed": True}


def _release_action(kind: str, action: str, data=None, run=None, spawn=None, sha_of_asset=None) -> dict:
    """POST /release/<publish|sync>/<prepare|confirm|cancel>. prepare runs the
    read-only part in the background -> state "ready" with a plan; confirm
    acts on exactly that plan -> "done" with a result; a failure -> "error".
    One job at a time; GET /release/status polls it."""
    run = run or _release_run
    spawn = spawn or (lambda fn: _threading.Thread(target=fn, daemon=True).start())
    data = data if isinstance(data, dict) else {}
    if kind not in ("publish", "sync") or action not in ("prepare", "confirm", "cancel"):
        return {"ok": False, "error": "unknown release action"}
    with _release_lock:
        job = _release_job
        if action == "cancel":
            if job["state"] == "running":
                return {"ok": False, "error": "it's running -- wait for it to finish"}
            job.update(kind="", state="idle", step="", error="", plan=None, result=None)
            return {"ok": True}
        if job["state"] == "running":
            return {"ok": False, "error": f"{job['kind']} is already running"}
        if action == "confirm" and not (job["state"] == "ready" and job["kind"] == kind and job["plan"]):
            return {"ok": False, "error": "nothing checked to confirm -- run the check first"}
        run_id = str(data.get("run_id") or "").strip()
        if action == "prepare" and run_id and not run_id.isdigit():
            return {"ok": False, "error": "the CI run id is a number"}
        plan, allow = job["plan"], bool(data.get("allow_internal"))
        step = {("publish", "prepare"): "dry run (downloading the CI installer)", ("publish", "confirm"): "publishing",
                ("sync", "prepare"): "exporting", ("sync", "confirm"): "committing + pushing"}[(kind, action)]
        job.update(kind=kind, state="running", step=step, error="", result=None, at=time.time(),
                   plan=plan if action == "confirm" else None)

    def work():
        try:
            if kind == "publish" and action == "prepare":
                out = {"state": "ready", "plan": _publish_dry_run(run_id, run)}
            elif kind == "publish":
                out = {"state": "done", "result": _publish_confirm(
                    plan, allow, run, sha_of_asset or (lambda v: _release_asset_sha256(v, run)))}
            elif action == "prepare":
                out = {"state": "ready", "plan": _sync_prepare(run)}
            else:
                out = {"state": "done", "result": _sync_confirm(plan, run)}
        except Exception as e:
            out = {"state": "error", "error": (str(e) or type(e).__name__)[:400]}
        with _release_lock:
            _release_job.update(step="", at=time.time(), **out)
    spawn(work)
    return {"ok": True}


def _release_status_view() -> dict:
    with _release_lock:
        return {"available": _release_tools_available(), **_release_job}


# ── PR/branch auto-link ────────────────────────────────────────────────────

_pr_link_cache = {}  # git_branch -> {"url": str|None, "checked_at": epoch}
_pr_link_lock = _threading.Lock()

def _check_pr_for_branch(branch: str, cwd: str):
    """Look up whether `branch` has an open GitHub PR, via the gh CLI run
    inside `cwd` (so gh infers the right repo from that directory's git
    remote). Returns the PR url, or None for no open PR / no gh CLI / no
    git remote / any other failure -- all treated the same as the
    self-update worker treats "not a git repo"/"no network": harmless,
    silent, cached as a negative result."""
    try:
        result = _run(["gh", "pr", "view", branch, "--json", "url"], cwd=cwd, timeout=8)
        if result.returncode != 0:
            return None
        return json.loads(result.stdout).get("url")
    except Exception:
        return None

def _pr_link_worker():
    """Background thread: every 60s, for each distinct git branch
    currently tracked across sessions, refresh the PR-link cache if the
    existing entry is missing or older than 5 min -- mirrors
    _selfupdate_worker's shape (periodic subprocess call + small TTL
    cache so a slow external command never blocks a request), and never
    calls `gh` more than once per branch per 5 min regardless of how many
    sessions share that branch or how often /status is polled.
    Thread started further down the file (see _agent_retention_worker's
    start, below), not right here -- calling _load_status() from a thread
    started at module load, this early, would risk the same NameError
    race _backup_history_db/_agent_retention_worker already work around,
    since _load_status isn't defined until later in the file. Confirmed
    live: this used to actually fire on every single monitor startup,
    once, before background_errors.log existed to reveal it."""
    import time as _t
    while True:
        try:
            status = _load_status()
            seen_branches = set()
            for sess in status.get("sessions", {}).values():
                branch = sess.get("git_branch", "")
                cwd = sess.get("cwd", "")
                if not branch or not cwd or branch in seen_branches:
                    continue
                seen_branches.add(branch)
                with _pr_link_lock:
                    cached = _pr_link_cache.get(branch)
                if cached and _t.time() - cached["checked_at"] < 300:
                    continue
                url = _check_pr_for_branch(branch, cwd)
                with _pr_link_lock:
                    _pr_link_cache[branch] = {"url": url, "checked_at": _t.time()}
        except Exception as e:
            _log_bg_error("_pr_link_worker", e)
        _t.sleep(60)


# ── weekly cost/activity digest ─────────────────────────────────────────────
# TOKEN_FILE-style small standalone marker (not status.json, which gets
# wholesale-rebuilt by /reset -- see WEBHOOK_SETTINGS_FILE's own comment
# above for the same reasoning) tracking the ISO week the digest was last
# sent, so a calendar-based schedule stays correct across watchdog restarts
# (a rolling "7 days since boot" timer would drift/double-fire instead).
DIGEST_MARKER_FILE = os.path.join(AOC_DIR, "aoc_last_digest.txt")

def _read_last_digest_week() -> str:
    try:
        with open(DIGEST_MARKER_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""

def _write_last_digest_week(week: str):
    try:
        with open(DIGEST_MARKER_FILE, "w", encoding="utf-8") as f:
            f.write(week)
    except Exception:
        pass

def _digest_marker_for(now, cadence: str) -> str:
    """The 'have we already sent this period's digest' marker string for
    a given cadence -- calendar date for daily, ISO week for weekly.
    Single place computing this so the scheduling check and the
    write-marker step can never drift out of sync with each other."""
    return now.strftime("%Y-%m-%d") if cadence == "daily" else now.strftime("%G-%V")

def _should_send_digest(now, last_sent_week: str, cadence: str = "weekly") -> bool:
    """True if `now` falls in the digest's send window (09:00-10:00) AND
    it hasn't already been sent for the current period, per `cadence`:
    - "off": never send.
    - "daily": any day, tracked by calendar date, so a mid-day restart
      doesn't cause a double-send.
    - "weekly" (default, and the fallback for any unrecognized value):
      Monday only, tracked via ISO week string %G-%V, so a mid-week
      restart doesn't cause a double-send, and a missed window doesn't
      cause a skipped week either -- next Monday's check just compares
      against the same stored value."""
    if cadence == "off":
        return False
    if not (9 <= now.hour < 10):
        return False
    if cadence == "daily":
        return _digest_marker_for(now, cadence) != last_sent_week
    if now.weekday() != 0:  # Monday
        return False
    return _digest_marker_for(now, cadence) != last_sent_week

def _week_windows(by_day, today):
    """Pure: by_day rows of the last 7 calendar days (today included) and of
    the 7 before. by_day only has days with sessions, so 7 rows could span
    weeks -- windows go by date. today = datetime.date."""
    import datetime as _dt
    last, prev = [], []
    for r in by_day or []:
        try:
            age = (today - _dt.date.fromisoformat(str(r.get("date") or "")[:10])).days
        except ValueError:
            continue
        if 0 <= age < 7:
            last.append(r)
        elif 7 <= age < 14:
            prev.append(r)
    return last, prev

def _build_digest_summary() -> dict:
    """The weekly digest: the last 7 calendar days of by_day, and the 7
    before for a week-over-week cost trend (see _week_windows).

    Excludes any project in muted_projects -- a muted project already has
    its individual toast/webhook events suppressed, and the digest is
    itself a notification, so its numbers silently inflating a "quiet"
    project's absence from the summary would defeat the point of muting
    it. Falls back to _db_analytics()'s own (unfiltered) by_day when
    nothing is muted, rather than always taking the excluding-query path
    for identical output."""
    muted = [p for p in (_notify_settings.get("muted_projects") or []) if p]
    by_day = _db_by_day_excluding_projects(muted) if muted else _db_analytics().get("by_day", [])
    last7, prev7 = _week_windows(by_day, datetime.now().date())
    cost = round(sum(r.get("cost") or 0.0 for r in last7), 2)
    prev_week_cost = round(sum(r.get("cost") or 0.0 for r in prev7), 2)
    cost_pct_change = round((cost - prev_week_cost) / prev_week_cost * 100, 1) if prev_week_cost > 0 else None
    return {
        "sessions": sum(r.get("sessions") or 0 for r in last7),
        "tokens": sum(r.get("tokens") or 0 for r in last7),
        "cost": cost,
        "errors": sum(r.get("errors") or 0 for r in last7),
        "prev_week_cost": prev_week_cost,
        "cost_pct_change": cost_pct_change,
    }

def _digest_worker():
    """Background thread: every 30 min, check whether it's time to send
    the weekly digest. Delivery mirrors every other server-side
    notification in this app: native toast (works with no browser tab
    open) plus a webhook if one is configured and opted in."""
    import time as _t
    while True:
        try:
            now = datetime.now()
            # Re-read fresh every iteration rather than caching it once,
            # matching how _webhook_notify_worker already re-reads
            # _webhook_settings every loop -- a cadence change in Settings
            # takes effect on the next check, no restart needed.
            cadence = _notify_settings.get("digest_cadence", "weekly")
            if _should_send_digest(now, _read_last_digest_week(), cadence):
                summary = _build_digest_summary()
                pct = summary.get("cost_pct_change")
                trend = f"{'+' if pct >= 0 else ''}{pct}% vs last week" if pct is not None else "no prior week data"
                try:
                    _show_native_toast(
                        "AOC Digest",
                        f"{summary['sessions']} sessions, ${summary['cost']:.2f} ({trend}), {summary['errors']} errors this week",
                        kind="weekly_digest",
                    )
                except Exception:
                    pass
                _notify_webhook("weekly_digest", {"event": "weekly_digest", "summary": summary})
                _write_last_digest_week(_digest_marker_for(now, cadence))
        except Exception as e:
            _log_bg_error("_digest_worker", e)
        _t.sleep(1800)

_threading.Thread(target=_digest_worker, daemon=True).start()
