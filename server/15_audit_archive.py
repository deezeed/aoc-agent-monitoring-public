# AOC server, part 15: audit log, auto-archive, session-start detection.
# monitor.py exec()s server/NN_*.py in number order into ONE shared
# namespace (its own) -- not an importable module: names from earlier
# parts are plain globals here, and __file__ is monitor.py.
# ── Audit Log ─────────────────────────────────────────────────────────────────

class LogWriter:
    """Writes session audit log to logs/session_YYYY-MM-DD_HH-MM-SS.log"""

    def __init__(self):
        self._lock = threading.Lock()
        self._path = None          # current log file path
        self._session_start = None # datetime of SESSION_START
        self._poll_count = 0       # total polls — used to decide when to log POLL
        self._sessions_logged = 0  # number of completed sessions this run

    # ── public API ────────────────────────────────────────────────────────────

    def session_start(self, status: dict):
        """Call when session_active becomes True."""
        with self._lock:
            os.makedirs(LOGS_DIR, exist_ok=True)
            ts = datetime.now()
            fname = ts.strftime("session_%Y-%m-%d_%H-%M-%S.log")
            self._path = os.path.join(LOGS_DIR, fname)
            self._session_start = ts
            agents = len(status.get("agents", []))
            project = status.get("project", "")
            self._write("SESSION_START", f"project={project} | agents={agents}")

    def session_reset(self):
        """Call when POST /reset is handled."""
        with self._lock:
            self._sessions_logged += 1
            self._write("SESSION_RESET", f"sessions_logged={self._sessions_logged}")
            self._path = None
            self._session_start = None

    def agent_start(self, agent: dict):
        """Call when an agent's status changes to 'running'."""
        with self._lock:
            self._write("AGENT_START",
                        f"agent={agent.get('name','')} | id={agent.get('id','')}")

    def task_complete(self, agent: dict, task: dict):
        """Call when a task flips done: False -> True."""
        with self._lock:
            self._write("TASK_COMPLETE",
                        f"agent={agent.get('name','')} | task={task.get('label','')}")

    def agent_done(self, agent: dict):
        """Call when an agent's status changes to 'done'."""
        with self._lock:
            duration = ""
            if self._session_start:
                secs = int((datetime.now() - self._session_start).total_seconds())
                duration = f" | duration={secs}s"
            self._write("AGENT_DONE",
                        f"agent={agent.get('name','')} | id={agent.get('id','')}{duration}")

    def poll(self):
        """Call on every poll — only writes every 3rd poll."""
        with self._lock:
            self._poll_count += 1
            if self._poll_count % 3 == 0:
                self._write("POLL", f"poll_count={self._poll_count}")

    # ── helpers ───────────────────────────────────────────────────────────────

    def _write(self, event: str, detail: str):
        """Write one log line. Called with self._lock held (by convention, from
        the other methods above) -- but releases it for the actual disk I/O.
        logs/ lives under a path that can be transiently locked by an external
        process (e.g. a cloud-sync client) mid-write; every /status request
        calls poll() -> _write() on this same shared lock, so if the write
        itself blocked while holding the lock, one stuck file lock would freeze
        every other concurrent caller (including the watchdog's own health
        check) instead of just this one log line."""
        if self._path is None:
            return
        path = self._path
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        line = f"{ts} | {event:<14} | {detail}\n"
        self._lock.release()
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            # Every _log.* call (from workers and request handlers alike)
            # funnels through here -- a swallowed failure used to silently
            # kill all session audit logging with zero fallback.
            _log_bg_error("LogWriter._write", e)
        finally:
            self._lock.acquire()

    def current_log_path(self):
        with self._lock:
            return self._path

    def read_tail(self, n=20):
        """Return last n lines of current session log as a list of strings."""
        with self._lock:
            path = self._path
        if not path or not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            return [l.rstrip("\n") for l in lines[-n:]]
        except Exception:
            return []

    def list_logs(self):
        """Return list of existing log filenames sorted newest first."""
        try:
            files = sorted(
                glob.glob(os.path.join(LOGS_DIR, "session_*.log")),
                reverse=True,
            )
            return [os.path.basename(p) for p in files]
        except Exception:
            return []


# Singleton used throughout the module
_log = LogWriter()

def _get_project_dir():
    """Read project_dir from status JSON if agents write it, else return None."""
    data = _load_json_file(STATUS_FILE, default={})
    return (data.get("project_dir") or None) if isinstance(data, dict) else None

_status_lock = threading.Lock()
_status_cond = threading.Condition()   # notifies /events SSE connections on change
_status_version = [0]

# _build_status_payload() cache: on a long session the status file (and every
# connected agent's accumulated log text) can reach multiple megabytes, and
# that function re-reads + re-parses it from disk on every call. Without
# caching, every open /events WebSocket client independently redoes that full
# rebuild each time _status_version bumps (every ~2s from _claude_proc_worker
# alone) -- several of those landing at once, stacked with the GIL cost of
# repeated multi-MB json.load/json.dumps, was tripping watchdog.py's 2-strikes
# health check (confirmed: recurring "Monitor not responding" restarts every
# 9-25min, self-healing, worsening as accumulated agent logs grew the payload
# size over the session). Version-gated so a real status change (dismiss,
# note, session start/stop) is never served stale; the short TTL alongside it
# catches state that changes without bumping the version at all (tunnel
# status, self_update, infra_health -- see _build_status_payload) so those
# never go stale for longer than a second.
_status_payload_cache_lock = threading.Lock()
_status_payload_cache = {"version": None, "built_at": 0.0, "payload": None}
_STATUS_PAYLOAD_CACHE_TTL = 1.0  # seconds

def _status_cache_is_fresh(cached_version, cached_built_at, current_version, now, ttl=_STATUS_PAYLOAD_CACHE_TTL) -> bool:
    """Pure decision logic for _build_status_payload's cache: a cached
    payload is safe to reuse only if nothing that bumps _status_version has
    happened since it was built AND it's within the TTL window (the guard
    against the version-less staleness cases above)."""
    if cached_version is None or cached_version != current_version:
        return False
    return (now - cached_built_at) < ttl

def _load_status():
    return _load_json_file(STATUS_FILE, default={"session_active": False, "agents": []})

def _replace_with_retry(src, dst, attempts=20, delay_s=0.05):
    """os.replace that rides out a brief lock on dst. On Windows replacing a
    file another handle has open fails with WinError 5 -- and the status file
    is read constantly (every /status rebuild opens it), so a hook /update or
    scanner write regularly collided with a reader and was simply lost
    (logged live as "[WinError 5] Access is denied: ...monitor-status.json.tmp").
    Retries for up to ~1s, then raises like os.replace would."""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay_s)

def _save_status(data):
    tmp = STATUS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _replace_with_retry(tmp, STATUS_FILE)
    with _status_cond:
        _status_version[0] += 1
        _status_cond.notify_all()


# ── Auto-archive old done/error agents ──────────────────────────────────────
# Nothing previously pruned the live `agents` array automatically -- only the
# manual /remove and /clear_done endpoints ever shrank it. Over a long session
# that meant unbounded growth (confirmed: 27 accumulated agents, ~3MB /status
# payload, a real contributor to watchdog's earlier recurring "Monitor not
# responding" restarts) and, once someone finally did clear it by hand, every
# agent-centric view (TIMELINE/SUMMARY/GRAPH/HEAT/TREE) going instantly blank.
# This keeps a rolling window of *recent* completed activity automatically
# instead of an all-or-nothing manual sweep.

def _hms_elapsed_hours(ts_hms: str, now_hms: str):
    """Hours elapsed since a bare "HH:MM:SS" local-time timestamp (as stored
    in an agent's completed_at -- see _now_ts) assuming it happened within
    the last 24h, via mod-24 arithmetic to handle the midnight wraparound.
    Returns None if either string doesn't parse as H:M:S. See
    _clamp_agent_retention_hours for why retention windows are capped below
    24h -- this measurement is fundamentally ambiguous past that point."""
    try:
        h1, m1, s1 = (int(x) for x in ts_hms.split(":"))
        h2, m2, s2 = (int(x) for x in now_hms.split(":"))
    except (ValueError, AttributeError):
        return None
    then_secs = h1 * 3600 + m1 * 60 + s1
    now_secs = h2 * 3600 + m2 * 60 + s2
    return ((now_secs - then_secs) % 86400) / 3600.0


def _prune_old_agents(status: dict, retention_hours, now_hms: str) -> int:
    """Remove agents whose status is done/error and whose completed_at is at
    least retention_hours old (see _hms_elapsed_hours). Never touches
    running/waiting agents, or ones missing a completed_at entirely (nothing
    to measure against -- left alone rather than guessed at). Mutates
    `status["agents"]` in place when anything is removed; returns the count
    removed."""
    agents = status.get("agents", [])
    kept = []
    removed = 0
    for a in agents:
        if a.get("status") not in ("done", "error"):
            kept.append(a)
            continue
        completed_at = a.get("completed_at")
        if not completed_at:
            kept.append(a)
            continue
        elapsed = _hms_elapsed_hours(completed_at, now_hms)
        if elapsed is None or elapsed < retention_hours:
            kept.append(a)
        else:
            removed += 1
    if removed:
        status["agents"] = kept
    return removed


def _agent_retention_worker():
    """Background thread: every 15 min, auto-clear done/error agents past the
    configured retention window. Started (see bottom of this section) only
    after _status_lock/_load_status/_save_status/_notify_settings all exist
    -- referencing any of those from a thread started earlier in the file,
    module-load-order, would risk the same NameError race _backup_history_db
    already works around."""
    import time as _t
    while True:
        _t.sleep(900)
        try:
            hours = _clamp_agent_retention_hours(_notify_settings.get("agent_retention_hours", 12))
            with _status_lock:
                status = _load_status()
                removed = _prune_old_agents(status, hours, _now_ts())
                if removed:
                    _save_status(status)
        except Exception as e:
            _log_bg_error("_agent_retention_worker", e)

threading.Thread(target=_agent_retention_worker, daemon=True).start()
# _pr_link_worker's own thread also starts here rather than right after its
# def (up near line 2105) -- see that function's docstring for why.
threading.Thread(target=_pr_link_worker, daemon=True).start()


# ── session-start detection ───────────────────────────────────────────────────
# Tracks the last known session state so we can fire SESSION_START exactly once.
_prev_session_active = False
_prev_session_active_lock = threading.Lock()

def _check_session_start(status: dict):
    """Compare new status with previous; fire SESSION_START if newly active."""
    global _prev_session_active
    with _prev_session_active_lock:
        now_active = bool(status.get("session_active"))
        if now_active and not _prev_session_active:
            _log.session_start(status)
        _prev_session_active = now_active
