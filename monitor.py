"""
AOC — Agent Operations Center
spusti: python monitor.py  ->  http://localhost:5151
"""
import json, os, time, threading, webbrowser, subprocess, glob, sqlite3, re, calendar, atexit
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timedelta

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, creationflags=_NO_WINDOW, **kw)

AOC_DIR  = os.path.dirname(os.path.abspath(__file__))
# Hot, constantly-written data (status json, sqlite db, per-session logs) lives
# under a local, non-cloud-synced directory. AOC_DIR sits inside OneDrive, and
# OneDrive can transiently hold an exclusive lock on a file mid-upload — since
# every /status request synchronously touches these files (see LogWriter.poll),
# a single stuck OneDrive lock used to be able to freeze the whole server long
# enough for the watchdog to consider it dead and restart it, dropping any
# hook /update that landed in that window (agents silently vanishing from AOC).
AOC_DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", AOC_DIR), "AOC")
os.makedirs(AOC_DATA_DIR, exist_ok=True)
STATUS_FILE = os.path.join(AOC_DATA_DIR, "monitor-status.json")
LOGS_DIR    = os.path.join(AOC_DATA_DIR, "logs")
DB_FILE     = os.path.join(AOC_DATA_DIR, "history.db")
KNOWN_VERSIONS_FILE = os.path.join(AOC_DATA_DIR, "known_cc_versions.json")
KILL_AUDIT_LOG = os.path.join(LOGS_DIR, "kill_audit.log")
ALERT_AUDIT_LOG = os.path.join(LOGS_DIR, "alert_audit.log")
PORT = 5151

# Static SVG app icon for the PWA manifest/apple-touch-icon -- mirrors the
# hexagon+center-dot brand mark the dynamic favicon already draws on a
# <canvas> at runtime (see the "dynamic favicon" JS section), but as a
# real static file: a manifest's icons are fetched by the browser/OS
# independently of the page's own JS, so a canvas data-URI generated at
# runtime can't serve as a manifest icon. No PNG here (no image library
# in this codebase's dependencies) -- SVG is supported directly by
# manifest icons in Chromium/Firefox with "sizes":"any"; iOS Safari's
# apple-touch-icon historically wants a raster format, so that one spot
# is best-effort only, not guaranteed to render on iOS.
_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <polygon points="16,3 27.4,9.5 27.4,22.5 16,29 4.6,22.5 4.6,9.5"
           fill="#00c4e833" stroke="#00c4e8" stroke-width="2"/>
  <circle cx="16" cy="16" r="4" fill="#00c4e8"/>
</svg>"""

def _build_manifest(token: str = "") -> dict:
    """Build the PWA manifest, re-embedding `token` (this request's own
    ?token=, if any) into every URL the manifest itself points at, so a
    PWA installed through a tunneled/remote session stays authenticated
    end-to-end (manifest fetch -> icon fetch -> the installed icon's
    start_url) instead of 401ing partway through -- mirrors how every
    other tunnel-carried resource URL in this app already appends its
    token."""
    suffix = f"?token={token}" if token else ""
    return {
        "name": "AOC — Agent Operations Center",
        "short_name": "AOC",
        "start_url": "/" + suffix,
        "display": "standalone",
        "background_color": "#04060e",
        "theme_color": "#04060e",
        "icons": [{"src": "/icon.svg" + suffix, "sizes": "any", "type": "image/svg+xml"}],
    }

def _append_audit_entry(path: str, entry: dict) -> None:
    """Shared append-only JSON-line writer for the audit logs (kill_audit.log,
    alert_audit.log) -- stamps 'ts', makes sure LOGS_DIR exists, and appends
    one JSON line. This was independently duplicated in both
    _log_kill_attempt and _log_alert_fired before being factored out here;
    each caller still builds its own entry shape and guards that construction
    with its own try/except, this only absorbs the shared I/O boilerplate."""
    os.makedirs(LOGS_DIR, exist_ok=True)
    stamped = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **entry}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(stamped, ensure_ascii=False) + "\n")

def _log_kill_attempt(sid: str, sess: dict, host_pid, outcome: str, remote_addr: str = "") -> None:
    """Append-only record of every /kill_session call, successful or refused.
    Force Stop has no undo, so once it's trusted for daily use (or ever shown
    to someone other than the one person running this locally) there needs
    to be a record of who/what got killed and when -- there previously was
    none at all."""
    try:
        _append_audit_entry(KILL_AUDIT_LOG, {
            "session_id": sid,
            "project": (sess or {}).get("project", ""),
            "cwd": (sess or {}).get("cwd", ""),
            "host_pid": host_pid,
            "outcome": outcome,
            "remote_addr": remote_addr,
        })
    except Exception:
        pass

def _log_alert_fired(payload: dict) -> None:
    """Append-only record of every webhook event AOC actually dispatched.
    Both the server-side worker (done/error/stuck/burn_spike/cost_spike/
    waiting_nudge/weekly_digest) and the client-triggered /webhook_fire path
    funnel through _fire_webhook, so hooking it there is the one place this
    can't miss an event. Nothing durable recorded this before -- the
    in-browser notification history panel is a plain in-memory array that
    resets on every page reload, so there was previously no way to answer
    "did my burn_spike webhook actually fire last night" after the fact."""
    try:
        sess = payload.get("session") or {}
        agent = payload.get("agent") or {}
        _append_audit_entry(ALERT_AUDIT_LOG, {
            "event": payload.get("event", ""),
            "project": sess.get("project", ""),
            "target": sess.get("display_name") or agent.get("name") or "",
        })
    except Exception:
        pass

BG_ERROR_LOG = os.path.join(LOGS_DIR, "background_errors.log")
_bg_error_last: dict = {}  # where -> (last_logged_epoch, last_message)
_BG_ERROR_MIN_INTERVAL_S = 300  # collapse a repeating failure to once per 5 min

def _log_bg_error(where: str, exc: Exception) -> None:
    """Append-only record of exceptions swallowed by background/maintenance
    workers' outermost except:pass (autosave, backups, digest, transcript
    scanner, webhook/snitch workers, etc.). These all run headless under
    pythonw.exe with no console -- a bare except:pass here previously left
    failures like disk-full/permissions/DB-lock completely invisible.
    Throttled per call site to the same message at most once per
    _BG_ERROR_MIN_INTERVAL_S so a persistently offline machine or a stuck
    lock doesn't re-log every loop iteration."""
    try:
        msg = str(exc)[:300]
        now = time.time()
        last = _bg_error_last.get(where)
        if last and last[1] == msg and now - last[0] < _BG_ERROR_MIN_INTERVAL_S:
            return
        _bg_error_last[where] = (now, msg)
        _append_audit_entry(BG_ERROR_LOG, {"where": where, "error": msg})
    except Exception:
        pass

_PROCESS_START = time.time()  # for the /diag view's uptime figure

def _now_ts():
    return datetime.now().strftime("%H:%M:%S")

def _iso_utc_to_local_hms(iso_ts: str) -> str:
    """Convert a transcript's UTC ISO timestamp (e.g. '2026-07-15T18:41:23.530Z')
    to the same local HH:MM:SS wall-clock format _now_ts() produces, so
    transcript-detected agents' started_at/completed_at compare correctly
    against hook-reported ones (the frontend's parseTimeStr assumes today's
    local time). Falls back to "now" on any parse failure."""
    try:
        dt = datetime.strptime(iso_ts.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
        epoch = calendar.timegm(dt.timetuple())  # iso_ts is UTC
        return time.strftime("%H:%M:%S", time.localtime(epoch))
    except Exception:
        return _now_ts()

def _load_json_file(path, default=None):
    """Shared open+json.load+except-return-default boilerplate -- this
    exact 4-line shape was independently repeated 5 times (known-CC-versions,
    webhook settings, notify settings, project-dir lookup, status.json)
    before being factored out here. Callers still do their own shape
    validation/post-processing on the returned value -- this only
    eliminates the duplicated try/open/load/except, not the different
    validation each site needs."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

_known_cc_versions_lock = threading.Lock()

def _load_known_cc_versions() -> set:
    """Loaded shape must be a list (json.dump'd from a set elsewhere) --
    previously blindly wrapped whatever json.load returned in set(), so a
    corrupted-but-valid-JSON file of the wrong shape (e.g. a dict) would
    have silently produced a set of dict keys instead of surfacing an
    error or falling back to empty, unlike this loader's two structural
    siblings (_load_webhook_settings/_load_notify_settings), which both
    already validate shape before trusting the loaded data."""
    data = _load_json_file(KNOWN_VERSIONS_FILE, default=[])
    return set(data) if isinstance(data, list) else set()

def _save_known_cc_versions(versions: set):
    try:
        with open(KNOWN_VERSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(versions), f)
    except Exception:
        pass

_known_cc_versions = _load_known_cc_versions()  # loaded once at startup, like _auth_token

def _check_new_cc_version(version: str):
    """The transcript-based Agent/Task fallback (and the hook side-channel
    it backstops) both depend on undocumented Claude Code internals --
    transcript JSONL shape, task-notification XML, tool_use_id matching.
    Nothing else in this app would notice if a Claude Code update changed
    any of that; this is a cheap early-warning signal so a silent breakage
    at least gets flagged instead of discovered by agents quietly vanishing
    again. version-check happens once per session (see call site: only
    fires the first time a session's cc_version is discovered), so this
    itself only runs rarely, not on every scan tick."""
    if not version:
        return
    with _known_cc_versions_lock:
        if version in _known_cc_versions:
            return
        _known_cc_versions.add(version)
        _save_known_cc_versions(_known_cc_versions)
    try:
        _show_native_toast(
            "AOC",
            f"New Claude Code version detected: {version} -- if agent tracking looks off, "
            "the transcript/hook format may have changed."
        )
    except Exception:
        pass  # _show_native_toast defined later in module; see docstring above for why this is safe to ignore

_TASK_NOTIF_RE = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)
_TASK_TAG_RE = re.compile(r"<(tool-use-id|status|summary|result)>(.*?)</\1>", re.S)
_TASK_USAGE_RE = re.compile(r"<subagent_tokens>(\d+)</subagent_tokens>")


# ── SQLite History DB ──────────────────────────────────────────────────────────

_db_lock = threading.Lock()

def _db_conn():
    c = sqlite3.connect(DB_FILE, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c

def _init_db():
    with _db_lock:
        c = _db_conn()
        c.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT PRIMARY KEY,
                project     TEXT,
                orchestrator TEXT, -- reserved, never populated or read (checked during a
                                   -- coverage/cleanup pass) -- left in place rather than
                                   -- DROP COLUMN'd since this codebase has no established
                                   -- column-removal migration precedent (only ADD COLUMN
                                   -- guards elsewhere in this function) and an empty TEXT
                                   -- column costs nothing to leave. Not a bug that it's
                                   -- always blank.
                date        TEXT,
                started_at  TEXT,
                ended_at    TEXT,
                duration_s  INTEGER DEFAULT 0,
                agents      INTEGER DEFAULT 0,
                done        INTEGER DEFAULT 0,
                errors      INTEGER DEFAULT 0,
                tokens      INTEGER DEFAULT 0,
                cost        REAL    DEFAULT 0,
                task_done   INTEGER DEFAULT 0,
                task_total  INTEGER DEFAULT 0,
                file_count  INTEGER DEFAULT 0,
                snapshot    TEXT
            );
            CREATE TABLE IF NOT EXISTS agents (
                rowid_      INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT,
                agent_id    TEXT,
                name        TEXT,
                unit        TEXT,
                status      TEXT,
                started_at  TEXT,
                completed_at TEXT,
                duration_s  INTEGER DEFAULT 0,
                tokens      INTEGER DEFAULT 0,
                cost        REAL    DEFAULT 0,
                task_done   INTEGER DEFAULT 0,
                task_total  INTEGER DEFAULT 0,
                file_count  INTEGER DEFAULT 0,
                error_msg   TEXT,
                UNIQUE(session_id, agent_id)
            );
            CREATE TABLE IF NOT EXISTS file_changes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT,
                agent_id    TEXT,
                path        TEXT,
                type        TEXT,
                lines       INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_agents_session ON agents(session_id);
            CREATE INDEX IF NOT EXISTS idx_files_session  ON file_changes(session_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_date  ON sessions(date);
        """)
        # Schema migration: detected_via didn't exist when this table was first
        # created, and CREATE TABLE IF NOT EXISTS above doesn't add columns to
        # an already-existing table -- ALTER TABLE has no "IF NOT EXISTS" in
        # SQLite, so just swallow the "duplicate column" error on every run
        # after the first.
        try:
            c.execute("ALTER TABLE agents ADD COLUMN detected_via TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE agents ADD COLUMN concurrent_sessions INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE agents ADD COLUMN model TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE agents ADD COLUMN subagent_type TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE agents ADD COLUMN tool_use_count INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE agents ADD COLUMN parent_id TEXT")
        except sqlite3.OperationalError:
            pass
        # cc_version was tracked live (per-CLI-session, via the transcript
        # scanner backfilling it from the transcript's own metadata -- see
        # _check_new_cc_version) but never persisted, so Session Compare/
        # export could show it for the CURRENT session only; once a session
        # was saved to history or reset, its cc_version was gone for good.
        try:
            c.execute("ALTER TABLE sessions ADD COLUMN cc_version TEXT")
        except sqlite3.OperationalError:
            pass
        # Cumulative seconds this session spent with waiting_on_you=True,
        # for the WAITING ON YOU history trend -- see _accumulate_waiting_time.
        # Like cc_version above, this was only ever live state before now.
        try:
            c.execute("ALTER TABLE sessions ADD COLUMN waiting_on_you_s INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        # User-assigned session tags for organizing History beyond project
        # name -- comma-separated (never contains a literal comma per-tag,
        # see _sanitize_tags), edited directly on an already-saved History
        # row rather than going through the live status dict the way
        # session_note does, since tagging is inherently a look-back
        # activity.
        try:
            c.execute("ALTER TABLE sessions ADD COLUMN tags TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        c.commit()
        c.close()

_HISTORY_RETENTION_DAYS = 90

def _db_prune_old(days: int = _HISTORY_RETENTION_DAYS):
    """Drop sessions (and their agents/file_changes rows) older than `days`.
    Nothing enforced this before — history.db and agents/file_changes only ever
    grew, one row per session forever. Run once at startup rather than on every
    autosave; a handful of DELETEs is cheap even against a large table."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with _db_lock:
        try:
            c = _db_conn()
            old_ids = [r[0] for r in c.execute(
                "SELECT id FROM sessions WHERE date < ?", (cutoff,)).fetchall()]
            if old_ids:
                c.executemany("DELETE FROM agents WHERE session_id=?", [(i,) for i in old_ids])
                c.executemany("DELETE FROM file_changes WHERE session_id=?", [(i,) for i in old_ids])
                c.execute("DELETE FROM sessions WHERE date < ?", (cutoff,))
                c.commit()
            c.close()
        except Exception as e:
            _log_bg_error("_db_prune_old", e)

def _prune_old_logs(days: int = _HISTORY_RETENTION_DAYS):
    """Same retention window as _db_prune_old, applied to logs/session_*.log —
    those also only ever accumulated, one file per session forever."""
    cutoff_epoch = time.time() - days * 86400
    try:
        for p in glob.glob(os.path.join(LOGS_DIR, "session_*.log")):
            try:
                if os.path.getmtime(p) < cutoff_epoch:
                    os.remove(p)
            except Exception:
                pass
    except Exception as e:
        _log_bg_error("_prune_old_logs", e)

# started_at/completed_at are bare "HH:MM:SS" strings with no date component
# (see _clamp_agent_retention_hours' own docstring on the same ambiguity).
# The "midnight crossover" correction below (raw diff negative -> +86400)
# assumes a negative diff always means "wrapped past midnight within the
# same ~24h window" -- but two timestamps that are actually multiple
# calendar days apart with a merely SIMILAR time-of-day (e.g. an autosave
# picking up a stale started_at, or a long-idle session resumed days
# later) also produce a small negative raw diff, and the correction then
# reports a bogus ~86400s duration for what was really days apart, not a
# same-night handful of hours. Real agent runs are bounded by context
# windows and essentially never span this long -- the slowest_agents
# leaderboard (History -> Analytics) surfaced exactly this artifact live
# the first time anything actually looked at individual durations instead
# of only ever averaging them. Any post-correction duration past this
# ceiling is treated as unreliable (stored as 0/unknown) rather than a
# real measurement, rather than trying to guess the true elapsed time.
_MAX_PLAUSIBLE_DURATION_S = 12 * 3600  # 12h -- generous for even an unusually long agent run

def _correct_hms_diff(raw_diff: int) -> int:
    """Apply the midnight-crossover correction (negative raw HH:MM:SS diff
    -> +86400) then cap the result at _MAX_PLAUSIBLE_DURATION_S, treating
    anything past that ceiling as an unreliable multi-day-apart artifact
    rather than a real same-night measurement (see that constant's own
    comment for why). Shared by _db_save_session's session-level and
    per-agent duration computation, which otherwise each inlined this
    identical correction independently."""
    if raw_diff < 0:
        raw_diff += 86400
    if raw_diff > _MAX_PLAUSIBLE_DURATION_S:
        return 0
    return raw_diff

def _db_save_session(status: dict, sid: str = None):
    """Snapshot the current session into history DB. Called on reset or auto-save.
    If sid is provided, INSERT OR REPLACE updates the same record (idempotent auto-save)."""
    with _db_lock:
        try:
            agents = [a for a in status.get("agents", [])
                      if not str(a.get("id", "")).startswith("hook_")]
            if not agents:
                return
            if sid is None:
                sid = datetime.now().strftime("%Y%m%d_%H%M%S")
            project = status.get("project", "")
            started_at = status.get("started_at", "")
            ended_at = _now_ts()
            date = datetime.now().strftime("%Y-%m-%d")
            # cc_version has no top-level status[] mirror the way project/
            # started_at do (see the "update session-level fields" loop in
            # the /update handler) -- it's only ever set per-CLI-session, by
            # the transcript scanner backfilling status["sessions"][sid].
            # Same simplification as project/started_at already make for a
            # multi-session snapshot: picks whichever session has one set,
            # rather than trying to attribute it to one specific session.
            cc_version = next(
                (s.get("cc_version") for s in status.get("sessions", {}).values() if s.get("cc_version")),
                "")
            # Unlike cc_version above (picks whichever session has one),
            # waiting time sums across every session in this snapshot -- the
            # same "attribute it to the whole multi-session snapshot"
            # treatment tokens/cost already get. A still-open waiting period
            # (waiting_on_you currently True) has its elapsed-so-far folded
            # in too, so saving mid-wait doesn't silently drop that tail --
            # see _accumulate_waiting_time, which only commits to
            # waiting_on_you_accum_s on the *next* transition back to False.
            _save_now_epoch = time.time()
            waiting_on_you_s = 0
            for s in status.get("sessions", {}).values():
                waiting_on_you_s += s.get("waiting_on_you_accum_s", 0) or 0
                if s.get("waiting_on_you") and s.get("waiting_on_you_since"):
                    waiting_on_you_s += max(0, _save_now_epoch - s["waiting_on_you_since"])
            waiting_on_you_s = int(waiting_on_you_s)

            all_tasks    = [t for a in agents for t in (a.get("tasks") or [])]
            task_done    = sum(1 for t in all_tasks if t.get("done"))
            file_count   = len({f["path"] for a in agents for f in (a.get("files_changed") or [])})

            dur_s = 0
            if started_at:
                try:
                    h, m, s = (int(x) for x in started_at.split(":"))
                    h2, m2, s2 = (int(x) for x in ended_at.split(":"))
                    dur_s = _correct_hms_diff((h2*3600+m2*60+s2) - (h*3600+m*60+s))
                except Exception:
                    pass

            snapshot = json.dumps(status, ensure_ascii=False)

            c = _db_conn()

            # Auto-save re-runs every 5 min against the same sid (see docstring).
            # file_changes has no unique constraint, so a plain INSERT below would
            # re-append the same files on every cycle; clear this session's rows
            # first so each save reflects the current file list exactly once.
            c.execute("DELETE FROM file_changes WHERE session_id=?", (sid,))

            for a in agents:
                tok = a.get("tokens_used", 0) or 0
                # Prefer the real per-model cost (set by /update when the hook sent
                # granular usage) over the flat-rate guess, so History stays
                # consistent with whatever was actually shown live for this agent.
                a_cost = a["estimated_cost"] if a.get("estimated_cost") is not None else tok/1_000_000*9
                a_tasks = a.get("tasks") or []
                a_done  = sum(1 for t in a_tasks if t.get("done"))
                a_files = a.get("files_changed") or []
                a_dur   = 0
                if a.get("started_at") and a.get("completed_at"):
                    try:
                        h,m,s = (int(x) for x in a["started_at"].split(":"))
                        h2,m2,s2 = (int(x) for x in a["completed_at"].split(":"))
                        a_dur = _correct_hms_diff((h2*3600+m2*60+s2)-(h*3600+m*60+s))
                    except Exception:
                        pass
                # ON CONFLICT DO UPDATE (not INSERT OR IGNORE / OR REPLACE): auto-save
                # reruns against the same (session_id, agent_id) every 5 min while the
                # agent may still be running, so a stale first snapshot must be refreshed
                # in place — OR IGNORE froze it at first-seen status forever, and OR REPLACE
                # would churn rowid_ (and the session-detail ORDER BY rowid_) on every save.
                c.execute("""
                    INSERT INTO agents
                    (session_id, agent_id, name, unit, status, started_at, completed_at,
                     duration_s, tokens, cost, task_done, task_total, file_count, error_msg,
                     detected_via, concurrent_sessions, model, subagent_type, tool_use_count, parent_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(session_id, agent_id) DO UPDATE SET
                        name=excluded.name, unit=excluded.unit, status=excluded.status,
                        started_at=excluded.started_at, completed_at=excluded.completed_at,
                        duration_s=excluded.duration_s, tokens=excluded.tokens, cost=excluded.cost,
                        task_done=excluded.task_done, task_total=excluded.task_total,
                        file_count=excluded.file_count, error_msg=excluded.error_msg,
                        detected_via=COALESCE(agents.detected_via, excluded.detected_via),
                        concurrent_sessions=COALESCE(agents.concurrent_sessions, excluded.concurrent_sessions),
                        model=COALESCE(agents.model, excluded.model),
                        subagent_type=COALESCE(agents.subagent_type, excluded.subagent_type),
                        tool_use_count=COALESCE(agents.tool_use_count, excluded.tool_use_count),
                        parent_id=COALESCE(agents.parent_id, excluded.parent_id)
                """, (sid, a.get("id",""), a.get("name",""), a.get("unit",""),
                      a.get("status",""), a.get("started_at",""), a.get("completed_at",""),
                      a_dur, tok, a_cost, a_done, len(a_tasks), len(a_files),
                      a.get("error_message",""), a.get("detected_via"), a.get("concurrent_sessions"),
                      a.get("model"), a.get("subagent_type"), a.get("tool_use_count"), a.get("parent_id")))
                for f in a_files:
                    c.execute("""
                        INSERT INTO file_changes (session_id, agent_id, path, type, lines)
                        VALUES (?,?,?,?,?)
                    """, (sid, a.get("id",""), f.get("path",""), f.get("type","changed"),
                          f.get("lines", 0) or 0))

            # Derived from the agents table *after* the upsert loop above, not
            # from the in-memory `agents` list this function started with --
            # that list only holds what _agent_retention_worker hasn't pruned
            # yet (old done/error agents get cleared from live status every
            # ~15min so TIMELINE/SUMMARY/etc. don't grow forever), while each
            # row upserted into the agents table stays there for this
            # session's lifetime (only _db_prune_old's whole-session cascade
            # delete ever removes it). Using SUM(agents.cost) also means this
            # total inherits the real per-model cost each row already carries
            # (see a_cost above) instead of re-deriving one flat-rate guess
            # from the session's total tokens regardless of model mix.
            totals = c.execute("""
                SELECT COUNT(*) as agents,
                       SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) as done,
                       SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) as errors,
                       SUM(tokens) as tokens, SUM(cost) as cost
                FROM agents WHERE session_id=?
            """, (sid,)).fetchone()

            c.execute("""
                INSERT OR REPLACE INTO sessions
                (id, project, date, started_at, ended_at, duration_s,
                 agents, done, errors, tokens, cost, task_done, task_total, file_count, snapshot, cc_version,
                 waiting_on_you_s)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (sid, project, date, started_at, ended_at, dur_s,
                  totals["agents"], totals["done"] or 0, totals["errors"] or 0,
                  totals["tokens"] or 0, totals["cost"] or 0.0,
                  task_done, len(all_tasks), file_count, snapshot, cc_version, waiting_on_you_s))

            c.commit()
            c.close()
        except Exception as e:
            # never crash the server over DB -- but this used to mean a
            # failure here (from /history/save_current or /reset) was
            # completely invisible: the request handler gets a normal
            # response, the client sees success, and the session's history
            # just silently never gets persisted.
            _log_bg_error("_db_save_session", e)

def _tags_list(raw: str) -> list:
    """Turn the comma-separated tags column back into a list for the
    client -- the inverse of _sanitize_tags' ",".join(tags)."""
    return [t for t in (raw or "").split(",") if t]

def _sanitize_tags(tags) -> list:
    """Whitelist + normalize a user-supplied tag list: strip, drop blanks,
    dedupe case-insensitively (keeping first-seen casing), cap length per
    tag and total count -- same defensive shape _sanitize_project_budgets
    already uses for structured user input. No tag may contain a comma
    (the column's own separator) since that would silently merge two tags
    back into one on the next read."""
    if not isinstance(tags, list):
        return []
    out = []
    seen = set()
    for t in tags:
        t = str(t).replace(",", "").strip()[:30]
        if not t:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
        if len(out) >= 10:
            break
    return out

def _db_set_session_tags(sid: str, tags) -> bool:
    """Persist a saved session's tags straight to history.db -- unlike
    session_note (live status-dict state, only snapshotted into the DB at
    save time), tags are edited on an ALREADY-saved History row, so this
    writes the DB directly rather than going through _db_save_session.
    Returns False (no row updated) if sid doesn't exist, same "can't
    attach to a session AOC never saved" guard _set_session_note has."""
    tags_str = ",".join(_sanitize_tags(tags))
    with _db_lock:
        try:
            c = _db_conn()
            cur = c.execute("UPDATE sessions SET tags=? WHERE id=?", (tags_str, sid))
            c.commit()
            ok = cur.rowcount > 0
            c.close()
            return ok
        except Exception as e:
            _log_bg_error("_db_set_session_tags", e)
            return False

def _db_get_sessions(limit=100):
    """Return list of past sessions newest-first."""
    with _db_lock:
        try:
            c = _db_conn()
            rows = c.execute("""
                SELECT id, project, date, started_at, ended_at, duration_s,
                       agents, done, errors, tokens, cost, task_done, task_total, file_count, cc_version, tags
                FROM sessions ORDER BY rowid DESC LIMIT ?
            """, (limit,)).fetchall()
            c.close()
            out = [dict(r) for r in rows]
            for r in out:
                r["tags"] = _tags_list(r.get("tags"))
            return out
        except Exception:
            return []

def _db_search_sessions(query: str = "", date_from: str = "", date_to: str = "", limit: int = 200):
    """Search past sessions by project name / session id / tag and/or date
    range, uncapped by _db_get_sessions' fixed 100-row window -- the
    /history endpoint only ever returns the 100 most recent sessions, so
    anything older is otherwise unreachable from the client. Empty
    query/date_from/date_to mean "no filter on that dimension", matching
    _export_costs_csv's existing convention for its own date params.
    Uses idx_sessions_date for the date-range half."""
    with _db_lock:
        try:
            c = _db_conn()
            like = f"%{query}%"
            rows = c.execute("""
                SELECT id, project, date, started_at, ended_at, duration_s,
                       agents, done, errors, tokens, cost, task_done, task_total, file_count, cc_version, tags
                FROM sessions
                WHERE (? = '' OR project LIKE ? OR id LIKE ? OR tags LIKE ?)
                  AND (? = '' OR date >= ?)
                  AND (? = '' OR date <= ?)
                ORDER BY rowid DESC LIMIT ?
            """, (query, like, like, like, date_from, date_from, date_to, date_to, limit)).fetchall()
            c.close()
            out = [dict(r) for r in rows]
            for r in out:
                r["tags"] = _tags_list(r.get("tags"))
            return out
        except Exception:
            return []

def _db_get_errors(limit=100):
    """Return past agent errors newest-first, with session/project context —
    backs the ERRORS tab. A per-agent error box already exists in the live
    UI but disappears ~15s after the agent errors (scheduleRemove); this is
    the persistent view across sessions."""
    with _db_lock:
        try:
            c = _db_conn()
            rows = c.execute("""
                SELECT a.session_id, a.agent_id, a.name, a.unit, a.completed_at,
                       a.error_msg, a.model, a.detected_via, a.concurrent_sessions,
                       s.project, s.date
                FROM agents a JOIN sessions s ON s.id = a.session_id
                WHERE a.status = 'error' AND a.error_msg != ''
                ORDER BY a.rowid_ DESC LIMIT ?
            """, (limit,)).fetchall()
            c.close()
            return [dict(r) for r in rows]
        except Exception:
            return []

def _db_get_session_detail(sid: str):
    """Return snapshot JSON for a session."""
    with _db_lock:
        try:
            c = _db_conn()
            row = c.execute("SELECT snapshot, cc_version, tags FROM sessions WHERE id=?", (sid,)).fetchone()
            agents = c.execute("""
                SELECT agent_id, name, unit, status, started_at, completed_at,
                       duration_s, tokens, cost, task_done, task_total, file_count, error_msg, model,
                       subagent_type, tool_use_count, detected_via, concurrent_sessions, parent_id
                FROM agents WHERE session_id=? ORDER BY rowid_
            """, (sid,)).fetchall()
            files = c.execute("""
                SELECT agent_id, path, type, lines FROM file_changes WHERE session_id=?
            """, (sid,)).fetchall()
            c.close()
            return {
                "snapshot": json.loads(row["snapshot"]) if row and row["snapshot"] else None,
                "cc_version": (row["cc_version"] if row else None) or "",
                "tags": _tags_list(row["tags"] if row else None),
                "agents": [dict(a) for a in agents],
                "files":  [dict(f) for f in files],
            }
        except Exception:
            return {}

def _db_analytics():
    """Return aggregate stats for the analytics view."""
    import datetime as _dt
    today_str = _dt.date.today().isoformat()
    with _db_lock:
        try:
            c = _db_conn()
            # agents/done/errors/tokens/cost come from the agents table
            # directly (COUNT(*)/SUM(...) over every row ever upserted for
            # these sessions) rather than from sessions.agents/done/errors/
            # tokens/cost -- those columns get written by _db_save_session
            # from whatever's currently in the live in-memory agent list at
            # save time, which _agent_retention_worker periodically prunes
            # (old done/error agents cleared every ~15min so TIMELINE/
            # SUMMARY/etc. don't grow forever). A session that's since been
            # closed never gets re-saved to self-heal that, so trusting the
            # sessions columns here would keep understating lifetime totals
            # (and, for cost, ignoring each agent's own real per-model price
            # in favor of a flat-rate guess) even after _db_save_session's
            # own write path was fixed to stop introducing new drift.
            total_sessions = c.execute("""
                SELECT COUNT(*) as sessions, SUM(file_count) as files FROM sessions
            """).fetchone()
            total_agents = c.execute("""
                SELECT COUNT(*) as agents,
                       SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) as done,
                       SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) as errors,
                       SUM(tokens) as tokens, SUM(cost) as cost
                FROM agents
            """).fetchone()
            total = {
                "sessions": total_sessions["sessions"], "files": total_sessions["files"],
                "agents": total_agents["agents"], "done": total_agents["done"],
                "errors": total_agents["errors"], "tokens": total_agents["tokens"],
                "cost": total_agents["cost"],
            }
            today_sessions = c.execute("""
                SELECT COUNT(*) as sessions, AVG(duration_s) as avg_duration_s
                FROM sessions WHERE date=?
            """, (today_str,)).fetchone()
            today_agents = c.execute("""
                SELECT COUNT(*) as agents,
                       SUM(CASE WHEN a.status='done' THEN 1 ELSE 0 END) as done,
                       SUM(CASE WHEN a.status='error' THEN 1 ELSE 0 END) as errors,
                       SUM(a.tokens) as tokens, SUM(a.cost) as cost
                FROM agents a JOIN sessions s ON a.session_id = s.id
                WHERE s.date=?
            """, (today_str,)).fetchone()
            today = {
                "sessions": today_sessions["sessions"], "avg_duration_s": today_sessions["avg_duration_s"],
                "agents": today_agents["agents"], "done": today_agents["done"],
                "errors": today_agents["errors"], "tokens": today_agents["tokens"],
                "cost": today_agents["cost"],
            }
            # Same agents-table-is-authoritative fix as total/today above,
            # applied to the day/project breakdowns -- tokens/cost/agents/
            # done/errors are joined in from the agents table (grouped by
            # the owning session's date/project) rather than trusted from
            # sessions.*, which stays the source only for fields the agents
            # table has no equivalent for (session count, task_done/
            # task_total, waiting_on_you_s, avg_duration_s). Two queries
            # merged in Python rather than one JOIN...GROUP BY, so a date/
            # project with sessions but zero agents rows still shows up
            # (an INNER JOIN would silently drop it, same bug this is fixing
            # just relocated) with agents/tokens/cost defaulting to 0.
            by_day_sessions = c.execute("""
                SELECT date, COUNT(*) as sessions, AVG(duration_s) as avg_duration_s,
                       SUM(task_done) as task_done, SUM(task_total) as task_total,
                       SUM(waiting_on_you_s) as waiting_on_you_s
                FROM sessions GROUP BY date ORDER BY date DESC LIMIT 30
            """).fetchall()
            by_day_agents = {r["date"]: r for r in c.execute("""
                SELECT s.date as date, SUM(a.tokens) as tokens, SUM(a.cost) as cost,
                       COUNT(*) as agents,
                       SUM(CASE WHEN a.status='error' THEN 1 ELSE 0 END) as errors
                FROM agents a JOIN sessions s ON a.session_id = s.id
                GROUP BY s.date
            """).fetchall()}
            by_day = []
            for r in by_day_sessions:
                ag = by_day_agents.get(r["date"])
                by_day.append({
                    "date": r["date"], "sessions": r["sessions"],
                    "tokens": ag["tokens"] if ag else 0, "cost": ag["cost"] if ag else 0.0,
                    "agents": ag["agents"] if ag else 0, "errors": ag["errors"] if ag else 0,
                    "avg_duration_s": r["avg_duration_s"],
                    "task_done": r["task_done"], "task_total": r["task_total"],
                    "waiting_on_you_s": r["waiting_on_you_s"],
                })
            by_project_sessions = c.execute("""
                SELECT project, COUNT(*) as sessions,
                       SUM(task_done) as task_done, SUM(task_total) as task_total,
                       SUM(waiting_on_you_s) as waiting_on_you_s
                FROM sessions WHERE project != '' GROUP BY project
            """).fetchall()
            by_project_agents = {r["project"]: r for r in c.execute("""
                SELECT s.project as project, SUM(a.tokens) as tokens, SUM(a.cost) as cost,
                       COUNT(*) as agents,
                       SUM(CASE WHEN a.status='done' THEN 1 ELSE 0 END) as done,
                       SUM(CASE WHEN a.status='error' THEN 1 ELSE 0 END) as errors
                FROM agents a JOIN sessions s ON a.session_id = s.id
                WHERE s.project != ''
                GROUP BY s.project
            """).fetchall()}
            by_project_all = []
            for r in by_project_sessions:
                ag = by_project_agents.get(r["project"])
                by_project_all.append({
                    "project": r["project"], "sessions": r["sessions"],
                    "tokens": ag["tokens"] if ag else 0, "cost": ag["cost"] if ag else 0.0,
                    "agents": ag["agents"] if ag else 0, "done": ag["done"] if ag else 0,
                    "errors": ag["errors"] if ag else 0,
                    "task_done": r["task_done"], "task_total": r["task_total"],
                    "waiting_on_you_s": r["waiting_on_you_s"],
                })
            by_project = sorted(by_project_all, key=lambda x: -(x["cost"] or 0))[:20]
            # Total agents whose *start* was only ever caught by the transcript
            # fallback scanner, never by Claude Code's own PreToolUse hook --
            # quantifies how often that hook dispatch actually drops an event
            # (previously invisible: the fallback silently fixed it with no
            # persistent count anywhere).
            hook_miss_row = c.execute(
                "SELECT COUNT(*) as n FROM agents WHERE detected_via='transcript'"
            ).fetchone()
            hook_misses = hook_miss_row["n"] if hook_miss_row else 0
            # Tests the hypothesis that the hook dispatch drops PreToolUse more
            # often when multiple CLI sessions are concurrently active (their
            # hook events competing/racing) rather than being purely random.
            concurrency_rows = c.execute("""
                SELECT detected_via='transcript' as is_hook_miss,
                       AVG(concurrent_sessions) as avg_concurrent, COUNT(*) as n
                FROM agents WHERE concurrent_sessions IS NOT NULL
                GROUP BY is_hook_miss
            """).fetchall()
            hook_miss_concurrency = {
                ("hook_miss" if r["is_hook_miss"] else "normal"): {
                    "avg_concurrent_sessions": round(r["avg_concurrent"], 2) if r["avg_concurrent"] is not None else None,
                    "n": r["n"],
                } for r in concurrency_rows
            }
            by_day_project = c.execute("""
                SELECT s.date as date, s.project as project,
                       SUM(a.tokens) as tokens, SUM(a.cost) as cost
                FROM agents a JOIN sessions s ON a.session_id = s.id
                WHERE s.project != ''
                GROUP BY s.date, s.project ORDER BY s.date ASC
            """).fetchall()
            # Cost/token breakdown by model (Sonnet vs Opus vs Haiku, etc.) --
            # only ever populated for agents saved since the `model` column
            # was added, so old rows are simply absent rather than showing a
            # blank/garbage bucket.
            # avg_duration_s excludes implausible/zero durations the same way
            # slowest_agents' WHERE clause does (see that query's own comment) --
            # this is a per-model average, so a single ~86400s midnight-crossover
            # artifact would otherwise skew it far worse than one bad row in a
            # 20-row leaderboard.
            by_model = c.execute("""
                SELECT model, COUNT(*) as agents, SUM(tokens) as tokens, SUM(cost) as cost,
                       SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) as done,
                       SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) as errors,
                       SUM(tool_use_count) as tool_use_count,
                       AVG(CASE WHEN duration_s > 0 AND duration_s <= ? THEN duration_s END) as avg_duration_s
                FROM agents WHERE model IS NOT NULL AND model != ''
                GROUP BY model ORDER BY SUM(cost) DESC
            """, (_MAX_PLAUSIBLE_DURATION_S,)).fetchall()
            # Day-level counterpart to by_model, same idea as by_day_project --
            # agents has no date of its own (only time-of-day started_at/
            # completed_at), so this needs the session it belongs to for the
            # date, unlike by_model/by_project which both aggregate a single
            # table directly.
            by_day_model = c.execute("""
                SELECT s.date as date, a.model as model, SUM(a.tokens) as tokens, SUM(a.cost) as cost
                FROM agents a JOIN sessions s ON a.session_id = s.id
                WHERE a.model IS NOT NULL AND a.model != ''
                GROUP BY s.date, a.model ORDER BY s.date ASC
            """).fetchall()
            # Cost/token breakdown by subagent_type (Explore vs Plan vs
            # general-purpose, etc.) -- same shape as by_model, just grouped
            # by the other identifying field an agent already carries.
            # subagent_type was already shown per-agent (card badge, Compare,
            # Markdown export) but never aggregated across history before.
            by_subagent_type = c.execute("""
                SELECT subagent_type, COUNT(*) as agents, SUM(tokens) as tokens, SUM(cost) as cost,
                       SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) as done,
                       SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) as errors,
                       SUM(tool_use_count) as tool_use_count
                FROM agents WHERE subagent_type IS NOT NULL AND subagent_type != ''
                GROUP BY subagent_type ORDER BY SUM(cost) DESC
            """).fetchall()
            # Day-level counterpart, same agents-JOIN-sessions shape
            # by_day_model already established (agents carry no date of
            # their own).
            by_day_subagent_type = c.execute("""
                SELECT s.date as date, a.subagent_type as subagent_type, SUM(a.tokens) as tokens, SUM(a.cost) as cost
                FROM agents a JOIN sessions s ON a.session_id = s.id
                WHERE a.subagent_type IS NOT NULL AND a.subagent_type != ''
                GROUP BY s.date, a.subagent_type ORDER BY s.date ASC
            """).fetchall()
            # Day-level counterpart to hook_misses/hook_miss_concurrency above --
            # those only ever surface an all-time total, with no way to see
            # whether the hook's reliability is trending better or worse over
            # time. Same sessions join as by_day_model, for the same reason
            # (agents carries no date of its own).
            by_day_hook_reliability = c.execute("""
                SELECT s.date as date,
                       SUM(CASE WHEN a.detected_via='transcript' THEN 1 ELSE 0 END) as misses,
                       COUNT(*) as total
                FROM agents a JOIN sessions s ON a.session_id = s.id
                GROUP BY s.date ORDER BY s.date ASC
            """).fetchall()
            # Most-frequently-changed files across all history -- file_changes
            # has every file any agent ever touched (path/type/lines, one row
            # per change) but was only ever queried per-session (the FILES
            # tab) before this, never aggregated. changes counts every touch
            # (a file edited by 3 different agents in 3 different sessions
            # counts 3), sessions is the distinct-session version of the same
            # thing -- the gap between the two says whether a file's edits
            # cluster in one session or are spread out over time.
            file_hotspots = c.execute("""
                SELECT path, COUNT(*) as changes, COUNT(DISTINCT session_id) as sessions,
                       SUM(lines) as total_lines
                FROM file_changes WHERE path != ''
                GROUP BY path ORDER BY COUNT(*) DESC LIMIT 20
            """).fetchall()
            # Day-level counterpart, same agents-JOIN-sessions shape by_day_model
            # already established -- scoped to just the top-20 paths above
            # (not every file ever touched) so this stays a small, bounded
            # query instead of a date x every-distinct-path cross product.
            top_paths = [r["path"] for r in file_hotspots]
            if top_paths:
                placeholders = ",".join("?" * len(top_paths))
                by_day_file_hotspots = c.execute(f"""
                    SELECT s.date as date, fc.path as path, COUNT(*) as changes
                    FROM file_changes fc JOIN sessions s ON fc.session_id = s.id
                    WHERE fc.path IN ({placeholders})
                    GROUP BY s.date, fc.path ORDER BY s.date ASC
                """, top_paths).fetchall()
            else:
                by_day_file_hotspots = []
            # File-type breakdown -- same file_changes source as file_hotspots
            # above, grouped by extension instead of path (which KIND of work
            # generates the most changes: .py vs .js vs .md, etc.). SQLite has
            # no clean "text after the last dot" builtin without a recursive
            # CTE, so this aggregates in Python instead of SQL -- file_changes
            # is a personal dev tool's history, not a scale where that matters.
            _ext_agg = {}
            for r in c.execute("SELECT path, lines, session_id FROM file_changes WHERE path != ''").fetchall():
                name = r["path"].replace("\\", "/").rsplit("/", 1)[-1]
                ext = name.rsplit(".", 1)[-1].lower() if "." in name else "(no extension)"
                bucket = _ext_agg.setdefault(ext, {"ext": ext, "changes": 0, "total_lines": 0, "_sessions": set()})
                bucket["changes"] += 1
                bucket["total_lines"] += r["lines"] or 0
                bucket["_sessions"].add(r["session_id"])
            by_file_type = sorted(
                ({"ext": b["ext"], "changes": b["changes"], "sessions": len(b["_sessions"]), "total_lines": b["total_lines"]}
                 for b in _ext_agg.values()),
                key=lambda x: -x["changes"])[:20]
            # Tag cloud -- every distinct tag used across history, ranked by
            # how many sessions carry it. Same reason as by_file_type above:
            # tags live in one comma-separated column (_sanitize_tags), so
            # this aggregates in Python rather than needing SQL to explode a
            # delimited string. Without this, the only way to discover what
            # tags you'd already used was to remember them or browse
            # individual sessions one at a time.
            _tag_counts = {}
            for r in c.execute("SELECT tags FROM sessions WHERE tags != ''").fetchall():
                for t in _tags_list(r["tags"]):
                    _tag_counts[t] = _tag_counts.get(t, 0) + 1
            tag_cloud = sorted(
                ({"tag": t, "count": n} for t, n in _tag_counts.items()),
                key=lambda x: -x["count"])[:30]
            # Cost by tag -- same Python aggregation as tag_cloud above (one
            # comma-separated column), but attributing a session's full
            # cost/tokens/done/errors to EVERY tag it carries rather than
            # just counting participation. A session with 2 tags contributes
            # its full total to both -- the same double-counting any tag
            # cloud has, not a strict partition of cost across tags.
            _tag_cost_agg = {}
            for r in c.execute("SELECT tags, cost, tokens, done, errors, agents FROM sessions WHERE tags != ''").fetchall():
                for t in _tags_list(r["tags"]):
                    bucket = _tag_cost_agg.setdefault(t, {"tag": t, "sessions": 0, "cost": 0.0, "tokens": 0, "done": 0, "errors": 0, "agents": 0})
                    bucket["sessions"] += 1
                    bucket["cost"] += r["cost"] or 0
                    bucket["tokens"] += r["tokens"] or 0
                    bucket["done"] += r["done"] or 0
                    bucket["errors"] += r["errors"] or 0
                    bucket["agents"] += r["agents"] or 0
            by_tag = sorted(_tag_cost_agg.values(), key=lambda x: -x["cost"])[:20]
            # Retry detection -- an agent named the same as an earlier,
            # errored one, run again in that SAME session. Exact-name match
            # only (no fuzzy matching): Claude Code doesn't rename a retried
            # Task call, so this is exactly what "you re-ran the same thing
            # after it failed" looks like, the same reasoning common_errors'
            # exact-text grouping already uses. Grouped and walked in Python
            # (ordered by rowid_ within each session+name group, i.e.
            # insertion order) since this needs an ordered same-group
            # comparison SQL doesn't do cheaply.
            _retry_groups = {}
            for r in c.execute("""
                SELECT session_id, name, status, error_msg FROM agents
                WHERE name IS NOT NULL AND name != ''
                ORDER BY session_id, name, rowid_
            """).fetchall():
                _retry_groups.setdefault((r["session_id"], r["name"]), []).append((r["status"], r["error_msg"]))
            _retry_agg = {}
            for (sid, name), attempts in _retry_groups.items():
                if len(attempts) < 2:
                    continue
                for i in range(len(attempts) - 1):
                    status_i, err_i = attempts[i]
                    if status_i == "error":
                        bucket = _retry_agg.setdefault(name, {"name": name, "retries": 0, "_sessions": set(), "last_error": ""})
                        bucket["retries"] += 1
                        bucket["_sessions"].add(sid)
                        # "last" here means last processed, not strictly
                        # chronological (rows are ordered per session+name
                        # group, not globally) -- good enough to answer "why
                        # does this keep failing" without a second query.
                        if err_i:
                            bucket["last_error"] = err_i
            retry_count = sum(b["retries"] for b in _retry_agg.values())
            retry_patterns = sorted(
                ({"name": b["name"], "retries": b["retries"], "sessions": len(b["_sessions"]), "last_error": b["last_error"]}
                 for b in _retry_agg.values()),
                key=lambda x: -x["retries"])[:20]
            # Longest-running individual agents -- duration_s has always been
            # stored per agent but never surfaced as a ranked list, only ever
            # averaged into today's session-level avg_duration_s. A leaderboard
            # rather than a GROUP BY aggregate (unlike everything else in this
            # function): each row is one specific agent run, not a summary
            # bucket, so it can be joined against subagent_type/model to spot
            # which combination tends to run longest.
            # duration_s > _MAX_PLAUSIBLE_DURATION_S excluded defensively, not
            # just relying on the write-side cap in _db_save_session -- rows
            # already saved before that cap existed can still carry a bogus
            # ~86400s "midnight crossover" artifact (see that function's own
            # comment on why), and this leaderboard is exactly the place that
            # would otherwise surface them as if they were real.
            slowest_agents = c.execute("""
                SELECT a.agent_id as agent_id, a.name as name, a.subagent_type as subagent_type,
                       a.model as model, a.session_id as session_id, s.project as project,
                       a.duration_s as duration_s, a.status as status, a.cost as cost
                FROM agents a JOIN sessions s ON a.session_id = s.id
                WHERE a.duration_s > 0 AND a.duration_s <= ?
                ORDER BY a.duration_s DESC LIMIT 20
            """, (_MAX_PLAUSIBLE_DURATION_S,)).fetchall()
            # Most frequently recurring error messages -- error_msg has
            # always been persisted per errored agent and shown in the
            # ERRORS panel, but only ever as a flat chronological list,
            # never grouped to answer "what keeps breaking". Exact-text
            # grouping (not fuzzy matching, which SQL can't do cheaply) --
            # a genuinely recurring failure (the same exception, the same
            # timeout) produces identical text every time it fires, so this
            # still catches the case that matters even though a message
            # containing something unique per-occurrence (a file path, a
            # timestamp) won't cluster. The two correlated subqueries pull
            # the most recent occurrence's session/project for click-through,
            # since GROUP BY alone can't give "the rest of that one row".
            common_errors = c.execute("""
                SELECT a1.error_msg as error_msg, COUNT(*) as occurrences,
                       (SELECT a2.session_id FROM agents a2
                        WHERE a2.error_msg = a1.error_msg AND a2.status = 'error'
                        ORDER BY a2.rowid_ DESC LIMIT 1) as last_session_id,
                       (SELECT s2.project FROM agents a2 JOIN sessions s2 ON a2.session_id = s2.id
                        WHERE a2.error_msg = a1.error_msg AND a2.status = 'error'
                        ORDER BY a2.rowid_ DESC LIMIT 1) as project
                FROM agents a1
                WHERE a1.status = 'error' AND a1.error_msg IS NOT NULL AND a1.error_msg != ''
                GROUP BY a1.error_msg ORDER BY COUNT(*) DESC LIMIT 15
            """).fetchall()
            c.close()
            return {
                "total": dict(total) if total else {},
                "today": dict(today) if today else {},
                "by_day": [dict(r) for r in by_day],
                "by_project": [dict(r) for r in by_project],
                "by_day_project": [dict(r) for r in by_day_project],
                "by_model": [dict(r) for r in by_model],
                "by_day_model": [dict(r) for r in by_day_model],
                "by_subagent_type": [dict(r) for r in by_subagent_type],
                "by_day_subagent_type": [dict(r) for r in by_day_subagent_type],
                "by_day_hook_reliability": [dict(r) for r in by_day_hook_reliability],
                "file_hotspots": [dict(r) for r in file_hotspots],
                "by_day_file_hotspots": [dict(r) for r in by_day_file_hotspots],
                "by_file_type": by_file_type,
                "tag_cloud": tag_cloud,
                "by_tag": by_tag,
                "retry_count": retry_count,
                "retry_patterns": retry_patterns,
                "slowest_agents": [dict(r) for r in slowest_agents],
                "common_errors": [dict(r) for r in common_errors],
                "hook_misses": hook_misses,
                "hook_miss_concurrency": hook_miss_concurrency,
            }
        except Exception:
            return {"total": {}, "today": {}, "by_day": [], "by_project": [], "by_day_project": [],
                    "by_model": [], "by_day_model": [], "by_subagent_type": [], "by_day_subagent_type": [],
                    "by_day_hook_reliability": [], "file_hotspots": [], "by_day_file_hotspots": [], "by_file_type": [],
                    "tag_cloud": [], "by_tag": [],
                    "retry_count": 0, "retry_patterns": [],
                    "slowest_agents": [],
                    "common_errors": [],
                    "hook_misses": 0, "hook_miss_concurrency": {}}

def _db_by_day_excluding_projects(excluded_projects, limit: int = 30) -> list:
    """Same shape as _db_analytics()'s by_day, but excludes any session
    whose project is in `excluded_projects` -- used by the weekly digest
    so a muted (noise-suppressed) project's cost/sessions/errors don't
    silently inflate a summary that's itself a notification. Muting a
    project already suppresses its individual toast/webhook events; the
    digest ignoring that was the one place mute didn't actually mean
    quiet. Empty `excluded_projects` behaves identically to the
    unfiltered by_day query (no project ever equals a value not in an
    empty exclusion, so every row passes)."""
    excluded_projects = [p for p in (excluded_projects or []) if p]
    with _db_lock:
        try:
            c = _db_conn()
            if excluded_projects:
                placeholders = ",".join("?" * len(excluded_projects))
                where = f"project NOT IN ({placeholders})"
                params = list(excluded_projects)
            else:
                where = "1=1"
                params = []
            rows = c.execute(f"""
                SELECT date, COUNT(*) as sessions, SUM(tokens) as tokens, SUM(cost) as cost,
                       SUM(errors) as errors
                FROM sessions WHERE {where}
                GROUP BY date ORDER BY date DESC LIMIT ?
            """, params + [limit]).fetchall()
            c.close()
            return [dict(r) for r in rows]
        except Exception:
            return []

def _prom_escape(s) -> str:
    """Escape a Prometheus label value per the text exposition format spec
    (backslash, double-quote, newline)."""
    return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

def _render_prometheus_metrics(data: dict, analytics: dict) -> str:
    """Render a subset of AOC's live status + DB analytics in Prometheus
    text exposition format -- same underlying numbers /status already
    returns, just re-rendered for a scraper instead of a browser, so
    AOC can plug into any existing monitoring/alerting stack rather than
    staying siloed."""
    lines = []
    lines.append("# HELP aoc_sessions_active Number of currently active CLI sessions")
    lines.append("# TYPE aoc_sessions_active gauge")
    lines.append(f'aoc_sessions_active {data.get("sessions_count", 0)}')

    status_counts = {}
    for a in data.get("agents", []):
        st = a.get("status", "unknown")
        status_counts[st] = status_counts.get(st, 0) + 1
    lines.append("# HELP aoc_agents_total Number of tracked agents by status")
    lines.append("# TYPE aoc_agents_total gauge")
    for st in sorted(status_counts):
        lines.append(f'aoc_agents_total{{status="{_prom_escape(st)}"}} {status_counts[st]}')

    lines.append("# HELP aoc_session_cost_dollars Estimated cost per session")
    lines.append("# TYPE aoc_session_cost_dollars gauge")
    for sess in data.get("sessions_list", []):
        sid = _prom_escape(sess.get("id", ""))
        project = _prom_escape(sess.get("project", "") or "none")
        cost = sess.get("estimated_cost", 0.0) or 0.0
        lines.append(f'aoc_session_cost_dollars{{session_id="{sid}",project="{project}"}} {cost}')

    lines.append("# HELP aoc_hook_misses_total Agent events detected via transcript fallback")
    lines.append("# TYPE aoc_hook_misses_total counter")
    lines.append(f'aoc_hook_misses_total {analytics.get("hook_misses", 0)}')

    return "\n".join(lines) + "\n"

def _export_costs_csv(date_from: str = "", date_to: str = "") -> str:
    """CSV cost report grouped by project, for a date range (inclusive on
    both ends; empty means unbounded on that side). Cost tracking already
    has global/per-project budgets and a forecast, but no way to get the
    numbers out for expensing or handing to someone else -- this is that
    escape hatch. Backs GET /export_costs.csv."""
    import csv, io
    with _db_lock:
        try:
            c = _db_conn()
            where, params = [], []
            if date_from:
                where.append("date >= ?")
                params.append(date_from)
            if date_to:
                where.append("date <= ?")
                params.append(date_to)
            clause = ("WHERE " + " AND ".join(where)) if where else ""
            rows = c.execute(f"""
                SELECT COALESCE(NULLIF(project,''),'(none)') as project,
                       COUNT(*) as sessions, SUM(tokens) as tokens, SUM(cost) as cost
                FROM sessions {clause}
                GROUP BY project ORDER BY SUM(cost) DESC
            """, params).fetchall()
            c.close()
        except Exception:
            rows = []
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["project", "sessions", "tokens", "cost_usd"])
    total_sessions = total_tokens = 0
    total_cost = 0.0
    for r in rows:
        cost = r["cost"] or 0
        tokens = r["tokens"] or 0
        w.writerow([r["project"], r["sessions"], tokens, round(cost, 4)])
        total_sessions += r["sessions"] or 0
        total_tokens += tokens
        total_cost += cost
    w.writerow(["TOTAL", total_sessions, total_tokens, round(total_cost, 4)])
    return buf.getvalue()

def _export_costs_by_model_csv(date_from: str = "", date_to: str = "") -> str:
    """CSV cost report grouped by model, mirroring _export_costs_csv's
    project-based report exactly -- COST BY PROJECT has had a CSV escape
    hatch since the Analytics tab's THIS MONTH/ALL TIME buttons, COST BY
    MODEL never did. Sourced from agents (joined against sessions for the
    date, since agents carry no date of their own -- the same
    agents-JOIN-sessions shape by_day_model already established for the
    trend chart). Backs GET /export_costs_by_model.csv."""
    import csv, io
    with _db_lock:
        try:
            c = _db_conn()
            where, params = ["a.model IS NOT NULL AND a.model != ''"], []
            if date_from:
                where.append("s.date >= ?")
                params.append(date_from)
            if date_to:
                where.append("s.date <= ?")
                params.append(date_to)
            clause = "WHERE " + " AND ".join(where)
            rows = c.execute(f"""
                SELECT a.model as model, COUNT(*) as agents, SUM(a.tokens) as tokens, SUM(a.cost) as cost
                FROM agents a JOIN sessions s ON a.session_id = s.id
                {clause}
                GROUP BY a.model ORDER BY SUM(a.cost) DESC
            """, params).fetchall()
            c.close()
        except Exception:
            rows = []
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["model", "agents", "tokens", "cost_usd"])
    total_agents = total_tokens = 0
    total_cost = 0.0
    for r in rows:
        cost = r["cost"] or 0
        tokens = r["tokens"] or 0
        w.writerow([r["model"], r["agents"], tokens, round(cost, 4)])
        total_agents += r["agents"] or 0
        total_tokens += tokens
        total_cost += cost
    w.writerow(["TOTAL", total_agents, total_tokens, round(total_cost, 4)])
    return buf.getvalue()

def _export_costs_by_subagent_type_csv(date_from: str = "", date_to: str = "") -> str:
    """CSV cost report grouped by subagent_type, mirroring
    _export_costs_by_model_csv exactly -- same agents-JOIN-sessions shape,
    just the other identifying column. Backs GET
    /export_costs_by_subagent_type.csv."""
    import csv, io
    with _db_lock:
        try:
            c = _db_conn()
            where, params = ["a.subagent_type IS NOT NULL AND a.subagent_type != ''"], []
            if date_from:
                where.append("s.date >= ?")
                params.append(date_from)
            if date_to:
                where.append("s.date <= ?")
                params.append(date_to)
            clause = "WHERE " + " AND ".join(where)
            rows = c.execute(f"""
                SELECT a.subagent_type as subagent_type, COUNT(*) as agents, SUM(a.tokens) as tokens, SUM(a.cost) as cost
                FROM agents a JOIN sessions s ON a.session_id = s.id
                {clause}
                GROUP BY a.subagent_type ORDER BY SUM(a.cost) DESC
            """, params).fetchall()
            c.close()
        except Exception:
            rows = []
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["subagent_type", "agents", "tokens", "cost_usd"])
    total_agents = total_tokens = 0
    total_cost = 0.0
    for r in rows:
        cost = r["cost"] or 0
        tokens = r["tokens"] or 0
        w.writerow([r["subagent_type"], r["agents"], tokens, round(cost, 4)])
        total_agents += r["agents"] or 0
        total_tokens += tokens
        total_cost += cost
    w.writerow(["TOTAL", total_agents, total_tokens, round(total_cost, 4)])
    return buf.getvalue()


_init_db()
_db_prune_old()
_prune_old_logs()

_BACKUP_RETENTION_DAYS = 14

def _backup_history_db():
    """history.db moved off OneDrive into %LOCALAPPDATA% (2026-07-16, to fix
    the OneDrive-lock freeze bug) and lost the automatic cloud backup it used
    to get for free as a side effect of living there. Copy it back into
    AOC_DIR (still OneDrive-synced) on a schedule instead -- a periodic,
    out-of-band copy doesn't reintroduce the original bug, which was
    specifically about every single request synchronously touching a
    OneDrive-locked file on the hot path; this runs on its own timer, never
    blocking a request. Uses sqlite3's own .backup() API rather than a raw
    file copy, since a raw copy mid-write could produce a torn/inconsistent
    snapshot -- .backup() is the documented safe way to copy a live DB."""
    try:
        backup_dir = os.path.join(AOC_DIR, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, f"history_{datetime.now().strftime('%Y-%m-%d')}.db")
        with _db_lock:
            src = _db_conn()
            dst = sqlite3.connect(backup_path)
            src.backup(dst)
            dst.close()
            src.close()
        # A completed .backup() call only means the copy operation didn't
        # raise -- it doesn't guarantee the result is actually restorable.
        # Verify with SQLite's own integrity_check rather than trusting a
        # silently-corrupt backup nobody would notice until they needed it.
        try:
            check_conn = sqlite3.connect(backup_path)
            result = check_conn.execute("PRAGMA integrity_check").fetchall()
            check_conn.close()
            if result != [("ok",)]:
                corrupt_path = backup_path[:-3] + "_CORRUPT.db"
                try:
                    os.replace(backup_path, corrupt_path)
                except Exception:
                    pass
                # Deliberate exception to "toasts are for agent status only"
                # (the only other caller is _headless_notify_worker) -- a
                # trusted-but-corrupt backup is worse than no backup, and
                # this is exactly the rare/important class of event native
                # toasts exist for. If _show_native_toast isn't defined yet
                # (module still loading when this runs at startup, extremely
                # unlikely given how rare an actual corruption is), the outer
                # except below absorbs it same as any other failure here.
                _show_native_toast("AOC Backup", "History DB backup failed integrity check -- see backups/ folder")
        except Exception:
            pass
        # globals().get(...) rather than a direct _notify_settings reference:
        # _backup_worker's thread (started right below, at module load) can
        # reach this line before _notify_settings is even defined further
        # down the file -- unlike _webhook_notify_worker/_digest_worker,
        # whose own thread-starts both come after that point. Falls back to
        # the plain constant either way, so this is never a hard dependency.
        # Inlined rather than calling _clamp_backup_retention_days: that
        # function is defined further down the file (after
        # _load_webhook_settings()), and this whole function can run from
        # _backup_worker's thread before that point at module load --
        # same reasoning as the globals().get() above, just one level
        # further to avoid a stray NameError silently skipping the entire
        # backup (this whole function body is one big try/except).
        try:
            retention_days = int(globals().get("_notify_settings", {}).get("backup_retention_days", _BACKUP_RETENTION_DAYS))
        except (TypeError, ValueError):
            retention_days = _BACKUP_RETENTION_DAYS
        retention_days = max(1, min(365, retention_days))
        cutoff = time.time() - retention_days * 86400
        for p in glob.glob(os.path.join(backup_dir, "history_*.db")):
            if p.endswith("_CORRUPT.db"):
                continue  # keep corrupt backups for inspection, don't auto-delete the evidence
            try:
                if os.path.getmtime(p) < cutoff:
                    os.remove(p)
            except Exception:
                pass
    except Exception as e:
        _log_bg_error("_backup_history_db", e)

def _backup_worker():
    while True:
        _backup_history_db()
        time.sleep(3600)  # hourly; dated filename means only today's snapshot actually changes

threading.Thread(target=_backup_worker, daemon=True).start()


def _list_backups() -> list:
    """Full backup listing for the DIAG view's restore picker -- distinct
    from _get_diag_info's single 'latest' summary below, this is what a
    user actually picks a restore target from."""
    backup_dir = os.path.join(AOC_DIR, "backups")
    out = []
    try:
        for p in sorted(glob.glob(os.path.join(backup_dir, "history_*.db")), reverse=True):
            name = os.path.basename(p)
            out.append({
                "filename": name,
                "size_bytes": os.path.getsize(p),
                "mtime": os.path.getmtime(p),
                "corrupt": name.endswith("_CORRUPT.db"),
            })
    except Exception:
        pass
    return out


def _restore_backup(filename: str) -> dict:
    """Restore history.db from a chosen backups/ snapshot. This is itself a
    destructive action -- whatever's live right now is gone the moment it's
    overwritten -- so it takes its own pre-restore safety copy first, the
    same way _backup_history_db already guarantees recoverability for every
    other point in time. Never previously built or tested; backups existed
    but restoring from one had no code path at all."""
    import shutil
    backup_dir = os.path.join(AOC_DIR, "backups")
    # filename is a POST body value, not a trusted internal path -- reject
    # anything that isn't a bare filename already sitting in backups/.
    if os.path.basename(filename) != filename or not filename.startswith("history_"):
        return {"ok": False, "error": "invalid filename"}
    if filename.endswith("_CORRUPT.db"):
        return {"ok": False, "error": "refusing to restore a backup that failed its own integrity check"}
    src_path = os.path.join(backup_dir, filename)
    if not os.path.isfile(src_path):
        return {"ok": False, "error": "backup file not found"}

    with _db_lock:
        try:
            pre_restore_name = f"history_PRE_RESTORE_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.db"
            pre_restore_path = os.path.join(backup_dir, pre_restore_name)
            if os.path.exists(DB_FILE):
                pre_conn = sqlite3.connect(DB_FILE)
                pre_dst = sqlite3.connect(pre_restore_path)
                pre_conn.backup(pre_dst)
                pre_dst.close()
                pre_conn.close()

            shutil.copy2(src_path, DB_FILE)

            check_conn = sqlite3.connect(DB_FILE)
            result = check_conn.execute("PRAGMA integrity_check").fetchall()
            check_conn.close()
            if result != [("ok",)]:
                return {"ok": False, "error": "restored file failed integrity check",
                         "pre_restore_backup": pre_restore_name}
            return {"ok": True, "pre_restore_backup": pre_restore_name}
        except Exception as e:
            return {"ok": False, "error": str(e)}

def _get_diag_info() -> dict:
    """Self-diagnostics for AOC itself -- uptime/DB size/thread count/memory/
    backup status. Nothing tracked any of this before; useful for debugging
    AOC the way today's OneDrive-freeze investigation needed (mtime checks,
    manual process inspection) without ad-hoc tooling next time."""
    info = {
        "uptime_s": time.time() - _PROCESS_START,
        "thread_count": threading.active_count(),
        "db_size_bytes": None,
        "memory_rss_bytes": None,
        "backups_count": 0,
        "backups_latest": None,
        "backups_latest_size_bytes": None,
    }
    try:
        info["db_size_bytes"] = os.path.getsize(DB_FILE)
    except Exception:
        pass
    try:
        import psutil
        info["memory_rss_bytes"] = psutil.Process(os.getpid()).memory_info().rss
    except Exception:
        pass
    try:
        backup_dir = os.path.join(AOC_DIR, "backups")
        files = sorted(glob.glob(os.path.join(backup_dir, "history_*.db")))
        info["backups_count"] = len(files)
        if files:
            latest = files[-1]
            info["backups_latest"] = os.path.basename(latest)
            info["backups_latest_size_bytes"] = os.path.getsize(latest)
    except Exception:
        pass
    return info

# ── Webhook settings (server-persisted, so delivery works with no browser
#    tab open at all -- see _webhook_notify_worker below) ─────────────────────
# The webhook URL/enabled-events used to live in localStorage only: the JS
# _fireWebhook() decided whether to fire and POSTed to /webhook_fire, which
# was a pure relay reading nothing stored server-side. If no tab was open,
# webhooks silently never fired. Modeled on TOKEN_FILE just below (a small
# standalone file, not status.json -- status.json gets wholesale-rebuilt by
# /reset, which already silently drops any key not explicitly carried into
# that handler's `idle = {...}` dict, e.g. session_note today).
WEBHOOK_SETTINGS_FILE = os.path.join(AOC_DIR, "aoc_webhook_settings.json")
_webhook_settings_lock = threading.Lock()
_webhook_settings = {"url": "", "events": {"done": True, "error": True, "stuck": False, "burn_spike": False, "weekly_digest": False, "waiting_nudge": False, "cost_spike": False, "budget_alert": False}}

def _load_webhook_settings():
    global _webhook_settings
    data = _load_json_file(WEBHOOK_SETTINGS_FILE)
    try:
        if isinstance(data, dict) and "url" in data:
            # Same defensive backfill _load_notify_settings already does for
            # its own newer fields (e.g. digest_cadence) -- a settings file
            # written before burn_spike/weekly_digest/waiting_nudge existed
            # would otherwise load with those three keys simply absent
            # (harmless today since every .get() downstream already
            # defaults sensibly, but worth being consistent/explicit).
            data["events"] = _sanitize_webhook_events(data.get("events") or {})
            _webhook_settings = data
    except Exception:
        pass

def _sanitize_webhook_events(events: dict) -> dict:
    """Whitelist + coerce-to-bool every known webhook event key, with the
    same defaults used everywhere else these keys appear (the
    _webhook_settings module default, the JS _webhookEvents default
    string, _soundPrefs.events). Extracted specifically because this used
    to be inlined in the /webhook_settings POST handler with only
    done/error/stuck listed -- burn_spike/weekly_digest/waiting_nudge
    were silently dropped on every save despite the client always
    sending all seven (cost_spike added later) and every background
    worker already reading them (confirmed dead: aoc_webhook_settings.json
    had never once contained those three keys)."""
    return {
        "done": bool(events.get("done", True)),
        "error": bool(events.get("error", True)),
        "stuck": bool(events.get("stuck", False)),
        "burn_spike": bool(events.get("burn_spike", False)),
        "weekly_digest": bool(events.get("weekly_digest", False)),
        "waiting_nudge": bool(events.get("waiting_nudge", False)),
        "cost_spike": bool(events.get("cost_spike", False)),
        "budget_alert": bool(events.get("budget_alert", False)),
    }

def _save_webhook_settings(data: dict):
    global _webhook_settings
    with _webhook_settings_lock:
        _webhook_settings = data
        try:
            with open(WEBHOOK_SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass

_load_webhook_settings()

def _clamp_backup_retention_days(value) -> int:
    """Coerce a user-supplied backup retention window to a sane int range
    (1-365 days) -- guards _backup_history_db's pruning loop against a
    malformed/absent value ever deleting everything (0 or negative) or a
    typo'd huge number effectively disabling pruning entirely."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 14
    return max(1, min(365, n))

def _clamp_agent_retention_hours(value) -> int:
    """Coerce a user-supplied agent-retention window to a sane int range.
    Capped at 23 (not 24+) because completed_at/started_at are stored as
    bare "HH:MM:SS" local-time strings with no date component (_now_ts) --
    _hms_elapsed_hours can only measure "hours since, assuming it was
    within the last day" (mod-24 arithmetic), so 24h+ is genuinely
    ambiguous with "just now". Floored at 1 so a malformed/zero value
    can't auto-clear every just-finished agent instantly."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 12
    return max(1, min(23, n))

# ── Notification quiet hours / per-project mute ────────────────────────────
# With toast + sound + webhook all now able to fire server-side with no tab
# open, notification fatigue became a real risk -- this is the one place
# that decides "should ANY channel notify about this agent right now",
# shared by both _headless_notify_worker (toast) and _webhook_notify_worker
# (webhook) below. Modeled on WEBHOOK_SETTINGS_FILE just above: its own
# small file, not status.json.
NOTIFY_SETTINGS_FILE = os.path.join(AOC_DIR, "aoc_notify_settings.json")
_notify_settings_lock = threading.Lock()
_notify_settings = {"quiet_start": "", "quiet_end": "", "muted_projects": [], "snitch_url": "", "digest_cadence": "weekly", "backup_retention_days": 14, "agent_retention_hours": 12, "project_budgets": {}}

def _sanitize_project_budgets(budgets) -> dict:
    """Whitelist + coerce a user-supplied {project: monthly $ budget} map --
    same reasoning as _sanitize_webhook_events: the client always sends
    whatever's currently in the Settings textarea, and this is the one
    place that gets trusted as project names / numeric thresholds
    _webhook_notify_worker's budget_alert check later compares against."""
    if not isinstance(budgets, dict):
        return {}
    out = {}
    for project, amount in budgets.items():
        project = str(project).strip()[:100]
        if not project:
            continue
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            continue
        if amount <= 0:
            continue
        out[project] = round(min(amount, 1_000_000), 2)
        if len(out) >= 50:
            break
    return out

def _load_notify_settings():
    global _notify_settings
    data = _load_json_file(NOTIFY_SETTINGS_FILE)
    try:
        if isinstance(data, dict) and "muted_projects" in data:
            data.setdefault("snitch_url", "")  # older settings files predate this field
            data.setdefault("digest_cadence", "weekly")  # ditto
            data.setdefault("backup_retention_days", 14)  # ditto
            data.setdefault("agent_retention_hours", 12)  # ditto
            data.setdefault("project_budgets", {})  # ditto
            _notify_settings = data
    except Exception:
        pass

def _save_notify_settings(data: dict):
    global _notify_settings
    with _notify_settings_lock:
        _notify_settings = data
        try:
            with open(NOTIFY_SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass

_load_notify_settings()

def _in_quiet_hours(quiet_start: str, quiet_end: str) -> bool:
    """True if the current local time falls in [quiet_start, quiet_end).
    A start > end is treated as an overnight window (e.g. 22:00-07:00)."""
    if not quiet_start or not quiet_end:
        return False
    try:
        now_str = datetime.now().strftime("%H:%M")
        if quiet_start <= quiet_end:
            return quiet_start <= now_str < quiet_end
        return now_str >= quiet_start or now_str < quiet_end
    except Exception:
        return False

def _notification_suppressed(project: str) -> bool:
    cfg = _notify_settings
    if project and project in (cfg.get("muted_projects") or []):
        return True
    return _in_quiet_hours(cfg.get("quiet_start", ""), cfg.get("quiet_end", ""))

_SNITCH_INTERVAL_S = 600  # 10 min -- comfortably inside the grace period any
                          # ping-or-alert service (healthchecks.io, Cronitor,
                          # UptimeRobot heartbeats) would reasonably be set to

def _snitch_ping(url: str) -> None:
    """Fire a single heartbeat GET at the configured dead-man's-snitch URL.
    A missed ping is exactly the signal these services are built to alert
    on -- no retry/backoff here, same as every other best-effort background
    worker in this file; the NEXT interval's ping is the retry."""
    if not url:
        return
    try:
        import urllib.request
        urllib.request.urlopen(url, timeout=8)
    except Exception as e:
        _log_bg_error("_snitch_ping", e)

def _snitch_worker():
    """Runs in monitor.py itself, deliberately -- not sentinel.py/watchdog.py.
    The whole point is that if monitor.py dies, the pings stop, which is the
    correct trigger for the external service to fire. sentinel.py/watchdog.py
    already cover 'is AOC alive on this machine'; this covers 'is this
    machine even reachable at all', which nothing on the machine itself can
    ever detect once it's the thing that's down."""
    while True:
        time.sleep(_SNITCH_INTERVAL_S)
        _snitch_ping(_notify_settings.get("snitch_url", ""))

threading.Thread(target=_snitch_worker, daemon=True).start()

def _compute_burn_rates(history, now: float, recent_window_s: int = 180, min_history_s: int = 300):
    """Given a session's (epoch, cumulative_tokens) samples -- already
    trimmed to whatever window the caller tracks -- return (recent_rate,
    avg_rate) in tokens/minute, or None if there isn't yet enough history
    to judge a burn rate meaningfully (avoids noisy false positives on
    brand-new sessions). `recent_rate` covers the last `recent_window_s`
    seconds; `avg_rate` covers the whole observed history, requiring at
    least `min_history_s` seconds of it before returning anything at all."""
    if len(history) < 2:
        return None
    oldest_ts, oldest_tokens = history[0]
    newest_ts, newest_tokens = history[-1]
    observed_s = newest_ts - oldest_ts
    if observed_s < min_history_s:
        return None
    avg_rate = (newest_tokens - oldest_tokens) / (observed_s / 60.0)
    recent_cutoff = now - recent_window_s
    recent = [(t, tok) for t, tok in history if t >= recent_cutoff]
    if len(recent) < 2:
        return None
    r_oldest_ts, r_oldest_tokens = recent[0]
    r_newest_ts, r_newest_tokens = recent[-1]
    r_observed_s = r_newest_ts - r_oldest_ts
    if r_observed_s <= 0:
        return None
    recent_rate = (r_newest_tokens - r_oldest_tokens) / (r_observed_s / 60.0)
    return (recent_rate, avg_rate)

def _is_burn_spike(recent_rate: float, avg_rate: float, min_floor: float = 5000.0, multiplier: float = 3.0) -> bool:
    """A session's token burn rate is spiking if its recent short-window
    rate (tokens/min) is both a multiple of its own session-long average
    AND above an absolute floor -- the floor guards a low-volume session
    where the ratio alone would be noisy but the real numbers are trivial."""
    return avg_rate > 0 and recent_rate > multiplier * avg_rate and recent_rate > min_floor

def _is_cost_spike(session_cost: float, project_avg_cost: float, min_floor: float = 1.0, multiplier: float = 3.0) -> bool:
    """Session-level counterpart to _is_burn_spike -- distinct signal: this
    looks at a session's *total* cost so far against that project's
    historical per-session average (from /analytics' by_project rows), not
    the token *rate*. Catches a session that's simply run up an unusually
    large bill for its project (e.g. an expensive multi-hour deep-dive)
    even if it never burned tokens fast enough to trip the rate-based
    burn-spike check. Same floor+multiplier shape as _is_burn_spike so a
    project with a trivial historical average doesn't generate noise."""
    return project_avg_cost > 0 and session_cost > multiplier * project_avg_cost and session_cost > min_floor

def _project_avg_costs(by_project: list) -> dict:
    """Turn /analytics' by_project rows (each a sum of cost + a session
    count) into {project: average cost per historical session} for
    _is_cost_spike to compare a live session's running cost against.
    Skips any row with a zero/missing session count rather than dividing by
    zero (a brand-new project with no completed sessions yet has no
    baseline to compare against)."""
    out = {}
    for row in by_project or []:
        project = row.get("project")
        sessions = row.get("sessions") or 0
        if not project or sessions <= 0:
            continue
        out[project] = (row.get("cost") or 0) / sessions
    return out

def _project_month_to_date_costs() -> dict:
    """{project: cost} summed over the current calendar month only -- same
    aggregation shape as _db_analytics()'s by_project, just date-scoped to
    this month, for _webhook_notify_worker's budget_alert check against
    _notify_settings["project_budgets"]. A dedicated query rather than
    reusing by_day_project client-side-style (summing per-day rows in a
    loop): this needs one server-side per-project total, refreshed on the
    same 5-min cache cadence as _project_avg_cost_cache."""
    month_prefix = datetime.now().strftime("%Y-%m") + "%"
    with _db_lock:
        try:
            c = _db_conn()
            rows = c.execute("""
                SELECT project, SUM(cost) as cost
                FROM sessions WHERE project != '' AND date LIKE ?
                GROUP BY project
            """, (month_prefix,)).fetchall()
            c.close()
            return {r["project"]: (r["cost"] or 0) for r in rows}
        except Exception:
            return {}

def _fire_webhook(url: str, payload: dict):
    """Shared relay used by both /webhook_fire (client-triggered, tab must be
    open) and _webhook_notify_worker (server-triggered, works with no tab
    open) -- one place the actual HTTP delivery can drift, not two."""
    if not url:
        return
    _log_alert_fired(payload)
    import urllib.request as _ur
    body = json.dumps({"source": "AOC", **payload}, ensure_ascii=False).encode()
    def _fire():
        try:
            req = _ur.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
            _ur.urlopen(req, timeout=8)
        except Exception as e:
            # _log_alert_fired above already recorded this as "fired" before
            # delivery was even attempted -- a failure here would otherwise
            # look identical to success in alert_audit.log.
            _log_bg_error("_fire_webhook", e)
    threading.Thread(target=_fire, daemon=True).start()

def _webhook_notify_worker():
    """Server-side counterpart to the client-only _fireWebhook -- mirrors
    _headless_notify_worker's own "poll /status every 3s, diff against a
    _prev dict, act on running->done/error transitions" shape exactly, just
    calling _fire_webhook with the persisted config instead of
    _show_native_toast. This is what makes webhooks actually fire when no
    browser tab is open at all.

    Also tracks stuck agents server-side (mirrors the client's own
    _lastProgressAt/_stuckNotified logic in the JS, which only fires
    _fireWebhook('stuck', ...) while a browser tab is open -- without this,
    'stuck' was the one event type that still silently required a tab).

    Used to bail out entirely with `if not url: continue` -- meaning the
    stuck/burn-spike/cost-spike/waiting-nudge detection below never ran at
    all without a webhook URL configured, and none of those four ever had a
    native toast either (unlike done/error, which get one from the
    completely separate _headless_notify_worker/_tray_monitor loops
    regardless of webhook config). That made them silently invisible in
    --headless mode with no webhook set up and no browser tab open --
    exactly the "nobody's watching" scenario a stuck-agent or
    forgotten-session alert exists for. Detection now always runs;
    _fire_webhook(url, ...) already no-ops safely on an empty url, and a
    toast is fired alongside it gated only on quiet-hours/muted-project
    suppression, mirroring the done/error toast precedent exactly."""
    import urllib.request
    _prev = {}
    _last_progress_at = {}
    _stuck_notified = set()
    _token_history = {}  # session_id -> [(epoch, cumulative_tokens), ...], trimmed to last 10 min
    _burn_notified = set()
    _waiting_notified = set()
    _cost_spike_notified = set()
    _project_avg_cost_cache = {}
    _project_avg_cost_cache_at = 0.0
    _budget_cost_cache = {}
    _budget_cost_cache_at = 0.0
    _budget_notified = set()  # projects already alerted this calendar month
    _budget_notified_month = [datetime.now().strftime("%Y-%m")]
    while True:
        try:
            time.sleep(3)
            cfg = _webhook_settings
            url = cfg.get("url", "")
            events = cfg.get("events", {})
            raw = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/status", timeout=2).read()
            d = json.loads(raw)
            agents = {a["id"]: a for a in (d.get("agents") or []) if not str(a.get("id", "")).startswith("hook_")}
            for aid, a in agents.items():
                old = _prev.get(aid, {})
                suppressed = _notification_suppressed(a.get("session_project", ""))
                if old.get("status") == "running":
                    new_status = a.get("status")
                    event = new_status if new_status in ("done", "error") else None
                    if event and events.get(event) and not suppressed:
                        # project/model were missing here even though both are
                        # sitting right on `a` -- an external webhook consumer
                        # (Slack, Discord, a custom bot) had no way to tell
                        # which project/model an event was even about without
                        # opening the dashboard, and an `error` notification in
                        # particular carried no error text at all, defeating
                        # much of the point of getting notified without having
                        # to go look.
                        agent_payload = {
                            "id": a.get("id"), "name": a.get("name"), "status": new_status,
                            "cost": round(a.get("estimated_cost") or 0, 4),
                            "project": a.get("session_project") or "",
                            "model": a.get("model") or "",
                        }
                        if new_status == "error" and a.get("error_message"):
                            agent_payload["error_message"] = a["error_message"][:500]
                        _fire_webhook(url, {"event": event, "agent": agent_payload})
                prev_done = len([t for t in (old.get("tasks") or []) if t.get("done")])
                cur_done = len([t for t in (a.get("tasks") or []) if t.get("done")])
                if a.get("status") == "running":
                    if cur_done > prev_done or aid not in _last_progress_at:
                        _last_progress_at[aid] = time.time()
                    if aid not in _stuck_notified and time.time() - _last_progress_at[aid] > 300:
                        _stuck_notified.add(aid)
                        if not suppressed:
                            if events.get("stuck"):
                                _fire_webhook(url, {
                                    "event": "stuck",
                                    "agent": {
                                        "id": a.get("id"), "name": a.get("name"), "status": "running",
                                        "cost": round(a.get("estimated_cost") or 0, 4),
                                        "project": a.get("session_project") or "",
                                        "model": a.get("model") or "",
                                    },
                                })
                            try:
                                _show_native_toast(f"⏸ {a.get('name','Agent')[:40]}", "No task progress for 5+ min")
                            except Exception:
                                pass
                else:
                    _stuck_notified.discard(aid)
                    _last_progress_at.pop(aid, None)
            _prev = agents

            # Refresh the per-project historical-average-cost cache every
            # 5min (not every 3s tick) -- it's a DB aggregate query and only
            # needs to track slow-moving history, not live session state.
            # No longer gated on events.get("cost_spike") -- that toggle only
            # controls the webhook, but the toast fires independently of it
            # now, and without this cache project_avg stays permanently 0,
            # which would silently make _is_cost_spike() never trigger.
            if time.time() - _project_avg_cost_cache_at > 300:
                try:
                    _project_avg_cost_cache = _project_avg_costs(_db_analytics().get("by_project"))
                except Exception:
                    pass
                _project_avg_cost_cache_at = time.time()

            # Month-to-date per-project cost for budget_alert below, same
            # 5-min refresh cadence as _project_avg_cost_cache above (a DB
            # aggregate, not something that needs per-tick freshness). Also
            # the natural place to detect a new calendar month starting and
            # re-arm _budget_notified -- month-to-date spend only ever goes
            # up within a month, so "the month rolled over" is the one
            # condition that should let an already-alerted project fire
            # again, mirroring how other *_notified sets re-arm on their
            # own condition clearing (e.g. _stuck_notified.discard above).
            if time.time() - _budget_cost_cache_at > 300:
                try:
                    _budget_cost_cache = _project_month_to_date_costs()
                except Exception:
                    pass
                _budget_cost_cache_at = time.time()
                this_month = datetime.now().strftime("%Y-%m")
                if this_month != _budget_notified_month[0]:
                    _budget_notified.clear()
                    _budget_notified_month[0] = this_month

            # Per-project monthly budget: fires once per project per
            # calendar month when month-to-date spend crosses the budget
            # configured in Settings (_notify_settings["project_budgets"]).
            # Consolidates two previously-disconnected client-only signals
            # (a browser-Notification check in renderKpi, tab-open only;
            # and a purely-visual Settings-panel summary that never fired
            # anything) into the same server-side worker every other alert
            # type already goes through, so it works headless too.
            for project, budget in ((_notify_settings.get("project_budgets") or {}).items() if _is_pro() else ()):
                spend = _budget_cost_cache.get(project, 0)
                if spend >= budget and project not in _budget_notified:
                    _budget_notified.add(project)
                    if not _notification_suppressed(project):
                        if events.get("budget_alert"):
                            _fire_webhook(url, {
                                "event": "budget_alert",
                                "project": project,
                                "spend": round(spend, 2),
                                "budget": round(budget, 2),
                            })
                        try:
                            _show_native_toast(
                                f"💸 {project}",
                                f"Monthly budget exceeded: ${spend:.2f} of ${budget:.2f}"
                            )
                        except Exception:
                            pass

            # Token-burn anomaly: a session whose recent tokens/min rate is a
            # multiple of its own session-long average -- catches a runaway
            # loop or retry storm. Live in-memory check, no persisted
            # baseline (see _compute_burn_rates/_is_burn_spike docstrings).
            now = time.time()
            for sess in (d.get("sessions_list") or []):
                sid = sess.get("id")
                if not sid or not sess.get("session_active"):
                    _token_history.pop(sid, None)
                    _burn_notified.discard(sid)
                    _waiting_notified.discard(sid)
                    _cost_spike_notified.discard(sid)
                    continue
                suppressed = _notification_suppressed(sess.get("project", ""))

                # Waiting-too-long nudge: the inverse of the burn-spike check
                # below -- flags a session that's been sitting in
                # waiting_on_you state for hours, so a forgotten session gets
                # surfaced instead of just showing a static WAITING pill
                # forever. waiting_secs is precomputed server-side in
                # _build_status_payload (see _compute_waiting_secs). Kept
                # independent of the burn-rate history-sufficiency check
                # below (which uses `continue` and would otherwise silently
                # skip this too for a session with under 5min of samples).
                if (sess.get("waiting_secs") or 0) > 7200:
                    if sid not in _waiting_notified:
                        _waiting_notified.add(sid)
                        if not suppressed:
                            if events.get("waiting_nudge"):
                                _fire_webhook(url, {
                                    "event": "waiting_nudge",
                                    "session": {
                                        "id": sid, "project": sess.get("project"),
                                        "display_name": sess.get("display_name"),
                                        "waiting_secs": sess.get("waiting_secs"),
                                    },
                                })
                            try:
                                ws = sess.get("waiting_secs") or 0
                                wh, wm = int(ws // 3600), int((ws % 3600) // 60)
                                _show_native_toast(
                                    f"⏳ {sess.get('display_name') or sess.get('project') or 'Session'}",
                                    f"Waiting on you for {wh}h {wm}m"
                                )
                            except Exception:
                                pass
                else:
                    _waiting_notified.discard(sid)

                # Cost-spike: a session's *total* cost so far vs. its
                # project's historical per-session average (see
                # _is_cost_spike) -- distinct from the token-*rate*
                # burn-spike below, and kept independent of that check's
                # history-sufficiency `continue` for the same reason
                # waiting_nudge above is.
                project_avg = _project_avg_cost_cache.get(sess.get("project", ""), 0)
                session_cost = sess.get("estimated_cost") or 0
                if _is_cost_spike(session_cost, project_avg):
                    if sid not in _cost_spike_notified:
                        _cost_spike_notified.add(sid)
                        if not suppressed:
                            if events.get("cost_spike"):
                                _fire_webhook(url, {
                                    "event": "cost_spike",
                                    "session": {
                                        "id": sid, "project": sess.get("project"),
                                        "display_name": sess.get("display_name"),
                                        "cost": round(session_cost, 4),
                                        "project_avg_cost": round(project_avg, 4),
                                    },
                                })
                            try:
                                _show_native_toast(
                                    f"💰 {sess.get('display_name') or sess.get('project') or 'Session'}",
                                    f"Cost spike: ${session_cost:.2f} (project avg ${project_avg:.2f})"
                                )
                            except Exception:
                                pass
                else:
                    _cost_spike_notified.discard(sid)

                total = (sess.get("input_tokens") or 0) + (sess.get("output_tokens") or 0)
                hist = _token_history.setdefault(sid, [])
                hist.append((now, total))
                cutoff = now - 600
                while hist and hist[0][0] < cutoff:
                    hist.pop(0)
                rates = _compute_burn_rates(hist, now)
                if not rates:
                    continue
                recent_rate, avg_rate = rates
                if _is_burn_spike(recent_rate, avg_rate):
                    if sid not in _burn_notified:
                        _burn_notified.add(sid)
                        if not suppressed:
                            if events.get("burn_spike"):
                                _fire_webhook(url, {
                                    "event": "burn_spike",
                                    "session": {
                                        "id": sid, "project": sess.get("project"),
                                        "display_name": sess.get("display_name"),
                                        "recent_rate": round(recent_rate, 1),
                                        "avg_rate": round(avg_rate, 1),
                                    },
                                })
                            try:
                                _show_native_toast(
                                    f"⚡ {sess.get('display_name') or sess.get('project') or 'Session'}",
                                    f"Burn spike: {recent_rate:.0f} tok/min (avg {avg_rate:.0f})"
                                )
                            except Exception:
                                pass
                else:
                    _burn_notified.discard(sid)
        except Exception as e:
            _log_bg_error("_webhook_notify_worker", e)

threading.Thread(target=_webhook_notify_worker, daemon=True).start()


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

def _pid_exe_name(pid) -> str:
    """Return the lowercase exe filename for a live PID via a
    CreateToolhelp32Snapshot walk (fast, subprocess-free) -- mirrors
    aoc_hook.py's own ancestor-walk technique rather than shelling out to
    tasklist, since this needs to run on every /status computation.
    Confirmed ~7ms/call for the full ancestor-walk version of this same
    snapshot technique; a single-PID lookup is at least as fast. Shared by
    every PID-liveness check in this file so the ctypes structure
    definition isn't duplicated per caller."""
    if not pid:
        return ""
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
            return ""
        try:
            entry = PROCESSENTRY32()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
            if kernel32.Process32First(snapshot, ctypes.byref(entry)):
                while True:
                    if entry.th32ProcessID == pid:
                        return entry.szExeFile.decode("mbcs", "ignore").lower()
                    if not kernel32.Process32Next(snapshot, ctypes.byref(entry)):
                        break
        finally:
            kernel32.CloseHandle(snapshot)
    except Exception:
        pass
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

def _tail_log_lines(path: str, n: int = 20) -> list:
    """Return the last `n` non-empty, stripped lines of a log file. Simple
    readlines()-based tail -- watchdog.py/sentinel.py cap these logs at
    200KB (LOG_MAX_BYTES in each script) so reading the whole file is
    cheap; no need for a seek-from-end approach."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = [l.strip() for l in f if l.strip()]
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

def _ngrok_authtoken_configured() -> bool:
    """Cached — this backs a /status field polled every 500ms, and the real
    check shells out to ngrok.exe; don't spawn that twice a second."""
    ng = _find_ngrok()
    if not ng:
        return False
    if time.time() - _ngrok_authtoken_cache[1] < 10:
        return _ngrok_authtoken_cache[0]
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
    if not _ngrok_authtoken_configured():
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

def _selfupdate_worker():
    """Background thread: check every 15 min whether this machine's AOC
    checkout is behind origin/master, so a stale copy doesn't silently keep
    running old code across multiple machines. Read-only (fetch + rev-parse
    only, never pulls/merges) -- purely informational."""
    import time as _t
    while True:
        try:
            _run(["git", "-C", AOC_DIR, "fetch", "--quiet"], timeout=15)
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
                    _show_native_toast("AOC", f"Update available: {behind_by} commit(s) behind origin/master.")
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

def _build_digest_summary() -> dict:
    """Sum the last 7 entries of by_day (already ordered DESC by date) for
    the weekly digest. Also sums the PRECEDING 7 entries for a
    week-over-week cost trend. Approximation note: by_day only contains
    rows for days with actual activity, so slicing by array position
    isn't calendar-exact if a day had zero sessions -- same "simple
    run-rate, not a precise calendar reconciliation" spirit as the
    month-end cost projection elsewhere in this file.

    Excludes any project in muted_projects -- a muted project already has
    its individual toast/webhook events suppressed, and the digest is
    itself a notification, so its numbers silently inflating a "quiet"
    project's absence from the summary would defeat the point of muting
    it. Falls back to _db_analytics()'s own (unfiltered) by_day when
    nothing is muted, rather than always taking the excluding-query path
    for identical output."""
    muted = [p for p in (_notify_settings.get("muted_projects") or []) if p]
    by_day = _db_by_day_excluding_projects(muted) if muted else _db_analytics().get("by_day", [])
    last7 = by_day[:7]
    prev7 = by_day[7:14]
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
                        f"{summary['sessions']} sessions, ${summary['cost']:.2f} ({trend}), {summary['errors']} errors this week"
                    )
                except Exception:
                    pass
                cfg = _webhook_settings
                url = cfg.get("url", "")
                if url and cfg.get("events", {}).get("weekly_digest"):
                    _fire_webhook(url, {"event": "weekly_digest", "summary": summary})
                _write_last_digest_week(_digest_marker_for(now, cadence))
        except Exception as e:
            _log_bg_error("_digest_worker", e)
        _t.sleep(1800)

_threading.Thread(target=_digest_worker, daemon=True).start()


# ── Claude process counter ─────────────────────────────────────────────────────

_claude_proc_count = [0]  # cached count of running claude.exe processes

def _claude_proc_worker():
    """Background thread: count running claude.exe processes every 2 seconds.
    Was 10s, which — stacked with the /events WS heartbeat — meant a closed CLI
    could take up to ~12s to disappear from the dashboard's total-CLI count.
    Also wakes any connected /events WebSocket immediately when the count
    actually changes, instead of waiting for the next heartbeat tick."""
    import subprocess as _sp, time as _t
    _cflags = _sp.CREATE_NO_WINDOW if os.name == "nt" else 0
    while True:
        try:
            r = _sp.run(
                ["tasklist", "/FI", "IMAGENAME eq claude.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5, creationflags=_cflags
            )
            new_count = r.stdout.count("claude.exe")
            if new_count != _claude_proc_count[0]:
                _claude_proc_count[0] = new_count
                with _status_cond:
                    _status_version[0] += 1
                    _status_cond.notify_all()
        except Exception as e:
            _log_bg_error("_claude_proc_worker", e)
        _t.sleep(2)

_threading.Thread(target=_claude_proc_worker, daemon=True).start()


# ── Encoding utilities ─────────────────────────────────────────────────────────

def _set_session_note(status: dict, session_id: str, note: str) -> bool:
    """Write a per-session note (distinct from the single global
    status["session_note"] scratchpad) onto sessions[session_id]["note"],
    capped at 2000 chars like the global note. Returns False without
    mutating `status` if session_id doesn't exist -- a note can't attach
    to a session AOC has never seen."""
    sessions = status.setdefault("sessions", {})
    if session_id not in sessions:
        return False
    sessions[session_id]["note"] = str(note)[:2000]
    return True

def _fix_mojibake(s: str) -> str:
    """Fix strings where UTF-8 bytes were decoded as a single-byte Windows
    codepage instead of UTF-8 -- a recurring Windows pipe/subprocess issue.
    Tries latin-1 first (the original case this was written for), then
    cp1250/Central European (this machine's system codepage -- a Slovak
    display_name that took that specific wrong turn would read as
    "PokraÄŤovĂˇnĂ..." instead of "Pokračování...";
    not confirmed to have actually happened to live data, added purely
    as defensive coverage for the same class of issue)."""
    for codec in ("latin-1", "cp1250"):
        try:
            fixed = s.encode(codec).decode("utf-8")
            if fixed != s:
                return fixed
        except Exception:
            continue
    return s


def _decode_project_name(encoded: str) -> str:
    """Decode Claude Code's encoded project path (C--Users-marek-ai-antivirus)
    to a readable project name by walking the filesystem to resolve hyphens."""
    import re as _re2
    m = _re2.match(r'^([A-Za-z])--(.+)$', encoded)
    if not m:
        return encoded
    path = m.group(1) + ":\\"
    parts = m.group(2).split('-')
    i = 0
    while i < len(parts):
        matched = False
        for j in range(len(parts), i, -1):
            candidate = os.path.join(path, '-'.join(parts[i:j]))
            if os.path.isdir(candidate):
                path = candidate
                i = j
                matched = True
                break
        if not matched:
            break
    name = os.path.basename(path)
    home_name = os.path.basename(os.path.expanduser("~"))
    return "" if name in (home_name, "Users", "home", "") else name


# ── Transcript scanner ─────────────────────────────────────────────────────────

def _waiting_on_you_from_line(t, stop_reason):
    """Given a transcript line's `type` and (for assistant lines) its
    `stop_reason`, decide whether this line marks a turn boundary that
    should update the session's waiting_on_you flag -- and to what value.
    Returns None for anything that isn't a real conversation turn (the
    side-channel metadata lines like ai-title/agent-name/mode/permission-
    mode), so those never reset the flag. An assistant line whose
    stop_reason is "tool_use" means Claude is still mid-turn (about to run
    a tool and keep going); any other stop_reason (end_turn, max_tokens,
    stop_sequence) means the turn is over and control is back with the
    human. Any "user" line -- a real reply the human typed, or a
    tool_result fed back to Claude -- means the turn is no longer sitting
    with the human either way."""
    if t == "assistant":
        if stop_reason is None:
            return None
        return stop_reason != "tool_use"
    if t == "user":
        return False
    return None

def _compute_waiting_secs(sess: dict, now_epoch: float) -> int:
    """How long a session has been sitting in waiting_on_you state, in
    seconds. Works with no new tracking field: the Stop heartbeat that
    flips waiting_on_you to True is the same event that last refreshed
    last_seen_epoch, and nothing updates last_seen_epoch again until the
    user's next prompt flips waiting_on_you back to False -- so
    now - last_seen_epoch already *is* the waiting duration."""
    if not sess.get("waiting_on_you"):
        return 0
    return int(now_epoch - sess.get("last_seen_epoch", now_epoch))

def _accumulate_waiting_time(sess: dict, new_waiting, now_epoch: float) -> None:
    """Mutates `sess` in place to build a running total of how long this
    CLI session has spent with waiting_on_you=True across its whole
    lifetime (waiting_on_you_accum_s), for the WAITING ON YOU history trend.
    _compute_waiting_secs above only ever answers "how long has it been
    waiting *right now*" -- fine for a live badge, useless for a lifetime
    total, since nothing else records when each waiting period started or
    ended. Called every time either code path that sets waiting_on_you (the
    /update heartbeat, and the transcript-scanner fallback) is about to
    change it -- only an actual True<->False transition does anything; a
    same-value update (the common case) is a no-op."""
    was_waiting = bool(sess.get("waiting_on_you"))
    new_waiting = bool(new_waiting)
    if new_waiting and not was_waiting:
        sess["waiting_on_you_since"] = now_epoch
    elif not new_waiting and was_waiting:
        since = sess.get("waiting_on_you_since")
        if since:
            sess["waiting_on_you_accum_s"] = sess.get("waiting_on_you_accum_s", 0) + max(0, now_epoch - since)
        sess["waiting_on_you_since"] = None

def _read_ai_title(jsonl_path: str) -> str:
    """Read the ai-title entry from a Claude Code transcript JSONL file."""
    try:
        with open(jsonl_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get("type") == "ai-title":
                    return obj.get("aiTitle", "")
    except Exception:
        pass
    return ""


# Model pricing: (input_$/1M, output_$/1M, cache_write_$/1M, cache_read_$/1M)
# Standard context window across every Claude model tier currently priced
# above (does not account for the opt-in 1M-context beta some models
# offer) -- the agent card's token progress bar (see renderAgents' tokensHtml)
# has been reading a.token_limit to compute this all along, but nothing
# ever sent it, so that bar never rendered.
_CLAUDE_CONTEXT_WINDOW = 200_000

_MODEL_PRICING = {
    "claude-opus-4":     (15.0, 75.0, 18.75, 1.50),
    "claude-sonnet-4":   (3.0,  15.0,  3.75, 0.30),
    "claude-haiku-4":    (0.80,  4.0,  1.00, 0.08),
    "claude-opus-3":     (15.0, 75.0, 18.75, 1.50),
    "claude-sonnet-3-5": (3.0,  15.0,  3.75, 0.30),
    "claude-haiku-3":    (0.25,  1.25, 0.30, 0.03),
}

_transcript_cursors: dict = {}   # session_id → {"path": str, "offset": int, "stats": dict}
_transcript_cursors_lock = _threading.Lock()

def _model_pricing(model: str):
    for prefix, pricing in _MODEL_PRICING.items():
        if model.startswith(prefix):
            return pricing
    return (3.0, 15.0, 3.75, 0.30)  # default: sonnet-4

def _calc_cost(inp: int, out: int, cache_write: int, cache_read: int, model: str) -> float:
    pi, po, pw, pr = _model_pricing(model)
    return (inp * pi + out * po + cache_write * pw + cache_read * pr) / 1_000_000


# child_id -> parent_id, for a parent's child_ids arriving before the
# child agent itself has been created yet (see _apply_agent_update below).
# In practice the child's own PreToolUse/PostToolUse almost always fire
# before the parent's own PostToolUse gets around to reporting child_ids
# (the parent's tool call blocks on the child completing first), so this
# is a rare-ordering safety net, not the common path. Transient/in-memory
# only, same as _pr_link_cache and friends -- doesn't need to survive a
# restart.
_pending_parent_links: dict = {}

def _apply_agent_update(status: dict, au: dict, session_id: str, session_project: str, now: str) -> None:
    """Create or update one agent entry in status["agents"], keyed by au["id"].
    Extracted from the /update HTTP handler so the transcript-based fallback
    scanner (which detects Agent/Task tool_use blocks directly from a
    session's transcript, for when Claude Code's hook dispatch silently never
    fires PreToolUse/PostToolUse) can create/update the exact same schema
    through the exact same upsert path -- one place the agent schema can
    drift, not two."""
    aid = au.get("id")
    if not aid:
        return
    au.setdefault("session_id", session_id)
    au.setdefault("session_project", session_project)
    # Real per-model cost when granular usage is present (aoc_hook.py's
    # PostToolUse) -- reuses the exact _calc_cost the session-level KPI gauge
    # already trusts, instead of the flat _costRate guess the frontend falls
    # back to otherwise.
    if au.get("model") and "input_tokens" in au:
        au["estimated_cost"] = _calc_cost(
            au.get("input_tokens", 0), au.get("output_tokens", 0),
            au.get("cache_write_tokens", 0), au.get("cache_read_tokens", 0),
            au["model"])
    existing_ids = {a["id"]: a for a in status.get("agents", [])}
    # child_ids (this agent itself spawned these subagents, per aoc_hook.py's
    # _extract_child_agent_ids) sets TREE view's parent_id on each child --
    # either directly if the child already exists, or via the pending-links
    # map above if it hasn't been created yet.
    child_ids = au.pop("child_ids", None)
    if child_ids:
        for cid in child_ids:
            if cid in existing_ids:
                existing_ids[cid]["parent_id"] = aid
            else:
                _pending_parent_links[cid] = aid
    if aid in existing_ids:
        a = existing_ids[aid]
        old_status = a.get("status", "")
        new_status = au.get("status", old_status)
        if old_status != new_status:
            try:
                if new_status == "running":
                    _log.agent_start({**a, **au})
                elif new_status == "done":
                    _log.agent_done({**a, **au})
            except Exception as e:
                _log_bg_error("_apply_agent_update:status_transition", e)
        if "tasks" in au and "tasks" in a:
            for i, t_new in enumerate(au["tasks"]):
                if i < len(a["tasks"]):
                    was_done = a["tasks"][i].get("done", False)
                    is_done  = t_new.get("done", False)
                    if not was_done and is_done:
                        t_new["completed_at"] = now
                        try:
                            _log.task_complete(a, t_new)
                        except Exception as e:
                            _log_bg_error("_apply_agent_update:task_complete", e)
                    elif a["tasks"][i].get("completed_at"):
                        t_new.setdefault("completed_at", a["tasks"][i]["completed_at"])
        # accumulate log entries instead of replacing
        if "log" in au:
            new_entries = au.pop("log")
            combined = a.get("log", []) + new_entries
            a["log"] = combined[-300:]  # keep last 300 entries
        a.update(au)
        if au.get("status") == "done" and not a.get("completed_at"):
            a["completed_at"] = now
    else:
        # ── CREATE new agent ──
        # A done/error update for an agent we never saw start means its
        # PreToolUse registration was lost (e.g. AOC restarted mid-task, or
        # the hook dispatch itself silently never fired) -- still surface it
        # rather than silently dropping it: a late, incomplete card beats an
        # agent that ran, failed, and left no trace anywhere (not even the
        # error tracking panel, since that reads from this same agents list).
        # started_at is unknown in this case, so fall back to completed_at
        # (reads as a ~0s duration rather than a nonsense value) and note it.
        late_arrival = au.get("status") in ("done", "error") and not au.get("started_at")
        new_agent = {
            "id": aid,
            "name": au.get("name", aid),
            "icon": au.get("icon", "??"),
            "description": au.get("description", ""),
            "status": au.get("status", "waiting"),
            "tasks": au.get("tasks", []),
            "log": (["[registration missed — surfaced late]"] + au.get("log", []))
                   if late_arrival else au.get("log", []),
            "started_at": au.get("started_at") or au.get("completed_at") or now,
            "completed_at": None,
            "token_limit": _CLAUDE_CONTEXT_WINDOW,
            "session_id": au.get("session_id", session_id),
            "session_project": au.get("session_project", session_project),
            # How many *other* CLI sessions were active at the moment this
            # agent was first registered -- captured once, at creation, for
            # both hook- and transcript-created agents (both paths run
            # through this one function). Lets us test the hypothesis that
            # Claude Code's hook dispatch drops PreToolUse more often under
            # concurrent hook activity from multiple sessions, instead of
            # just guessing (see project notes, 2026-07-15/16).
            "concurrent_sessions": sum(
                1 for s in status.get("sessions", {}).values()
                if s.get("session_active") and not s.get("dismissed")
            ),
        }
        if au.get("completed_at"):
            new_agent["completed_at"] = au["completed_at"]
        if au.get("error_message"):
            new_agent["error_message"] = au["error_message"]
        if au.get("files_changed"):
            new_agent["files_changed"] = au["files_changed"]
        if au.get("tokens_used"):
            new_agent["tokens_used"] = au["tokens_used"]
        if au.get("tool_use_count") is not None:
            new_agent["tool_use_count"] = au["tool_use_count"]
        if au.get("estimated_cost") is not None:
            new_agent["estimated_cost"] = au["estimated_cost"]
        # Carried over so a late-arrival/transcript-created agent gets the
        # same accurate per-model cost data the update path already gets via
        # its blind a.update(au) merge -- previously only estimated_cost (the
        # derived number) made it into a brand-new card, not the raw
        # breakdown a future recompute or SQLite export would need.
        if au.get("model"):
            new_agent["model"] = au["model"]
            new_agent["input_tokens"] = au.get("input_tokens", 0)
            new_agent["output_tokens"] = au.get("output_tokens", 0)
            new_agent["cache_write_tokens"] = au.get("cache_write_tokens", 0)
            new_agent["cache_read_tokens"] = au.get("cache_read_tokens", 0)
        if au.get("detected_via"):
            new_agent["detected_via"] = au["detected_via"]
        if au.get("subagent_type"):
            new_agent["subagent_type"] = au["subagent_type"]
        pending_parent = _pending_parent_links.pop(aid, None)
        if pending_parent:
            new_agent["parent_id"] = pending_parent
        if not status.get("session_active"):
            status["session_active"] = True
            status["started_at"] = now
        status.setdefault("agents", []).append(new_agent)
        try:
            _log.agent_start(new_agent)
        except Exception as e:
            _log_bg_error("_apply_agent_update:agent_start", e)


def _transcript_scanner_worker():
    """Background thread: scan ~/.claude/projects/ for active sessions every 30s.
    Incrementally reads transcripts to extract token usage, model, cost, and ai-title.
    Fills in sessions that hooks may have missed."""
    import time as _t
    import hashlib as _hashlib  # local import: the thread starts (line ~1143,
    # below) before module-level "import hashlib as _hashlib" (line ~1321) runs,
    # so relying on that global would be a startup-order race.
    projects_base = os.path.join(os.path.expanduser("~"), ".claude", "projects")

    while True:
        try:
            if not os.path.isdir(projects_base):
                _t.sleep(30)
                continue

            now = _t.time()
            active_cutoff = now - 300        # 5 min → actively running session
            presence_cutoff = now - 1800     # 30 min → recently used session

            for encoded_proj in os.listdir(projects_base):
                proj_dir = os.path.join(projects_base, encoded_proj)
                if not os.path.isdir(proj_dir):
                    continue

                for fname in os.listdir(proj_dir):
                    if not fname.endswith(".jsonl"):
                        continue
                    fpath = os.path.join(proj_dir, fname)
                    try:
                        mtime = os.path.getmtime(fpath)
                    except OSError:
                        continue

                    if mtime < presence_cutoff:
                        continue

                    session_id = fname[:-6]  # strip .jsonl

                    # Get current cursor for this session
                    with _transcript_cursors_lock:
                        cursor = _transcript_cursors.get(session_id, {"path": fpath, "offset": 0, "stats": {}})

                    # Read new bytes from current offset
                    try:
                        with open(fpath, "rb") as f:
                            f.seek(cursor["offset"])
                            new_data = f.read()
                        new_offset = cursor["offset"] + len(new_data)
                        new_lines = [l for l in new_data.decode("utf-8", errors="replace").splitlines() if l.strip()]
                    except Exception:
                        continue

                    stats = cursor["stats"].copy()
                    ai_title = stats.get("ai_title", "")
                    # Agent/Task subagent starts + completions detected directly from
                    # this transcript -- a fallback for when Claude Code's hook
                    # dispatch silently never fires PreToolUse/PostToolUse for a real
                    # Agent tool call (confirmed live, 2026-07-15: zero TRACE lines
                    # anywhere for a session with real Agent tool_use blocks in its
                    # transcript). The transcript is Claude Code's own authoritative
                    # record, independent of that fragile side-channel.
                    agent_events = []

                    # Also read ai-title from first 50 lines if we haven't found it yet
                    # and this is the first scan (offset was 0 before)
                    if not ai_title and cursor["offset"] == 0:
                        ai_title = _read_ai_title(fpath)
                        if ai_title:
                            stats["ai_title"] = ai_title

                    for line in new_lines:
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue
                        t = obj.get("type")
                        if t == "ai-title" and not ai_title:
                            ai_title = obj.get("aiTitle", "")
                            stats["ai_title"] = ai_title
                        elif t == "assistant":
                            msg = obj.get("message", {})
                            usage = msg.get("usage", {})
                            # "waiting on you" fallback for sessions whose Stop/
                            # UserPromptSubmit heartbeat hook silently misses.
                            _woy = _waiting_on_you_from_line(t, msg.get("stop_reason"))
                            if _woy is not None:
                                stats["waiting_on_you"] = _woy
                            if not stats.get("model") and msg.get("model"):
                                stats["model"] = msg["model"]
                            if not stats.get("cc_version") and obj.get("version"):
                                stats["cc_version"] = obj["version"]
                                _check_new_cc_version(obj["version"])
                            if not stats.get("git_branch") and obj.get("gitBranch"):
                                stats["git_branch"] = obj["gitBranch"]
                            ts = obj.get("timestamp", "")
                            if ts:
                                if not stats.get("first_ts"):
                                    stats["first_ts"] = ts
                                stats["last_ts"] = ts
                            stats["input_tokens"] = stats.get("input_tokens", 0) + usage.get("input_tokens", 0)
                            stats["output_tokens"] = stats.get("output_tokens", 0) + usage.get("output_tokens", 0)
                            stats["cache_write_tokens"] = stats.get("cache_write_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
                            stats["cache_read_tokens"] = stats.get("cache_read_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                            stats["msg_count"] = stats.get("msg_count", 0) + 1

                            # Agent/Task subagent start -- same id scheme as
                            # aoc_hook.py's agent_id_from_hook() (ag_ + md5(tool_use_id)),
                            # so a hook that *does* fire for the same call lands on the
                            # same id and the existing exact-match upsert merges them.
                            for block in msg.get("content", []) or []:
                                if not isinstance(block, dict) or block.get("type") != "tool_use":
                                    continue
                                if block.get("name") not in ("Agent", "Task"):
                                    continue
                                tu_id = block.get("id", "")
                                if not tu_id:
                                    continue
                                inp = block.get("input", {}) or {}
                                desc = inp.get("description") or inp.get("prompt") or ""
                                agent_events.append({
                                    "id": "ag_" + _hashlib.md5(tu_id.encode()).hexdigest()[:10],
                                    "name": (desc[:50] or tu_id),
                                    "icon": "AI",
                                    "description": desc,
                                    "status": "running",
                                    "started_at": _iso_utc_to_local_hms(ts) if ts else _now_ts(),
                                    # Only ever set on the *start* event -- quantifies how
                                    # often Claude Code's hook dispatch actually drops a
                                    # PreToolUse (previously invisible/unmeasurable, since
                                    # the fallback silently fixed it). Not set on completion
                                    # events, so an agent the hook DID catch never gets
                                    # mislabeled just because the transcript scanner also
                                    # (harmlessly, redundantly) saw its completion.
                                    "detected_via": "transcript",
                                })
                                # Tracked persistently (via `stats`, which survives
                                # across scan passes through the cursor) so the
                                # immediate-failure fallback below can still
                                # recognize this id's completion even if it lands
                                # in a later 30s scan pass than the start did.
                                stats.setdefault("agent_tool_use_ids", set()).add(tu_id)

                        elif t == "user":
                            stats["waiting_on_you"] = _waiting_on_you_from_line(t, None)
                            # Agent/Task completion via the SYNCHRONOUS path -- a
                            # foreground Agent call (run_in_background: false)
                            # resolves with a normal tool_result whose sibling
                            # toolUseResult carries full per-model usage, richer
                            # than the async task-notification's flat token total.
                            tur = obj.get("toolUseResult")
                            if isinstance(tur, dict) and "totalTokens" in tur and "resolvedModel" in tur:
                                tu_id = ""
                                for block in (obj.get("message", {}) or {}).get("content", []) or []:
                                    if isinstance(block, dict) and block.get("type") == "tool_result":
                                        tu_id = block.get("tool_use_id", "")
                                        break
                                if tu_id:
                                    usage = tur.get("usage", {}) or {}
                                    au = {
                                        "id": "ag_" + _hashlib.md5(tu_id.encode()).hexdigest()[:10],
                                        "status": "done" if tur.get("status") == "completed" else "error",
                                        "completed_at": _now_ts(),
                                        "tokens_used": tur.get("totalTokens"),
                                        "model": tur.get("resolvedModel", ""),
                                        "input_tokens": usage.get("input_tokens", 0),
                                        "output_tokens": usage.get("output_tokens", 0),
                                        "cache_write_tokens": usage.get("cache_creation_input_tokens", 0),
                                        "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
                                        "_completion_only": True,
                                    }
                                    content = tur.get("content")
                                    if isinstance(content, list) and content and isinstance(content[0], dict):
                                        au["log"] = [content[0].get("text", "")]
                                    agent_events.append(au)
                            else:
                                # Immediate-failure fallback: an Agent/Task call that
                                # errors before ever actually running (e.g. "not in a
                                # git repository" for worktree isolation) never gets a
                                # totalTokens/resolvedModel-shaped toolUseResult -- it's
                                # either a plain error string or a dict missing those
                                # keys. Without this, such an agent stays stuck showing
                                # "running" forever (confirmed live, 2026-07-18: a
                                # worktree-creation failure's toolUseResult was just the
                                # bare string "Error: Cannot create agent worktree...").
                                # Only fires for a tool_use_id we've actually seen as an
                                # Agent/Task start (tracked above), so an unrelated
                                # failed tool call (Bash, Read, etc.) never gets
                                # mistaken for a stuck agent completion.
                                known_ids = stats.get("agent_tool_use_ids") or set()
                                for block in (obj.get("message", {}) or {}).get("content", []) or []:
                                    if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                                        continue
                                    tu_id = block.get("tool_use_id", "")
                                    if not tu_id or tu_id not in known_ids or not block.get("is_error"):
                                        continue
                                    error_text = block.get("content")
                                    if not isinstance(error_text, str):
                                        error_text = str(tur) if tur else "Agent call failed"
                                    agent_events.append({
                                        "id": "ag_" + _hashlib.md5(tu_id.encode()).hexdigest()[:10],
                                        "status": "error",
                                        "completed_at": _now_ts(),
                                        "error_message": error_text[:4000],
                                        "log": [error_text[:80]],
                                        "_completion_only": True,
                                    })

                        # Agent/Task completion -- arrives asynchronously, well after
                        # the tool_use block, as a task-notification block (seen both
                        # as a queue-operation line and mirrored into a "user" message;
                        # checked regardless of `t` for that reason). Regex over the
                        # raw line rather than the parsed structure since the block is
                        # plain text embedded in a content string, not nested JSON.
                        for notif_m in _TASK_NOTIF_RE.finditer(line):
                            notif_body = notif_m.group(1)
                            tags = dict(_TASK_TAG_RE.findall(notif_body))
                            tu_id = tags.get("tool-use-id", "")
                            if not tu_id:
                                continue
                            usage_m = _TASK_USAGE_RE.search(notif_body)
                            au = {
                                "id": "ag_" + _hashlib.md5(tu_id.encode()).hexdigest()[:10],
                                "status": "done" if tags.get("status") == "completed" else "error",
                                "completed_at": _now_ts(),
                                "log": [tags.get("result") or tags.get("summary") or ""],
                                # This same task-notification XML shape is also used for
                                # background Bash/Monitor tool completions, not just
                                # Agent/Task subagents -- only ever update an agent that
                                # a real "start" already created (see the apply loop
                                # below), never create a new card from a completion
                                # alone here (unlike the hook's own late-arrival path,
                                # which trusts its update came from a real agent).
                                "_completion_only": True,
                            }
                            if usage_m:
                                au["tokens_used"] = int(usage_m.group(1))
                            agent_events.append(au)

                    # Recalculate cost
                    model = stats.get("model", "")
                    stats["estimated_cost"] = _calc_cost(
                        stats.get("input_tokens", 0),
                        stats.get("output_tokens", 0),
                        stats.get("cache_write_tokens", 0),
                        stats.get("cache_read_tokens", 0),
                        model
                    )

                    # Save updated cursor
                    with _transcript_cursors_lock:
                        _transcript_cursors[session_id] = {"path": fpath, "offset": new_offset, "stats": stats}

                    project_name = _decode_project_name(encoded_proj)

                    with _status_lock:
                        status = _load_status()
                        sessions = status.setdefault("sessions", {})
                        if sessions.get(session_id, {}).get("dismissed"):
                            continue  # user dismissed this session — don't re-add
                        if session_id not in sessions:
                            sessions[session_id] = {}
                        sess = sessions[session_id]
                        # Only fill base fields if hook data is stale
                        last_seen_epoch = sess.get("last_seen_epoch", 0)
                        if now - last_seen_epoch >= 120:
                            if not sess.get("last_seen_epoch") or sess["last_seen_epoch"] < mtime:
                                sess["last_seen_epoch"] = mtime
                                sess["last_seen"] = _now_ts()
                            sess.setdefault("project", project_name)
                            # Mark active if transcript was modified within last 5
                            # minutes, OR the underlying claude.exe is confirmed still
                            # running -- a transcript can legitimately go quiet for
                            # longer than 5 min mid-turn (a long tool call, thinking)
                            # without the CLI actually having closed.
                            sess["session_active"] = mtime > active_cutoff or _is_claude_pid_alive(sess.get("host_pid"))
                        # Always update stats fields regardless of hook freshness
                        for field in ("model", "cc_version", "git_branch", "input_tokens", "output_tokens",
                                      "cache_read_tokens", "cache_write_tokens", "estimated_cost",
                                      "msg_count", "first_ts", "last_ts"):
                            val = stats.get(field)
                            if val is not None and val != 0 and val != "":
                                sess[field] = val
                        # Not folded into the loop above: "val != 0" is True for a bool,
                        # since bool is an int subclass and False == 0 -- that check would
                        # silently swallow every "False" value forever.
                        if "waiting_on_you" in stats:
                            _accumulate_waiting_time(sess, stats["waiting_on_you"], now)
                            sess["waiting_on_you"] = stats["waiting_on_you"]
                        if ai_title:
                            # Self-heals an already-corrupted stored value, not
                            # just "set if missing" -- the previous version of
                            # this code only ever filled in a MISSING
                            # display_name, so any future genuine corruption
                            # would have been permanent (the auto-un-dismiss
                            # style bug class: a one-time bad write with no
                            # path back to good). Compares against a fresh
                            # read of the transcript (always re-derivable)
                            # rather than checking whether the stored value
                            # merely "looks fixable" via _fix_mojibake, since
                            # some hypothetical corruption (a genuine
                            # unrecoverable U+FFFD from an old errors="replace"
                            # decode, as opposed to a systematic codepage
                            # mismatch) can't be reversed by re-encoding an
                            # already-lossy stored string -- only a fresh
                            # source has the real characters.
                            fixed_title = _fix_mojibake(ai_title)
                            if sess.get("display_name") != fixed_title:
                                sess["display_name"] = fixed_title
                        # B — fix mojibake cwd that may have been stored before the encoding fix
                        if sess.get("cwd"):
                            sess["cwd"] = _fix_mojibake(sess["cwd"])
                        # Apply any Agent/Task starts/completions detected above through
                        # the same upsert path the hook-driven /update handler uses, so
                        # a session whose hook never fires still gets agent cards.
                        known_ids = {a["id"] for a in status.get("agents", [])}
                        for au in agent_events:
                            if au.pop("_completion_only", False) and au["id"] not in known_ids:
                                continue  # not a real Agent/Task call -- see marker comment above
                            _apply_agent_update(status, au, session_id, sess.get("project", project_name), _now_ts())
                            known_ids.add(au["id"])
                        _save_status(status)

                    # A — persist token/cost data to SQLite so analytics are accurate
                    total_tokens = stats.get("input_tokens", 0) + stats.get("output_tokens", 0)
                    estimated_cost = stats.get("estimated_cost", 0.0)
                    if total_tokens > 0 or estimated_cost > 0:
                        first_ts = stats.get("first_ts", "")
                        date_str = first_ts[:10] if len(first_ts) >= 10 else datetime.now().strftime("%Y-%m-%d")
                        started_str = first_ts[11:19] if len(first_ts) >= 19 else ""
                        # Prefer in-memory project (set by hook) over decoded path (may fail for long paths)
                        db_project = sess.get("project", "") or project_name or ""
                        try:
                            with _db_lock:
                                c = _db_conn()
                                c.execute("""
                                    INSERT INTO sessions (id, project, date, started_at, tokens, cost)
                                    VALUES (?, ?, ?, ?, ?, ?)
                                    ON CONFLICT(id) DO UPDATE SET
                                        tokens = excluded.tokens,
                                        cost   = excluded.cost,
                                        project = CASE WHEN project = '' OR project IS NULL
                                                       THEN excluded.project ELSE project END
                                """, (session_id, db_project, date_str, started_str,
                                      total_tokens, estimated_cost))
                                c.commit()
                                c.close()
                        except Exception:
                            pass

        except Exception as e:
            _log_bg_error("_transcript_scanner_worker", e)

        _t.sleep(30)


_threading.Thread(target=_transcript_scanner_worker, daemon=True).start()


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

def _save_status(data):
    tmp = STATUS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATUS_FILE)
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


# ── WebSocket + Terminal ──────────────────────────────────────────────────────
import hashlib as _hashlib, base64 as _b64, struct as _struct

def _ws_key(k):
    return _b64.b64encode(
        _hashlib.sha1((k+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()
    ).decode()

def _ws_recv(sock):
    def rx(n):
        buf=b''
        while len(buf)<n:
            c=sock.recv(n-len(buf))
            if not c: raise ConnectionError
            buf+=c
        return buf
    try:
        h=rx(2); op=h[0]&0xf; msk=bool(h[1]&0x80); ln=h[1]&0x7f
        if ln==126: ln=_struct.unpack('>H',rx(2))[0]
        elif ln==127: ln=_struct.unpack('>Q',rx(8))[0]
        mk=rx(4) if msk else b''; data=rx(ln)
        if msk: data=bytes(b^mk[i%4] for i,b in enumerate(data))
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

HTML = r"""<!DOCTYPE html>
<html lang="sk">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#04060e">
<link rel="apple-touch-icon" href="/icon.svg">
<title>AOC — Agent Operations Center</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

:root {
  --bg:    #060d1f;
  --c:     #00c4e8;
  --c2:    #0090b8;
  /* Bare "R,G,B" triplets (no alpha) so rgba(var(--c-rgb),X) can express
     translucent accent tints at any alpha -- var(--c) alone is a solid
     color and can't be plugged into rgba(). Kept in sync with --c/--c2
     by _applyAccent() whenever the user picks a different swatch. */
  --c-rgb:  0,196,232;
  --c2-rgb: 0,144,184;
  --c3:    rgba(0,72,104,.6);
  --g:     #00e887;
  --r:     #ff3355;
  --o:     #ff8c00;
  --t:     rgba(200,230,255,.88);
  --t2:    rgba(140,190,230,.7);
  --t3:    rgba(110,160,195,.78);  /* was .55 alpha at rgb(90,140,180) = 2.4:1 contrast, failed WCAG AA; this is ~4.6:1 */
  --border: rgba(255,255,255,.08);
  --glass: rgba(10,25,50,.55);
  --specular: rgba(255,255,255,.2);
  --font:  'Inter', system-ui, sans-serif;
  --font2: Consolas, 'Cascadia Code', 'Courier New', monospace;
  --radius: 18px;
}

* { margin:0; padding:0; box-sizing:border-box; }
html, body { height:100%; overflow:hidden; }
body {
  background:
    radial-gradient(ellipse 80% 60% at 15% 10%, rgba(0,120,200,.09) 0%, transparent 55%),
    radial-gradient(ellipse 60% 70% at 85% 90%, rgba(0,80,160,.07) 0%, transparent 55%),
    radial-gradient(ellipse 40% 40% at 50% 50%, rgba(0,60,120,.05) 0%, transparent 70%),
    #060d1f;
  color: var(--t);
  font-family: var(--font);
  font-size: 14px;
  line-height: 1.55;
}

/* subtle noise texture */
body::before {
  content:'';
  position:fixed; inset:0; z-index:0; pointer-events:none;
  background-image:
    linear-gradient(rgba(var(--c-rgb),.015) 1px, transparent 1px),
    linear-gradient(90deg, rgba(var(--c-rgb),.015) 1px, transparent 1px);
  background-size: 64px 64px;
  opacity: .6;
}

.root {
  position: relative; z-index:1;
  display: grid;
  grid-template-columns: 1fr 290px;
  grid-template-rows: 52px 1fr;
  height: 100vh;
  overflow: hidden;
  transition: grid-template-columns .3s ease;
}
.root.panel-collapsed { grid-template-columns: 1fr 0; }

/* ── TOP BAR — Liquid Glass ── */
.topbar {
  grid-column: 1/-1;
  background: rgba(6,14,30,.65);
  backdrop-filter: blur(32px) saturate(180%) brightness(1.02);
  -webkit-backdrop-filter: blur(32px) saturate(180%) brightness(1.02);
  border-bottom: 1px solid rgba(255,255,255,.07);
  display: flex; align-items: center; padding: 0 20px; gap: 16px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.1), 0 4px 32px rgba(0,0,0,.5);
}

.logo-wrap { display:flex; align-items:center; gap:10px; margin-right:6px; }
.logo-mark { width:52px; height:52px; flex-shrink:0; filter:drop-shadow(0 0 12px rgba(var(--c-rgb),.45)); }

.logo-text .t1 {
  font-family: 'Orbitron', var(--font2); font-size:15px; font-weight:900;
  letter-spacing:.28em; color:var(--c);
  text-shadow: 0 0 20px rgba(var(--c-rgb),.7);
  animation: flicker 8s infinite;
}
.logo-text .t2 { font-size:10px; color:var(--t3); letter-spacing:.13em; margin-top:2px; }

@keyframes flicker { 0%,94%,100%{opacity:1} 96%{opacity:.55} }

.divider { width:1px; height:26px; background:var(--border); }

.top-stat { display:flex; flex-direction:column; align-items:center; padding:0 12px; }
.top-stat .val {
  font-family: var(--font2); font-size:18px; font-weight:700;
  color:var(--c); text-shadow:0 0 10px rgba(var(--c-rgb),.4);
}
.top-stat .lbl { font-size:11px; color:var(--t3); letter-spacing:.08em; text-transform:uppercase; margin-top:1px; }

.topbar-right { margin-left:auto; display:flex; align-items:center; gap:12px; }

.status-online { display:flex; align-items:center; gap:6px; font-size:12px; color:var(--g); font-weight:600; letter-spacing:.06em; transition:color .4s; }
.status-online .dot { width:7px; height:7px; border-radius:50%; background:var(--g); box-shadow:0 0 8px var(--g); animation:pulse 2s infinite; transition:background .4s,box-shadow .4s; }
.status-online.offline { color:var(--r); }
.status-online.offline .dot { background:var(--r); box-shadow:0 0 8px var(--r); }
#reconnect-overlay { display:none; position:fixed; inset:0; background:rgba(5,7,15,.7); backdrop-filter:blur(4px); z-index:999; align-items:center; justify-content:center; flex-direction:column; gap:16px; }
#reconnect-overlay.show { display:flex; }
#reconnect-overlay .ro-icon { width:48px; height:48px; border:2px solid var(--r); border-top-color:transparent; border-radius:50%; animation:spin 1s linear infinite; }
@keyframes spin { to { transform:rotate(360deg); } }
#reconnect-overlay .ro-text { font-family:var(--font2); font-size:13px; letter-spacing:.12em; color:var(--r); }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }

.clock { text-align:right; }
.clock-time { font-family:'Orbitron', var(--font2); font-size:15px; color:var(--c); letter-spacing:.08em; }
.clock-date { font-size:11px; color:var(--t3); letter-spacing:.06em; margin-top:1px; }

.btn-reset {
  display:flex; align-items:center; gap:6px;
  padding:6px 14px;
  background: rgba(255,255,255,.06);
  backdrop-filter: blur(8px);
  border: 1px solid rgba(255,255,255,.1);
  border-top: 1px solid rgba(255,255,255,.18);
  border-radius: 10px;
  color:var(--t2);
  font-family:var(--font); font-size:12px; font-weight:600;
  letter-spacing:.06em; cursor:pointer;
  box-shadow: 0 2px 8px rgba(0,0,0,.3), inset 0 1px 0 rgba(255,255,255,.07);
  transition:all .2s;
}
.btn-reset:hover {
  border-color:rgba(var(--c-rgb),.3); color:var(--c);
  background:rgba(var(--c-rgb),.09);
  box-shadow: 0 2px 12px rgba(var(--c-rgb),.15), inset 0 1px 0 rgba(255,255,255,.1);
}
.btn-reset svg { width:11px; height:11px; stroke:currentColor; fill:none; stroke-width:2; }
.btn-mute {
  display:flex; align-items:center; justify-content:center;
  width:32px; height:32px;
  background: rgba(255,255,255,.06);
  backdrop-filter: blur(8px);
  border: 1px solid rgba(255,255,255,.1);
  border-top: 1px solid rgba(255,255,255,.18);
  border-radius: 10px;
  color:var(--t2);
  font-size:14px; cursor:pointer;
  box-shadow: 0 2px 8px rgba(0,0,0,.3), inset 0 1px 0 rgba(255,255,255,.07);
  transition:all .2s;
}
.btn-mute:hover { border-color:rgba(var(--c-rgb),.3); background:rgba(var(--c-rgb),.09); box-shadow:0 2px 12px rgba(var(--c-rgb),.15),inset 0 1px 0 rgba(255,255,255,.1); }
.btn-mute.muted { border-color:rgba(255,51,85,.3); background:rgba(255,51,85,.07); color:var(--r); }

/* ── MAIN ── */
.main { padding:16px; overflow-y:auto; display:flex; flex-direction:column; gap:0; background:transparent; }
#cards-area { display:flex; flex-direction:column; gap:12px; }
#timeline-area { display:none; flex-direction:column; gap:8px; }

/* ── IDLE STATE ── */
.idle {
  display:flex; flex-direction:column; align-items:center; justify-content:center;
  flex:1; min-height:60vh; gap:20px; opacity:.75;
}
.idle-hex {
  width:120px; height:120px;
  display:flex; align-items:center; justify-content:center;
  background: linear-gradient(135deg, rgba(var(--c-rgb),.1) 0%, rgba(0,60,120,.07) 100%);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border: 1px solid rgba(var(--c-rgb),.2);
  border-top: 1px solid rgba(255,255,255,.18);
  clip-path: polygon(25% 0%,75% 0%,100% 50%,75% 100%,25% 100%,0% 50%);
  box-shadow: 0 8px 40px rgba(0,0,0,.3), inset 0 1px 0 rgba(255,255,255,.12);
  animation: hexPulse 3s ease-in-out infinite;
}
@keyframes hexPulse {
  0%,100%{box-shadow:0 8px 32px rgba(0,0,0,.3),inset 0 1px 0 rgba(255,255,255,.1),0 0 20px rgba(var(--c-rgb),.07);}
  50%{box-shadow:0 8px 48px rgba(0,0,0,.4),inset 0 1px 0 rgba(255,255,255,.15),0 0 50px rgba(var(--c-rgb),.18);}
}
.idle-hex svg { width:48px; height:48px; stroke:var(--c); fill:none; stroke-width:1; opacity:.6; }
.idle-title { font-family:var(--font2); font-size:14px; font-weight:700; color:var(--c); letter-spacing:.2em; opacity:.8; }
.idle-sub { font-size:11px; color:var(--t3); letter-spacing:.1em; text-transform:uppercase; }

/* ── AGENT GRID ── */
.agents { display:grid; grid-template-columns:repeat(2,1fr); gap:10px; }
.agents.list-mode { display:flex; flex-direction:column; gap:4px; }
.agent-row { display:flex; align-items:center; gap:10px; padding:8px 12px; border-radius:8px; background:rgba(255,255,255,.03); border:1px solid rgba(255,255,255,.07); transition:background .2s; cursor:default; }
.agent-row:hover { background:rgba(255,255,255,.06); }
.agent-row.running { border-left:2px solid var(--c); }
.agent-row.done    { border-left:2px solid var(--g); }
.agent-row.error   { border-left:2px solid var(--r); }
.ar-unit { font-size:9px; font-family:var(--font2); font-weight:700; letter-spacing:.07em; color:var(--t3); min-width:28px; text-align:center; }
.ar-name { flex:1; font-size:12px; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.ar-pill { font-size:9px; font-family:var(--font2); font-weight:700; letter-spacing:.07em; padding:2px 8px; border-radius:4px; white-space:nowrap; }
.ar-pill.running { color:var(--c); background:rgba(var(--c-rgb),.1); }
.ar-pill.done    { color:var(--g); background:rgba(0,232,135,.08); }
.ar-pill.error   { color:var(--r); background:rgba(255,51,85,.08); }
.ar-prog { width:80px; height:3px; border-radius:999px; background:rgba(255,255,255,.06); flex-shrink:0; overflow:hidden; }
.ar-prog-fill { height:100%; border-radius:999px; transition:width .6s ease; }
.ar-elapsed { font-size:10px; font-family:var(--font2); color:var(--t3); min-width:48px; text-align:right; flex-shrink:0; }
/* List-mode's row dismiss button -- was inline-styled with JS
   onmouseenter/onmouseleave toggling opacity (the only spot in CARDS not
   using the CSS :hover pattern everything else does), 0 default opacity
   (same discoverability problem as the grid-mode corner buttons), and a
   2px hit target. Matches the grid-mode fix: low-but-visible by default,
   real padding for a bigger click target. */
.ar-dismiss { background:none; border:none; color:var(--t3); cursor:pointer; font-size:13px; padding:4px 8px; opacity:.35; transition:opacity .2s,color .2s; flex-shrink:0; }
.agent-row:hover .ar-dismiss { opacity:1; }
.ar-dismiss:hover { color:var(--r); }
.density-btn { padding:3px 8px; border-radius:6px; border:1px solid rgba(255,255,255,.08); background:rgba(255,255,255,.03); color:var(--t3); font-size:10px; font-family:var(--font2); font-weight:700; letter-spacing:.06em; cursor:pointer; transition:all .2s; margin-left:auto; }
.density-btn:hover,.density-btn.active { background:rgba(var(--c-rgb),.1); border-color:rgba(var(--c-rgb),.3); color:var(--c); }
.search-wrap { display:flex; align-items:center; gap:6px; padding:0 0 6px; }
.search-input { flex:1; background:rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.1); border-radius:8px; padding:5px 12px 5px 30px; color:var(--t1); font-size:12px; font-family:var(--font); outline:none; transition:border-color .2s; }
.search-input:focus { border-color:rgba(var(--c-rgb),.4); background:rgba(var(--c-rgb),.04); }
.search-input::placeholder { color:var(--t3); }
.search-wrap svg { position:absolute; left:10px; top:50%; transform:translateY(-50%); width:13px; height:13px; color:var(--t3); pointer-events:none; }
.search-wrap { position:relative; }
.search-clear { position:absolute; right:10px; top:50%; transform:translateY(-50%); background:none; border:none; cursor:pointer; color:var(--t3); font-size:14px; line-height:1; padding:0; display:none; }
.search-clear.show { display:block; }

/* ── LIQUID GLASS CARD ── */
.card {
  background: linear-gradient(
    145deg,
    rgba(255,255,255,.07) 0%,
    rgba(10,26,58,.62) 35%,
    rgba(6,18,42,.68) 100%
  );
  backdrop-filter: blur(24px) saturate(160%) brightness(1.05);
  -webkit-backdrop-filter: blur(24px) saturate(160%) brightness(1.05);
  border: 1px solid rgba(255,255,255,.1);
  border-top: 1px solid rgba(255,255,255,.22);
  border-radius: var(--radius);
  position: relative; overflow:hidden;
  padding:16px;
  box-shadow:
    0 8px 32px rgba(0,0,0,.45),
    0 2px 8px rgba(0,0,0,.25),
    inset 0 1px 0 rgba(255,255,255,.13),
    inset 0 -1px 0 rgba(0,0,0,.12);
  transition: border-color .3s, box-shadow .3s, transform .15s;
}
/* Card density (Settings > Appearance) hangs off the stable #agents container,
   not individual .card elements — renderAgents() fully replaces .card nodes via
   innerHTML on every status push, which would wipe a per-card inline style
   moments after it was applied. #agents itself is only ever created once. */
#agents.density-compact .card { padding:10px 12px; }
#agents.density-comfortable .card { padding:20px 18px; }
/* specular top highlight */
.card::before {
  content:'';
  position:absolute; top:0; left:0; right:0; height:1px;
  background: linear-gradient(90deg, transparent 5%, rgba(255,255,255,.28) 50%, transparent 95%);
  pointer-events:none;
}
/* left edge inner reflection */
.card::after {
  content:'';
  position:absolute; top:12px; left:0; bottom:12px; width:1px;
  background: linear-gradient(180deg, transparent, rgba(255,255,255,.1) 50%, transparent);
  pointer-events:none;
}

@keyframes cardPulse {
  0%,100% { box-shadow: 0 8px 40px rgba(0,0,0,.45), 0 0 20px rgba(var(--c-rgb),.06), inset 0 1px 0 rgba(0,220,255,.22), inset 0 -1px 0 rgba(0,0,0,.12); }
  50%      { box-shadow: 0 8px 48px rgba(0,0,0,.5),  0 0 48px rgba(var(--c-rgb),.2),  inset 0 1px 0 rgba(0,220,255,.36), inset 0 -1px 0 rgba(0,0,0,.12); }
}
.card.running {
  border-color: rgba(var(--c-rgb),.32);
  border-top-color: rgba(0,220,255,.5);
  animation: cardPulse 2.5s ease-in-out infinite;
}
.card.done {
  border-color: rgba(0,232,135,.25);
  border-top-color: rgba(0,255,150,.38);
  box-shadow:
    0 8px 32px rgba(0,0,0,.4),
    0 0 24px rgba(0,232,135,.05),
    inset 0 1px 0 rgba(0,232,135,.2),
    inset 0 -1px 0 rgba(0,0,0,.1);
}
.card.error {
  border-color: rgba(255,51,85,.3);
  border-top-color: rgba(255,80,110,.42);
  box-shadow:
    0 8px 32px rgba(0,0,0,.4),
    0 0 24px rgba(255,51,85,.06),
    inset 0 1px 0 rgba(255,100,120,.16),
    inset 0 -1px 0 rgba(0,0,0,.1);
}
.card.waiting {
  border-color: rgba(255,140,0,.26);
  border-top-color: rgba(255,160,30,.38);
  box-shadow:
    0 8px 32px rgba(0,0,0,.4),
    0 0 24px rgba(255,140,0,.05),
    inset 0 1px 0 rgba(255,160,30,.14),
    inset 0 -1px 0 rgba(0,0,0,.1);
}

/* sweep on running */
.sweep {
  position:absolute; inset:0; pointer-events:none;
  background:linear-gradient(90deg, transparent, rgba(var(--c-rgb),.05) 50%, transparent);
  animation:sweep 3s ease-in-out infinite;
}
@keyframes sweep { 0%{transform:translateX(-100%)} 100%{transform:translateX(200%)} }
.card.agent-fading { pointer-events:none; transition:opacity .5s; }

/* card header */
.card-head { display:flex; align-items:flex-start; gap:10px; margin-bottom:10px; flex-wrap:wrap; row-gap:6px; }
.unit-badge {
  padding:3px 10px;
  font-family:var(--font2); font-size:11px; font-weight:700; letter-spacing:.08em;
  border:1px solid; border-radius:6px; flex-shrink:0; margin-top:2px;
  backdrop-filter: blur(8px);
}
.card.running .unit-badge { border-color:var(--c); color:var(--c); background:rgba(var(--c-rgb),.08); }
.card.done    .unit-badge { border-color:var(--g); color:var(--g); background:rgba(0,232,135,.06); }
.card.error   .unit-badge { border-color:var(--r); color:var(--r); background:rgba(255,51,85,.06); }
.card.waiting .unit-badge { border-color:var(--o); color:var(--o); background:rgba(255,140,0,.06); }

.card-meta { flex:1; min-width:0; }
.card-name { font-family:var(--font); font-size:13px; font-weight:700; letter-spacing:.03em; margin-bottom:4px; }
.card.running .card-name { color:var(--c); }
.card.done    .card-name { color:var(--g); }
.card.error   .card-name { color:var(--r); }
.card.waiting .card-name { color:var(--o); }
.card-desc { font-size:12px; color:var(--t2); line-height:1.5; }

.status-pill {
  display:flex; align-items:center; gap:5px;
  padding:3px 10px; font-size:10px; font-weight:700;
  letter-spacing:.08em; border:1px solid; border-radius:999px;
  flex-shrink:0; font-family:var(--font2);
  backdrop-filter: blur(8px);
}
.status-pill.running { border-color:var(--c); color:var(--c); }
.status-pill.done    { border-color:var(--g); color:var(--g); }
.status-pill.error   { border-color:var(--r); color:var(--r); }
.status-pill.waiting { border-color:var(--o); color:var(--o); }
.status-pill .sdot { width:5px; height:5px; border-radius:50%; }
.status-pill.running .sdot { background:var(--c); animation:pulse 1.5s infinite; }
.status-pill.done    .sdot { background:var(--g); }
.status-pill.error   .sdot { background:var(--r); }
.status-pill.waiting .sdot { background:var(--o); animation:pulse 1.5s infinite; }

/* tasks */
.tasks { display:flex; flex-direction:column; gap:3px; margin:10px 0 8px; }
.task {
  display:flex; align-items:baseline; gap:7px;
  padding:5px 10px; font-size:13px;
  border-left:2px solid; border-radius:0 6px 6px 0;
  line-height:1.4; transition:all .25s;
}
.task.p { border-color:rgba(var(--c-rgb),.15); color:rgba(140,185,225,.55); background:rgba(255,255,255,.02); }
.task.d { border-color:rgba(0,232,135,.4); color:rgba(140,235,175,.75); background:rgba(0,232,135,.04); }
.task.d:hover { background:rgba(0,232,135,.08); }
.task-chk { font-size:13px; width:16px; flex-shrink:0; }
.task-lbl { flex:1; font-weight:400; }

/* progress row */
.prog-row { display:flex; align-items:center; gap:10px; margin-top:6px; }
.arc-wrap { position:relative; width:40px; height:40px; flex-shrink:0; display:flex; align-items:center; justify-content:center; }
.arc-wrap svg { transform:rotate(-90deg); position:absolute; top:0; left:0; }
.arc-pct {
  position:relative; z-index:1; display:flex; align-items:center; justify-content:center;
  font-family:var(--font2); font-size:10px; font-weight:700;
}
.card.running .arc-pct { color:var(--c); }
.card.done    .arc-pct { color:var(--g); }
.card.error   .arc-pct { color:var(--r); }
.card.waiting .arc-pct { color:var(--o); }

.prog-info { flex:1; }
.prog-track { height:3px; background:rgba(255,255,255,.05); border-radius:999px; position:relative; margin-bottom:4px; overflow:hidden; }
.prog-fill { height:3px; border-radius:999px; position:absolute; left:0; top:0; transition:width .6s ease; }
.card.running .prog-fill { background:linear-gradient(90deg,var(--c2),var(--c)); box-shadow:0 0 6px var(--c); }
.card.done    .prog-fill { background:linear-gradient(90deg,#00aa66,var(--g)); box-shadow:0 0 6px var(--g); }
.card.error   .prog-fill { background:var(--r); }
.card.waiting .prog-fill { background:var(--o); }

.prog-lbl { display:flex; justify-content:space-between; font-size:11px; color:var(--t3); }

/* ── RIGHT PANEL ── */
.right {
  border-left: 1px solid rgba(255,255,255,.06);
  background: rgba(4,10,22,.72);
  backdrop-filter: blur(28px) saturate(150%);
  -webkit-backdrop-filter: blur(28px) saturate(150%);
  display:flex; flex-direction:column; overflow:hidden;
  box-shadow: inset 1px 0 0 rgba(255,255,255,.04);
  position:relative; min-width:0;
}
.panel-toggle {
  position:absolute; top:50%; left:-14px; transform:translateY(-50%);
  z-index:10; width:14px; height:40px;
  background:rgba(4,10,22,.85);
  border:1px solid rgba(255,255,255,.08); border-right:none;
  border-radius:6px 0 0 6px;
  display:flex; align-items:center; justify-content:center;
  cursor:pointer; color:var(--t3); font-size:9px; transition:all .2s;
}
.panel-toggle:hover { background:rgba(var(--c-rgb),.12); color:var(--c); border-color:rgba(var(--c-rgb),.25); }
.root.panel-collapsed .panel-toggle { left:-18px; border-radius:6px; border-right:1px solid rgba(255,255,255,.08); }

.live-badge { display:flex; align-items:center; gap:5px; font-size:11px; color:var(--g); font-weight:600; }
.live-badge .dot { width:5px; height:5px; border-radius:50%; background:var(--g); animation:pulse 2s infinite; }

.log-area { flex:1; overflow-y:auto; padding:6px 12px; }
.le { display:flex; gap:8px; padding:3px 0; line-height:1.55; font-size:12px; }
.le-t { color:var(--t3); flex-shrink:0; font-variant-numeric:tabular-nums; }
.le-m { flex:1; }
.le-m.info    { color:rgba(var(--c-rgb),.75); }
.le-m.success { color:var(--g); }
.le-m.warn    { color:var(--o); }
.le-m.error   { color:var(--r); }
.le-tag { font-family:var(--font2); font-size:10px; font-weight:700; letter-spacing:.06em; color:rgba(var(--c-rgb),.6); border:1px solid rgba(var(--c-rgb),.25); border-radius:3px; padding:0 4px; flex-shrink:0; line-height:1.7; background:none; }
button.le-tag { cursor:pointer; }

.fe { display:flex; align-items:center; gap:7px; padding:5px 8px; font-size:12px; border-left:2px solid; border-radius:0 6px 6px 0; transition:background .2s; }
.fe.new     { border-color:var(--g); color:#60c890; background:rgba(0,232,135,.03); }
.fe.changed { border-color:var(--c); color:var(--t2); background:rgba(var(--c-rgb),.03); }
.fe.new:hover     { background:rgba(0,232,135,.07); }
.fe.changed:hover { background:rgba(var(--c-rgb),.07); }
.fe-badge {
  font-size:10px; padding:1px 5px; font-weight:700; letter-spacing:.06em;
  border:1px solid; border-radius:4px; flex-shrink:0;
  backdrop-filter: blur(6px);
}
.fe.new     .fe-badge { border-color:var(--g); color:var(--g); }
.fe.changed .fe-badge { border-color:var(--c); color:var(--c); }
.fe-name { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.fe-lines { margin-left:auto; color:var(--t3); font-size:11px; flex-shrink:0; }
.card.pinned { border-top-color:rgba(255,200,60,.6) !important; box-shadow:0 8px 32px rgba(0,0,0,.45),0 0 20px rgba(255,200,60,.08),inset 0 1px 0 rgba(255,200,60,.25),inset 0 -1px 0 rgba(0,0,0,.12) !important; }
/* Default opacity is a low-but-visible .35, not 0 -- fully invisible-until-
   hover made these 4 corner buttons undiscoverable without trial-and-error
   mousing over every card, and doesn't work at all on touch (no hover
   state), so the buttons were effectively unreachable there too. */
.card-pin-btn { position:absolute; top:8px; right:56px; width:22px; height:22px; border-radius:50%; border:1px solid rgba(255,255,255,.1); background:rgba(255,255,255,.04); color:var(--t3); font-size:11px; line-height:1; cursor:pointer; display:flex; align-items:center; justify-content:center; opacity:.35; transition:opacity .2s,color .2s,background .2s; }
.card:hover .card-pin-btn { opacity:1; }
.card.pinned .card-pin-btn { opacity:1; color:rgba(255,200,60,.9); background:rgba(255,200,60,.1); border-color:rgba(255,200,60,.3); }
.card.collapsed { overflow:hidden; cursor:pointer; }
.card.collapsed .card-body { display:none; }
.card.collapsed .card-head { margin-bottom:0; }
.card.collapsed .sweep { display:none; }
.card-collapse-btn { position:absolute; top:8px; right:31px; width:22px; height:22px; border-radius:50%; border:1px solid rgba(255,255,255,.1); background:rgba(255,255,255,.04); color:var(--t3); font-size:10px; line-height:1; cursor:pointer; display:flex; align-items:center; justify-content:center; opacity:.35; transition:opacity .2s,transform .2s; }
.card:hover .card-collapse-btn { opacity:1; }
.card.collapsed .card-collapse-btn { opacity:.6; transform:rotate(180deg); }
.card-dismiss { position:absolute; top:8px; right:6px; width:22px; height:22px; border-radius:50%; border:1px solid rgba(255,255,255,.1); background:rgba(255,255,255,.04); color:var(--t3); font-size:12px; line-height:1; cursor:pointer; display:flex; align-items:center; justify-content:center; opacity:.35; transition:opacity .2s,background .2s; }
.card:hover .card-dismiss { opacity:1; }
.card-dismiss:hover { background:rgba(255,51,85,.2); border-color:rgba(255,51,85,.4); color:var(--r); }
.card { position:relative; }
.stuck-badge { display:inline-flex; align-items:center; gap:4px; font-size:10px; font-family:var(--font2); font-weight:700; letter-spacing:.07em; color:var(--o); padding:2px 8px; border-radius:4px; background:rgba(255,140,0,.08); border:1px solid rgba(255,140,0,.3); margin-top:5px; animation:stuckPulse 2s infinite; }
@keyframes stuckPulse { 0%,100%{opacity:1;border-color:rgba(255,140,0,.3)} 50%{opacity:.6;border-color:rgba(255,140,0,.6)} }
.burn-badge { display:inline-flex; align-items:center; gap:4px; font-size:10px; font-family:var(--font2); font-weight:700; letter-spacing:.07em; color:var(--r); padding:2px 8px; border-radius:4px; background:rgba(204,26,53,.08); border:1px solid rgba(204,26,53,.3); margin-top:5px; animation:stuckPulse 2s infinite; }

/* ── micro-animations ── */
@keyframes cardEnter {
  from { opacity:0; transform:translateY(18px) scale(.97); }
  to   { opacity:1; transform:translateY(0)    scale(1);  }
}
.card.card-enter { animation: cardEnter .38s cubic-bezier(.23,1,.32,1) both; }

@keyframes taskPop {
  0%   { transform:scale(1); }
  40%  { transform:scale(1.55); color:var(--g); }
  100% { transform:scale(1); }
}
.task-chk-pop { animation: taskPop .35s cubic-bezier(.34,1.56,.64,1) both; }

@keyframes statFlip {
  0%   { transform:translateY(0);    opacity:1; }
  40%  { transform:translateY(-8px); opacity:0; }
  60%  { transform:translateY(8px);  opacity:0; }
  100% { transform:translateY(0);    opacity:1; }
}
.stat-flip { animation: statFlip .32s ease both; }
.log-toggle { display:flex; align-items:center; gap:5px; margin-top:8px; cursor:pointer; padding:4px 0; border-top:1px solid rgba(255,255,255,.05); color:var(--t3); font-size:10px; font-family:var(--font2); letter-spacing:.07em; user-select:none; }
.log-toggle:hover { color:var(--t2); }
.log-toggle svg { width:10px; height:10px; transition:transform .2s; flex-shrink:0; }
.log-toggle.open svg { transform:rotate(90deg); }
.log-panel { display:none; margin-top:4px; padding:6px 8px; border-radius:7px; background:rgba(0,0,0,.25); border:1px solid rgba(255,255,255,.05); max-height:140px; overflow-y:auto; }
.log-panel.open { display:block; }
.log-entry { font-size:10px; font-family:var(--font2); color:var(--t2); line-height:1.6; padding:1px 0; border-bottom:1px solid rgba(255,255,255,.03); white-space:pre-wrap; word-break:break-all; }
.log-entry:last-child { border-bottom:none; }

/* ── AUDIT LOG PANEL ── */
.audit-list { overflow-y:auto; padding:5px 12px; display:flex; flex-direction:column; gap:1px; }
.al { font-size:11px; color:var(--t2); font-family:var(--font2); white-space:pre; padding:2px 4px; border-radius:3px; line-height:1.7; }
.al.SESSION_START  { color:var(--g); }
.al.SESSION_RESET  { color:var(--o); }
.al.AGENT_START    { color:var(--c); }
.al.TASK_COMPLETE  { color:#80e8b8; }
.al.AGENT_DONE     { color:var(--g); }
.al.POLL           { color:var(--t3); }
.audit-files { padding:5px 12px; display:flex; flex-direction:column; gap:3px; }
.audit-file-link { font-size:9px; color:var(--t2); text-decoration:none; padding:2px 0; }
.audit-file-link:hover { color:var(--c); }
.btn-dl {
  display:inline-flex; align-items:center; gap:4px;
  padding:5px 12px; margin:5px 12px 7px;
  background: rgba(var(--c-rgb),.07);
  backdrop-filter: blur(8px);
  border: 1px solid rgba(var(--c-rgb),.18);
  border-top: 1px solid rgba(255,255,255,.12);
  border-radius: 8px;
  color:var(--t2); font-size:11px; font-weight:600; letter-spacing:.06em;
  cursor:pointer; text-decoration:none; font-family:var(--font);
  box-shadow: 0 2px 6px rgba(0,0,0,.2), inset 0 1px 0 rgba(255,255,255,.07);
  transition: all .2s;
}
.btn-dl:hover {
  border-color:rgba(var(--c-rgb),.4); color:var(--c);
  background:rgba(var(--c-rgb),.12);
  box-shadow: 0 2px 12px rgba(var(--c-rgb),.15), inset 0 1px 0 rgba(255,255,255,.1);
}

/* ── HISTORY VIEW ── */
#history-area { overflow-y:auto;padding:20px 24px;align-items:center; }
.hist-toolbar { display:flex;align-items:center;gap:10px;padding:12px 16px 8px;border-bottom:1px solid rgba(255,255,255,.05);flex-shrink:0; }
.hist-title { font-family:var(--font2);font-size:11px;letter-spacing:.14em;color:var(--c);font-weight:700; }
.hist-body { flex:1;overflow-y:auto;padding:14px 18px;display:flex;flex-direction:column;gap:8px; }
.hist-section { font-family:var(--font2);font-size:9px;letter-spacing:.14em;color:var(--t3);margin:8px 0 4px;text-transform:uppercase; }
.hist-row { display:flex;align-items:center;gap:10px;padding:10px 14px;border-radius:11px;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.06);cursor:pointer;transition:background .2s,border-color .2s; }
.hist-row:hover { background:rgba(var(--c-rgb),.06);border-color:rgba(var(--c-rgb),.2); }
.hist-row .hr-date { font-family:var(--font2);font-size:10px;color:var(--t3);min-width:82px;flex-shrink:0; }
.hist-row .hr-project { font-size:12px;font-weight:600;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap; }
.hist-row .hr-chips { display:flex;gap:5px;flex-shrink:0;flex-wrap:wrap;justify-content:flex-end; }
.hist-chip { font-family:var(--font2);font-size:9px;padding:1px 7px;border-radius:4px;border:1px solid;letter-spacing:.05em; }
.hist-chip.ok   { border-color:rgba(0,232,135,.3);color:rgba(0,232,135,.85);background:rgba(0,232,135,.06); }
.hist-chip.err  { border-color:rgba(255,51,85,.3);color:rgba(255,80,100,.85);background:rgba(255,51,85,.06); }
.hist-chip.cost { border-color:rgba(var(--c-rgb),.25);color:var(--c);background:rgba(var(--c-rgb),.06); }
.hist-chip.tok  { border-color:rgba(255,255,255,.1);color:var(--t3);background:rgba(255,255,255,.03); }
.hist-analytics { display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;margin-bottom:4px; }
.hist-kpi { padding:12px 14px;border-radius:11px;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.06);display:flex;flex-direction:column;gap:3px; }
.hist-kpi .hk-val { font-family:var(--font2);font-size:22px;font-weight:800;color:var(--c);line-height:1; }
.hist-kpi .hk-lbl { font-size:10px;color:var(--t3);letter-spacing:.07em; }
.hist-bar-wrap { padding:10px 14px;border-radius:11px;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.06);margin-bottom:4px; }
.hist-bar-row { display:flex;align-items:center;gap:8px;margin-bottom:5px; }
.hist-bar-lbl { font-family:var(--font2);font-size:10px;color:var(--t2);min-width:70px;flex-shrink:0; }
.hist-bar-track { flex:1;height:8px;background:rgba(255,255,255,.05);border-radius:4px;overflow:hidden; }
.hist-bar-fill { height:100%;border-radius:4px;transition:width .6s ease; }
.hist-bar-val { font-family:var(--font2);font-size:10px;color:var(--t3);min-width:54px;text-align:right;flex-shrink:0; }
body.light .hist-row { background:rgba(0,0,0,.025);border-color:rgba(0,0,0,.07); }
body.light .hist-row:hover { background:rgba(0,120,180,.06);border-color:rgba(0,120,180,.2); }
body.light .hist-kpi,.hist-bar-wrap { background:rgba(0,0,0,.025);border-color:rgba(0,0,0,.07); }
.trend-wrap { padding:10px 14px 4px;border-radius:11px;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.06);margin-bottom:8px; }
.trend-title { font-family:var(--font2);font-size:9px;letter-spacing:.12em;color:var(--t3);text-transform:uppercase;margin-bottom:2px; }
.trend-svg { width:100%;height:64px;display:block;overflow:visible; }
.trend-hover-line { stroke:var(--t3);stroke-width:1;stroke-dasharray:2,2;pointer-events:none; }
.trend-hover-dot { pointer-events:none; }
.trend-tip { position:absolute;pointer-events:none;font-family:var(--font2);font-size:10px;padding:3px 7px;border-radius:6px;background:rgba(10,20,30,.92);color:#fff;white-space:nowrap;transform:translate(-50%,-130%);display:none;z-index:5;border:1px solid rgba(255,255,255,.12); }
body.light .trend-wrap { background:rgba(0,0,0,.025);border-color:rgba(0,0,0,.07); }
body.light .trend-tip { background:rgba(255,255,255,.96);color:#0a1420;border-color:rgba(0,0,0,.12); }

/* ── VIEW TOGGLE ── */
.view-toggle { display:flex; align-items:center; gap:6px; padding:10px 16px 4px; flex-shrink:0; flex-wrap:wrap; }
.vtbtn {
  padding:5px 16px; border-radius:8px; border:1px solid; cursor:pointer;
  font-size:11px; font-family:var(--font); font-weight:600; letter-spacing:.06em; transition:all .2s;
  background:rgba(255,255,255,.03); border-color:rgba(255,255,255,.08); color:var(--t3);
}
.vtbtn.active { background:rgba(var(--c-rgb),.1); border-color:rgba(var(--c-rgb),.38); color:var(--c); }
.vtbtn:hover:not(.active) { background:rgba(255,255,255,.06); color:var(--t2); }
.vtbtn.term-btn { border-color:rgba(0,232,135,.2); color:rgba(0,232,135,.6); }
.vtbtn.term-btn.active { background:rgba(0,232,135,.1); border-color:rgba(0,232,135,.4); color:var(--g); }
/* Thin divider between the toolbar's 3 semantic groups (live agent
   visualizations / history / self-diagnostic+utility tools) -- the 9
   view buttons used to render as one undifferentiated row with no visual
   cue that they're not all the same kind of thing. A subtle separator
   rather than a dropdown/collapsed group, since hiding any of these
   behind an extra click would cut against this app's own "everything
   visible, fast keyboard access" design language (command palette,
   single-letter hotkeys). */
.vt-sep { width:1px; align-self:stretch; margin:2px 2px; background:rgba(255,255,255,.08); flex-shrink:0; }
body.light .vt-sep { background:rgba(0,0,0,.1); }

/* ── TERMINAL VIEW ── */
#term-area { display:none; flex:1; flex-direction:column; min-height:0; background:#0d1117; border-radius:12px; overflow:hidden; border:1px solid rgba(255,255,255,.07); }
#term-toolbar { display:flex; align-items:center; gap:8px; padding:6px 12px; background:rgba(0,0,0,.4); border-bottom:1px solid rgba(255,255,255,.06); flex-shrink:0; }
.tt-title { font-family:var(--font2); font-size:10px; letter-spacing:.14em; color:var(--g); font-weight:700; }
.tt-git { font-family:var(--font2); font-size:9px; color:var(--t3); letter-spacing:.05em; padding:2px 8px; border-radius:4px; border:1px solid rgba(255,255,255,.07); background:rgba(255,255,255,.03); }
.tt-btn { font-size:9px !important; padding:2px 9px !important; }
.tt-claude { border-color:rgba(var(--c-rgb),.3) !important; color:var(--c) !important; }
.tt-claude:hover { background:rgba(var(--c-rgb),.1) !important; }
#term-tabs { display:flex; gap:4px; padding:5px 10px; border-bottom:1px solid rgba(255,255,255,.05); flex-shrink:0; overflow-x:auto; min-height:32px; align-items:center; }
#term-tabs::-webkit-scrollbar { height:3px; } #term-tabs::-webkit-scrollbar-thumb { background:rgba(255,255,255,.1); border-radius:3px; }
.t-tab { display:flex; align-items:center; gap:5px; padding:3px 8px 3px 10px; border-radius:6px; cursor:pointer; border:1px solid rgba(255,255,255,.07); background:rgba(255,255,255,.03); font-family:var(--font2); font-size:10px; color:var(--t3); letter-spacing:.04em; flex-shrink:0; transition:all .15s; white-space:nowrap; }
.t-tab:hover { background:rgba(255,255,255,.07); border-color:rgba(255,255,255,.14); color:var(--t2); }
.t-tab.active { background:rgba(var(--c-rgb),.08); border-color:rgba(var(--c-rgb),.3); color:var(--c); }
.t-tab.claude-tab.active { border-color:rgba(0,232,135,.3); color:var(--g); background:rgba(0,232,135,.07); }
.t-tab .t-close { background:none; border:none; cursor:pointer; color:currentColor; font-size:13px; padding:0 0 0 2px; opacity:.45; line-height:1; transition:opacity .15s; margin-left:1px; }
.t-tab .t-close:hover { opacity:1; }
.t-badge { background:rgba(255,160,0,.9); color:#000; border-radius:8px; font-size:8px; padding:1px 5px; min-width:14px; text-align:center; font-weight:700; }
.t-tab-add { font-size:9px; font-family:var(--font2); background:none; border:1px dashed rgba(255,255,255,.12); border-radius:6px; color:var(--t3); padding:2px 8px; cursor:pointer; flex-shrink:0; transition:all .15s; }
.t-tab-add:hover { background:rgba(255,255,255,.06); border-color:rgba(255,255,255,.25); color:var(--t2); }
.t-grid-btn { font-size:11px; font-family:var(--font2); background:none; border:1px solid rgba(255,255,255,.1); border-radius:6px; color:var(--t3); padding:2px 7px; cursor:pointer; flex-shrink:0; transition:all .15s; margin-left:4px; }
.t-grid-btn:hover { background:rgba(255,255,255,.06); color:var(--t2); }
.t-grid-btn.active { border-color:rgba(var(--c-rgb),.3); color:var(--c); background:rgba(var(--c-rgb),.07); }
#xterm-area { flex:1; min-height:0; display:flex; flex-direction:column; }
#xterm-area.grid-mode { display:grid; grid-template-columns:repeat(3,1fr); grid-template-rows:repeat(3,1fr); gap:4px; }
.tg-cell { display:flex; flex-direction:column; overflow:hidden; }
.tg-cell.grid-cell { border:1px solid rgba(255,255,255,.07); border-radius:8px; }
.tg-cell.grid-cell.tg-focused { border-color:rgba(var(--c-rgb),.4); }
.tg-cell.grid-cell.tg-claude { border-color:rgba(0,232,135,.15); }
.tg-cell.grid-cell.tg-claude.tg-focused { border-color:rgba(0,232,135,.4); }
.tg-hdr { display:none; align-items:center; padding:2px 6px; background:rgba(0,0,0,.3); border-bottom:1px solid rgba(255,255,255,.05); font-family:var(--font2); font-size:9px; color:var(--t3); flex-shrink:0; gap:4px; letter-spacing:.06em; }
.tg-hdr.claude-hdr { color:var(--g); }
.tg-title { flex:1; cursor:default; user-select:none; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.tg-inner { flex:1; min-height:0; display:flex; padding:4px 6px; }
.main.term-mode { overflow:hidden; padding:8px; }

/* ── KPI BAR ── */
.kpi-bar { display:flex; align-items:center; gap:0; padding:6px 16px 8px; flex-shrink:0; border-bottom:1px solid rgba(255,255,255,.04); }
.kpi-card { display:flex; flex-direction:column; align-items:center; gap:2px; padding:4px 14px; min-width:70px; }
.kpi-val { font-family:var(--font2); font-size:18px; font-weight:800; color:var(--c); line-height:1; letter-spacing:-.01em; }
.kpi-lbl { font-family:var(--font2); font-size:8px; color:var(--t3); letter-spacing:.1em; white-space:nowrap; }
.kpi-sep { width:1px; height:28px; background:rgba(255,255,255,.07); margin:0 6px; flex-shrink:0; }
.kpi-val.green { color:rgba(0,232,135,.9); }
.kpi-val.red   { color:var(--r); }
body.light .kpi-bar { border-bottom-color:rgba(0,0,0,.06); }
body.light .kpi-sep { background:rgba(0,0,0,.08); }
.kpi-card-cost { min-width:82px; padding:2px 10px; }
.cost-gauge-svg { display:block; width:70px; height:34px; }
@keyframes gaugeFlash { 0%{filter:drop-shadow(0 0 5px rgba(0,210,130,.85));} 100%{filter:none;} }
.cost-gauge-svg.gflash { animation:gaugeFlash .7s ease-out; }

/* ── STATUS FILTER BAR ── */
.stab { padding:4px 12px; border-radius:7px; border:1px solid rgba(255,255,255,.08); cursor:pointer; font-size:10px; font-family:var(--font2); font-weight:700; letter-spacing:.07em; transition:all .2s; background:rgba(255,255,255,.03); color:var(--t3); }
.stab.active { background:rgba(120,80,255,.12); border-color:rgba(120,80,255,.4); color:#a78bfa; }
.stab:hover:not(.active) { background:rgba(255,255,255,.07); color:var(--t2); }
.sfbtn { padding:4px 12px; border-radius:7px; border:1px solid rgba(255,255,255,.08); cursor:pointer; font-size:10px; font-family:var(--font2); font-weight:700; letter-spacing:.08em; transition:all .2s; background:rgba(255,255,255,.03); color:var(--t3); }
.sfbtn.active { background:rgba(var(--c-rgb),.1); border-color:rgba(var(--c-rgb),.35); color:var(--c); }
.sfbtn.active.running { background:rgba(var(--c-rgb),.1); border-color:rgba(var(--c-rgb),.35); color:var(--c); }
.sfbtn.active.done { background:rgba(0,232,135,.08); border-color:rgba(0,232,135,.35); color:var(--g); }
.sfbtn.active.error { background:rgba(255,51,85,.08); border-color:rgba(255,51,85,.35); color:var(--r); }
.sfbtn:hover:not(.active) { background:rgba(255,255,255,.07); color:var(--t2); }

/* ── TIMELINE VIEW ── */
#timeline-area { display:none; padding:16px; overflow-y:auto; flex:1; flex-direction:column; min-height:0; }
.tl-row { display:flex; align-items:center; gap:10px; margin-bottom:10px; }
.tl-name { width:140px; font-size:11px; font-family:var(--font); letter-spacing:.02em; text-align:right; flex-shrink:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.tl-track { flex:1; position:relative; height:22px; }
.tl-toolbar { display:flex;align-items:center;gap:8px;margin-bottom:10px;font-family:var(--font2);font-size:10px; }
.tl-zoom-lbl { min-width:28px;text-align:center;font-size:12px;font-weight:700;color:var(--c);font-family:var(--font2); }
.tl-name.clickable { cursor:pointer; }
.tl-name.clickable:hover { text-decoration:underline;text-decoration-style:dotted; }
.tl-bg { position:absolute; top:10px; left:0; right:0; height:1px; background:rgba(255,255,255,.04); }
.tl-bar { position:absolute; top:3px; height:16px; border-radius:4px; min-width:3px; transition:all .5s; }
.tl-elapsed { width:52px; font-size:11px; color:var(--t3); font-family:var(--font2); flex-shrink:0; }

/* ── DIFF OVERLAY ── */
.diff-overlay {
  position:fixed; inset:0; z-index:200; background:rgba(2,6,16,.88);
  backdrop-filter:blur(10px); display:flex; align-items:center; justify-content:center;
}
.diff-box {
  background:linear-gradient(145deg,rgba(255,255,255,.06) 0%,rgba(6,16,36,.92) 30%,rgba(4,12,28,.95) 100%);
  border:1px solid rgba(255,255,255,.1); border-top:1px solid rgba(255,255,255,.2);
  border-radius:var(--radius); width:80vw; max-height:75vh;
  display:flex; flex-direction:column; overflow:hidden;
  box-shadow:0 32px 80px rgba(0,0,0,.75), inset 0 1px 0 rgba(255,255,255,.12);
}
.diff-hdr {
  padding:11px 18px; border-bottom:1px solid rgba(255,255,255,.06);
  background:linear-gradient(180deg,rgba(255,255,255,.03) 0%,transparent 100%);
  display:flex; align-items:center; gap:10px; flex-shrink:0;
}
.diff-hdr-name { font-family:var(--font2); font-size:12px; color:var(--c); letter-spacing:.04em; flex:1; }
.diff-close {
  cursor:pointer; color:var(--t3); background:none; border:none; font-size:16px;
  width:24px; height:24px; border-radius:6px; display:flex; align-items:center; justify-content:center;
  transition:all .2s;
}
.diff-close:hover { background:rgba(255,255,255,.08); color:var(--t); }
.diff-body { overflow:auto; padding:14px 18px; font-family:var(--font2); font-size:13px; line-height:1.75; white-space:pre; }
.diff-add  { color:#50d890; }
.diff-del  { color:#ff5577; }
.diff-hunk { color:rgba(var(--c-rgb),.8); }
.diff-meta { color:var(--t3); }

/* ── AGENT DETAIL PANEL ── */
.agent-detail-overlay {
  position:fixed; inset:0; z-index:300; background:rgba(2,6,16,.82);
  backdrop-filter:blur(10px); display:none; align-items:flex-start; justify-content:flex-end;
}
.agent-detail-overlay.open { display:flex; animation:adpFadeIn .18s ease; }
@keyframes adpFadeIn { from{opacity:0} to{opacity:1} }
.agent-detail-panel {
  width:500px; max-width:94vw; height:100vh;
  background:linear-gradient(145deg,rgba(8,18,44,.98) 0%,rgba(4,11,26,.99) 100%);
  border-left:1px solid rgba(255,255,255,.09);
  display:flex; flex-direction:column; overflow:hidden;
  box-shadow:-28px 0 72px rgba(0,0,0,.7);
  animation:adpSlideIn .22s cubic-bezier(.23,1,.32,1);
}
@keyframes adpSlideIn { from{transform:translateX(100%)} to{transform:none} }
.adp-hdr {
  padding:16px 18px; border-bottom:1px solid rgba(255,255,255,.06);
  background:linear-gradient(180deg,rgba(255,255,255,.035) 0%,transparent 100%);
  display:flex; align-items:flex-start; gap:12px; flex-shrink:0;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.06);
}
.adp-close {
  margin-left:auto; width:26px; height:26px; border-radius:7px;
  border:1px solid rgba(255,255,255,.1); background:rgba(255,255,255,.04);
  color:var(--t3); font-size:13px; cursor:pointer;
  display:flex; align-items:center; justify-content:center; flex-shrink:0;
  transition:all .2s;
}
.adp-close:hover { background:rgba(255,51,85,.15); border-color:rgba(255,51,85,.3); color:var(--r); }
.adp-tabs { display:flex; border-bottom:1px solid rgba(255,255,255,.06); flex-shrink:0; background:rgba(255,255,255,.01); }
.adp-tab {
  padding:9px 18px; font-size:10px; font-family:var(--font2); font-weight:700; letter-spacing:.09em;
  color:var(--t3); cursor:pointer; border-bottom:2px solid transparent; transition:all .2s;
}
.adp-tab.active { color:var(--c); border-bottom-color:var(--c); background:rgba(var(--c-rgb),.04); }
.adp-tab:hover:not(.active) { color:var(--t2); background:rgba(255,255,255,.03); }
.adp-body { flex:1; overflow-y:auto; padding:14px 16px; display:flex; flex-direction:column; gap:5px; }
.adp-sec { font-family:var(--font2); font-size:9px; font-weight:700; letter-spacing:.12em; color:var(--t3); padding:5px 0 4px; border-bottom:1px solid rgba(255,255,255,.05); margin-bottom:4px; display:flex; align-items:center; justify-content:space-between; }
.adp-log-line { font-family:var(--font2); font-size:11px; color:var(--t2); line-height:1.7; padding:2px 5px; border-radius:3px; white-space:pre-wrap; word-break:break-all; }
.adp-log-line:hover { background:rgba(255,255,255,.03); }
.adp-task-row { display:flex; align-items:baseline; gap:8px; padding:5px 9px; font-size:12px; border-left:2px solid; border-radius:0 6px 6px 0; margin-bottom:2px; }
.adp-task-row.done { border-color:rgba(0,232,135,.4); color:rgba(140,235,175,.8); background:rgba(0,232,135,.04); }
.adp-task-row.pend { border-color:rgba(var(--c-rgb),.12); color:rgba(140,185,225,.5); background:rgba(255,255,255,.015); }
.adp-btn { font-size:9px; padding:2px 8px; border-radius:4px; border:1px solid rgba(var(--c-rgb),.2); background:rgba(var(--c-rgb),.06); color:var(--t2); cursor:pointer; font-family:var(--font2); font-weight:700; letter-spacing:.06em; transition:all .2s; }
.adp-btn:hover { background:rgba(var(--c-rgb),.15); color:var(--c); border-color:rgba(var(--c-rgb),.4); }
.card-info-btn { padding:2px 8px; border-radius:5px; border:1px solid rgba(255,255,255,.08); background:rgba(255,255,255,.03); color:var(--t3); font-size:9px; font-family:var(--font2); font-weight:700; letter-spacing:.08em; cursor:pointer; flex-shrink:0; transition:all .2s; }
.card-info-btn:hover { background:rgba(var(--c-rgb),.1); border-color:rgba(var(--c-rgb),.3); color:var(--c); }

/* ── settings panel tabs (6 sections now -- too many for one long scroll) ── */
.settings-tabs { display:flex; flex-wrap:wrap; gap:2px; padding:10px 14px 0; flex-shrink:0; }
.settings-tab { padding:5px 9px; font-size:9px; font-family:var(--font2); font-weight:700; letter-spacing:.05em; color:var(--t3); cursor:pointer; border-radius:5px; transition:all .2s; }
.settings-tab.active { color:var(--c); background:rgba(var(--c-rgb),.1); }
.settings-tab:hover:not(.active) { color:var(--t2); background:rgba(255,255,255,.04); }
body.light .settings-tab.active { background:rgba(0,140,180,.1); }

/* ── cost badge ── */
.cost-badge { display:inline-flex; align-items:center; gap:3px; padding:2px 7px; border-radius:5px; border:1px solid rgba(0,210,130,.22); background:rgba(0,210,130,.06); color:rgba(0,210,130,.9); font-size:9px; font-family:var(--font2); font-weight:700; letter-spacing:.04em; flex-shrink:0; transition:color .3s,background .3s; }
.cost-badge.flash { animation:costFlash .6s ease-out; }
.hookmiss-badge { display:inline-flex; align-items:center; gap:3px; padding:2px 7px; border-radius:5px; border:1px solid rgba(255,170,0,.3); background:rgba(255,170,0,.08); color:rgba(255,170,0,.95); font-size:9px; font-family:var(--font2); font-weight:700; letter-spacing:.04em; flex-shrink:0; }
body.light .hookmiss-badge { border-color:rgba(200,120,0,.3); background:rgba(200,120,0,.08); color:rgba(180,105,0,.95); }
@keyframes costFlash { 0%{background:rgba(0,210,130,.35);color:#fff;box-shadow:0 0 8px rgba(0,210,130,.5);} 100%{background:rgba(0,210,130,.06);color:rgba(0,210,130,.9);box-shadow:none;} }
body.light .cost-badge { border-color:rgba(0,140,80,.2); background:rgba(0,140,80,.06); color:rgba(0,130,70,.9); }

/* ── agent compare ── */
.card-cmp-btn { position:absolute; top:8px; right:80px; width:22px; height:22px; border-radius:50%; border:1px solid rgba(255,255,255,.1); background:rgba(255,255,255,.04); color:var(--t3); font-size:11px; line-height:1; cursor:pointer; display:flex; align-items:center; justify-content:center; opacity:.35; transition:opacity .2s,color .2s,background .2s; }
.card:hover .card-cmp-btn { opacity:1; }
.card-cmp-btn.selected { opacity:1; color:var(--o); background:rgba(255,140,0,.12); border-color:rgba(255,140,0,.38); box-shadow:0 0 6px rgba(255,140,0,.2); }
.cmp-overlay { position:fixed;inset:0;background:rgba(5,10,20,.82);backdrop-filter:blur(8px);z-index:2800;display:none;align-items:center;justify-content:center; }
.cmp-overlay.open { display:flex; }
.cmp-panel { background:linear-gradient(145deg,rgba(10,22,48,.97) 0%,rgba(6,14,30,.99) 100%);border:1px solid rgba(var(--c-rgb),.2);border-top:1px solid rgba(255,255,255,.1);border-radius:18px;max-width:920px;width:95%;max-height:86vh;display:flex;flex-direction:column;box-shadow:0 28px 80px rgba(0,0,0,.72),inset 0 1px 0 rgba(255,255,255,.07); }
.cmp-hdr { display:flex;align-items:center;padding:14px 20px 12px;border-bottom:1px solid rgba(255,255,255,.06);flex-shrink:0; }
.cmp-hdr-title { flex:1;font-family:var(--font2);font-size:11px;letter-spacing:.2em;color:var(--c);font-weight:700;text-shadow:0 0 12px rgba(var(--c-rgb),.35); }
.cmp-body { display:grid;grid-template-columns:1fr 1fr;flex:1;overflow:hidden;min-height:0; }
.cmp-col { display:flex;flex-direction:column;overflow-y:auto;padding:16px 18px; }
.cmp-col:first-child { border-right:1px solid rgba(255,255,255,.06); }
.cmp-sec { font-family:var(--font2);font-size:9px;letter-spacing:.14em;color:var(--t3);margin:14px 0 6px;text-transform:uppercase;padding-bottom:4px;border-bottom:1px solid rgba(255,255,255,.04); }
.cmp-stat-row { display:flex;align-items:center;justify-content:space-between;padding:5px 0;border-bottom:1px solid rgba(255,255,255,.03); }
.cmp-stat-lbl { font-size:11px;color:var(--t3); }
.cmp-stat-val { font-family:var(--font2);font-size:12px;color:var(--t); }
.cmp-stat-val.better { color:var(--g);text-shadow:0 0 8px rgba(0,232,135,.3); }
.cmp-stat-val.worse { color:var(--t3); }
.cmp-task-row { display:flex;align-items:center;gap:6px;padding:3px 0;font-size:11px;line-height:1.4; }
.cmp-task-row.done { color:rgba(0,232,135,.75); }
.cmp-task-row.pend { color:var(--t3); }
body.light .cmp-panel { background:linear-gradient(145deg,rgba(235,242,255,.98) 0%,rgba(225,235,255,.99) 100%);border-color:rgba(0,100,180,.18); }
body.light .card-cmp-btn { border-color:rgba(0,0,0,.08);background:rgba(0,0,0,.03); }

/* ── keyboard shortcuts overlay ── */
.kb-overlay { position:fixed;inset:0;background:rgba(5,10,20,.78);backdrop-filter:blur(7px);z-index:2900;display:none;align-items:center;justify-content:center; }
.kb-overlay.open { display:flex; }
.palette-overlay { position:fixed;inset:0;background:rgba(5,10,20,.78);backdrop-filter:blur(7px);z-index:3100;display:none;align-items:flex-start;justify-content:center;padding-top:14vh; }
.palette-overlay.open { display:flex; }
.palette-panel { background:linear-gradient(145deg,rgba(10,22,48,.97) 0%,rgba(6,14,30,.99) 100%);border:1px solid rgba(var(--c-rgb),.22);border-top:1px solid rgba(255,255,255,.12);border-radius:14px;padding:12px;max-width:560px;width:90%;box-shadow:0 28px 80px rgba(0,0,0,.75),inset 0 1px 0 rgba(255,255,255,.07);display:flex;flex-direction:column;gap:8px; }
.palette-input { width:100%;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.1);border-radius:9px;color:var(--t1);font-family:var(--font2);font-size:13px;padding:10px 12px;outline:none;transition:border-color .2s;box-sizing:border-box; }
.palette-input:focus { border-color:rgba(var(--c-rgb),.35); }
.palette-results { display:flex;flex-direction:column;max-height:340px;overflow-y:auto; }
.palette-item { display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:8px;cursor:pointer;font-family:var(--font2); }
.palette-item .pi-label { font-size:12px;color:var(--t1); }
.palette-item .pi-sub { font-size:9px;color:var(--t3);margin-left:auto;letter-spacing:.04em;white-space:nowrap; }
.palette-item.active { background:rgba(var(--c-rgb),.1); }
.palette-empty { padding:14px;text-align:center;font-size:10px;color:var(--t3);font-family:var(--font2); }
.kb-panel { background:linear-gradient(145deg,rgba(10,22,48,.97) 0%,rgba(6,14,30,.99) 100%);border:1px solid rgba(var(--c-rgb),.22);border-top:1px solid rgba(255,255,255,.12);border-radius:18px;padding:20px 24px;max-width:700px;width:92%;box-shadow:0 28px 80px rgba(0,0,0,.75),inset 0 1px 0 rgba(255,255,255,.07); }
.kb-hdr { display:flex;align-items:center;margin-bottom:14px;border-bottom:1px solid rgba(255,255,255,.06);padding-bottom:12px; }
.kb-hdr-title { flex:1;font-family:var(--font2);font-size:11px;letter-spacing:.2em;color:var(--c);font-weight:700;text-shadow:0 0 12px rgba(var(--c-rgb),.4); }
.kb-group { margin-bottom:14px; }
.kb-group-title { font-family:var(--font2);font-size:9px;letter-spacing:.14em;color:var(--t3);margin-bottom:6px;text-transform:uppercase; }
.kb-row { display:flex;align-items:center;gap:8px;padding:3px 0; }
.kb-key { font-family:var(--font2);font-size:11px;background:rgba(var(--c-rgb),.1);border:1px solid rgba(var(--c-rgb),.28);border-bottom:2px solid rgba(var(--c-rgb),.45);border-radius:6px;padding:1px 8px;color:var(--c);min-width:30px;text-align:center;flex-shrink:0;letter-spacing:.04em; }
.kb-desc { font-size:12px;color:var(--t2); }
body.light .kb-panel { background:linear-gradient(145deg,rgba(235,242,255,.98) 0%,rgba(225,235,255,.99) 100%);border-color:rgba(0,100,180,.18); }
body.light .kb-key { background:rgba(0,120,180,.1);border-color:rgba(0,120,180,.3);border-bottom-color:rgba(0,120,180,.5); }

/* ── notification history panel ── */
.notif-overlay { position:fixed;inset:0;z-index:3000;pointer-events:none; }
.notif-overlay.open { pointer-events:all; }
.notif-panel { position:fixed;top:0;left:0;width:360px;height:100%;background:rgba(4,10,24,.97);backdrop-filter:blur(32px);border-right:1px solid rgba(var(--c-rgb),.12);display:flex;flex-direction:column;transform:translateX(-100%);transition:transform .28s cubic-bezier(.4,0,.2,1);box-shadow:4px 0 40px rgba(0,0,0,.55);z-index:3001; }
.notif-overlay.open .notif-panel { transform:translateX(0); }
.notif-hdr { display:flex;align-items:center;gap:8px;padding:14px 16px 12px;border-bottom:1px solid rgba(255,255,255,.06);flex-shrink:0; }
.notif-hdr-title { font-family:var(--font2);font-size:11px;letter-spacing:.14em;color:var(--c);font-weight:700; }
.notif-hdr-count { font-family:var(--font2);font-size:10px;color:var(--t3);margin-right:auto; }
.notif-list { flex:1;overflow-y:auto;padding:6px 0; }
.notif-entry { display:flex;align-items:flex-start;gap:8px;padding:7px 16px;border-bottom:1px solid rgba(255,255,255,.03);font-size:11px;line-height:1.4;transition:background .15s; }
.notif-entry:hover { background:rgba(255,255,255,.025); }
.notif-entry-time { font-family:var(--font2);font-size:9px;color:var(--t3);flex-shrink:0;margin-top:2px;letter-spacing:.04em;min-width:54px; }
.notif-entry-tag { font-family:var(--font2);font-size:9px;background:rgba(var(--c-rgb),.1);border:1px solid rgba(var(--c-rgb),.18);border-radius:4px;padding:1px 6px;color:var(--c);flex-shrink:0;letter-spacing:.06em;margin-top:1px;height:fit-content; }
.notif-entry-msg { flex:1;color:var(--t2);word-break:break-word; }
.notif-entry.success .notif-entry-msg { color:rgba(0,232,135,.85); }
.notif-entry.error .notif-entry-msg { color:rgba(255,80,100,.85); }
.notif-entry.warn .notif-entry-msg { color:rgba(255,165,0,.85); }
.notif-badge { position:absolute;top:-5px;right:-5px;min-width:16px;height:16px;background:var(--r);border-radius:999px;font-size:9px;font-family:var(--font2);font-weight:700;color:#fff;display:flex;align-items:center;justify-content:center;padding:0 3px;pointer-events:none;line-height:1; }
#btn-notif { position:relative; }
body.light .notif-panel { background:rgba(238,242,251,.97);border-right-color:rgba(0,120,180,.14); }
body.light .notif-entry { border-bottom-color:rgba(0,0,0,.05); }
.notes-overlay { position:fixed;inset:0;z-index:3000;pointer-events:none; }
.notes-overlay.open { pointer-events:all; }
.notes-panel { position:fixed;top:56px;right:12px;width:340px;background:rgba(4,10,24,.97);backdrop-filter:blur(32px);border:1px solid rgba(var(--c-rgb),.15);border-radius:14px;display:flex;flex-direction:column;box-shadow:0 8px 40px rgba(0,0,0,.55);z-index:3001;transform:translateY(-8px) scale(.97);opacity:0;transition:transform .22s cubic-bezier(.4,0,.2,1),opacity .2s; }
.notes-overlay.open .notes-panel { transform:translateY(0) scale(1);opacity:1; }
body.light .notes-panel { background:rgba(238,242,251,.97);border-color:rgba(0,120,180,.2); }
body.light #notes-ta { background:rgba(0,0,0,.04);border-color:rgba(0,0,0,.12);color:var(--t1); }
body.light .notif-entry:hover { background:rgba(0,0,0,.025); }
.settings-overlay { position:fixed;inset:0;z-index:3000;pointer-events:none;background:rgba(0,0,0,0);transition:background .25s; }
.settings-overlay.open { pointer-events:all;background:rgba(0,0,0,.45); }
.settings-panel { position:fixed;top:0;right:0;width:360px;height:100%;background:rgba(4,10,24,.98);backdrop-filter:blur(32px);border-left:1px solid rgba(var(--c-rgb),.12);display:flex;flex-direction:column;transform:translateX(100%);transition:transform .28s cubic-bezier(.4,0,.2,1);box-shadow:-4px 0 40px rgba(0,0,0,.55);z-index:3001; }
.settings-overlay.open .settings-panel { transform:translateX(0); }
.settings-body { flex:1;overflow-y:auto;padding:14px 18px 20px;display:flex;flex-direction:column;gap:10px; }
.settings-section { font-family:var(--font2);font-size:9px;font-weight:700;letter-spacing:.14em;color:var(--c);padding:8px 0 4px;border-bottom:1px solid rgba(var(--c-rgb),.12);margin-top:4px; }
.settings-row { display:flex;align-items:center;gap:10px;padding:4px 0; }
.settings-lbl { font-family:var(--font2);font-size:10px;color:var(--t2);min-width:140px;flex-shrink:0; }
.settings-input { background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.1);border-radius:7px;color:var(--t1);font-family:var(--font2);font-size:11px;padding:5px 9px;outline:none;transition:border-color .2s;width:80px; }
.settings-input:focus { border-color:rgba(var(--c-rgb),.4); }
/* these 3 inputs use inline outline:none (styled ad-hoc, no shared class)
   with no replacement focus indicator -- keyboard focus was invisible on
   all three. #auth-url-input/#tunnel-url-input { border-color } and
   #tunnel-ngrok-token-input { outline } since the latter isn't readonly. */
#auth-url-input:focus, #tunnel-url-input:focus { border-color:rgba(var(--c-rgb),.5); }
#tunnel-ngrok-token-input:focus { outline:1px solid rgba(var(--c-rgb),.4); outline-offset:-1px; }
.settings-input-wide { width:100%;flex:1; }
.settings-toggle { display:flex;align-items:center;gap:5px;font-family:var(--font2);font-size:10px;color:var(--t2);cursor:pointer;user-select:none; }
.settings-toggle input { accent-color:var(--c);width:13px;height:13px;cursor:pointer; }
.accent-swatch { width:22px;height:22px;border-radius:50%;border:2px solid transparent;cursor:pointer;background:var(--sc);transition:transform .15s,border-color .15s; }
.accent-swatch:hover { transform:scale(1.15); }
.accent-swatch.active { border-color:rgba(255,255,255,.8);transform:scale(1.1); }
body.light .settings-panel { background:rgba(238,242,251,.98);border-left-color:rgba(0,120,180,.14); }
body.light .settings-input { background:rgba(0,0,0,.04);border-color:rgba(0,0,0,.12);color:var(--t1); }
body.light .settings-section { border-bottom-color:rgba(0,120,180,.15); }
body.light .notif-entry-tag { background:rgba(0,120,180,.08);border-color:rgba(0,120,180,.15); }
body.light .notif-entry.success .notif-entry-msg { color:rgba(0,140,80,.9); }
body.light .notif-entry.error .notif-entry-msg { color:rgba(180,30,50,.9); }
body.light .notif-entry.warn .notif-entry-msg { color:rgba(160,100,0,.9); }

/* ── HEAT VIEW ── */
#heat-area { padding:16px; }
#diag-area { padding:16px; }
.heat-toolbar { display:flex;align-items:center;gap:10px;margin-bottom:14px;font-family:var(--font2);font-size:10px;flex-wrap:wrap; }
.heat-mode-btn { padding:3px 12px;border-radius:6px;border:1px solid rgba(255,255,255,.08);background:transparent;color:var(--t2);font-family:var(--font2);font-size:9px;letter-spacing:.08em;cursor:pointer;transition:all .2s; }
.heat-mode-btn.active { border-color:rgba(var(--c-rgb),.5);background:rgba(var(--c-rgb),.1);color:var(--c); }
.heat-grid { display:flex;flex-direction:column;gap:4px; }
.heat-row { display:flex;align-items:center;gap:6px; }
.heat-label { width:130px;font-size:10px;color:var(--t2);text-align:right;flex-shrink:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:var(--font);cursor:pointer; }
.heat-label:hover { color:var(--c); }
.heat-cells { display:flex;gap:2px;flex:1; }
.heat-cell { height:20px;border-radius:3px;flex:1;min-width:8px;cursor:pointer;transition:opacity .15s;position:relative; }
.heat-cell:hover { opacity:.75;outline:1px solid rgba(255,255,255,.3); }
.heat-axis { display:flex;gap:2px;padding-left:136px;margin-bottom:4px; }
.heat-axis-lbl { flex:1;font-size:8px;color:var(--t3);font-family:var(--font2);text-align:center;min-width:8px;overflow:hidden; }
.heat-legend { display:flex;align-items:center;gap:8px;margin-top:12px;font-family:var(--font2);font-size:9px;color:var(--t3); }
.heat-legend-scale { display:flex;gap:2px; }
.heat-legend-cell { width:16px;height:10px;border-radius:2px; }
body.light .heat-mode-btn { border-color:rgba(0,0,0,.09);color:var(--t2); }
body.light .heat-mode-btn.active { border-color:rgba(0,120,180,.4);background:rgba(0,120,180,.08);color:var(--c); }
body.light .heat-label { color:var(--t2); }

/* ── TREE VIEW ── */
/* No body.light override here: #tree-area svg sets background/border via
   inline style="" in renderTree()'s JS, and inline style always outranks a
   stylesheet rule on the same property regardless of selector specificity
   (short of !important) -- a CSS override here would be dead code. Theme
   awareness is handled in JS instead, see _graphThemeColors(). */
#tree-area { padding:16px; }

/* scrollbar */
::-webkit-scrollbar { width:3px; }
::-webkit-scrollbar-track { background:transparent; }
::-webkit-scrollbar-thumb { background:rgba(var(--c-rgb),.22); border-radius:2px; }
::-webkit-scrollbar-thumb:hover { background:rgba(var(--c-rgb),.4); }

/* ── LIGHT THEME ── */
body.light {
  --bg:#eef2fb; --c:#0078a0; --c2:#005580; --c-rgb:0,120,160; --c2-rgb:0,85,128; --c3:rgba(0,80,120,.12);
  --g:#009a4a; --r:#cc1a35; --o:#b05500;
  --t:rgba(15,30,55,.92); --t2:rgba(40,70,110,.78); --t3:rgba(50,80,120,.85);  /* was .55 alpha rgba(70,110,160) = 2.1:1, failed WCAG AA; this is ~5.1:1 */
  --border:rgba(0,0,0,.08); --glass:rgba(255,255,255,.6); --specular:rgba(255,255,255,.88);
  background:
    radial-gradient(ellipse 80% 60% at 15% 10%, rgba(0,120,200,.05) 0%, transparent 55%),
    radial-gradient(ellipse 60% 70% at 85% 90%, rgba(0,80,160,.04) 0%, transparent 55%),
    #eef2fb;
  color:var(--t);
}
body.light::before {
  background-image: linear-gradient(rgba(0,80,160,.018) 1px,transparent 1px), linear-gradient(90deg,rgba(0,80,160,.018) 1px,transparent 1px);
}
body.light .topbar {
  background:rgba(235,243,255,.9); border-bottom-color:rgba(0,0,0,.07);
  box-shadow:inset 0 1px 0 rgba(255,255,255,.88),0 4px 32px rgba(0,0,0,.07);
}
body.light .logo-mark { filter:drop-shadow(0 0 8px rgba(0,120,180,.35)); }
body.light .logo-text .t1 { text-shadow:0 0 14px rgba(0,120,180,.4); }
body.light .divider { background:rgba(0,0,0,.09); }
body.light .btn-reset { background:rgba(0,0,0,.04); border-color:rgba(0,0,0,.1); color:var(--t2); box-shadow:none; }
body.light .btn-reset:hover { background:rgba(0,120,180,.07); border-color:rgba(0,120,180,.22); }
body.light .btn-mute { background:rgba(0,0,0,.04); border-color:rgba(0,0,0,.1); color:var(--t2); box-shadow:none; }
body.light .btn-mute:hover { background:rgba(0,120,180,.07); border-color:rgba(0,120,180,.22); }
body.light .btn-mute.muted { border-color:rgba(200,26,53,.22); background:rgba(200,26,53,.05); }
body.light .card {
  background:linear-gradient(145deg,rgba(255,255,255,.88) 0%,rgba(235,244,255,.75) 35%,rgba(225,238,255,.78) 100%);
  border-color:rgba(0,0,0,.08); border-top-color:rgba(255,255,255,.95);
  box-shadow:0 4px 20px rgba(0,0,0,.08),0 1px 4px rgba(0,0,0,.05),inset 0 1px 0 rgba(255,255,255,.9),inset 0 -1px 0 rgba(0,0,0,.04);
}
body.light .card::before { background:linear-gradient(90deg,transparent 5%,rgba(255,255,255,.7) 50%,transparent 95%); }
body.light .card::after { background:linear-gradient(180deg,transparent,rgba(255,255,255,.2) 50%,transparent); }
body.light .card.running { border-color:rgba(0,120,180,.28); border-top-color:rgba(0,160,220,.45); }
body.light .card.done { border-color:rgba(0,154,74,.22); border-top-color:rgba(0,200,100,.35); box-shadow:0 4px 20px rgba(0,0,0,.07),0 0 18px rgba(0,154,74,.04),inset 0 1px 0 rgba(0,180,100,.22),inset 0 -1px 0 rgba(0,0,0,.04); }
body.light .card.error { border-color:rgba(200,26,53,.22); border-top-color:rgba(220,60,80,.32); box-shadow:0 4px 20px rgba(0,0,0,.07),0 0 18px rgba(200,26,53,.04),inset 0 1px 0 rgba(210,80,100,.16),inset 0 -1px 0 rgba(0,0,0,.04); }
body.light .card.waiting { border-color:rgba(176,85,0,.18); border-top-color:rgba(200,100,0,.28); }
body.light .card.pinned { border-top-color:rgba(180,140,0,.48) !important; box-shadow:0 4px 20px rgba(0,0,0,.08),0 0 14px rgba(180,140,0,.05),inset 0 1px 0 rgba(200,160,20,.28),inset 0 -1px 0 rgba(0,0,0,.04) !important; }
body.light .sweep { background:linear-gradient(90deg,transparent,rgba(0,120,180,.04) 50%,transparent); }
body.light .right { background:rgba(230,240,255,.82); border-left-color:rgba(0,0,0,.07); box-shadow:inset 1px 0 0 rgba(255,255,255,.4); }
body.light .panel-toggle { background:rgba(230,240,255,.9); border-color:rgba(0,0,0,.08); color:var(--t3); }
body.light .panel-toggle:hover { background:rgba(0,120,180,.09); border-color:rgba(0,120,180,.2); }
body.light .log-area .le-t { color:rgba(70,110,160,.6); }
body.light .le-m.info { color:rgba(0,100,150,.85); }
body.light .le-m.success { color:var(--g); }
body.light .le-m.warn { color:var(--o); }
body.light .le-m.error { color:var(--r); }
body.light .le-tag { color:rgba(0,100,150,.65); border-color:rgba(0,100,150,.22); }
body.light .fe.new { border-color:var(--g); color:rgba(0,110,65,.85); background:rgba(0,154,74,.04); }
body.light .fe.changed { border-color:var(--c); color:var(--t2); background:rgba(0,100,150,.04); }
body.light .fe.new:hover { background:rgba(0,154,74,.09); }
body.light .fe.changed:hover { background:rgba(0,100,150,.08); }
body.light .al { color:var(--t2); }
body.light .al.SESSION_START { color:var(--g); } body.light .al.SESSION_RESET { color:var(--o); }
body.light .al.AGENT_START { color:var(--c); } body.light .al.TASK_COMPLETE { color:#008844; }
body.light .al.AGENT_DONE { color:var(--g); } body.light .al.POLL { color:var(--t3); }
body.light .vtbtn { background:rgba(0,0,0,.04); border-color:rgba(0,0,0,.08); color:var(--t3); }
body.light .vtbtn.active { background:rgba(0,120,180,.09); border-color:rgba(0,120,180,.32); color:var(--c); }
body.light .vtbtn:hover:not(.active) { background:rgba(0,0,0,.06); color:var(--t2); }
body.light .stab { background:rgba(0,0,0,.04); border-color:rgba(0,0,0,.08); color:var(--t3); }
body.light .stab.active { background:rgba(90,60,200,.07); border-color:rgba(90,60,200,.28); color:#5840c0; }
body.light .sfbtn { background:rgba(0,0,0,.04); border-color:rgba(0,0,0,.08); color:var(--t3); }
body.light .sfbtn.active { background:rgba(0,120,180,.08); border-color:rgba(0,120,180,.28); color:var(--c); }
body.light .sfbtn.active.done { background:rgba(0,154,74,.07); border-color:rgba(0,154,74,.28); color:var(--g); }
body.light .sfbtn.active.error { background:rgba(200,26,53,.07); border-color:rgba(200,26,53,.28); color:var(--r); }
body.light .density-btn { background:rgba(0,0,0,.04); border-color:rgba(0,0,0,.08); color:var(--t3); }
body.light .density-btn:hover,.body.light .density-btn.active { background:rgba(0,120,180,.08); border-color:rgba(0,120,180,.28); color:var(--c); }
body.light .search-input { background:rgba(255,255,255,.7); border-color:rgba(0,0,0,.1); color:var(--t); }
body.light .search-input:focus { border-color:rgba(0,120,180,.38); background:rgba(255,255,255,.9); }
body.light .search-input::placeholder { color:var(--t3); }
body.light .task.p { border-color:rgba(0,120,180,.13); color:rgba(60,90,140,.6); background:rgba(0,0,0,.02); }
body.light .task.d { border-color:rgba(0,154,74,.32); color:rgba(0,100,55,.8); background:rgba(0,154,74,.05); }
body.light .prog-track { background:rgba(0,0,0,.06); }
body.light .log-panel { background:rgba(0,0,0,.04); border-color:rgba(0,0,0,.06); }
body.light .log-entry { color:var(--t2); border-bottom-color:rgba(0,0,0,.04); }
body.light .log-toggle { color:var(--t3); border-top-color:rgba(0,0,0,.05); }
body.light .log-toggle:hover { color:var(--t2); }
body.light .stuck-badge { background:rgba(176,85,0,.07); border-color:rgba(176,85,0,.28); }
body.light .burn-badge { background:rgba(180,20,45,.06); border-color:rgba(180,20,45,.25); }
body.light .card-pin-btn { border-color:rgba(0,0,0,.08); background:rgba(0,0,0,.03); color:var(--t3); }
body.light .card-collapse-btn { border-color:rgba(0,0,0,.08); background:rgba(0,0,0,.03); color:var(--t3); }
body.light .card-dismiss:hover { background:rgba(200,26,53,.11); border-color:rgba(200,26,53,.28); }
body.light .card-info-btn { border-color:rgba(0,0,0,.09); background:rgba(0,0,0,.04); color:var(--t3); }
body.light .card-info-btn:hover { background:rgba(0,120,180,.08); border-color:rgba(0,120,180,.22); }
body.light .agent-row { background:rgba(255,255,255,.45); border-color:rgba(0,0,0,.07); }
body.light .agent-row:hover { background:rgba(255,255,255,.7); }
body.light #reconnect-overlay { background:rgba(215,228,248,.78); }
body.light .diff-overlay { background:rgba(215,228,248,.88); }
body.light .diff-box { background:linear-gradient(145deg,rgba(255,255,255,.96) 0%,rgba(232,241,255,.98) 100%); border-color:rgba(0,0,0,.1); }
body.light .diff-hdr { border-bottom-color:rgba(0,0,0,.07); background:linear-gradient(180deg,rgba(255,255,255,.2) 0%,transparent 100%); }
body.light .diff-hdr-name { color:var(--c); }
body.light .diff-body { color:var(--t); }
body.light .idle-hex { background:linear-gradient(135deg,rgba(0,120,180,.09) 0%,rgba(0,80,120,.06) 100%); border-color:rgba(0,120,180,.18); border-top-color:rgba(255,255,255,.75); }
body.light .idle-title { color:var(--c); }
body.light .idle-sub { color:var(--t3); }
body.light .sum-stat { background:linear-gradient(145deg,rgba(255,255,255,.78),rgba(232,242,255,.68)); border-color:rgba(0,0,0,.08); border-top-color:rgba(255,255,255,.92); box-shadow:0 3px 12px rgba(0,0,0,.06); }
body.light .sum-stat .sl { color:var(--t3); }
body.light .sum-agent-row { background:rgba(255,255,255,.45); border-color:rgba(0,0,0,.06); }
body.light .agent-detail-overlay { background:rgba(210,225,248,.75); }
body.light .agent-detail-panel { background:linear-gradient(145deg,rgba(238,244,255,.98) 0%,rgba(228,238,255,.99) 100%); border-left-color:rgba(0,0,0,.08); box-shadow:-24px 0 64px rgba(0,0,0,.14); }
body.light .adp-hdr { border-bottom-color:rgba(0,0,0,.07); background:linear-gradient(180deg,rgba(255,255,255,.4) 0%,transparent 100%); }
body.light .adp-tabs { border-bottom-color:rgba(0,0,0,.07); background:rgba(255,255,255,.18); }
body.light .adp-tab { color:var(--t3); }
body.light .adp-tab.active { color:var(--c); background:rgba(0,120,180,.06); }
body.light .adp-log-line { color:var(--t2); }
body.light .adp-log-line:hover { background:rgba(0,0,0,.03); }
body.light .adp-task-row.done { border-color:rgba(0,154,74,.32); color:rgba(0,100,55,.85); background:rgba(0,154,74,.05); }
body.light .adp-task-row.pend { border-color:rgba(0,120,180,.1); color:rgba(50,80,130,.55); }
body.light .adp-btn { border-color:rgba(0,120,180,.18); background:rgba(0,120,180,.05); color:var(--t2); }
body.light .adp-btn:hover { background:rgba(0,120,180,.12); color:var(--c); }
body.light .btn-dl { border-color:rgba(0,120,180,.2); background:rgba(0,120,180,.06); }
body.light .btn-dl:hover { background:rgba(0,120,180,.13); }
body.light ::-webkit-scrollbar-thumb { background:rgba(0,100,160,.2); }
body.light ::-webkit-scrollbar-thumb:hover { background:rgba(0,100,160,.38); }

/* ── SUMMARY VIEW ── */
#summary-area { padding:20px 24px; overflow-y:auto; flex-direction:column; gap:0; align-items:center; }
.sum-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; }
.sum-stat {
  background:linear-gradient(145deg,rgba(255,255,255,.05),rgba(6,16,36,.8));
  border:1px solid rgba(255,255,255,.08); border-top:1px solid rgba(255,255,255,.16);
  border-radius:14px; padding:14px 16px; text-align:center;
  box-shadow:0 4px 16px rgba(0,0,0,.35);
}
.sum-stat .sv { font-family:var(--font2); font-size:22px; font-weight:700; color:var(--c); }
.sum-stat .sl { font-size:10px; color:var(--t3); letter-spacing:.08em; margin-top:4px; text-transform:uppercase; }
.sum-agent-row {
  display:flex; align-items:center; gap:10px;
  padding:8px 12px; border-radius:10px;
  background:rgba(255,255,255,.02); border:1px solid rgba(255,255,255,.05);
}

/* ── GRAPH VIEW ── */
#graph-area { overflow:auto; align-items:flex-start; justify-content:center; padding:20px; }

/* ── MOBILE NAV ── */
#mobile-nav { display:none; position:fixed; bottom:0; left:0; right:0; z-index:300; height:54px; background:rgba(4,10,22,.97); border-top:1px solid rgba(255,255,255,.08); backdrop-filter:blur(20px); -webkit-backdrop-filter:blur(20px); padding:0 4px; padding-bottom:env(safe-area-inset-bottom); }
#mobile-nav .mnav-btn { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:3px; background:none; border:none; color:var(--t3); font-family:var(--font2); font-size:8px; letter-spacing:.06em; padding:6px 2px; cursor:pointer; transition:color .2s; -webkit-tap-highlight-color:transparent; }
#mobile-nav .mnav-btn.active { color:var(--c); }
#mobile-nav .mnav-btn svg { width:18px; height:18px; stroke:currentColor; fill:none; stroke-width:1.5; }

/* ── MOBILE RIGHT PANEL OVERLAY ── */
@media (max-width: 768px) {
  /* Layout: collapse to single column */
  .root {
    grid-template-columns: 1fr !important;
    grid-template-rows: 48px 1fr;
  }
  .root.panel-collapsed { grid-template-columns: 1fr !important; }

  /* Right panel: hidden by default, shows as full-screen overlay when toggled */
  .right {
    display: none;
    position: fixed; inset: 0; z-index: 250;
    background: rgba(4,10,22,.98);
    flex-direction: column;
    padding-bottom: 54px; /* space for mobile nav */
  }
  .right.mobile-open { display: flex; }
  .panel-toggle { display: none; }
  #rp-mobile-close { display: block !important; }

  /* Topbar: compact */
  .topbar { padding: 0 10px; gap: 6px; height: 48px; }
  .logo-mark { width: 32px; height: 32px; }
  .logo-text .t2 { display: none; }
  .logo-text .t1 { font-size: 10px; letter-spacing: .14em; }
  .logo-wrap { margin-right: 2px; gap: 6px; }
  .divider { display: none; }
  .top-stat { padding: 0 6px; }
  .top-stat .val { font-size: 14px; }
  .top-stat .lbl { font-size: 8px; }
  /* Keep only Run, Done, Tasks — hide Files, Errors, Duration */
  #s-files, #s-errors, #s-elapsed { display: none; }
  .topbar-right { gap: 4px; }
  /* Hide verbose topbar-right items */
  #btn-reset, #btn-notes, #btn-fs, #btn-mute { display: none; }
  #conn-label { display: none; }
  .clock-date { display: none; }
  .clock-time { font-size: 11px; }

  /* KPI bar: horizontal scroll, no wrap */
  .kpi-bar { overflow-x: auto; flex-wrap: nowrap; padding: 4px 10px; -webkit-overflow-scrolling: touch; scrollbar-width: none; }
  .kpi-bar::-webkit-scrollbar { display: none; }
  .kpi-card { padding: 3px 10px; min-width: 56px; }
  .kpi-val { font-size: 14px; }
  .kpi-lbl { font-size: 7px; }
  .kpi-card-cost { min-width: 72px; }

  /* View tabs: horizontal scroll */
  .view-toggle { overflow-x: auto; flex-wrap: nowrap; padding: 4px 10px; -webkit-overflow-scrolling: touch; scrollbar-width: none; gap: 4px; }
  .view-toggle::-webkit-scrollbar { display: none; }
  .vtbtn { padding: 5px 10px; font-size: 9px; flex-shrink: 0; }

  /* Main area */
  .main { padding: 8px 10px; padding-bottom: 62px; }

  /* Agent grid: single column */
  .agents { grid-template-columns: 1fr; }

  /* Cards: touch-friendly */
  /* Real class names -- .card-hdr/.card-btn (the previous selectors here)
     never matched anything in the actual markup (the real header class is
     .card-head, and the real buttons are .card-cmp-btn/.card-pin-btn/
     .card-collapse-btn/.card-dismiss/.card-info-btn), so this touch-target
     enlargement silently never applied on mobile until this fix. */
  .card-head { min-height: 44px; padding: 10px 12px; }
  /* Bigger touch targets need wider spacing too, or adjacent 30px buttons
     overlap -- right offsets recomputed for 30px width + 2px gaps. */
  .card-dismiss       { width: 30px; height: 30px; right: 6px; }
  .card-collapse-btn  { width: 30px; height: 30px; right: 38px; }
  .card-pin-btn       { width: 30px; height: 30px; right: 70px; }
  .card-cmp-btn       { width: 30px; height: 30px; right: 102px; }

  /* Summary grid: 2 cols */
  .sum-grid { grid-template-columns: repeat(2, 1fr); }

  /* History analytics: 2 cols */
  .hist-analytics { grid-template-columns: repeat(2, 1fr); }

  /* Mobile nav: show */
  #mobile-nav { display: flex; }
}

@media (max-width: 480px) {
  .top-stat:nth-child(n+5) { display: none; } /* keep only Run + Done on very small screens */
}
</style>
</head>
<body>
<div class="root">

  <!-- TOP BAR -->
  <div id="reconnect-overlay">
    <div class="ro-icon"></div>
    <div class="ro-text">RECONNECTING...</div>
  </div>

  <div class="topbar">
    <div class="logo-wrap">
      <svg class="logo-mark" viewBox="0 0 52 52" fill="none" xmlns="http://www.w3.org/2000/svg">
        <!-- hex outer border -->
        <polygon points="26,3 48,15 48,37 26,49 4,37 4,15"
                 stroke="rgba(var(--c-rgb),0.55)" stroke-width="1.5"
                 fill="rgba(var(--c-rgb),0.07)"/>
        <!-- dashed radar ring outer -->
        <circle cx="26" cy="26" r="17" stroke="rgba(var(--c-rgb),0.22)" stroke-width="0.8"
                stroke-dasharray="2.8 2.2" fill="none"/>
        <!-- mid ring -->
        <circle cx="26" cy="26" r="11" stroke="rgba(var(--c-rgb),0.38)" stroke-width="0.8" fill="none"/>
        <!-- inner ring -->
        <circle cx="26" cy="26" r="5.5" stroke="rgba(var(--c-rgb),0.28)" stroke-width="0.6" fill="none"/>
        <!-- cardinal ticks -->
        <line x1="26" y1="5" x2="26" y2="10" stroke="rgba(var(--c-rgb),0.65)" stroke-width="1"/>
        <line x1="26" y1="42" x2="26" y2="47" stroke="rgba(var(--c-rgb),0.65)" stroke-width="1"/>
        <line x1="5" y1="26" x2="10" y2="26" stroke="rgba(var(--c-rgb),0.65)" stroke-width="1"/>
        <line x1="42" y1="26" x2="47" y2="26" stroke="rgba(var(--c-rgb),0.65)" stroke-width="1"/>
        <!-- diagonal ticks -->
        <line x1="10" y1="10" x2="13" y2="13" stroke="rgba(var(--c-rgb),0.3)" stroke-width="0.8"/>
        <line x1="42" y1="10" x2="39" y2="13" stroke="rgba(var(--c-rgb),0.3)" stroke-width="0.8"/>
        <line x1="10" y1="42" x2="13" y2="39" stroke="rgba(var(--c-rgb),0.3)" stroke-width="0.8"/>
        <line x1="42" y1="42" x2="39" y2="39" stroke="rgba(var(--c-rgb),0.3)" stroke-width="0.8"/>
        <!-- rotating sweep group -->
        <g>
          <animateTransform attributeName="transform" type="rotate"
                            from="0 26 26" to="360 26 26"
                            dur="4s" repeatCount="indefinite"/>
          <!-- main sweep line -->
          <line x1="26" y1="26" x2="43" y2="26"
                stroke="var(--c)" stroke-width="1.6" stroke-linecap="round"/>
          <!-- trail lines -->
          <line x1="26" y1="26" x2="41" y2="21"
                stroke="rgba(var(--c-rgb),0.45)" stroke-width="0.9" stroke-linecap="round"/>
          <line x1="26" y1="26" x2="37" y2="17"
                stroke="rgba(var(--c-rgb),0.2)" stroke-width="0.7" stroke-linecap="round"/>
        </g>
        <!-- blip 1 — near, 1-o'clock -->
        <circle cx="26" cy="15" r="1.8" fill="var(--c)">
          <animate attributeName="fill-opacity" values="0;1;1;0" dur="4s" begin="0.9s" repeatCount="indefinite"/>
          <animate attributeName="r" values="1.8;2.6;1.8" dur="4s" begin="0.9s" repeatCount="indefinite"/>
        </circle>
        <!-- blip 2 — mid, 2-o'clock -->
        <circle cx="36" cy="18" r="1.3" fill="var(--c)">
          <animate attributeName="fill-opacity" values="0;0.8;0.8;0" dur="4s" begin="2.1s" repeatCount="indefinite"/>
          <animate attributeName="r" values="1.3;2;1.3" dur="4s" begin="2.1s" repeatCount="indefinite"/>
        </circle>
        <!-- blip 3 — far, 7-o'clock -->
        <circle cx="17" cy="34" r="1" fill="var(--c)">
          <animate attributeName="fill-opacity" values="0;0.65;0.65;0" dur="4s" begin="3.3s" repeatCount="indefinite"/>
        </circle>
        <!-- center dot with pulse -->
        <circle cx="26" cy="26" r="3" fill="var(--c)">
          <animate attributeName="r" values="3;4;3" dur="2s" repeatCount="indefinite"/>
          <animate attributeName="fill-opacity" values="1;0.6;1" dur="2s" repeatCount="indefinite"/>
        </circle>
        <!-- center pulse ring -->
        <circle cx="26" cy="26" r="5" stroke="var(--c)" stroke-width="0.8" fill="none">
          <animate attributeName="r" values="4;7;4" dur="2s" repeatCount="indefinite"/>
          <animate attributeName="stroke-opacity" values="0.5;0;0.5" dur="2s" repeatCount="indefinite"/>
        </circle>
      </svg>
      <div class="logo-text">
        <div class="t1">AOC</div>
        <div class="t2">AGENT OPERATIONS CENTER</div>
      </div>
    </div>

    <div class="divider"></div>
    <div class="top-stat"><div class="val" id="s-run">—</div><div class="lbl">Online</div></div>
    <div class="divider"></div>
    <div class="top-stat"><div class="val" id="s-done">—</div><div class="lbl">Done</div></div>
    <div class="divider"></div>
    <div class="top-stat"><div class="val" id="s-tasks">—</div><div class="lbl">Tasks</div></div>
    <div class="divider"></div>
    <div class="top-stat"><div class="val" id="s-files">—</div><div class="lbl">Files</div></div>
    <div class="divider"></div>
    <div class="top-stat"><div class="val" id="s-clis" style="color:var(--c)">—</div><div class="lbl">CLIs</div></div>
    <div class="divider"></div>
    <div class="top-stat"><div class="val" id="s-errors" style="color:var(--t3)">—</div><div class="lbl">Errors</div></div>
    <div class="divider"></div>
    <div class="top-stat"><div class="val" id="s-elapsed" style="font-size:13px">—</div><div class="lbl">Duration</div></div>

    <div class="topbar-right">
      <button class="btn-reset" id="btn-reset" onclick="resetSession()">
        <svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>
        RESET SESSION
      </button>
      <div id="tunnel-badge" style="display:none;align-items:center;gap:5px;padding:3px 9px;border-radius:20px;background:rgba(var(--c-rgb),.1);border:1px solid rgba(var(--c-rgb),.25);cursor:pointer;transition:background .2s" onclick="openSettings()" title="Cloudflare Tunnel active — click for URL">
        <span style="width:6px;height:6px;border-radius:50%;background:rgba(var(--c-rgb),1);box-shadow:0 0 6px rgba(var(--c-rgb),.8);animation:pulse 2s infinite;flex-shrink:0"></span>
        <span id="tunnel-badge-lbl" style="font-family:var(--font2);font-size:9px;color:rgba(var(--c-rgb),.9);letter-spacing:.05em;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></span>
      </div>
      <div id="selfupdate-badge" style="display:none;align-items:center;gap:5px;padding:3px 9px;border-radius:20px;background:rgba(255,140,0,.1);border:1px solid rgba(255,140,0,.3);cursor:default" title="">
        <span style="width:6px;height:6px;border-radius:50%;background:var(--o);flex-shrink:0"></span>
        <span id="selfupdate-badge-lbl" style="font-family:var(--font2);font-size:9px;color:var(--o);letter-spacing:.05em;white-space:nowrap">UPDATE AVAILABLE</span>
      </div>
      <div id="infra-badge" style="display:none;align-items:center;gap:5px;padding:3px 9px;border-radius:20px;background:rgba(204,26,53,.1);border:1px solid rgba(204,26,53,.3);cursor:pointer" onclick="openSettings();setSettingsTab('infra')" title="">
        <span style="width:6px;height:6px;border-radius:50%;background:var(--r);flex-shrink:0"></span>
        <span id="infra-badge-lbl" style="font-family:var(--font2);font-size:9px;color:var(--r);letter-spacing:.05em;white-space:nowrap">WATCHDOG DOWN</span>
      </div>
      <button class="btn-mute" id="btn-settings" onclick="openSettings()" title="Settings [,]">⚙</button>
      <button class="btn-mute" id="btn-notes" onclick="openNotesPanel()" title="Session notes [O]">📝</button>
      <button class="btn-mute" id="btn-fs" onclick="toggleFullscreen()" title="Fullscreen [Z]">⛶</button>
      <button class="btn-mute" id="btn-mute" onclick="openSoundPanel()" title="Sound settings">🔊</button>
      <button class="btn-mute" id="btn-theme" onclick="toggleTheme()" title="Toggle light/dark theme [H]">🌙</button>
      <button class="btn-mute" id="btn-notif" onclick="openNotifPanel()" title="Notification history [N]">🔔<span class="notif-badge" id="notif-badge" style="display:none">0</span></button>
      <div class="divider"></div>
      <div class="status-online" id="conn-status">
        <div class="dot"></div><span id="conn-label">SYSTEM ONLINE</span>
      </div>
      <div class="divider"></div>
      <div class="clock">
        <div class="clock-time" id="clock">--:--:--</div>
        <div class="clock-date" id="cdate">----.--.--</div>
      </div>
    </div>
  </div>

  <!-- MAIN -->
  <div class="main" id="main-area">
    <div class="view-toggle">
      <!-- Group 1: live-agent visualizations -- 7 different views of the exact same running-agent/session data -->
      <button class="vtbtn active" id="vt-agents" onclick="setView('agents')">AGENTS</button>
      <button class="vtbtn"        id="vt-cli"    onclick="setView('cli')">CLI</button>
      <button class="vtbtn"        id="vt-tl"      onclick="setView('timeline')">TIMELINE</button>
      <button class="vtbtn"        id="vt-summary" onclick="setView('summary')">SUMMARY</button>
      <button class="vtbtn"        id="vt-graph"   onclick="setView('graph')">GRAPH</button>
      <button class="vtbtn"        id="vt-heat"    onclick="setView('heat')">HEAT</button>
      <button class="vtbtn"        id="vt-tree"    onclick="setView('tree')">TREE</button>
      <div class="vt-sep"></div>
      <!-- Group 2: history -- a different data source entirely (past sessions, not live agents) -->
      <button class="vtbtn"        id="vt-history" onclick="setView('history')">HISTORY</button>
      <div class="vt-sep"></div>
      <!-- Group 3: utility tools -- unrelated to agent visualization -->
      <button class="vtbtn"        id="vt-diag"    onclick="setView('diag')">HEALTH</button>
      <button class="vtbtn term-btn" id="vt-term"    onclick="setView('term')">TERM</button>
    </div>
    <div class="kpi-bar" id="kpi-bar">
      <div class="kpi-card" title="Agents currently running">
        <div class="kpi-val" id="kpi-running">0</div>
        <div class="kpi-lbl">RUNNING</div>
      </div>
      <div class="kpi-sep"></div>
      <div class="kpi-card" title="Active CLI sessions">
        <div class="kpi-val" id="kpi-sessions" style="color:var(--c)">—</div>
        <div class="kpi-lbl">SESSIONS</div>
      </div>
      <div class="kpi-sep"></div>
      <div class="kpi-card" title="Agents with errors">
        <div class="kpi-val" id="kpi-errors" style="color:var(--t3)">—</div>
        <div class="kpi-lbl">ERRORS</div>
      </div>
      <div class="kpi-sep"></div>
      <div class="kpi-card" title="Today's agent success rate (done / total)">
        <div class="kpi-val" id="kpi-success" style="color:var(--t3)">—</div>
        <div class="kpi-lbl">SUCCESS</div>
      </div>
      <div class="kpi-card kpi-card-cost" id="kpi-cost-card" title="Live session cost — set budget in Settings">
        <svg class="cost-gauge-svg" id="cost-gauge-svg" viewBox="0 0 70 34">
          <path d="M 7 32 A 28 28 0 0 0 63 32" stroke="rgba(255,255,255,.09)" stroke-width="4.5" fill="none" stroke-linecap="round"/>
          <path id="cost-gauge-arc" d="M 7 32 A 28 28 0 0 0 63 32" stroke="rgba(0,210,130,.85)" stroke-width="4.5" fill="none" stroke-linecap="round" stroke-dasharray="88 89" stroke-dashoffset="88" style="transition:stroke-dashoffset .6s ease,stroke .3s"/>
          <text id="kpi-cost" x="35" y="25" text-anchor="middle" font-family="'JetBrains Mono',monospace" font-size="12" font-weight="800" fill="rgba(0,232,135,.9)" style="transition:fill .3s">$0.000</text>
          <text id="cost-gauge-tok" x="35" y="33" text-anchor="middle" font-family="'JetBrains Mono',monospace" font-size="7" fill="rgba(255,255,255,.28)">— tok</text>
        </svg>
        <div class="kpi-lbl">SESSION COST</div>
      </div>
      <div class="kpi-sep"></div>
      <div class="kpi-card" title="Total agents tracked all time">
        <div class="kpi-val" id="kpi-total">—</div>
        <div class="kpi-lbl">ALL TIME</div>
      </div>
    </div>
    <div id="top-projects-bar" style="display:none;gap:10px;padding:0 0 10px;flex-shrink:0;font-family:var(--font2);font-size:10px;color:var(--t3);align-items:center;flex-wrap:wrap"></div>
    <div id="cards-area">
      <div id="sweep-bar" style="display:flex;gap:6px;padding:0 0 10px;flex-shrink:0">
        <button class="stab" id="btn-sweep-stale" onclick="sweepStaleSessions()" style="margin-left:auto;display:none;color:var(--t3)" title="Dismiss all idle CLI sessions at once">SWEEP&nbsp;<span id="sweep-stale-n" style="opacity:.55;font-weight:400"></span></button>
      </div>
      <div class="search-wrap" id="search-wrap" style="display:none">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
        <input class="search-input" id="search-input" type="text" placeholder="Search agents..." oninput="setSearch(this.value)">
        <button class="search-clear" id="search-clear" onclick="setSearch('')">✕</button>
      </div>
      <div id="session-tab-bar" style="display:none;gap:4px;padding:0 0 6px;flex-wrap:wrap"></div>
      <div id="status-filter-bar" style="display:none;gap:6px;padding:0 0 8px;flex-wrap:wrap"></div>
      <div class="agents" id="agents"></div>
    </div>
    <div id="timeline-area" style="display:none"></div>
    <div id="summary-area" style="display:none;flex-direction:column;gap:0;flex:1;align-items:center"></div>
    <div id="graph-area"   style="display:none;flex:1;align-items:center;justify-content:center;padding:20px;overflow:auto;min-height:0"></div>
    <div id="heat-area"    style="display:none;flex:1;overflow-y:auto;padding:20px 24px;flex-direction:column;align-items:center"></div>
    <div id="diag-area"    style="display:none;flex:1;overflow-y:auto;padding:20px 24px"></div>
    <div id="tree-area"    style="display:none;flex:1;overflow:auto;padding:20px 24px;display:flex;align-items:flex-start;justify-content:center"></div>
    <div id="history-area" style="display:none;flex:1;flex-direction:column;min-height:0"></div>
    <div id="term-area">
      <div id="term-toolbar">
        <span class="tt-title">▶ TERMINAL</span>
        <span class="tt-git" id="term-git"></span>
        <div style="flex:1"></div>
        <button class="adp-btn tt-btn" onclick="_termNew_pub()" title="New shell session">+ SHELL</button>
        <button class="adp-btn tt-btn tt-claude" onclick="_termClaude()" title="Launch Claude CLI in new session">◈ CLAUDE</button>
        <button class="adp-btn tt-btn" onclick="_termClearActive()" title="Clear active terminal">⊘ CLEAR</button>
        <button class="adp-btn tt-btn" onclick="_termKillActive()" title="Kill active session">⊗ KILL</button>
      </div>
      <div id="term-tabs"></div>
      <div id="xterm-area"></div>
    </div>
    <div id="audit-area"   style="display:none;flex:1;flex-direction:column;min-height:0">
      <div style="display:flex;align-items:center;border-bottom:1px solid rgba(255,255,255,.05);flex-shrink:0">
        <div class="adp-tabs" style="flex:1;border-bottom:none" role="tablist">
          <div class="adp-tab active" id="audt-log"   role="tab" aria-selected="true"  tabindex="0" onclick="setAuditTab('log')">LOG</div>
          <div class="adp-tab"        id="audt-files" role="tab" aria-selected="false" tabindex="0" onclick="setAuditTab('files')">FILES</div>
        </div>
        <span id="al-fname" style="color:var(--t3);font-size:9px;font-family:monospace;overflow:hidden;text-overflow:ellipsis;max-width:180px;padding:0 8px"></span>
        <a id="btn-dl" class="btn-dl" href="#" target="_blank" style="display:none;margin-right:12px">↓ DOWNLOAD</a>
      </div>
      <div class="audit-list" id="audit-list" style="flex:1;overflow-y:auto;padding:8px 16px;"></div>
      <div id="audit-files" class="audit-files" style="display:none;padding:8px 16px;flex-wrap:wrap;gap:6px;border-top:1px solid rgba(255,255,255,.05)"></div>
      <div id="audit-file-changes" style="display:none;flex:1;overflow-y:auto;padding:8px 16px;flex-direction:column;gap:4px"></div>
    </div>
  </div>

  <!-- RIGHT PANEL -->
  <div class="right" id="right-panel">
    <button class="panel-toggle" id="panel-toggle-btn" onclick="toggleRightPanel()" title="Toggle sidebar [P]">◀</button>
    <div class="adp-tabs" style="flex-shrink:0;border-top:none;border-bottom:1px solid rgba(255,255,255,.06)" role="tablist">
      <div class="adp-tab active" id="rpt-log"   role="tab" aria-selected="true"  tabindex="0" onclick="setRightTab('log')">LOG</div>
      <div class="adp-tab"        id="rpt-files" role="tab" aria-selected="false" tabindex="0" onclick="setRightTab('files')">FILES</div>
      <div class="adp-tab"        id="rpt-audit" role="tab" aria-selected="false" tabindex="0" onclick="setRightTab('audit')">AUDIT</div>
      <div class="adp-tab"        id="rpt-errors" role="tab" aria-selected="false" tabindex="0" onclick="setRightTab('errors')">ERRORS</div>
      <button id="rp-mobile-close" onclick="_mnavToggleLogs(false)" aria-label="Close" style="display:none;margin-left:auto;margin-right:8px;background:none;border:none;color:var(--t3);font-size:16px;cursor:pointer;padding:4px 8px;line-height:1">✕</button>
    </div>
    <div class="log-area" id="log-area" style="flex:1;overflow-y:auto"></div>
    <div id="rp-files" style="display:none;flex:1;overflow-y:auto;padding:6px 10px;flex-direction:column;gap:4px"></div>
    <div id="rp-audit" style="display:none;flex:1;overflow-y:auto;padding:4px 10px;flex-direction:column;gap:1px"></div>
    <div id="rp-errors-wrap" style="display:none;flex:1;min-height:0;flex-direction:column">
      <div class="search-wrap" id="rp-errors-search" style="padding:6px 10px 4px">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
        <input class="search-input" id="rp-errors-search-input" type="text" placeholder="Search errors..." oninput="_setErrorSearch(this.value)">
        <button class="search-clear" id="rp-errors-search-clear" onclick="_setErrorSearch('')">✕</button>
      </div>
      <div id="rp-errors-types" style="display:none;flex-wrap:wrap;gap:4px;padding:0 10px 6px"></div>
      <div id="rp-errors" style="flex:1;overflow-y:auto;padding:0 10px 6px;display:flex;flex-direction:column;gap:6px"></div>
    </div>
  </div>

  <!-- MOBILE BOTTOM NAV -->
  <nav id="mobile-nav">
    <button class="mnav-btn active" id="mnav-agents" onclick="_mnavGo('agents')">
      <svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
      AGENTS
    </button>
    <button class="mnav-btn" id="mnav-summary" onclick="_mnavGo('summary')">
      <svg viewBox="0 0 24 24"><path d="M3 3h18M3 9h18M3 15h12M3 21h8"/></svg>
      SUMMARY
    </button>
    <button class="mnav-btn" id="mnav-term" onclick="_mnavGo('term')">
      <svg viewBox="0 0 24 24"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
      TERM
    </button>
    <button class="mnav-btn" id="mnav-logs" onclick="_mnavToggleLogs()">
      <svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
      LOGS
    </button>
    <button class="mnav-btn" id="mnav-settings" onclick="openSettings()">
      <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
      SETTINGS
    </button>
  </nav>

  <!-- AGENT COMPARE OVERLAY -->
  <div class="cmp-overlay" id="cmp-overlay" role="dialog" aria-modal="true" aria-label="Agent Compare" onclick="if(event.target===this)closeCompare()">
    <div class="cmp-panel">
      <div class="cmp-hdr">
        <div class="cmp-hdr-title">AGENT COMPARE</div>
        <div style="font-family:var(--font2);font-size:9px;color:var(--t3);margin-right:14px;letter-spacing:.06em" id="cmp-hint">select agents with ⊞ on cards</div>
        <button class="adp-close" onclick="closeCompare()" aria-label="Close">✕</button>
      </div>
      <div class="cmp-body" id="cmp-body"></div>
    </div>
  </div>

  <div class="cmp-overlay" id="sess-cmp-overlay" role="dialog" aria-modal="true" aria-label="Session Compare" onclick="if(event.target===this)closeSessionCompare()">
    <div class="cmp-panel">
      <div class="cmp-hdr">
        <div class="cmp-hdr-title">SESSION COMPARE</div>
        <div style="font-family:var(--font2);font-size:9px;color:var(--t3);margin-right:14px;letter-spacing:.06em" id="sess-cmp-hint">select sessions with ⊞ on cards</div>
        <button class="adp-close" onclick="closeSessionCompare()" aria-label="Close">✕</button>
      </div>
      <div class="cmp-body" id="sess-cmp-body"></div>
    </div>
  </div>

  <!-- KEYBOARD SHORTCUTS OVERLAY -->
  <div class="kb-overlay" id="kb-overlay" role="dialog" aria-modal="true" aria-label="Keyboard Shortcuts" onclick="if(event.target===this)_toggleKbHelp()">
    <div class="kb-panel">
      <div class="kb-hdr">
        <div class="kb-hdr-title">KEYBOARD SHORTCUTS</div>
        <div style="font-family:var(--font2);font-size:9px;color:var(--t3);margin-right:14px;letter-spacing:.08em">press <kbd class="kb-key" style="font-size:9px;padding:0 5px">?</kbd> or ESC to close</div>
        <button class="adp-close" onclick="_toggleKbHelp()" aria-label="Close">✕</button>
      </div>
      <div id="kb-body" style="display:grid;grid-template-columns:1fr 1fr;gap:0 36px"></div>
    </div>
  </div>

  <!-- COMMAND PALETTE -->
  <div class="palette-overlay" id="palette-overlay" role="dialog" aria-modal="true" aria-label="Command Palette" onclick="if(event.target===this)closePalette()">
    <div class="palette-panel">
      <input id="palette-input" class="palette-input" type="text" placeholder="Jump to a session, agent, or setting…" spellcheck="false" autocomplete="off" oninput="_paletteRender(this.value)" onkeydown="_paletteInputKeydown(event)">
      <div id="palette-results" class="palette-results"></div>
    </div>
  </div>

  <!-- NOTIFICATION HISTORY PANEL -->
  <div class="notif-overlay" id="notif-overlay" role="dialog" aria-modal="true" aria-label="Notification History" onclick="if(event.target===this)closeNotifPanel()">
    <div class="notif-panel">
      <div class="notif-hdr">
        <div class="notif-hdr-title">NOTIFICATION HISTORY</div>
        <div class="notif-hdr-count" id="notif-hdr-count">—</div>
        <button class="adp-btn" onclick="clearNotifHistory()" style="padding:2px 10px;font-size:9px">CLEAR</button>
        <button class="adp-close" onclick="closeNotifPanel()" aria-label="Close">✕</button>
      </div>
      <div class="notif-list" id="notif-list"></div>
    </div>
  </div>

  <!-- SESSION NOTES PANEL -->
  <div class="notes-overlay" id="notes-overlay" role="dialog" aria-modal="true" aria-labelledby="notes-title-lbl" onclick="if(event.target===this)closeNotesPanel()">
    <div class="notes-panel">
      <div class="notif-hdr">
        <div class="notif-hdr-title" id="notes-title-lbl">SESSION NOTES</div>
        <div style="font-size:9px;font-family:var(--font2);color:var(--t3);flex:1;padding-left:8px" id="notes-subtitle-lbl">saved to session history</div>
        <button class="adp-close" onclick="closeNotesPanel()" aria-label="Close">✕</button>
      </div>
      <div style="padding:14px 16px;flex:1;display:flex;flex-direction:column;gap:10px">
        <textarea id="notes-ta" placeholder="Čo si robil v tejto session..." style="flex:1;min-height:180px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.1);border-radius:9px;color:var(--t1);font-family:var(--font2);font-size:12px;line-height:1.65;padding:10px 12px;resize:none;outline:none;transition:border-color .2s" onfocus="this.style.borderColor='rgba(var(--c-rgb),.35)'" onblur="this.style.borderColor='rgba(255,255,255,.1)'"></textarea>
        <div style="display:flex;gap:8px;justify-content:flex-end">
          <button class="adp-btn" onclick="saveNotes()" style="padding:5px 16px;font-size:10px;letter-spacing:.08em">SAVE</button>
          <button class="adp-btn" onclick="clearNotes()" style="padding:5px 12px;font-size:10px;color:var(--t3)">CLEAR</button>
        </div>
        <div id="notes-saved-msg" style="display:none;font-size:10px;font-family:var(--font2);color:var(--g);text-align:right;letter-spacing:.07em">✓ Saved</div>
      </div>
    </div>
  </div>

  <!-- SETTINGS PANEL -->
  <div class="settings-overlay" id="settings-overlay" role="dialog" aria-modal="true" aria-label="Settings" onclick="if(event.target===this)closeSettings()">
    <div class="settings-panel">
      <div class="notif-hdr">
        <div class="notif-hdr-title">SETTINGS</div>
        <button class="adp-close" onclick="closeSettings()" aria-label="Close">✕</button>
      </div>
      <div class="settings-tabs" role="tablist">
        <div class="settings-tab active" data-pane="cost" role="tab" aria-selected="true"  tabindex="0" onclick="setSettingsTab('cost')">COST</div>
        <div class="settings-tab" data-pane="notify" role="tab" aria-selected="false" tabindex="0" onclick="setSettingsTab('notify')">NOTIFY</div>
        <div class="settings-tab" data-pane="look" role="tab" aria-selected="false" tabindex="0" onclick="setSettingsTab('look')">LOOK</div>
        <div class="settings-tab" data-pane="access" role="tab" aria-selected="false" tabindex="0" onclick="setSettingsTab('access')">ACCESS</div>
        <div class="settings-tab" data-pane="tunnel" role="tab" aria-selected="false" tabindex="0" onclick="setSettingsTab('tunnel')">TUNNEL</div>
        <div class="settings-tab" data-pane="remote" role="tab" aria-selected="false" tabindex="0" onclick="setSettingsTab('remote')">REMOTE</div>
        <div class="settings-tab" data-pane="infra" role="tab" aria-selected="false" tabindex="0" onclick="setSettingsTab('infra')">INFRA</div>
        <div class="settings-tab" data-pane="license" role="tab" aria-selected="false" tabindex="0" onclick="setSettingsTab('license')">LICENSE</div>
      </div>
      <div class="settings-body">

        <div class="settings-pane" data-pane="cost">
        <div class="settings-section">COST &amp; BUDGET</div>
        <div class="settings-row">
          <label class="settings-lbl">Cost rate ($/M tokens)</label>
          <input class="settings-input" id="st-cost-rate" type="number" min="0.1" max="100" step="0.1" value="9.0" title="Claude Sonnet ~$9/M avg">
        </div>
        <div class="settings-row">
          <label class="settings-lbl">Budget alert ($/session)</label>
          <input class="settings-input" id="st-budget" type="number" min="0" step="0.01" placeholder="e.g. 1.00" title="Show red KPI when exceeded">
        </div>
        <div class="settings-row" style="align-items:flex-start">
          <label class="settings-lbl" style="padding-top:6px">Per-project budgets</label>
          <textarea class="settings-input settings-input-wide" id="st-project-budgets" rows="3" spellcheck="false"
            placeholder='{"AOC": 5, "PHANTOM AI": 10}'
            title="JSON object: project name -> monthly budget in $. Toast + webhook (if enabled below) fire once per project per calendar month when month-to-date spend crosses it -- works even with no browser tab open."
            style="font-family:var(--font2);font-size:11px;resize:vertical"></textarea>
        </div>
        <div id="st-project-budget-summary" style="display:flex;flex-direction:column;gap:2px;font-family:var(--font2);font-size:9px;margin:2px 0 8px"></div>
        <div class="settings-row">
          <label class="settings-lbl">Projected month-end spend</label>
          <span id="st-projected-spend" style="font-family:var(--font2);font-size:11px;color:var(--t2)" title="Simple run-rate projection: (cost so far this month / days elapsed) x days remaining, added to cost so far">—</span>
        </div>

        </div>

        <div class="settings-pane" data-pane="notify" style="display:none">
        <div class="settings-section">NOTIFICATIONS</div>
        <div class="settings-row">
          <label class="settings-lbl">Webhook URL</label>
          <input class="settings-input settings-input-wide" id="st-webhook" type="text" placeholder="https://hooks.slack.com/..." spellcheck="false">
        </div>
        <div class="settings-row" style="gap:16px;flex-wrap:wrap">
          <label class="settings-toggle"><input type="checkbox" id="st-wh-done" checked> Agent done</label>
          <label class="settings-toggle"><input type="checkbox" id="st-wh-error" checked> Agent error</label>
          <label class="settings-toggle"><input type="checkbox" id="st-wh-stuck"> Agent stuck</label>
          <label class="settings-toggle"><input type="checkbox" id="st-wh-burn"> Token burn spike</label>
          <label class="settings-toggle"><input type="checkbox" id="st-wh-digest"> Weekly digest</label>
          <label class="settings-toggle"><input type="checkbox" id="st-wh-waiting"> Waiting too long</label>
          <label class="settings-toggle"><input type="checkbox" id="st-wh-cost"> Cost spike</label>
          <label class="settings-toggle"><input type="checkbox" id="st-wh-budget"> Budget exceeded</label>
        </div>
        <div class="settings-row" style="justify-content:flex-end">
          <button class="adp-btn" onclick="testWebhook()" style="padding:4px 14px;font-size:9px">TEST WEBHOOK</button>
          <span id="st-wh-status" style="font-size:9px;font-family:var(--font2);color:var(--t3);margin-left:8px"></span>
        </div>
        <div class="settings-row" style="gap:10px">
          <label class="settings-lbl">Quiet hours</label>
          <input class="settings-input" id="st-quiet-start" type="time" style="width:auto" title="No sound/toast/webhook from this time...">
          <span style="color:var(--t3);font-size:11px">to</span>
          <input class="settings-input" id="st-quiet-end" type="time" style="width:auto" title="...until this time (overnight windows like 22:00-07:00 are fine)">
        </div>
        <div class="settings-row" style="align-items:flex-start">
          <label class="settings-lbl" style="padding-top:6px">Muted projects</label>
          <textarea class="settings-input settings-input-wide" id="st-muted-projects" rows="2" spellcheck="false"
            placeholder="AOC, PHANTOM AI"
            title="Comma-separated project names -- never notify for these, regardless of quiet hours."
            style="font-family:var(--font2);font-size:11px;resize:vertical"></textarea>
        </div>
        <div class="settings-row">
          <label class="settings-lbl">Dead man's snitch URL</label>
          <input class="settings-input settings-input-wide" id="st-snitch-url" type="text" spellcheck="false"
            placeholder="https://hc-ping.com/..."
            title="AOC pings this URL every 10 min. Works with healthchecks.io / Cronitor / UptimeRobot-style 'ping-or-alert' services -- if the pings ever stop (machine off, AOC dead), THAT service notifies you, since nothing on this machine can once it's the thing that's down.">
        </div>
        <div class="settings-row">
          <label class="settings-lbl">Digest cadence</label>
          <select class="settings-input" id="st-digest-cadence" title="How often the cost/activity digest (native toast + optional webhook) is sent">
            <option value="weekly">Weekly (Monday 9am)</option>
            <option value="daily">Daily (9am)</option>
            <option value="off">Off</option>
          </select>
        </div>
        <div class="settings-row" style="justify-content:flex-end">
          <button class="adp-btn" onclick="_snitchPingNow()" style="padding:4px 14px;font-size:9px">PING NOW</button>
          <span id="st-snitch-status" style="font-size:9px;font-family:var(--font2);color:var(--t3);margin-left:8px"></span>
        </div>

        </div>

        <div class="settings-pane" data-pane="look" style="display:none">
        <div class="settings-section">APPEARANCE</div>
        <div class="settings-row" style="gap:8px;flex-wrap:wrap">
          <label class="settings-lbl">Accent color</label>
          <div style="display:flex;gap:6px">
            <button class="accent-swatch" data-accent="cyan"   style="--sc:rgba(var(--c-rgb),1)"   onclick="setAccent('cyan')"></button>
            <button class="accent-swatch" data-accent="purple" style="--sc:rgba(140,80,255,1)"  onclick="setAccent('purple')"></button>
            <button class="accent-swatch" data-accent="green"  style="--sc:rgba(0,220,120,1)"   onclick="setAccent('green')"></button>
            <button class="accent-swatch" data-accent="orange" style="--sc:rgba(255,140,40,1)"  onclick="setAccent('orange')"></button>
            <button class="accent-swatch" data-accent="pink"   style="--sc:rgba(255,80,180,1)"  onclick="setAccent('pink')"></button>
          </div>
        </div>
        <div class="settings-row">
          <label class="settings-lbl">Card density</label>
          <select class="settings-input" id="st-density" style="padding:4px 8px">
            <option value="normal">Normal</option>
            <option value="compact">Compact</option>
            <option value="comfortable">Comfortable</option>
          </select>
        </div>

        </div>

        <div class="settings-pane" data-pane="access" style="display:none">
        <div class="settings-section">NETWORK ACCESS</div>
        <div id="auth-panel" style="display:flex;flex-direction:column;gap:8px">
          <div style="font-size:10px;font-family:var(--font2);color:var(--t3)" id="auth-status-lbl">Loading...</div>
          <div id="auth-url-row" style="display:none;flex-direction:column;gap:5px">
            <div style="display:flex;align-items:center;gap:6px">
              <input id="auth-url-input" readonly style="flex:1;background:rgba(var(--c-rgb),.05);border:1px solid rgba(var(--c-rgb),.15);border-radius:6px;color:var(--c);font-family:var(--font2);font-size:9px;padding:5px 8px;outline:none;cursor:pointer" onclick="this.select()" title="Click to select all">
              <button class="adp-btn" onclick="_authCopyUrl()" style="white-space:nowrap">COPY URL</button>
            </div>
            <div style="display:flex;align-items:center;gap:6px">
              <span style="font-size:9px;font-family:var(--font2);color:var(--t3)">TOKEN:</span>
              <code id="auth-token-display" style="font-size:9px;font-family:var(--font2);color:rgba(var(--c-rgb),.8);word-break:break-all"></code>
            </div>
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap">
            <button class="adp-btn" id="auth-gen-btn" onclick="_authGenerate()" style="background:rgba(var(--c-rgb),.1);border-color:rgba(var(--c-rgb),.3);color:var(--c)">⚡ GENERATE TOKEN</button>
            <button class="adp-btn" id="auth-del-btn" onclick="_authDisable()" style="display:none;background:rgba(255,51,85,.07);border-color:rgba(255,51,85,.25);color:var(--r)">✕ DISABLE</button>
          </div>
          <div id="auth-note" style="font-size:9px;color:var(--t3);font-family:var(--font2);line-height:1.6"></div>
        </div>

        </div>

        <div class="settings-pane" data-pane="tunnel" style="display:none">
        <div class="settings-section">REMOTE ACCESS</div>
        <div id="tunnel-panel" style="display:flex;flex-direction:column;gap:8px">
          <div style="display:flex;gap:6px" id="tunnel-provider-row">
            <button class="adp-btn" id="tunnel-provider-cf" onclick="_tunnelSetProvider('cloudflare')" style="flex:1;background:rgba(var(--c-rgb),.18)">CLOUDFLARE</button>
            <button class="adp-btn" id="tunnel-provider-ngrok" onclick="_tunnelSetProvider('ngrok')" style="flex:1">NGROK</button>
          </div>
          <div id="tunnel-desc-cf" style="font-size:10px;font-family:var(--font2);color:var(--t3);line-height:1.55">
            Creates a public HTTPS URL without port forwarding.<br>
            Requires <code style="color:var(--c)">cloudflared.exe</code> (downloaded automatically, ~30 MB).
          </div>
          <div id="tunnel-desc-ngrok" style="display:none;font-size:10px;font-family:var(--font2);color:var(--t3);line-height:1.55">
            Requires an ngrok account and authtoken (free) —
            <a href="https://dashboard.ngrok.com/get-started/your-authtoken" target="_blank" style="color:var(--c)">dashboard.ngrok.com/get-started/your-authtoken</a>.
          </div>
          <div id="tunnel-ngrok-missing" style="display:none;font-size:10px;font-family:var(--font2);color:var(--o);line-height:1.55">
            <code style="color:var(--o)">ngrok.exe</code> not found — install via <code style="color:var(--o)">winget install Ngrok.Ngrok</code> or add it to PATH.
          </div>
          <div id="tunnel-ngrok-auth-row" style="display:none;flex-direction:column;gap:5px">
            <div style="display:flex;align-items:center;gap:6px">
              <input id="tunnel-ngrok-token-input" type="password" placeholder="ngrok authtoken" style="flex:1;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.1);border-radius:6px;color:var(--t1);font-family:var(--font2);font-size:9px;padding:5px 8px;outline:none">
              <button class="adp-btn" onclick="_tunnelSaveNgrokToken()" style="white-space:nowrap">SAVE TOKEN</button>
            </div>
            <div id="tunnel-ngrok-auth-msg" style="font-size:9px;font-family:var(--font2)"></div>
          </div>
          <div id="tunnel-status-row" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <span id="tunnel-status-lbl" style="font-size:10px;font-family:var(--font2);color:var(--t3)">Status: OFF</span>
            <button class="adp-btn" id="tunnel-start-btn" onclick="_tunnelStart()" style="background:rgba(var(--c-rgb),.1);border-color:rgba(var(--c-rgb),.3);color:var(--c)">▶ START TUNNEL</button>
            <button class="adp-btn" id="tunnel-stop-btn" onclick="_tunnelStop()" style="display:none;background:rgba(255,51,85,.07);border-color:rgba(255,51,85,.25);color:var(--r)">■ STOP</button>
          </div>
          <div id="tunnel-error-detail" style="display:none;font-size:9px;font-family:var(--font2);color:var(--r);word-break:break-word"></div>
          <div id="tunnel-url-row" style="display:none;flex-direction:column;gap:5px">
            <div style="display:flex;align-items:center;gap:6px">
              <input id="tunnel-url-input" readonly style="flex:1;background:rgba(var(--c-rgb),.05);border:1px solid rgba(var(--c-rgb),.15);border-radius:6px;color:var(--c);font-family:var(--font2);font-size:9px;padding:5px 8px;outline:none;cursor:pointer" onclick="this.select()" title="Click to select">
              <button class="adp-btn" onclick="_tunnelCopyUrl()" style="white-space:nowrap">COPY</button>
            </div>
            <div style="font-size:9px;color:var(--t3);font-family:var(--font2)">
              Token: <code id="tunnel-token-note" style="color:rgba(var(--c-rgb),.7)"></code>
            </div>
          </div>
          <div id="tunnel-dl-row" style="display:none;flex-direction:column;gap:4px">
            <div style="font-size:9px;font-family:var(--font2);color:var(--t3)" id="tunnel-dl-lbl">Downloading cloudflared...</div>
            <div style="height:3px;background:rgba(255,255,255,.08);border-radius:999px;overflow:hidden">
              <div id="tunnel-dl-bar" style="height:100%;width:0%;background:rgba(var(--c-rgb),.7);border-radius:999px;transition:width .3s"></div>
            </div>
          </div>
        </div>

        </div>

        <div class="settings-pane" data-pane="remote" style="display:none">
        <div class="settings-section">REMOTE MACHINES</div>
        <div style="font-size:9px;font-family:var(--font2);color:var(--t3);line-height:1.5;margin-bottom:6px">
          Shows sessions and agents from other machines running AOC (same LAN). The target machine needs a generated token (REMOTE ACCESS above) even for pure LAN access — without a token it refuses even a LAN request. View-only: Force Stop only ever works on this machine.
        </div>
        <div class="settings-row">
          <label class="settings-lbl">This machine</label>
          <input class="settings-input" id="st-local-machine-name" type="text" placeholder="e.g. Desktop" style="max-width:160px">
        </div>
        <div id="remote-machines-list" style="display:flex;flex-direction:column;gap:6px;margin:4px 0"></div>
        <button class="adp-btn" id="btn-add-remote-machine" onclick="_addRemoteMachineRow()" style="align-self:flex-start;font-size:9px;padding:3px 12px">+ ADD MACHINE</button>
        </div>

        <div class="settings-pane" data-pane="license" style="display:none">
        <div class="settings-section">LICENSE</div>
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
          <span id="license-tier-badge" style="font-family:var(--font2);font-size:10px;letter-spacing:.08em;padding:3px 10px;border-radius:20px"></span>
          <span id="license-email-note" style="font-size:9px;font-family:var(--font2);color:var(--t3)"></span>
        </div>
        <div style="font-size:9px;font-family:var(--font2);color:var(--t3);line-height:1.5;margin-bottom:10px">
          Free covers every live view, local History &amp; Analytics, native toast notifications, backups, and the terminal grid.
          Pro unlocks Remote Machines, the remote-access tunnel, webhook delivery, per-project monthly budgets, and CSV exports.
        </div>
        <div id="license-active-row" style="display:none;align-items:center;gap:8px;margin-bottom:10px">
          <button class="adp-btn" onclick="_deactivateLicense()" style="color:var(--r)">DEACTIVATE</button>
        </div>
        <div id="license-inactive-row" style="display:flex;flex-direction:column;gap:6px">
          <div style="display:flex;gap:6px">
            <input id="license-key-input" class="settings-input" type="text" placeholder="AOC-PRO-..." style="flex:1;font-family:var(--font2)">
            <button class="adp-btn" onclick="_activateLicense()" style="white-space:nowrap">ACTIVATE</button>
          </div>
          <a href="#" onclick="return false" id="license-buy-link" style="font-size:9px;font-family:var(--font2);color:var(--c);text-decoration:underline;text-decoration-style:dotted;align-self:flex-start">Buy Pro →</a>
        </div>
        <div id="license-msg" style="font-size:9px;font-family:var(--font2);margin-top:8px"></div>
        </div>

        <div class="settings-pane" data-pane="infra" style="display:none">
        <div class="settings-section">INFRASTRUCTURE HEALTH</div>
        <div style="font-size:9px;font-family:var(--font2);color:var(--t3);line-height:1.5;margin-bottom:6px">
          watchdog.py restarts monitor.py when it hangs; sentinel.py watches over watchdog.py. This panel shows their live status without needing to check the log files by hand.
          For this process's own uptime/memory/history.db size/backups see
          <a href="#" onclick="closeSettings();setView('diag');return false" style="color:var(--c);text-decoration:underline;text-decoration-style:dotted">DIAG →</a>
        </div>
        <div style="display:flex;flex-direction:column;gap:4px;padding:8px 10px;border-radius:8px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);margin-bottom:8px">
          <div style="display:flex;align-items:center;gap:6px">
            <span id="infra-wd-dot" style="width:7px;height:7px;border-radius:50%;flex-shrink:0"></span>
            <span style="font-family:var(--font2);font-size:10px;letter-spacing:.06em;color:var(--t2)">WATCHDOG</span>
            <span id="infra-wd-status" style="font-family:var(--font2);font-size:10px;margin-left:auto"></span>
          </div>
          <div id="infra-wd-detail" style="font-size:9px;color:var(--t3);font-family:var(--font2)"></div>
        </div>
        <div style="display:flex;flex-direction:column;gap:4px;padding:8px 10px;border-radius:8px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);margin-bottom:8px">
          <div style="display:flex;align-items:center;gap:6px">
            <span id="infra-sn-dot" style="width:7px;height:7px;border-radius:50%;flex-shrink:0"></span>
            <span style="font-family:var(--font2);font-size:10px;letter-spacing:.06em;color:var(--t2)">SENTINEL</span>
            <span id="infra-sn-status" style="font-family:var(--font2);font-size:10px;margin-left:auto"></span>
          </div>
          <div id="infra-sn-detail" style="font-size:9px;color:var(--t3);font-family:var(--font2)"></div>
        </div>
        <div style="font-size:9px;color:var(--t3);font-family:var(--font2);margin-bottom:8px">Monitor running: <span id="infra-uptime"></span></div>
        <div class="settings-row">
          <label class="settings-lbl">Backup retention (days)</label>
          <input class="settings-input" id="st-backup-retention" type="number" min="1" max="365" step="1" title="history.db snapshots older than this are auto-deleted every hour. Corrupt backups are always kept for inspection regardless of age.">
        </div>
        <div id="infra-backup-summary" style="font-size:9px;color:var(--t3);font-family:var(--font2)"></div>
        <div class="settings-row">
          <label class="settings-lbl">Agent retention (hours)</label>
          <input class="settings-input" id="st-agent-retention" type="number" min="1" max="23" step="1" title="Completed/errored subagents older than this are auto-cleared every ~15min, keeping TIMELINE/SUMMARY/GRAPH/HEAT/TREE showing recent activity instead of growing forever. Capped under 24h since completion times are stored as time-of-day only (no date).">
        </div>
        <div id="infra-agent-retention-summary" style="font-size:9px;color:var(--t3);font-family:var(--font2)"></div>
        <div class="settings-row">
          <label class="settings-lbl" title="How often an agent's start was only ever caught by the transcript fallback scanner instead of Claude Code's own PreToolUse hook, and whether that correlates with more sessions running at once">Hook reliability</label>
          <span id="infra-hook-reliability" style="font-size:9px;color:var(--t3);font-family:var(--font2)">—</span>
        </div>
        <div class="settings-row">
          <label class="settings-lbl" title="Every webhook event AOC actually dispatched, from logs/alert_audit.log">Alerts fired (7d)</label>
          <span id="infra-alert-audit" style="font-size:9px;color:var(--t3);font-family:var(--font2)">—</span>
        </div>
        <div class="settings-row">
          <label class="settings-lbl" title="Every Force Stop call, successful or refused, from logs/kill_audit.log">Force stops (7d)</label>
          <span id="infra-kill-audit" style="font-size:9px;color:var(--t3);font-family:var(--font2)">—</span>
        </div>
        </div>

        <div style="margin-top:auto;padding-top:16px;display:flex;gap:8px;align-items:center">
          <button class="adp-btn" onclick="exportAllSettings()" title="Download every setting on this page as one JSON file" style="padding:5px 12px;font-size:10px;color:var(--t3)">EXPORT</button>
          <button class="adp-btn" onclick="triggerImportSettings()" title="Load settings from a previously exported JSON file (reloads the page to apply)" style="padding:5px 12px;font-size:10px;color:var(--t3)">IMPORT</button>
          <input type="file" id="settings-import-input" accept=".json" style="display:none" onchange="importAllSettings(this)">
          <div style="flex:1"></div>
          <button class="adp-btn" onclick="saveSettings()" style="padding:5px 18px;font-size:10px;background:rgba(var(--c-rgb),.12);border-color:rgba(var(--c-rgb),.35);color:var(--c)">SAVE</button>
          <button class="adp-btn" onclick="closeSettings()" style="padding:5px 12px;font-size:10px;color:var(--t3)">CANCEL</button>
        </div>
        <div id="st-saved-msg" style="display:none;font-size:10px;font-family:var(--font2);color:var(--g);text-align:right;margin-top:6px;letter-spacing:.07em">✓ Settings saved</div>
      </div>
    </div>
  </div>

  <!-- AGENT DETAIL PANEL -->
  <div class="agent-detail-overlay" id="agent-detail-overlay" role="dialog" aria-modal="true" aria-label="Agent Detail" onclick="if(event.target===this)closeAgentDetail()">
    <div class="agent-detail-panel">
      <div class="adp-hdr" id="adp-hdr"></div>
      <div class="adp-tabs" role="tablist">
        <div class="adp-tab active" id="adpt-log"   role="tab" aria-selected="true"  tabindex="0" onclick="setAdpTab('log')">LOG</div>
        <div class="adp-tab"        id="adpt-tasks" role="tab" aria-selected="false" tabindex="0" onclick="setAdpTab('tasks')">TASKS</div>
        <div class="adp-tab"        id="adpt-files" role="tab" aria-selected="false" tabindex="0" onclick="setAdpTab('files')">FILES</div>
      </div>
      <div class="adp-body" id="adp-body"></div>
    </div>
  </div>

</div>

<script>
/* ── auth token injected by server ── */
window._AOC_TOKEN = '__AOC_TOKEN_PLACEHOLDER__';
/* ── license tier injected by server (same pattern as the token above) ── */
window._AOC_IS_PRO = __AOC_IS_PRO_PLACEHOLDER__;
/* wrapper always installed (not gated on a token existing at load time) — a
   Cloudflare tunnel can provision a token later in this same session, and
   subsequent fetches must start carrying it without a page reload */
(()=>{
  const _origFetch=window.fetch;
  window.fetch=(url,opts={})=>{
    if(window._AOC_TOKEN&&typeof url==='string'&&!url.startsWith('http')){
      opts={...opts,headers:{'X-AOC-Token':window._AOC_TOKEN,...(opts.headers||{})}};
    }
    return _origFetch(url,opts);
  };
})();

let logs = [];
let lastStatus = null;
let fcCount = 0;
let currentView = 'agents';
let statusFilter = 'all';
let sessionFilter = null;
let listMode = false;
let searchQuery = '';
let pollFails = 0;
let _connOnline = true;
let _soundMuted = false;
let _soundPrefs = { volume: 0.7, profile: 'default', events: { done: true, error: true, running: false, stuck: false, burn_spike: false, waiting_nudge: false, cost_spike: false } };
let _soundPanelOpen = false;
// First-run default follows the OS theme preference; _loadPrefs() below
// unconditionally overrides this the moment a user has ever manually
// toggled the theme, so an explicit choice always wins over the OS
// setting -- this only ever matters before that first toggle happens.
let _theme = (typeof matchMedia!=='undefined' && matchMedia('(prefers-color-scheme: light)').matches) ? 'light' : 'dark';
let _collapsedIds = new Set();
let _pinnedIds = new Set();
let _prevTokens = {};
let _notifHistory = [];
let _notifUnread = 0;
let _notifOpen = false;
let _tlZoom = 1;
let _tlZoomFocus = 0.5;
const _NOTIF_MAX = 300;
const _knownAgentIds = new Set();
const _completedTaskKeys = new Set();

/* ── helpers ── */
function pad(n){ return String(n).padStart(2,'0'); }
function ts(){
  const d=new Date();
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
function parseTimeStr(s){
  if(!s) return null;
  if(s.includes('T')) return new Date(s).getTime();
  /* HH:MM:SS — handle midnight crossover: a past event can't be in the future,
     so any computed time more than a few seconds ahead (clock/render jitter) must be
     yesterday's timestamp. A large threshold here would misjudge near-24h-old events
     as "today" instead of "yesterday". */
  const [h,m,sec]=(s||'').split(':').map(Number);
  const d=new Date(); d.setHours(h||0,m||0,sec||0,0);
  const t=d.getTime();
  return t>Date.now()+5000 ? t-86400000 : t;
}

/* ── unit abbreviation helper ── */
function _deriveUnit(a){
  if(a.unit && a.unit!=='??') return a.unit;
  if(a.icon && a.icon!=='??') return a.icon;
  const words=(a.name||'').split(/[\s\-_.()]+/).filter(Boolean);
  if(words.length>=2) return (words[0][0]+words[1][0]).toUpperCase();
  if(words.length===1) return words[0].slice(0,3).toUpperCase();
  return String(a.id).slice(-3).toUpperCase();
}

/* ── log ── */
function log(msg, type='info', tag=''){
  const _t=ts();
  logs.push({t:_t, msg, type, tag});
  if(logs.length>400) logs.shift();
  _notifHistory.push({t:_t, type, msg:String(msg), tag:tag||''});
  if(_notifHistory.length>_NOTIF_MAX) _notifHistory.shift();
  if(!_notifOpen){ _notifUnread++; _updateNotifBadge(); }
  else _renderNotifPanel();
  renderLog();
}
function renderLog(){
  const el=document.getElementById('log-area');
  if(!el) return;
  if(!logs.length){
    el.innerHTML='<div style="color:var(--t3);font-family:var(--font2);font-size:10px;text-align:center;padding:30px 0;letter-spacing:.08em">// awaiting events</div>';
    return;
  }
  el.innerHTML=logs.slice().reverse().slice(0,100).map(e=>
    `<div class="le"><span class="le-t">[${e.t}]</span>${e.tag?`<span class="le-tag">${e.tag}</span>`:''}<span class="le-m ${e.type}">${e.msg}</span></div>`
  ).join('');
}

/* ── arc ── */
function arcDash(pct, r=16){
  const c=2*Math.PI*r;
  return {c, d:Math.min(pct/100,1)*c};
}

/* ── color by status ── */
function sCol(s){ return {running:'var(--c)',done:'var(--g)',error:'var(--r)',waiting:'var(--o)'}[s]||'var(--c)'; }
/* ── status label by status ──
   Genuinely global (unlike the identically-named function that used to live only
   inside _renderCompare(), which was invisible to every other call site). Commit
   5d42e48 deduped 4 local `const stxt={...}` objects into calls to this function
   on the assumption a shared global already existed — it didn't, so every one of
   those call sites (main card render in both grid and list mode included) has
   been throwing ReferenceError: stxt is not defined on every render since. */
function stxt(ag){ return ({running:'ACTIVE',done:'COMPLETE',error:'ERROR',waiting:'STANDBY'})[ag.status]||(ag.status||'').toUpperCase(); }

/* ── connection health ── */
function _setConnStatus(online){
  if(_connOnline===online) return;
  _connOnline=online;
  const cs=document.getElementById('conn-status');
  const cl=document.getElementById('conn-label');
  const ov=document.getElementById('reconnect-overlay');
  if(cs) cs.classList.toggle('offline',!online);
  if(cl) cl.textContent=online?'SYSTEM ONLINE':'OFFLINE';
  if(ov) ov.classList.toggle('show',!online);
}

/* ── status filter bar ── */
function renderStatusFilterBar(agents){
  const bar=document.getElementById('status-filter-bar');
  if(!bar) return;
  const counts={all:agents.length,running:0,done:0,error:0};
  agents.forEach(a=>{
    if(a.status==='running'||a.status==='waiting') counts.running++;
    else if(a.status==='done') counts.done++;
    else if(a.status==='error') counts.error++;
  });
  if(agents.length===0){ bar.style.display='none'; return; }
  bar.style.display='flex';
  const btns=[
    {f:'all',label:'ALL',cls:''},
    {f:'running',label:`RUNNING ${counts.running}`,cls:'running'},
    {f:'done',label:`DONE ${counts.done}`,cls:'done'},
    {f:'error',label:`ERROR ${counts.error}`,cls:'error'},
  ];
  const clearBtn=counts.done>0?`<button class="density-btn" onclick="clearDoneAgents()" title="Remove all completed agents [X]" style="margin-left:4px;border-color:rgba(0,232,135,.25);color:rgba(0,232,135,.65)">✕ CLEAR DONE</button>`:'';
  const collapseBtn=counts.done>0?`<button class="density-btn" onclick="collapseAllDone()" title="Collapse all done cards [D]" style="border-color:rgba(var(--c-rgb),.2);color:rgba(var(--c-rgb),.6)">⊟ COLLAPSE DONE</button>`:'';
  bar.innerHTML=btns.map(b=>
    `<button class="sfbtn ${b.cls} ${statusFilter===b.f?'active':''}" onclick="setStatusFilter('${b.f}')">${b.label}</button>`
  ).join('')+`<button class="density-btn ${listMode?'active':''}" onclick="toggleListMode()" title="Toggle compact list view [L]">${listMode?'⊟ LIST':'⊞ GRID'}</button>`+collapseBtn+clearBtn;
}
function setStatusFilter(f){
  statusFilter=f;
  _savePrefs();
  if(lastStatus) renderAgents(lastStatus);
}
function toggleListMode(){
  listMode=!listMode;
  _savePrefs();
  if(lastStatus) renderAgents(lastStatus);
}
function setSearch(val){
  searchQuery=val.toLowerCase();
  const inp=document.getElementById('search-input');
  const clr=document.getElementById('search-clear');
  if(inp && inp.value!==val) inp.value=val;
  if(clr) clr.classList.toggle('show', val.length>0);
  if(lastStatus) renderAgents(lastStatus);
}

/* ── session tab bar ── */
function renderSessionTabBar(data, agents){
  const bar=document.getElementById('session-tab-bar');
  if(!bar) return;
  const sessions=data.sessions_list||[];
  /* collect session IDs that have agents */
  const sessIds=new Set(agents.map(a=>a.session_id).filter(Boolean));
  const activeSessions=sessions.filter(s=>sessIds.has(s.id));
  if(activeSessions.length<=1){ bar.style.display='none'; return; }
  bar.style.display='flex';
  const allBtn=`<button class="stab ${sessionFilter===null?'active':''}" onclick="setSessionFilter(null)">ALL CLIs</button>`;
  const sessBtns=activeSessions.map(s=>{
    const cwdName=s.cwd?(s.cwd.replace(/\\/g,'/').split('/').filter(Boolean).pop()||''):'';
    const label=(s.display_name||s.project||cwdName||s.id.slice(-6)).slice(0,30);
    const cnt=agents.filter(a=>a.session_id===s.id).length;
    const isActive=s.session_active!==false;
    const dimBtn=(!isActive&&s._isLocal!==false)?`<button onclick="event.stopPropagation();_dismissSession('${s.id}')" title="Dismiss closed session" style="background:none;border:1px solid rgba(255,255,255,.08);border-left:none;border-radius:0 7px 7px 0;color:var(--t3);cursor:pointer;padding:3px 6px;font-size:10px;line-height:1;transition:color .2s" onmouseover="this.style.color='var(--r)'" onmouseout="this.style.color='var(--t3)'">✕</button>`:'';
    return `<span style="display:inline-flex;align-items:center;gap:0">
      <button class="stab ${sessionFilter===s.id?'active':''}" onclick="setSessionFilter('${s.id}')" title="${s.id}">${label} (${cnt})</button>${dimBtn}</span>`;
  }).join('');
  bar.innerHTML=allBtn+sessBtns;
}
function setSessionFilter(id){
  sessionFilter=id;
  if(lastStatus) renderAgents(lastStatus);
}
async function _dismissSession(id,machineName){
  try{
    await fetch(_apiUrl(_machineFor(machineName),`/session/${encodeURIComponent(id)}`),{method:'DELETE'});
  }catch(e){}
  /* immediately remove from local state so card vanishes without waiting for next poll */
  if(lastStatus){
    lastStatus.sessions_list=(lastStatus.sessions_list||[]).filter(s=>s.id!==id);
    const sessions=lastStatus.sessions||{};
    if(sessions[id]) sessions[id].dismissed=true;
    if(lastStatus.sessions_count>0) lastStatus.sessions_count--;
    renderAgents(lastStatus);
  }
}

/* ── stale-session sweep ── bulk-dismiss every closed session at once
   (local or merged-in from a Remote Machine) instead of clicking Dismiss
   on each card individually -- _dismissSession already routes each one
   to its own machine. */
function _staleSessions(sr){
  return ((sr||lastStatus||{}).sessions_list||[]).filter(s=>s.session_active===false);
}
function _updateSweepButton(sr){
  const btn=document.getElementById('btn-sweep-stale');
  const nEl=document.getElementById('sweep-stale-n');
  if(!btn) return;
  const stale=_staleSessions(sr);
  btn.style.display=stale.length>0?'':'none';
  if(nEl) nEl.textContent=stale.length>0?stale.length:'';
}
async function sweepStaleSessions(){
  const stale=_staleSessions();
  if(!stale.length) return;
  // native confirm() -- same reasoning _forceStopSession already uses:
  // impossible to accidentally click through the way a styled in-page
  // modal could be, and this is a bulk, irreversible action.
  if(!confirm(`Dismiss ${stale.length} idle session${stale.length!==1?'s':''}? This can't be undone.`)) return;
  for(const s of stale){ await _dismissSession(s.id,s.machine); }
  _updateSweepButton(lastStatus);
  log(`Swept ${stale.length} idle session${stale.length!==1?'s':''}`,'success');
}

async function _forceStopSession(id,label,machineName){
  /* Destructive -- kills a real OS process (the user's actual claude.exe),
     not just an AOC-side card. Native confirm() is a deliberate choice over
     a custom modal: it's a blocking native browser dialog, impossible to
     accidentally click through the way a styled in-page modal could be. */
  if(!confirm(`Force stop "${label}"?\n\nThis will kill the claude.exe process for this session immediately. Any unsaved work in that terminal will be lost.`)) return;
  try{
    const r=await fetch(_apiUrl(_machineFor(machineName),'/kill_session'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:id})});
    const j=await r.json();
    if(j.ok){
      log(`Force-stopped session: ${label}`,'success');
    } else {
      log(`Force stop failed: ${j.error||'unknown error'}`,'error');
    }
  }catch(e){
    log('Force stop failed: request error','error');
  }
}

/* ── view toggle ── */
let _panelCollapsed = false;
/* ── agent compare ── */
let _compareIds=[];
let _cmpOpen=false;
function toggleCompare(id,event){
  if(event) event.stopPropagation();
  const idx=_compareIds.indexOf(id);
  if(idx>=0){ _compareIds.splice(idx,1); }
  else { if(_compareIds.length>=2) _compareIds.shift(); _compareIds.push(id); }
  document.querySelectorAll('.card-cmp-btn').forEach(btn=>{
    btn.classList.toggle('selected',_compareIds.includes(btn.dataset.id));
  });
  if(_compareIds.length===2){ openCompare(); }
  else if(_compareIds.length===1){ log('⊞ Select one more agent to compare','info'); }
  else { closeCompare(); }
}
function openCompare(){
  _cmpOpen=true;
  const ov=document.getElementById('cmp-overlay');
  if(ov) ov.classList.add('open');
  _renderCompare();
}
function closeCompare(){
  _cmpOpen=false;
  const ov=document.getElementById('cmp-overlay');
  if(ov) ov.classList.remove('open');
}
function _renderCompare(){
  const body=document.getElementById('cmp-body');
  if(!body||!lastStatus||!_cmpOpen) return;
  const agents=_compareIds.map(id=>(lastStatus.agents||[]).find(a=>a.id===id)).filter(Boolean);
  if(agents.length<2){ body.innerHTML='<div style="grid-column:1/-1;text-align:center;padding:60px;color:var(--t3);font-family:var(--font2);font-size:11px;letter-spacing:.1em">// select 2 agents to compare</div>'; return; }
  const [a,b]=agents;
  function dur(ag){ if(!ag.started_at) return null; const s=parseTimeStr(ag.started_at); let e=Date.now(); if(ag.status!=='running'&&ag.completed_at) e=parseTimeStr(ag.completed_at); return Math.max(0,Math.floor((e-s)/1000)); }
  const fmtDur=_fmtDurShort;
  function pct(ag){ const t=(ag.tasks||[]).length; return t>0?Math.round((ag.tasks||[]).filter(t=>t.done).length/t*100):0; }
  function col(ag){ return ({running:'var(--c)',done:'var(--g)',error:'var(--r)',waiting:'var(--o)'})[ag.status]||'var(--t3)'; }
  function renderCol(ag,other){
    const c=col(ag); const dA=dur(ag); const dB=dur(other);
    const tA=ag.tokens_used||0; const tB=other.tokens_used||0;
    const pA=pct(ag); const pB=pct(other);
    const tasks=ag.tasks||[]; const done=tasks.filter(t=>t.done).length;
    const files=ag.files_changed||[];
    const logs=(ag.log||[]).slice(-8).reverse();
    const durBetter=dA!==null&&dB!==null&&dA<=dB;
    const tokBetter=tA>0&&tB>0&&tA<=tB;
    const pctBetter=pA>=pB;
    const cost=tA?_agentCost(ag).toFixed(4):null;
    // Parent-only, deliberately: unlike the other 4 parent_id surfaces (detail
    // panel / Markdown export / History Detail / GRAPH-TREE tooltip), Compare
    // does not also show a "spawned N subagents" row here. Compare is a
    // peer-vs-peer tool and comparing an orchestrator against its own
    // subagent isn't a typical use case, so the reverse direction was judged
    // lower-value and left out rather than added just for symmetry (see
    // a0e55b7). The one-directional "Parent" row below stays for consistency
    // with Model/Type as a plain informational (no better/worse) stat.
    const parentAg=_resolveParentAgent(ag);
    return `<div class="cmp-col">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
        <div class="unit-badge" style="border-color:${c};color:${c};flex-shrink:0">${_deriveUnit(ag)}</div>
        <div style="flex:1;min-width:0">
          <div style="font-size:13px;font-weight:700;color:${c};overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(ag.name)}</div>
          ${ag.description?`<div style="font-size:10px;color:var(--t3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-top:1px">${escHtml(ag.description)}</div>`:''}
        </div>
        ${ag.detected_via==='transcript'?`<span class="hookmiss-badge" style="flex-shrink:0" title="${escHtml(_hookMissTitle(ag))}">⚠ HOOK MISS</span>`:''}
        ${_stuckSecs(ag)>300?`<span class="stuck-badge" style="flex-shrink:0;margin-top:0">⚠ STUCK</span>`:''}
        <div class="status-pill ${ag.status}" style="font-size:9px;padding:2px 7px;flex-shrink:0"><span class="sdot"></span>${stxt(ag)}</div>
      </div>
      <div style="height:3px;background:rgba(255,255,255,.05);border-radius:999px;margin:8px 0;overflow:hidden">
        <div style="height:100%;width:${pA}%;background:${c};border-radius:999px;transition:width .6s ease;box-shadow:0 0 4px ${c}55"></div>
      </div>
      <div class="cmp-sec">STATS</div>
      ${(ag.model||other.model)?`<div class="cmp-stat-row"><span class="cmp-stat-lbl">Model</span><span class="cmp-stat-val" style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(ag.model||'')}">${ag.model?escHtml(ag.model):'—'}</span></div>`:''}
      ${(ag.subagent_type||other.subagent_type)?`<div class="cmp-stat-row"><span class="cmp-stat-lbl">Type</span><span class="cmp-stat-val">${ag.subagent_type?escHtml(ag.subagent_type):'—'}</span></div>`:''}
      ${(ag.parent_id||other.parent_id)?`<div class="cmp-stat-row"><span class="cmp-stat-lbl">Parent</span><span class="cmp-stat-val" style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(parentAg?.name||'')}">${parentAg?escHtml(parentAg.name):'—'}</span></div>`:''}
      ${(ag.tool_use_count!=null||other.tool_use_count!=null)?`<div class="cmp-stat-row"><span class="cmp-stat-lbl">Tool calls</span><span class="cmp-stat-val ${ag.tool_use_count!=null&&other.tool_use_count!=null?(ag.tool_use_count<=other.tool_use_count?'better':'worse'):''}">${ag.tool_use_count!=null?ag.tool_use_count:'—'}</span></div>`:''}
      <div class="cmp-stat-row"><span class="cmp-stat-lbl">Duration</span><span class="cmp-stat-val ${dA!==null&&dB!==null?(durBetter?'better':'worse'):''}">${fmtDur(dA)}</span></div>
      <div class="cmp-stat-row"><span class="cmp-stat-lbl">Tokens</span><span class="cmp-stat-val ${tA>0&&tB>0?(tokBetter?'better':'worse'):''}">${tA>0?Number(tA).toLocaleString():'—'}</span></div>
      <div class="cmp-stat-row"><span class="cmp-stat-lbl">Est. cost</span><span class="cmp-stat-val ${tA>0&&tB>0?(tokBetter?'better':'worse'):''}">${cost?'$'+cost:'—'}</span></div>
      <div class="cmp-stat-row"><span class="cmp-stat-lbl">Progress</span><span class="cmp-stat-val ${pctBetter?'better':''}">${done}/${tasks.length} tasks (${pA}%)</span></div>
      <div class="cmp-stat-row"><span class="cmp-stat-lbl">Files</span><span class="cmp-stat-val">${files.length}</span></div>
      ${tasks.length?`<div class="cmp-sec">TASKS</div>${tasks.map(t=>`<div class="cmp-task-row ${t.done?'done':'pend'}"><span style="font-family:var(--font2);flex-shrink:0">${t.done?'✓':'·'}</span><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1">${escHtml(t.label||'')}</span>${t.completed_at?`<span style="font-size:9px;color:var(--t3);font-family:var(--font2);flex-shrink:0">${t.completed_at}</span>`:''}</div>`).join('')}`:''}
      ${files.length?`<div class="cmp-sec">FILES (${files.length})</div>${files.slice(0,12).map(f=>`<div class="fe ${f.type==='new'?'new':'changed'}" style="padding:3px 7px;margin-bottom:2px;border-radius:5px"><span class="fe-badge">${f.type==='new'?'NEW':'MOD'}</span><span class="fe-name" style="font-size:10px">${escHtml(f.path.split('/').pop())}</span></div>`).join('')}${files.length>12?`<div style="font-size:10px;color:var(--t3);padding:3px 0">+${files.length-12} more</div>`:''}`:''}
      ${logs.length?`<div class="cmp-sec">RECENT LOG</div>${logs.map(e=>`<div class="adp-log-line" style="font-size:10px">${escHtml(String(e))}</div>`).join('')}`:''}
    </div>`;
  }
  body.innerHTML=renderCol(a,b)+renderCol(b,a);
}

/* ── session compare ── mirrors the agent-compare pattern immediately
   above (_compareIds/toggleCompare/openCompare/closeCompare/_renderCompare)
   at the session level instead of the agent level. */
let _sessCompareIds=[];
let _sessCmpOpen=false;
function toggleSessionCompare(id,event){
  if(event) event.stopPropagation();
  const idx=_sessCompareIds.indexOf(id);
  if(idx>=0){ _sessCompareIds.splice(idx,1); }
  else { if(_sessCompareIds.length>=2) _sessCompareIds.shift(); _sessCompareIds.push(id); }
  if(lastStatus) renderAgents(lastStatus);
  if(_sessCompareIds.length===2){ openSessionCompare(); }
  else if(_sessCompareIds.length===1){ log('⊞ Select one more session to compare','info'); }
  else { closeSessionCompare(); }
}
function openSessionCompare(){
  _sessCmpOpen=true;
  const ov=document.getElementById('sess-cmp-overlay');
  if(ov) ov.classList.add('open');
  _renderSessionCompare();
}
function closeSessionCompare(){
  _sessCmpOpen=false;
  const ov=document.getElementById('sess-cmp-overlay');
  if(ov) ov.classList.remove('open');
}
function _renderSessionCompare(){
  const body=document.getElementById('sess-cmp-body');
  if(!body||!lastStatus||!_sessCmpOpen) return;
  const sessions=_sessCompareIds.map(id=>(lastStatus.sessions_list||[]).find(s=>s.id===id)).filter(Boolean);
  if(sessions.length<2){ body.innerHTML='<div style="grid-column:1/-1;text-align:center;padding:60px;color:var(--t3);font-family:var(--font2);font-size:11px;letter-spacing:.1em">// select 2 sessions to compare</div>'; return; }
  const [a,b]=sessions;
  function agentsOf(s){ return (lastStatus.agents||[]).filter(ag=>ag.session_id===s.id); }
  function dur(s){
    if(!s.first_ts||!s.last_ts) return null;
    const ms=new Date(s.last_ts)-new Date(s.first_ts);
    return isNaN(ms)||ms<=0?null:Math.floor(ms/1000);
  }
  const fmtDur=_fmtDurShort;
  const fmtCost=_fmtCost;
  function fmtTok(t){ return t?Number(t).toLocaleString():'—'; }
  function taskStats(s){
    const ags=agentsOf(s);
    const tasks=ags.flatMap(ag=>ag.tasks||[]);
    return {done:tasks.filter(t=>t.done).length,total:tasks.length};
  }
  function col(s){ return s.session_active!==false?'var(--c)':'var(--t3)'; }
  function renderCol(s,other){
    const c=col(s);
    const dA=dur(s),dB=dur(other);
    const tA=(s.input_tokens||0)+(s.output_tokens||0),tB=(other.input_tokens||0)+(other.output_tokens||0);
    const cA=s.estimated_cost||0,cB=other.estimated_cost||0;
    const aA=agentsOf(s).length,aB=agentsOf(other).length;
    const tsA=taskStats(s),tsB=taskStats(other);
    const pctA=tsA.total>0?Math.round(tsA.done/tsA.total*100):0;
    const pctB=tsB.total>0?Math.round(tsB.done/tsB.total*100):0;
    const durBetter=dA!==null&&dB!==null&&dA<=dB;
    const costBetter=cA>0&&cB>0&&cA<=cB;
    const tokBetter=tA>0&&tB>0&&tA<=tB;
    const label=escHtml(s.display_name||s.project||s.id.slice(-6));
    // isActive/waitingOnYou/waitingDurStr mirror the session card's own
    // status pill exactly (renderAgents' statsRow) -- Compare's pill was
    // hardcoded to ACTIVE/CLOSED only, so two sessions being compared
    // could never show which one is actually WAITING on you right now.
    const isActive=s.session_active!==false;
    const waitingOnYou=isActive&&!!s.waiting_on_you;
    const waitingDurStr=(waitingOnYou&&(s.waiting_secs||0)>=60)?' · '+_fmtDurationDHM(s.waiting_secs):'';
    return `<div class="cmp-col">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
        <div class="unit-badge" style="border-color:${c};color:${c};flex-shrink:0">CLI</div>
        <div style="flex:1;min-width:0">
          <div style="font-size:13px;font-weight:700;color:${c};overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${label}</div>
          ${s.git_branch?`<div style="font-size:10px;color:var(--t3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-top:1px">⎇ ${s.pr_url?`<a href="${escHtml(s.pr_url)}" target="_blank" onclick="event.stopPropagation()" style="color:inherit;text-decoration:underline;text-decoration-style:dotted" title="Open PR on GitHub">${escHtml(s.git_branch)}</a>`:escHtml(s.git_branch)}</div>`:''}
        </div>
        <div class="status-pill ${isActive?(waitingOnYou?'waiting':'running'):''}" style="font-size:9px;padding:2px 7px;flex-shrink:0" ${waitingOnYou?'title="Claude finished its last turn and is waiting for your next message"':''}><span class="sdot"></span>${isActive?(waitingOnYou?'WAITING'+waitingDurStr:'ACTIVE'):'CLOSED'}</div>
      </div>
      <div class="cmp-sec">STATS</div>
      <div class="cmp-stat-row"><span class="cmp-stat-lbl">Duration</span><span class="cmp-stat-val ${dA!==null&&dB!==null?(durBetter?'better':'worse'):''}">${fmtDur(dA)}</span></div>
      <div class="cmp-stat-row"><span class="cmp-stat-lbl">Tokens</span><span class="cmp-stat-val ${tA>0&&tB>0?(tokBetter?'better':'worse'):''}"${tA>0?` title="${escHtml(_tokBreakdownStr(s)+(s.msg_count?' · '+s.msg_count+' msg':''))}"`:''}>${fmtTok(tA)}</span></div>
      <div class="cmp-stat-row"><span class="cmp-stat-lbl">Est. cost</span><span class="cmp-stat-val ${cA>0&&cB>0?(costBetter?'better':'worse'):''}">${fmtCost(cA)}</span></div>
      <div class="cmp-stat-row"><span class="cmp-stat-lbl">Agents</span><span class="cmp-stat-val">${aA}</span></div>
      <div class="cmp-stat-row"><span class="cmp-stat-lbl">Tasks</span><span class="cmp-stat-val ${pctA>=pctB?'better':''}">${tsA.done}/${tsA.total} (${pctA}%)</span></div>
      ${(s.cc_version||other.cc_version)?`<div class="cmp-stat-row"><span class="cmp-stat-lbl">CC Version</span><span class="cmp-stat-val">${s.cc_version?'v'+escHtml(s.cc_version):'—'}</span></div>`:''}
      ${(()=>{
        // Distinct models across this session's own agents -- a session
        // can mix models (e.g. an orchestrator agent on one, subagents on
        // another), so this is a set, not a single value like the
        // per-agent Model row Agent Compare shows.
        const models=[...new Set(agentsOf(s).map(ag=>ag.model).filter(Boolean))];
        return models.length?`<div class="cmp-stat-row"><span class="cmp-stat-lbl">Models</span><span class="cmp-stat-val" style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(models.join(', '))}">${escHtml(models.join(', '))}</span></div>`:'';
      })()}
      ${s.note?`<div class="cmp-sec">NOTE</div><div class="adp-log-line" style="font-size:10px">${escHtml(s.note)}</div>`:''}
    </div>`;
  }
  body.innerHTML=renderCol(a,b)+renderCol(b,a);
}

let _rightTab='log';
function setRightTab(tab){
  _rightTab=tab;
  ['log','files','audit','errors'].forEach(t=>{
    const btn=document.getElementById('rpt-'+t);
    if(btn){ btn.classList.toggle('active',t===tab); btn.setAttribute('aria-selected',t===tab); }
  });
  document.getElementById('log-area').style.display    = tab==='log'   ? '' : 'none';
  document.getElementById('rp-files').style.display   = tab==='files' ? 'flex' : 'none';
  document.getElementById('rp-audit').style.display   = tab==='audit' ? 'flex' : 'none';
  document.getElementById('rp-errors-wrap').style.display  = tab==='errors' ? 'flex' : 'none';
  if(tab==='files' && lastStatus) _renderRpFiles(lastStatus);
  if(tab==='audit') _renderRpAudit();
  if(tab==='errors') _renderRpErrors();
}
function _renderRpFiles(data){
  const el=document.getElementById('rp-files');
  if(!el || _rightTab!=='files') return;
  const agents=(data.agents||[]).filter(a=>!String(a.id||'').startsWith('hook_'));
  const seen=new Set(); const files=[];
  agents.forEach(a=>(a.files_changed||[]).forEach(f=>{ if(!seen.has(f.path)){seen.add(f.path);files.push({...f,agent:a.unit||a.name});} }));
  const ftEl=document.getElementById('rpt-files');
  if(ftEl) ftEl.textContent=`FILES (${files.length})`;
  if(!files.length){
    el.innerHTML='<div style="color:var(--t3);font-family:var(--font2);font-size:10px;text-align:center;padding:30px 0;letter-spacing:.08em">// no file changes</div>';
    return;
  }
  el.innerHTML=files.map(f=>`<div class="fe ${f.type==='new'?'new':'changed'}" onclick="showDiff('${escHtml(f.path.replace(/'/g,"\\'"))}');event.stopPropagation()" style="cursor:pointer;border-radius:6px;padding:5px 8px">
    <span class="fe-badge">${f.type==='new'?'NEW':'MOD'}</span>
    <span class="fe-name" style="font-size:11px">${escHtml(f.path.split('/').pop())}</span>
    ${f.lines?`<span class="fe-lines">${f.lines}L</span>`:''}
    <span style="font-size:9px;color:var(--t3);font-family:var(--font2);flex-shrink:0">${escHtml(f.agent)}</span>
  </div>`).join('');
}
let _auditLastRender=0;
async function _renderRpAudit(){
  const el=document.getElementById('rp-audit');
  if(!el || _rightTab!=='audit') return;
  const now=Date.now();
  if(now-_auditLastRender<5000) return;
  _auditLastRender=now;
  try{
    const d=await fetch('/auditlog').then(r=>r.json());
    const atEl=document.getElementById('rpt-audit');
    if(atEl) atEl.textContent=d.current?`AUDIT (${d.lines.length})`:'AUDIT';
    if(!d.lines||!d.lines.length){
      el.innerHTML='<div style="color:var(--t3);font-family:var(--font2);font-size:10px;text-align:center;padding:30px 0;letter-spacing:.08em">// no audit log</div>';
      return;
    }
    el.innerHTML=d.lines.slice().reverse().map(l=>`<div class="al ${eventClass(l)}" style="font-size:10px">${l}</div>`).join('');
  }catch(e){}
}
/* /errors already returns session_id per error (needed for the join
   against sessions), but nothing ever read it -- there was no way to
   jump from a past error straight to that session's full History
   context. */
async function _jumpToSessionHistory(sid){
  if(!sid) return;
  await setView('history');
  await showHistoryDetail(sid);
}
async function _selectTagFilter(tag){
  if(!tag) return;
  _histTab='sessions';
  await renderHistory();
  setHistSearch(tag);
}
let _errorsLastRender=0;
let _errorsCache=[];
let _errorSearchQuery='';
let _errorTypeFilter='all';
/* Errors have no structured category in the DB (error_msg is whatever
   string the agent/hook happened to report) -- classify by matching the
   handful of shapes that actually recur in production (worktree/git setup
   failures, the user declining a permission prompt, upstream network
   timeouts, "model temporarily unavailable", and raw tracebacks), falling
   back to 'other' rather than inventing a bucket per one-off message. */
const _ERROR_TYPE_LABELS={worktree:'WORKTREE/GIT',rejected:'USER REJECTED',unavailable:'MODEL UNAVAILABLE',network:'NETWORK/TIMEOUT',exception:'EXCEPTION',other:'OTHER'};
function _classifyError(msg){
  const m=msg||'';
  if(/worktree/i.test(m)) return 'worktree';
  if(/doesn't want to proceed|was rejected/i.test(m)) return 'rejected';
  if(/temporarily unavailable/i.test(m)) return 'unavailable';
  if(/timeout|connection refused|connection dropped|econnreset|upstream/i.test(m)) return 'network';
  if(/traceback \(most recent call last\)/i.test(m)) return 'exception';
  return 'other';
}
function _filterErrors(errs,query,type){
  const q=(query||'').trim().toLowerCase();
  return errs.filter(e=>{
    if(type && type!=='all' && _classifyError(e.error_msg)!==type) return false;
    if(!q) return true;
    return (e.error_msg||'').toLowerCase().includes(q)
      || (e.name||'').toLowerCase().includes(q)
      || (e.project||'').toLowerCase().includes(q);
  });
}
function _renderErrorTypeBar(){
  const bar=document.getElementById('rp-errors-types');
  if(!bar) return;
  if(!_errorsCache.length){ bar.style.display='none'; return; }
  const counts={};
  _errorsCache.forEach(e=>{ const t=_classifyError(e.error_msg); counts[t]=(counts[t]||0)+1; });
  if(_errorTypeFilter!=='all' && !counts[_errorTypeFilter]) _errorTypeFilter='all';
  const types=Object.keys(counts).sort((a,b)=>counts[b]-counts[a]);
  bar.style.display='flex';
  const btn=(f,label)=>`<button class="sfbtn error ${_errorTypeFilter===f?'active':''}" style="padding:3px 8px;font-size:9px" onclick="_setErrorTypeFilter('${f}')">${label}</button>`;
  bar.innerHTML=btn('all',`ALL ${_errorsCache.length}`)
    +types.map(t=>btn(t,`${_ERROR_TYPE_LABELS[t]||t.toUpperCase()} ${counts[t]}`)).join('');
}
function _setErrorTypeFilter(type){
  _errorTypeFilter=type;
  _renderErrorTypeBar();
  _renderErrorsList();
}
function _renderErrorsList(){
  const el=document.getElementById('rp-errors');
  if(!el) return;
  const errs=_filterErrors(_errorsCache,_errorSearchQuery,_errorTypeFilter);
  if(!_errorsCache.length){
    el.innerHTML='<div style="color:var(--t3);font-family:var(--font2);font-size:10px;text-align:center;padding:30px 0;letter-spacing:.08em">// no errors recorded</div>';
    return;
  }
  if(!errs.length){
    el.innerHTML='<div style="color:var(--t3);font-family:var(--font2);font-size:10px;text-align:center;padding:30px 0;letter-spacing:.08em">// no errors match your filters</div>';
    return;
  }
  el.innerHTML=errs.map(e=>`<div style="padding:8px 10px;border-radius:8px;background:rgba(255,51,85,.06);border:1px solid rgba(255,51,85,.2);border-left:3px solid var(--r);cursor:pointer" onclick="_jumpToSessionHistory('${e.session_id}')" title="Open this session in History">
      <div style="display:flex;justify-content:space-between;gap:8px;margin-bottom:4px;font-size:9px;font-family:var(--font2);letter-spacing:.06em;color:var(--t3)">
        <span style="display:flex;align-items:center;gap:6px;min-width:0">
          <span style="color:var(--r);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(e.name||e.agent_id||'agent')}</span>
          ${e.detected_via==='transcript'?`<span class="hookmiss-badge" style="flex-shrink:0" title="${escHtml(_hookMissTitle(e))}">⚠ HOOK MISS</span>`:''}
        </span>
        <span style="flex-shrink:0">${e.model?escHtml(e.model)+' · ':''}${escHtml(e.project||'')} · ${escHtml(e.date||'')} ${escHtml(e.completed_at||'')}</span>
      </div>
      <div style="font-size:11px;color:rgba(255,120,140,.85);font-family:var(--font2);line-height:1.5;white-space:pre-wrap;word-break:break-all;max-height:160px;overflow-y:auto">${escHtml(e.error_msg||'')}</div>
    </div>`).join('');
}
function _setErrorSearch(val){
  _errorSearchQuery=val.toLowerCase();
  const inp=document.getElementById('rp-errors-search-input');
  const clr=document.getElementById('rp-errors-search-clear');
  if(inp && inp.value!==val) inp.value=val;
  if(clr) clr.classList.toggle('show', val.length>0);
  _renderErrorsList();
}
async function _renderRpErrors(){
  const el=document.getElementById('rp-errors');
  if(!el || _rightTab!=='errors') return;
  const now=Date.now();
  if(now-_errorsLastRender<5000) return;
  _errorsLastRender=now;
  try{
    _errorsCache=await fetch('/errors').then(r=>r.json());
    const etEl=document.getElementById('rpt-errors');
    if(etEl) etEl.textContent=_errorsCache.length?`ERRORS (${_errorsCache.length})`:'ERRORS';
    _renderErrorTypeBar();
    _renderErrorsList();
  }catch(e){}
}
function toggleRightPanel(){
  _panelCollapsed=!_panelCollapsed;
  document.querySelector('.root').classList.toggle('panel-collapsed',_panelCollapsed);
  const btn=document.getElementById('panel-toggle-btn');
  if(btn) btn.textContent=_panelCollapsed?'▶':'◀';
  _savePrefs();
}
/* ── Mobile nav ── */
let _mnavLogsOpen=false;
function _mnavGo(view){
  if(_mnavLogsOpen){ _mnavToggleLogs(false); }
  setView(view);
  document.querySelectorAll('#mobile-nav .mnav-btn').forEach(b=>b.classList.remove('active'));
  const btn=document.getElementById('mnav-'+view);
  if(btn) btn.classList.add('active');
}
function _mnavToggleLogs(force){
  const rp=document.getElementById('right-panel');
  _mnavLogsOpen = force!==undefined ? force : !_mnavLogsOpen;
  if(rp) rp.classList.toggle('mobile-open', _mnavLogsOpen);
  const btn=document.getElementById('mnav-logs');
  if(btn) btn.classList.toggle('active', _mnavLogsOpen);
}
function _savePrefs(){
  try{ localStorage.setItem('aoc_prefs',JSON.stringify({view:currentView,list:listMode,sf:statusFilter,muted:_soundMuted,theme:_theme,collapsed:[..._collapsedIds],pinned:[..._pinnedIds],panelCollapsed:_panelCollapsed})); }catch(e){}
}
function _loadPrefs(){
  try{
    const p=JSON.parse(localStorage.getItem('aoc_prefs')||'{}');
    // migrate a pref saved before AGENTS/CLI were promoted from a CARDS
    // sub-tab to top-level views -- v==='cards' would otherwise match no
    // view at all and leave the main area blank for returning users.
    let v=p.view;
    if(v==='cards') v=(p.cardsTab==='cli')?'cli':'agents';
    if(v) { currentView=v; setView(v); }
    if(p.list)  listMode=p.list;
    if(p.sf && p.sf!=='all') statusFilter=p.sf;
    if(p.muted) { _soundMuted=true; _applyMuteUI(); }
    if(p.theme) { _theme=p.theme; _applyTheme(); }
    if(Array.isArray(p.collapsed)) _collapsedIds=new Set(p.collapsed);
    if(Array.isArray(p.pinned)) _pinnedIds=new Set(p.pinned);
    if(p.panelCollapsed){ _panelCollapsed=true; document.querySelector('.root').classList.add('panel-collapsed'); const btn=document.getElementById('panel-toggle-btn'); if(btn) btn.textContent='▶'; }
  }catch(e){}
}
function togglePin(id, event){
  if(event) event.stopPropagation();
  if(_pinnedIds.has(id)) _pinnedIds.delete(id);
  else _pinnedIds.add(id);
  _savePrefs();
  if(lastStatus) renderAgents(lastStatus);
}
function toggleCollapse(id, event){
  if(event) event.stopPropagation();
  if(_collapsedIds.has(id)) _collapsedIds.delete(id);
  else _collapsedIds.add(id);
  _savePrefs();
  if(lastStatus) renderAgents(lastStatus);
}
function collapseAllDone(){
  if(!lastStatus) return;
  const agents=(lastStatus.agents||[]).filter(a=>!String(a.id||'').startsWith('hook_'));
  agents.filter(a=>a.status==='done').forEach(a=>_collapsedIds.add(a.id));
  _savePrefs();
  renderAgents(lastStatus);
  log('All done agents collapsed','info');
}
function expandAll(){
  _collapsedIds.clear();
  _savePrefs();
  if(lastStatus) renderAgents(lastStatus);
  log('All agents expanded','info');
}
function _applyMuteUI(){
  const btn=document.getElementById('btn-mute');
  if(btn){ btn.textContent=_soundMuted?'🔇':'🔊'; btn.classList.toggle('muted',_soundMuted); btn.title=_soundMuted?'Sound muted — click to unmute':'Mute sound notifications'; }
}
function _applyTheme(){
  document.body.classList.toggle('light',_theme==='light');
  const btn=document.getElementById('btn-theme');
  if(btn){ btn.textContent=_theme==='light'?'☀️':'🌙'; btn.title=_theme==='light'?'Switch to dark mode [H]':'Switch to light mode [H]'; }
}
function toggleTheme(){
  _theme=_theme==='dark'?'light':'dark';
  _applyTheme();
  _savePrefs();
  log('Theme → '+_theme.toUpperCase(),'info');
}

function setView(v){
  currentView=v;
  _savePrefs();
  const areas = { timeline:'flex', summary:'flex', graph:'flex', heat:'flex', tree:'flex', history:'flex', term:'flex' };
  ['timeline-area','summary-area','graph-area','heat-area','diag-area','tree-area','history-area','term-area'].forEach(id=>{
    const key=id.replace('-area','');
    document.getElementById(id).style.display = v===key ? (areas[key]||'flex') : 'none';
  });
  /* AGENTS and CLI are two top-level views over the same #cards-area
     container (agent cards vs. session cards) -- see renderAgents(),
     which reads currentView to decide which to draw into #agents. */
  const cardsAreaEl=document.getElementById('cards-area');
  if(cardsAreaEl) cardsAreaEl.style.display = (v==='agents'||v==='cli') ? 'flex' : 'none';
  [['vt-agents','agents'],['vt-cli','cli'],['vt-tl','timeline'],['vt-summary','summary'],['vt-graph','graph'],['vt-heat','heat'],['vt-diag','diag'],['vt-tree','tree'],['vt-history','history'],['vt-term','term']].forEach(([id,val])=>{
    const el=document.getElementById(id); if(el) el.classList.toggle('active',v===val);
  });
  /* KPI bar covers both AGENTS and CLI (same underlying live data) */
  const kpiEl=document.getElementById('kpi-bar');
  if(kpiEl) kpiEl.style.display=(v==='agents'||v==='cli')?'flex':'none';
  /* term-mode removes main padding so xterm fills space */
  const mainEl=document.getElementById('main-area');
  if(mainEl) mainEl.classList.toggle('term-mode', v==='term');
  if(v==='term') { _initTerm(); return; }
  if(!lastStatus) return;
  if(v==='agents'||v==='cli') renderAgents(lastStatus);
  if(v==='timeline') renderTimeline(lastStatus);
  if(v==='summary')  renderSummary(lastStatus);
  if(v==='graph')    renderGraph(lastStatus);
  if(v==='heat')     renderHeatmap(lastStatus);
  if(v==='diag')     renderDiag();
  if(v==='tree')     renderTree(lastStatus);
  /* returns renderHistory()'s promise (existing call sites just ignore it,
     a no-op change for them) so _jumpToSessionHistory below can await the
     SESSIONS list render finishing before overwriting #hist-body with a
     detail view -- both write the same element asynchronously, so without
     this ordering the list fetch resolving second would clobber it back. */
  if(v==='history')  return renderHistory();
}

/* ── timeline ── */
/* ── timeline zoom helpers ── */
function _tlZoomIn(){ if(_tlZoom<16){ _tlZoom=Math.min(16,_tlZoom*2); if(lastStatus)renderTimeline(lastStatus); } }
function _tlZoomOut(){ if(_tlZoom>1){ _tlZoom=Math.max(1,_tlZoom/2); if(lastStatus)renderTimeline(lastStatus); } }
function _tlZoomReset(){ _tlZoom=1; _tlZoomFocus=0.5; if(lastStatus)renderTimeline(lastStatus); }
function _tlClickAgent(id, frac){
  openAgentDetail(id, null);
  _tlZoomFocus = frac;
  if(_tlZoom===1) _tlZoom=2;
  if(lastStatus) renderTimeline(lastStatus);
}

function renderTimeline(data){
  const tl=document.getElementById('timeline-area');
  const agents=(data.agents||[]).filter(a=>!String(a.id||'').startsWith('hook_')&&a.started_at);
  if(!agents.length){
    tl.innerHTML='<div style="color:var(--t3);text-align:center;padding:60px;font-family:var(--font2);font-size:9px;letter-spacing:.12em">NO AGENT DATA FOR TIMELINE</div>';
    return;
  }
  const now=Date.now();
  let minT=Infinity, maxT=-Infinity;
  agents.forEach(a=>{
    const s=parseTimeStr(a.started_at); if(s&&s<minT) minT=s;
    const e=parseTimeStr(a.completed_at)||now; if(e>maxT) maxT=e;
  });
  if(!isFinite(minT)) minT=now-60000;
  maxT=Math.max(maxT,now); /* extend to now so running agents reach edge */
  const originalRange=Math.max(maxT-minT,10000);
  const fmtMs=ms=>{ const s=Math.round(ms/1000); return s<60?s+'s':Math.floor(s/60)+'m'+(s%60?(' '+(s%60)+'s'):''); };
  const hasRunning=agents.some(a=>a.status==='running'||a.status==='waiting');

  /* ── zoom window ── */
  const zoomedRange = originalRange / _tlZoom;
  const focusMs = minT + _tlZoomFocus * originalRange;
  let dispMin = focusMs - zoomedRange * _tlZoomFocus;
  let dispMax = dispMin + zoomedRange;
  /* clamp to original bounds */
  if(dispMin < minT){ dispMin=minT; dispMax=dispMin+zoomedRange; }
  if(dispMax > maxT){ dispMax=maxT; dispMin=dispMax-zoomedRange; }
  dispMin=Math.max(dispMin,minT);
  dispMax=Math.min(dispMax,maxT);
  const dispRange=Math.max(dispMax-dispMin,1000);

  const nowPct=((now-dispMin)/dispRange*100).toFixed(2);

  /* compute tick interval: aim for ~4-6 ticks */
  const rangeSec=dispRange/1000;
  let tickSec=10;
  if(rangeSec>600) tickSec=120;
  else if(rangeSec>300) tickSec=60;
  else if(rangeSec>120) tickSec=30;
  else if(rangeSec>60) tickSec=15;
  const tickMs=tickSec*1000;
  const firstTick=Math.ceil(dispMin/tickMs)*tickMs;
  const ticks=[];
  for(let t=firstTick;t<=dispMax;t+=tickMs) ticks.push(t);

  const tickSvg=ticks.map(t=>{
    const left=((t-dispMin)/dispRange*100).toFixed(2);
    const label=fmtMs(t-minT);
    return `<div style="position:absolute;left:${left}%;top:0;height:100%;pointer-events:none">
      <div style="position:absolute;top:0;left:0;width:1px;height:100%;background:rgba(255,255,255,.05)"></div>
      <div style="position:absolute;top:0;left:2px;font-size:8px;font-family:var(--font2);color:var(--t3)">${label}</div>
    </div>`;
  }).join('');

  /* ── zoom toolbar ── */
  const zoomLbl=Number.isInteger(_tlZoom)?`${_tlZoom}×`:`${_tlZoom}×`;
  const zoomInfo=_tlZoom>1?`<span style="color:var(--t3);font-size:9px">showing ${fmtMs(dispRange)} of ${fmtMs(originalRange)}</span>`:'';
  const toolbar=`<div class="tl-toolbar">
    <button class="density-btn" onclick="_tlZoomOut()" title="Zoom out (-)">−</button>
    <span class="tl-zoom-lbl">${zoomLbl}</span>
    <button class="density-btn" onclick="_tlZoomIn()" title="Zoom in (+)">+</button>
    <button class="density-btn" onclick="_tlZoomReset()" title="Reset zoom (0)" style="margin-left:2px">FIT</button>
    ${zoomInfo}
    <span style="flex:1"></span>
    <span style="color:var(--t3)">START <span style="color:var(--t2)">${new Date(minT).toLocaleTimeString()}</span></span>
    <span style="color:var(--t3)">RANGE <span style="color:var(--c)">${fmtMs(originalRange)}</span></span>
    ${hasRunning?`<span style="display:flex;align-items:center;gap:4px;color:var(--c)"><span style="width:6px;height:6px;border-radius:50%;background:var(--c);animation:pulse 1.5s infinite;display:inline-block"></span>LIVE</span>`:''}
  </div>`;

  tl.innerHTML= toolbar +
    `<div style="position:relative;height:20px;margin-bottom:4px;overflow:visible">
      ${tickSvg}
      ${hasRunning&&now>=dispMin&&now<=dispMax?`<div style="position:absolute;left:${nowPct}%;top:0;height:calc(100% + ${agents.length*32}px);width:1px;background:rgba(var(--c-rgb),.3);pointer-events:none;z-index:1"><div style="position:absolute;top:-14px;left:3px;font-size:8px;font-family:var(--font2);color:var(--c)">NOW</div></div>`:''}
    </div>` +
    agents.map(a=>{
      const sT=parseTimeStr(a.started_at)||minT;
      const eT=parseTimeStr(a.completed_at)||(a.status==='running'||a.status==='waiting'?now:sT+5000);
      /* clamp bar to display window */
      const clampedS=Math.max(sT,dispMin);
      const clampedE=Math.min(eT,dispMax);
      if(clampedS>=clampedE && sT>=dispMax) return ''; /* fully outside right */
      if(clampedS>=clampedE && eT<=dispMin) return ''; /* fully outside left */
      const left=Math.max(0,((clampedS-dispMin)/dispRange*100)).toFixed(2);
      const width=Math.max(((Math.max(clampedE,clampedS+1)-clampedS)/dispRange*100),.3).toFixed(2);
      /* original bar metrics for labels */
      const origLeft=((sT-dispMin)/dispRange*100).toFixed(2);
      const origWidth=Math.max(((eT-sT)/dispRange*100),.3).toFixed(2);
      const col=sCol(a.status);
      const dur=fmtMs(eT-sT);
      const tokStr=a.tokens_used?` · $${_agentCost(a).toFixed(4)}`:'';
      /* center fraction of this agent's bar in the original range (for click-to-focus) */
      const barMid=sT+(eT-sT)/2;
      const frac=Math.max(0,Math.min(1,(barMid-minT)/originalRange));
      const clickAgent=`_tlClickAgent('${a.id}',${frac.toFixed(4)})`;
      const inlineTicksInTrack=ticks.map(t=>`<div style="position:absolute;left:${((t-dispMin)/dispRange*100).toFixed(2)}%;top:0;bottom:0;width:1px;background:rgba(255,255,255,.04);pointer-events:none"></div>`).join('');
      return `<div class="tl-row">
        <div class="tl-name clickable" style="color:${col}" title="${escHtml(a.description||a.name)}" onclick="${clickAgent}">${escHtml(a.name.slice(0,22))}</div>
        <div class="tl-track" style="position:relative">
          <div class="tl-bg"></div>
          ${inlineTicksInTrack}
          <div class="tl-bar" style="left:${left}%;width:${width}%;background:${col};opacity:.8;box-shadow:0 0 6px ${col}55;border-radius:3px;cursor:pointer" title="${escHtml(a.name)} — ${dur}${tokStr}" onclick="${clickAgent}"></div>
          ${a.status==='running'?`<div class="tl-bar" style="left:${left}%;width:${width}%;background:linear-gradient(90deg,transparent 60%,${col}44);animation:sweep 2s linear infinite;border-radius:3px;pointer-events:none"></div>`:''}
          ${a.status==='done'&&parseFloat(origLeft)+parseFloat(origWidth)>=0&&parseFloat(origLeft)+parseFloat(origWidth)<=100?`<div style="position:absolute;left:calc(${origLeft}% + ${origWidth}%);top:3px;font-size:8px;font-family:var(--font2);color:${col};white-space:nowrap;opacity:.7">${dur}</div>`:''}
        </div>
        <div class="tl-elapsed" style="color:${col}">${a.status==='running'?fmtMs(now-sT):dur}</div>
      </div>`;
    }).join('');
}

/* ── diff overlay ── */
/* Escapes &<> for text-node safety plus "' so the 4 existing title="${escHtml(...)}"
   attribute-context call sites are actually safe too -- &<> alone stops tag
   injection in text content but a bare " still terminates a double-quoted HTML
   attribute early, letting injected text add its own event-handler attribute. */
function escHtml(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
/* Shared HOOK MISS badge tooltip -- 4 near-identical call sites (card,
   SUMMARY agent row, AGENTS-tab row, Compare) previously hardcoded the
   same fixed text. concurrent_sessions is exactly the metric this whole
   feature exists to test (see the concurrent_sessions comment in
   monitor.py's _apply_agent_update: does the hook drop PreToolUse more
   often under concurrent session load?), but was only ever shown as a
   cross-agent average in Settings -> INFRA, never per-agent -- append it
   here when known instead of adding a whole new badge just for one
   number. */
function _hookMissTitle(a){
  const base="Claude Code's PreToolUse hook never fired for this agent -- surfaced via transcript fallback instead";
  return a.concurrent_sessions!=null ? base+` (${a.concurrent_sessions} other session${a.concurrent_sessions!==1?'s':''} active at the time)` : base;
}
/* Shared "Input: X · Output: Y · Cache read: Z · Cache write: W" token
   breakdown formatter -- independently written twice (the session-card
   stats tooltip and the agent detail panel's cost badge tooltip both
   surface the same four input/output/cache_read/cache_write_tokens
   fields, tracked at the session and agent level respectively). Callers
   append their own extra suffix (msg_count, tool_use_count) separately. */
function _tokBreakdownStr(o){
  return `Input: ${(o.input_tokens||0).toLocaleString()} · Output: ${(o.output_tokens||0).toLocaleString()} · Cache read: ${(o.cache_read_tokens||0).toLocaleString()} · Cache write: ${(o.cache_write_tokens||0).toLocaleString()}`;
}
/* Tool-use efficiency -- tokens spent per tool call, for a group of
   agents (COST BY MODEL / COST BY AGENT TYPE rows). tool_use_count and
   tokens were both already persisted per-agent and summed into those
   rows for their own existing stats, but never divided against each
   other -- this is the one place that turns "which model/type burns
   more tokens overall" into "...per action it actually took", a
   genuinely different signal (a model that's simply used on bigger
   tasks would win the raw-tokens comparison either way). Returns null
   (not a string) when there's no tool_use_count to divide by, same
   null-for-"nothing to show" convention _ctxPct already uses. */
function _tokPerCall(r){
  return (r.tool_use_count>0) ? Math.round((r.tokens||0)/r.tool_use_count) : null;
}
/* Task completion rate -- task_done/task_total were already summed into
   COST BY PROJECT rows (and by_day, for the trend chart) for other
   stats, but never divided against each other. Same null-for-"nothing to
   show" convention as _tokPerCall/_ctxPct. */
function _taskCompletionPct(r){
  return (r.task_total>0) ? Math.round((r.task_done||0)/r.task_total*100) : null;
}
/* Shared days/hours/minutes formatter -- this exact expression was
   independently duplicated 3 times (the WAITING pill's duration, the
   infra panel's monitor uptime, and renderDiag's own fmtUptime); factored
   out once enough copies existed that "3 similar lines" stopped being the
   simpler option. */
function _fmtDurationDHM(totalSeconds){
  if(totalSeconds==null) return '—';
  const d=Math.floor(totalSeconds/86400), h=Math.floor((totalSeconds%86400)/3600), m=Math.floor((totalSeconds%3600)/60);
  return d>0?`${d}d ${h}h ${m}m`:h>0?`${h}h ${m}m`:`${m}m`;
}
/* Shared context-window-used percentage -- token_limit/tokens_used yield
   the same clamped ratio in three places (renderAgents' token counter bar,
   the agent detail panel's ctx badge, and the Markdown export); factored
   out so all three read the same number instead of three copies of the
   same Math.min/toFixed expression. Returns null (not a string) when
   either input is missing so callers can gate their own rendering. */
function _ctxPct(a){
  return (a.token_limit&&a.tokens_used)?Math.min(a.tokens_used/a.token_limit*100,100).toFixed(0):null;
}
/* Shared parent/child agent resolvers -- (lastStatus.agents||[]).find(ag=>ag.id===a.parent_id)
   for the parent and (lastStatus.agents||[]).filter(ag=>ag.parent_id===a.id) for children were
   independently written in 5 places across the parent_id thread (Agent Compare, the GRAPH/TREE
   hover tooltip, the agent detail panel, exportAgentDetail, exportSessionDetail) -- all 5 resolving
   against this same live lastStatus.agents shape, keyed by "id". showHistoryDetail resolves parent/
   child too, but against a session's DB-row agents array keyed by "agent_id" instead -- a genuinely
   different shape, left alone rather than bent into one helper with an extra key-name parameter. */
function _resolveParentAgent(a){
  return a.parent_id ? (lastStatus?.agents||[]).find(ag=>ag.id===a.parent_id) : null;
}
function _resolveChildAgents(a){
  return (lastStatus?.agents||[]).filter(ag=>ag.parent_id===a.id);
}
/* Shared short duration formatter ("Xs" / "Xm Ys", no days) -- distinct
   range from _fmtDurationDHM above (that one's for multi-hour/day uptimes;
   this one's for session/agent durations that are usually under an hour).
   Was independently written 6 times across the compare modals, history
   view, and KPI bar with tiny drift (===null vs falsy null-check, rounded
   vs not). Standardized here on `s==null` (only null/undefined -> em-dash)
   rather than the falsy-check 4 of the 6 originals used -- a sub-second
   session duration can legitimately compute to a real 0 (see _renderCompare's
   local dur(), which floors ms/1000), and those 4 sites were silently
   showing '—' for that case instead of the more honest '0s' the other 2
   sites already showed. Minor latent inconsistency fixed as a side effect
   of consolidating, not left in place under the new shared function. */
function _fmtDurShort(s, round=false){
  if(s==null) return '—';
  return s<60?(round?Math.round(s):s)+'s':Math.floor(s/60)+'m '+((round?Math.round(s):s)%60)+'s';
}
/* Shared cost formatter ($X.XXXX) -- independently re-declared identically
   4 times (session compare, history view, KPI-adjacent render paths). */
function _fmtCost(c){ return c!=null?'$'+(+c).toFixed(4):'—'; }
async function dismissAgent(id,machineName){
  try{
    await fetch(_apiUrl(_machineFor(machineName),'/remove'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});
  }catch(e){}
}
async function clearDoneAgents(){
  try{
    await fetch('/clear_done',{method:'POST'});
    log('All done agents cleared','success');
  }catch(e){ log('Clear failed: '+e.message,'error'); }
}
function toggleAgentLog(id){
  const tog=document.getElementById('lt-'+id);
  const pan=document.getElementById('lp-'+id);
  if(!tog||!pan) return;
  const open=pan.classList.toggle('open');
  tog.classList.toggle('open',open);
}

/* ── copy to clipboard ── */
function copyText(btn,text){
  navigator.clipboard.writeText(text).then(()=>{
    const o=btn.textContent; btn.textContent='✓ copied';
    setTimeout(()=>btn.textContent=o,1500);
  }).catch(()=>{});
}

/* ── toast notifications ── */
(function(){
  const style=document.createElement('style');
  style.textContent=`
    #toast-container{position:fixed;bottom:24px;right:24px;z-index:9998;display:flex;flex-direction:column-reverse;gap:8px;pointer-events:none;}
    .toast{display:flex;align-items:flex-start;gap:10px;padding:10px 14px;border-radius:12px;background:rgba(6,14,30,.92);backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,.1);box-shadow:0 8px 32px rgba(0,0,0,.5);font-size:12px;min-width:220px;max-width:320px;pointer-events:auto;animation:toastIn .3s ease;}
    .toast.done{border-color:rgba(0,232,135,.3);} .toast.error{border-color:rgba(255,51,85,.3);}
    .toast-icon{font-size:15px;flex-shrink:0;margin-top:1px;}
    .toast-body{flex:1;} .toast-title{font-family:var(--font2);font-size:11px;font-weight:700;letter-spacing:.06em;margin-bottom:2px;}
    .toast.done .toast-title{color:var(--g);} .toast.error .toast-title{color:var(--r);}
    .toast-msg{color:var(--t2);font-size:11px;line-height:1.4;word-break:break-word;}
    .toast-close{background:none;border:none;color:var(--t3);cursor:pointer;font-size:13px;padding:0;flex-shrink:0;line-height:1;}
    @keyframes toastIn{from{opacity:0;transform:translateX(24px)}to{opacity:1;transform:none}}
  `;
  document.head.appendChild(style);
  const container=document.createElement('div');
  container.id='toast-container';
  document.body.appendChild(container);
})();

function showToast(type, title, msg, duration=5000){
  const c=document.getElementById('toast-container');
  if(!c) return;
  const t=document.createElement('div');
  t.className=`toast ${type}`;
  t.innerHTML=`<div class="toast-icon">${type==='done'?'✓':'✕'}</div><div class="toast-body"><div class="toast-title">${title}</div><div class="toast-msg">${msg||''}</div></div><button class="toast-close" onclick="this.parentNode.remove()">✕</button>`;
  c.appendChild(t);
  setTimeout(()=>t.remove(),duration);
}

/* ── audio notifications (Web Audio API) ── */
function _note(ac, freq, vol, start, dur, wave='sine'){
  const o=ac.createOscillator(), g=ac.createGain();
  o.type=wave; o.connect(g); g.connect(ac.destination);
  o.frequency.value=freq;
  g.gain.setValueAtTime(Math.max(vol,0.001), ac.currentTime+start);
  g.gain.exponentialRampToValueAtTime(0.001, ac.currentTime+start+dur);
  o.start(ac.currentTime+start); o.stop(ac.currentTime+start+dur);
}
const _soundProfiles = {
  default: {
    done:    (ac,v)=>{ _note(ac,880,v*.18,0,.35); },
    error:   (ac,v)=>{ _note(ac,220,v*.2,0,.25); _note(ac,180,v*.2,.18,.25); },
    running: (ac,v)=>{ _note(ac,440,v*.1,0,.12); _note(ac,660,v*.1,.1,.12); },
    stuck:   (ac,v)=>{ [0,.28,.56].forEach(t=>_note(ac,330,v*.15,t,.2)); },
    burn_spike: (ac,v)=>{ [0,.15,.3,.45].forEach(t=>_note(ac,500,v*.15,t,.12)); },
    waiting_nudge: (ac,v)=>{ _note(ac,392,v*.1,0,.5); },
    cost_spike: (ac,v)=>{ _note(ac,340,v*.15,0,.2); _note(ac,300,v*.15,.18,.25); },
  },
  minimal: {
    done:    (ac,v)=>{ _note(ac,1000,v*.08,0,.1); },
    error:   (ac,v)=>{ _note(ac,200,v*.1,0,.15); },
    running: (ac,v)=>{ _note(ac,600,v*.05,0,.07); },
    stuck:   (ac,v)=>{ _note(ac,300,v*.07,0,.14); },
    burn_spike: (ac,v)=>{ _note(ac,450,v*.07,0,.1); },
    waiting_nudge: (ac,v)=>{ _note(ac,392,v*.05,0,.3); },
    cost_spike: (ac,v)=>{ _note(ac,340,v*.06,0,.12); },
  },
  retro: {
    done:    (ac,v)=>{ [523,659,784].forEach((f,i)=>_note(ac,f,v*.2,i*.08,.1,'square')); },
    error:   (ac,v)=>{ [200,160,120].forEach((f,i)=>_note(ac,f,v*.22,i*.1,.14,'square')); },
    running: (ac,v)=>{ _note(ac,440,v*.15,0,.07,'square'); _note(ac,880,v*.1,.06,.07,'square'); },
    stuck:   (ac,v)=>{ [0,.2,.4].forEach(t=>_note(ac,220,v*.18,t,.13,'square')); },
    burn_spike: (ac,v)=>{ [0,.1,.2,.3].forEach(t=>_note(ac,330,v*.16,t,.08,'square')); },
    waiting_nudge: (ac,v)=>{ _note(ac,392,v*.12,0,.2,'square'); },
    cost_spike: (ac,v)=>{ [340,300].forEach((f,i)=>_note(ac,f,v*.16,i*.1,.12,'square')); },
  },
  subtle: {
    done:    (ac,v)=>{ _note(ac,660,v*.06,0,.55); },
    error:   (ac,v)=>{ _note(ac,160,v*.07,0,.4); },
    running: (ac,v)=>{ _note(ac,440,v*.04,0,.2); },
    stuck:   (ac,v)=>{ _note(ac,250,v*.05,0,.35); },
    burn_spike: (ac,v)=>{ _note(ac,380,v*.05,0,.3); },
    waiting_nudge: (ac,v)=>{ _note(ac,392,v*.04,0,.4); },
    cost_spike: (ac,v)=>{ _note(ac,340,v*.045,0,.35); },
  },
  alert: {
    done:    (ac,v)=>{ _note(ac,880,v*.25,0,.13); _note(ac,1100,v*.25,.11,.13); _note(ac,1320,v*.22,.2,.22); },
    error:   (ac,v)=>{ [440,220,440,180].forEach((f,i)=>_note(ac,f,v*.28,i*.12,.12,'sawtooth')); },
    running: (ac,v)=>{ _note(ac,550,v*.18,0,.11); _note(ac,770,v*.15,.09,.11); },
    stuck:   (ac,v)=>{ [0,.14,.28,.42].forEach(t=>_note(ac,330,v*.22,t,.11,'sawtooth')); },
    burn_spike: (ac,v)=>{ [0,.08,.16,.24].forEach(t=>_note(ac,500,v*.2,t,.08,'sawtooth')); },
    waiting_nudge: (ac,v)=>{ _note(ac,392,v*.15,0,.3,'sawtooth'); },
    cost_spike: (ac,v)=>{ [340,300,260].forEach((f,i)=>_note(ac,f,v*.22,i*.1,.12,'sawtooth')); },
  },
};
function _playSound(eventType){
  if(_soundMuted||!_soundPrefs.events[eventType]) return;
  try{
    const ac=new(window.AudioContext||window.webkitAudioContext)();
    ((_soundProfiles[_soundPrefs.profile]||_soundProfiles.default)[eventType]||(() => {}))(ac, _soundPrefs.volume);
  }catch(e){}
}
/* legacy shim for any remaining playBeep calls */
function playBeep(type){ _playSound(type); }

/* ── sound settings panel ── */
function openSoundPanel(){
  _soundPanelOpen=!_soundPanelOpen;
  _renderSoundPanel();
}
function closeSoundPanel(){
  _soundPanelOpen=false;
  const p=document.getElementById('sound-panel');
  if(p) p.style.display='none';
}
function _renderSoundPanel(){
  let p=document.getElementById('sound-panel');
  if(!p){
    p=document.createElement('div');
    p.id='sound-panel';
    p.style.cssText='position:fixed;bottom:50px;right:16px;z-index:4500;background:rgba(6,14,30,.97);backdrop-filter:blur(28px);border:1px solid rgba(var(--c-rgb),.18);border-radius:14px;padding:14px 16px;width:272px;box-shadow:0 8px 40px rgba(0,0,0,.65);font-size:11px';
    document.body.appendChild(p);
    document.addEventListener('click', e=>{
      if(!p.contains(e.target) && e.target.id!=='btn-mute'){ closeSoundPanel(); }
    }, {capture:true});
  }
  p.style.display=_soundPanelOpen?'block':'none';
  if(!_soundPanelOpen) return;
  const profiles=['default','minimal','retro','subtle','alert'];
  const evts=[{k:'done',l:'MISSION COMPLETE'},{k:'error',l:'SYSTEM FAILURE'},{k:'running',l:'AGENT ACTIVATED'},{k:'stuck',l:'AGENT STUCK'},{k:'burn_spike',l:'BURN SPIKE'},{k:'waiting_nudge',l:'WAITING TOO LONG'},{k:'cost_spike',l:'COST SPIKE'}];
  const btnBase='padding:3px 10px;border-radius:6px;font-family:var(--font2);font-size:9px;letter-spacing:.08em;cursor:pointer;';
  p.innerHTML=`
  <div style="display:flex;align-items:center;gap:6px;margin-bottom:11px">
    <div style="font-family:var(--font2);font-size:11px;letter-spacing:.14em;color:var(--c);font-weight:700;flex:1">SOUND SETTINGS</div>
    <button onclick="closeSoundPanel()" style="background:none;border:none;color:var(--t3);cursor:pointer;font-size:15px;padding:0;line-height:1">✕</button>
  </div>
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:9px">
    <span style="font-family:var(--font2);font-size:9px;letter-spacing:.1em;color:var(--t3);min-width:46px">MUTE</span>
    <button onclick="_spToggleMute()" style="${btnBase}border:1px solid ${_soundMuted?'rgba(255,51,85,.4)':'rgba(var(--c-rgb),.3)'};background:${_soundMuted?'rgba(255,51,85,.08)':'rgba(var(--c-rgb),.07)'};color:${_soundMuted?'var(--r)':'var(--c)'};">${_soundMuted?'🔇 MUTED':'🔊 ON'}</button>
  </div>
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:11px">
    <span style="font-family:var(--font2);font-size:9px;letter-spacing:.1em;color:var(--t3);min-width:46px">VOLUME</span>
    <input type="range" min="0" max="1" step="0.05" value="${_soundPrefs.volume}" oninput="_spSetVol(this.value)" style="flex:1;accent-color:var(--c);height:3px">
    <span id="sp-vol-lbl" style="font-family:var(--font2);font-size:10px;color:var(--c);min-width:30px;text-align:right">${Math.round(_soundPrefs.volume*100)}%</span>
  </div>
  <div style="font-family:var(--font2);font-size:9px;letter-spacing:.1em;color:var(--t3);margin-bottom:6px">PROFILE</div>
  <div style="display:flex;gap:5px;flex-wrap:wrap;margin-bottom:11px">
    ${profiles.map(pr=>`<button onclick="_spSetProfile('${pr}')" style="${btnBase}border:1px solid ${_soundPrefs.profile===pr?'rgba(var(--c-rgb),.5)':'rgba(255,255,255,.08)'};background:${_soundPrefs.profile===pr?'rgba(var(--c-rgb),.12)':'transparent'};color:${_soundPrefs.profile===pr?'var(--c)':'var(--t2)'};">${pr.toUpperCase()}</button>`).join('')}
  </div>
  <div style="font-family:var(--font2);font-size:9px;letter-spacing:.1em;color:var(--t3);margin-bottom:5px">EVENTS</div>
  ${evts.map(ev=>`<div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid rgba(255,255,255,.04)">
    <input type="checkbox" ${_soundPrefs.events[ev.k]?'checked':''} onchange="_spToggleEvent('${ev.k}',this.checked)" style="accent-color:var(--c);flex-shrink:0;cursor:pointer">
    <span style="flex:1;font-family:var(--font2);font-size:9px;letter-spacing:.06em;color:var(--t2)">${ev.l}</span>
    <button onclick="_spPreview('${ev.k}')" title="Preview sound" style="padding:2px 9px;border-radius:5px;border:1px solid rgba(255,255,255,.07);background:rgba(255,255,255,.03);color:var(--t3);font-family:var(--font2);font-size:9px;cursor:pointer" onmouseover="this.style.color='var(--c)';this.style.borderColor='rgba(var(--c-rgb),.3)'" onmouseout="this.style.color='var(--t3)';this.style.borderColor='rgba(255,255,255,.07)'">▶</button>
  </div>`).join('')}`;
}
function _spToggleMute(){ _soundMuted=!_soundMuted; _savePrefs(); _applyMuteUI(); _renderSoundPanel(); }
function _spSetVol(v){
  _soundPrefs.volume=parseFloat(v);
  const l=document.getElementById('sp-vol-lbl'); if(l) l.textContent=Math.round(v*100)+'%';
  _saveSoundPrefs();
}
function _spSetProfile(pr){ _soundPrefs.profile=pr; _saveSoundPrefs(); _renderSoundPanel(); }
function _spToggleEvent(k,v){ _soundPrefs.events[k]=v; _saveSoundPrefs(); }
function _spPreview(eventType){
  const wasM=_soundMuted, wasE=_soundPrefs.events[eventType];
  _soundMuted=false; _soundPrefs.events[eventType]=true;
  _playSound(eventType);
  _soundMuted=wasM; _soundPrefs.events[eventType]=wasE;
}
function _saveSoundPrefs(){ try{ localStorage.setItem('aoc_sound',JSON.stringify(_soundPrefs)); }catch(e){} }
function _loadSoundPrefs(){
  try{
    const s=localStorage.getItem('aoc_sound');
    if(s){ const p=JSON.parse(s); _soundPrefs={..._soundPrefs,...p}; if(!_soundPrefs.events) _soundPrefs.events={done:true,error:true,running:false,stuck:false,burn_spike:false,waiting_nudge:false,cost_spike:false}; }
  }catch(e){}
}

/* ── conflict detector ── */
function checkConflicts(agents){
  const fileMap={};
  agents.filter(a=>!String(a.id||'').startsWith('hook_')).forEach(a=>{
    (a.files_changed||[]).forEach(f=>{
      if(!fileMap[f.path]) fileMap[f.path]=[];
      fileMap[f.path].push(a.unit||String(a.id).slice(-4).toUpperCase());
    });
  });
  return Object.entries(fileMap).filter(([,units])=>units.length>1).map(([path,units])=>({path,units}));
}

function renderConflicts(agents){
  const conflicts=checkConflicts(agents);
  let el=document.getElementById('conflict-banner');
  if(!el){
    el=document.createElement('div');
    el.id='conflict-banner';
    el.style.cssText='display:none;margin:0 16px 8px;padding:7px 12px;border-radius:10px;background:rgba(255,140,0,.08);border:1px solid rgba(255,140,0,.3);border-left:3px solid var(--o);font-size:11px;color:rgba(255,180,60,.85);';
    const cardsArea=document.getElementById('cards-area');
    cardsArea&&cardsArea.parentNode.insertBefore(el,cardsArea);
  }
  if(conflicts.length===0){ el.style.display='none'; return; }
  el.style.display='block';
  el.innerHTML='<b style="font-family:var(--font2);font-size:10px;letter-spacing:.06em;color:var(--o)">⚠ CONFLICT</b> '+conflicts.map(c=>`<span style="font-family:var(--font2)">${c.path.split('/').pop()}</span> edited by ${c.units.join(' + ')}`).join(' &nbsp;·&nbsp; ');
}

async function showDiff(filepath){
  try{
    const d=await fetch('/diff?file='+encodeURIComponent(filepath)).then(r=>r.json());
    const html=d.diff.split('\n').map(line=>{
      const s=escHtml(line);
      if(line.startsWith('+++') || line.startsWith('---')) return `<span class="diff-meta">${s}</span>`;
      if(line.startsWith('+')) return `<span class="diff-add">${s}</span>`;
      if(line.startsWith('-')) return `<span class="diff-del">${s}</span>`;
      if(line.startsWith('@@')) return `<span class="diff-hunk">${s}</span>`;
      if(line.startsWith('diff')||line.startsWith('index')) return `<span class="diff-meta">${s}</span>`;
      return s;
    }).join('\n');
    const overlay=document.createElement('div');
    overlay.className='diff-overlay';
    overlay.innerHTML=`
      <div class="diff-box">
        <div class="diff-hdr">
          <div class="diff-hdr-name">// ${filepath}</div>
          <button onclick="copyText(this,this.closest('.diff-box').querySelector('.diff-body').innerText)" style="font-size:11px;padding:2px 10px;border-radius:6px;border:1px solid rgba(var(--c-rgb),.22);background:rgba(var(--c-rgb),.06);color:var(--t2);cursor:pointer;font-family:var(--font);margin-right:6px">copy</button>
          <button class="diff-close" onclick="this.closest('.diff-overlay').remove()">✕</button>
        </div>
        <div class="diff-body">${html||'<span style="color:var(--t3)">(no diff — file unchanged)</span>'}</div>
      </div>`;
    overlay.addEventListener('click',e=>{ if(e.target===overlay) overlay.remove(); });
    document.body.appendChild(overlay);
  }catch(e){ log('DIFF ERROR: '+e.message,'error'); }
}

/* ── session summary view ── */
function renderSummary(data){
  const el=document.getElementById('summary-area');
  if(!el) return;
  const agents=(data.agents||[]).filter(a=>!String(a.id||'').startsWith('hook_'));
  if(!agents.length && !data.session_active){
    el.innerHTML='<div style="color:var(--t3);font-family:var(--font2);font-size:11px;text-align:center;padding:60px 0;letter-spacing:.1em">// no session data — start agents to see summary</div>';
    return;
  }
  const running=agents.filter(a=>a.status==='running').length;
  const done=agents.filter(a=>a.status==='done').length;
  const errors=agents.filter(a=>a.status==='error').length;
  const totalTokens=agents.reduce((s,a)=>s+(a.tokens_used||0),0);
  const cost=agents.reduce((s,a)=>s+_agentCost(a),0).toFixed(4);
  const allTasks=agents.flatMap(a=>a.tasks||[]);
  const doneTasks=allTasks.filter(t=>t.done).length;
  const seen=new Set(); const allFiles=[];
  agents.forEach(a=>{
    (a.files_changed||[]).forEach(f=>{
      if(!seen.has(f.path)){ seen.add(f.path); allFiles.push({...f,agent:a.unit||a.name}); }
    });
  });
  const sessStart=data.started_at?parseTimeStr(data.started_at):null;
  const duration=sessStart?Math.round((Date.now()-sessStart)/1000):0;
  const durStr=duration>0?(duration<60?duration+'s':Math.floor(duration/60)+'m '+(duration%60)+'s'):'—';
  const hdr=(k,v)=>`<div style="font-family:var(--font2);font-size:10px;color:var(--t3);letter-spacing:.08em;padding:4px 0 2px">${k}: <span style="color:var(--c)">${v}</span></div>`;

  /* ── per-session breakdown ── */
  const _sessMap={};
  agents.forEach(a=>{
    const sid=a.session_id||'default';
    if(!_sessMap[sid]) _sessMap[sid]={id:sid,project:a.session_project||a.session_id||'—',agents:[],running:0,done:0,errors:0,tasksDone:0,tasksTotal:0,tokens:0,cost:0,files:new Set()};
    const _s=_sessMap[sid];
    _s.agents.push(a);
    if(a.status==='running'||a.status==='waiting') _s.running++;
    else if(a.status==='done') _s.done++;
    else if(a.status==='error') _s.errors++;
    (a.tasks||[]).forEach(t=>{_s.tasksTotal++;if(t.done)_s.tasksDone++;});
    _s.tokens+=a.tokens_used||0;
    _s.cost+=_agentCost(a);
    (a.files_changed||[]).forEach(f=>_s.files.add(f.path));
  });
  const _sessions=Object.values(_sessMap).sort((a,b)=>b.running-a.running||b.agents.length-a.agents.length);

  el.innerHTML=`<div style="max-width:860px;width:100%;margin:0 auto;display:flex;flex-direction:column;gap:14px">
    <div class="sum-grid">
      <div class="sum-stat"><div class="sv">${agents.length}</div><div class="sl">Agents</div></div>
      <div class="sum-stat"><div class="sv" style="color:var(--g)">${done}</div><div class="sl">Completed</div></div>
      <div class="sum-stat"><div class="sv" style="color:var(--o)">${running}</div><div class="sl">Active</div></div>
      <div class="sum-stat"><div class="sv" style="color:${errors?'var(--r)':'var(--t3)'}">${errors}</div><div class="sl">Errors</div></div>
    </div>
    <div class="sum-grid">
      <div class="sum-stat"><div class="sv">${totalTokens>0?Number(totalTokens).toLocaleString():'—'}</div><div class="sl">Tokens</div></div>
      <div class="sum-stat"><div class="sv" style="color:rgba(0,210,130,.9)">${totalTokens>0?'$'+cost:'—'}</div><div class="sl">Est. Cost</div></div>
      <div class="sum-stat"><div class="sv">${doneTasks}/${allTasks.length}</div><div class="sl">Tasks</div></div>
      <div class="sum-stat"><div class="sv">${allFiles.length}</div><div class="sl">Files</div></div>
    </div>
    ${hdr('DURATION',durStr)} ${hdr('PROJECT',data.project||'—')}
    ${_sessions.length>1?`
    <div style="font-family:var(--font2);font-size:10px;color:var(--t3);letter-spacing:.08em;padding:8px 0 4px">CLI SESSIONS (${_sessions.length})</div>
    <div style="display:flex;flex-direction:column;gap:6px">
    ${_sessions.map(_s=>{
      const _spct=_s.tasksTotal>0?Math.round(_s.tasksDone/_s.tasksTotal*100):(_s.done>0?100:0);
      const _scol=_s.errors>0?'var(--r)':_s.running>0?'var(--c)':'var(--g)';
      const _scost=_s.cost.toFixed(4);
      const _stokpct=totalTokens>0?(_s.tokens/totalTokens*100).toFixed(0):0;
      const _sstatus=_s.errors>0?'ERROR':_s.running>0?`${_s.running} RUNNING`:_s.done===_s.agents.length?'DONE':'IDLE';
      return `<div style="display:flex;align-items:center;gap:12px;padding:10px 14px;border-radius:11px;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.07);border-left:3px solid ${_scol};transition:background .2s">
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:5px">
            <span style="font-family:var(--font2);font-size:12px;font-weight:700;color:${_scol};overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:200px">${escHtml(_s.project)}</span>
            <span style="font-size:9px;font-family:var(--font2);font-weight:700;letter-spacing:.07em;padding:1px 6px;border-radius:4px;border:1px solid ${_scol};color:${_scol};opacity:.8;flex-shrink:0">${_sstatus}</span>
            ${_s.running>0?`<span style="display:flex;align-items:center;gap:3px;font-size:9px;font-family:var(--font2);color:var(--c);flex-shrink:0"><span style="width:5px;height:5px;border-radius:50%;background:var(--c);animation:pulse 1.5s infinite;display:inline-block"></span></span>`:''}
          </div>
          <div style="height:3px;background:rgba(255,255,255,.06);border-radius:999px;overflow:hidden;margin-bottom:5px">
            <div style="height:100%;width:${_spct}%;background:${_scol};border-radius:999px;transition:width .6s ease;box-shadow:0 0 4px ${_scol}55"></div>
          </div>
          <div style="display:flex;gap:12px;font-size:10px;color:var(--t3);font-family:var(--font2);flex-wrap:wrap">
            <span>${_s.agents.length} agent${_s.agents.length!==1?'s':''}</span>
            <span style="color:${_s.tasksDone===_s.tasksTotal&&_s.tasksTotal>0?'var(--g)':'var(--t3)'}">${_s.tasksDone}/${_s.tasksTotal} tasks</span>
            <span>${_s.files.size} file${_s.files.size!==1?'s':''}</span>
            ${_s.tokens>0?`<span style="color:rgba(0,210,130,.8)">$${_scost}</span>`:''}
            ${totalTokens>0&&_s.tokens>0?`<span style="color:var(--t3)">${_stokpct}% tokens</span>`:''}
          </div>
        </div>
        <div style="text-align:center;flex-shrink:0;min-width:42px">
          <div style="font-family:var(--font2);font-size:22px;font-weight:800;color:${_scol};line-height:1;text-shadow:0 0 12px ${_scol}44">${_spct}</div>
          <div style="font-size:9px;color:var(--t3);letter-spacing:.06em">%</div>
        </div>
      </div>`;
    }).join('')}
    </div>`:''}
    ${totalTokens>0?`<div style="font-family:var(--font2);font-size:10px;color:var(--t3);letter-spacing:.08em;padding:8px 0 4px">TOKEN USAGE HEATMAP</div>
    <div style="display:flex;flex-direction:column;gap:4px">
    ${[...agents].filter(a=>a.tokens_used>0).sort((a,b)=>(b.tokens_used||0)-(a.tokens_used||0)).map(a=>{
      const barW=(a.tokens_used/totalTokens*100).toFixed(1);
      const pct=(a.tokens_used/totalTokens*100).toFixed(0);
      const cost=_agentCost(a).toFixed(4);
      const heat=a.tokens_used/totalTokens;
      const col=heat>0.5?'rgba(255,80,80,.9)':heat>0.25?'rgba(255,140,0,.85)':'rgba(var(--c-rgb),.75)';
      return `<div style="display:flex;align-items:center;gap:8px">
        <div style="font-family:var(--font2);font-size:10px;color:var(--t2);min-width:38px;flex-shrink:0">${_deriveUnit(a)}</div>
        <div style="flex:1;height:10px;background:rgba(255,255,255,.04);border-radius:3px;overflow:hidden">
          <div style="height:100%;width:${barW}%;background:${col};border-radius:3px;transition:width .6s ease;box-shadow:0 0 6px ${col}"></div>
        </div>
        <div style="font-family:var(--font2);font-size:10px;color:${col};width:34px;text-align:right">${pct}%</div>
        <div style="font-family:var(--font2);font-size:10px;color:rgba(0,210,130,.7);width:52px;text-align:right">$${cost}</div>
      </div>`;
    }).join('')}
    </div>`:''}
    ${agents.length?`<div style="font-family:var(--font2);font-size:10px;color:var(--t3);letter-spacing:.08em;padding:6px 0 2px">AGENTS</div>
    <div style="display:flex;flex-direction:column;gap:5px">${agents.map(a=>{
      const ac=(a.tasks||[]).filter(t=>t.done).length, at=(a.tasks||[]).length;
      const col=sCol(a.status);
      return `<div class="sum-agent-row">
        <div style="font-family:var(--font2);font-size:11px;color:${col};min-width:36px">${a.unit||String(a.id).slice(-4).toUpperCase()}</div>
        ${a.detected_via==='transcript'?`<span style="color:rgba(255,170,0,.95);font-size:10px;flex-shrink:0" title="${escHtml(_hookMissTitle(a))}">⚠</span>`:''}
        <div style="flex:1;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(a.name)}</div>
        <div style="font-size:10px;color:var(--t3);font-family:var(--font2)">${ac}/${at}</div>
        ${a.tokens_used?`<div style="font-size:10px;color:rgba(0,210,130,.7);font-family:var(--font2)">$${_agentCost(a).toFixed(4)}</div>`:''}
        <div class="status-pill ${a.status}" style="font-size:9px;padding:2px 7px"><span class="sdot"></span>${a.status.toUpperCase()}</div>
      </div>`;
    }).join('')}</div>`:''}
    ${allFiles.length?`<div style="font-family:var(--font2);font-size:10px;color:var(--t3);letter-spacing:.08em;padding:6px 0 2px">FILES CHANGED (${allFiles.length})</div>
    <div style="display:flex;flex-direction:column;gap:3px">${allFiles.map(f=>`
      <div class="fe ${f.type==='new'?'new':'changed'}" onclick="showDiff('${escHtml(f.path.replace(/'/g,"\\\'"))}')" style="cursor:pointer">
        <span class="fe-badge">${f.type==='new'?'NEW':'MOD'}</span>
        <span class="fe-name" title="${escHtml(f.path)}">${escHtml(f.path)}</span>
        <span style="font-size:9px;color:var(--t3);font-family:var(--font2);flex-shrink:0">${escHtml(f.agent)}</span>
      </div>`).join('')}</div>`:''}
    <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">
      <button onclick="exportSession()" style="font-family:var(--font2);font-size:11px;letter-spacing:.06em;padding:6px 16px;border-radius:8px;border:1px solid rgba(var(--c-rgb),.3);background:rgba(var(--c-rgb),.08);color:var(--c);cursor:pointer;transition:all .2s" onmouseover="this.style.background='rgba(var(--c-rgb),.18)'" onmouseout="this.style.background='rgba(var(--c-rgb),.08)'">↓ EXPORT JSON</button>
      <button id="copy-md-btn" onclick="copyMarkdownSummary(this)" style="font-family:var(--font2);font-size:11px;letter-spacing:.06em;padding:6px 16px;border-radius:8px;border:1px solid rgba(0,232,135,.25);background:rgba(0,232,135,.07);color:rgba(0,232,135,.8);cursor:pointer;transition:all .2s" onmouseover="this.style.background='rgba(0,232,135,.16)'" onmouseout="this.style.background='rgba(0,232,135,.07)'">⧉ COPY MARKDOWN</button>
    </div>
    ${(()=>{
      const meta=(_lastAuditData&&_lastAuditData.file_meta)||[];
      if(!meta.length) return '';
      const fmtSize=b=>b<1024?b+'B':b<1048576?(b/1024).toFixed(1)+'KB':(b/1048576).toFixed(2)+'MB';
      return `<div style="font-family:var(--font2);font-size:10px;color:var(--t3);letter-spacing:.08em;padding:10px 0 4px">RECENT SESSION LOGS</div>
      <div style="display:flex;flex-direction:column;gap:3px">${meta.slice(0,8).map(f=>{
        const date=f.name.replace('session_','').replace('.log','').replace(/_/g,' ');
        return `<div style="display:flex;align-items:center;gap:8px;padding:5px 8px;border-radius:7px;background:rgba(255,255,255,.02);border:1px solid rgba(255,255,255,.05)">
          <span style="font-size:10px;font-family:var(--font2);color:var(--t2);flex:1">${date}</span>
          <span style="font-size:9px;color:var(--t3);font-family:var(--font2)">${fmtSize(f.size)}</span>
          <a href="/logs/${encodeURIComponent(f.name)}" target="_blank" style="font-size:9px;color:var(--c);font-family:var(--font2);text-decoration:none;padding:1px 6px;border:1px solid rgba(var(--c-rgb),.2);border-radius:4px">↓</a>
        </div>`;
      }).join('')}</div>`;
    })()}
  </div>`;
}

function exportSession(){
  if(!lastStatus) return;
  const ts=new Date().toISOString().replace(/[:.]/g,'-').slice(0,19);
  const blob=new Blob([JSON.stringify(lastStatus,null,2)],{type:'application/json'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download=`aoc-session-${ts}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
  log(`Session exported → aoc-session-${ts}.json`,'success');
}

/* ── settings export/import ── bundles every *configuration* field (not
   transient UI state like current view/collapsed-cards, which lives in
   aoc_prefs and is deliberately excluded) into one JSON, for moving to a
   new machine alongside what install.ps1 already does for the code
   itself. _buildSettingsBundle takes its pieces as arguments rather than
   reading globals directly so it stays a plain, testable function. */
function _buildSettingsBundle(state){
  return {
    version: 1,
    exported_at: new Date().toISOString(),
    cost_rate: state.costRate,
    budget: state.budget,
    project_budgets: state.projectBudgets,
    webhook_url: state.webhookUrl,
    webhook_events: state.webhookEvents,
    quiet_start: state.quietStart,
    quiet_end: state.quietEnd,
    muted_projects: state.mutedProjects,
    snitch_url: state.snitchUrl,
    digest_cadence: state.digestCadence,
    backup_retention_days: state.backupRetentionDays,
    agent_retention_hours: state.agentRetentionHours,
    accent: state.accent,
    density: state.density,
    sound: state.sound,
    local_machine_name: state.localMachineName,
    remote_machines: state.remoteMachines,
  };
}
function exportAllSettings(){
  const bundle=_buildSettingsBundle({
    costRate:_costRate, budget:_budgetLimit, projectBudgets:_projectBudgets,
    webhookUrl:_webhookUrl, webhookEvents:_webhookEvents,
    quietStart:_quietStart, quietEnd:_quietEnd, mutedProjects:_mutedProjects,
    snitchUrl:_snitchUrl, digestCadence:_digestCadence, backupRetentionDays:_backupRetentionDays,
    agentRetentionHours:_agentRetentionHours,
    accent:_accentName, density:_density, sound:_soundPrefs,
    localMachineName:_localMachineName, remoteMachines:_remoteMachines,
  });
  const ts=new Date().toISOString().replace(/[:.]/g,'-').slice(0,19);
  const blob=new Blob([JSON.stringify(bundle,null,2)],{type:'application/json'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download=`aoc-settings-${ts}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
  log(`Settings exported → aoc-settings-${ts}.json`,'success');
}
function triggerImportSettings(){
  const inp=document.getElementById('settings-import-input');
  if(inp) inp.click();
}
function importAllSettings(fileInput){
  const file=fileInput.files&&fileInput.files[0];
  if(!file) return;
  const reader=new FileReader();
  reader.onload=async ()=>{
    try{
      const b=JSON.parse(reader.result);
      localStorage.setItem('aoc_cost_rate', b.cost_rate ?? 9);
      localStorage.setItem('aoc_budget', b.budget ?? '');
      localStorage.setItem('aoc_project_budgets', JSON.stringify(b.project_budgets||{}));
      localStorage.setItem('aoc_webhook_url', b.webhook_url||'');
      localStorage.setItem('aoc_webhook_events', JSON.stringify(b.webhook_events||{}));
      localStorage.setItem('aoc_quiet_start', b.quiet_start||'');
      localStorage.setItem('aoc_quiet_end', b.quiet_end||'');
      localStorage.setItem('aoc_muted_projects', JSON.stringify(b.muted_projects||[]));
      localStorage.setItem('aoc_snitch_url', b.snitch_url||'');
      localStorage.setItem('aoc_digest_cadence', b.digest_cadence||'weekly');
      localStorage.setItem('aoc_backup_retention_days', b.backup_retention_days||14);
      localStorage.setItem('aoc_agent_retention_hours', b.agent_retention_hours||12);
      localStorage.setItem('aoc_accent', b.accent||'cyan');
      localStorage.setItem('aoc_density', b.density||'normal');
      localStorage.setItem('aoc_sound', JSON.stringify(b.sound||{}));
      localStorage.setItem('aoc_local_machine_name', b.local_machine_name||'');
      localStorage.setItem('aoc_remote_machines', JSON.stringify(b.remote_machines||[]));
      // Also persist the server-side half, matching saveSettings()'s own
      // two POSTs -- otherwise the background workers (webhook delivery,
      // quiet hours, digest) would keep using whatever was there before
      // until the next manual Settings save. Checked for .ok (not just
      // awaited-then-swallowed) for the same reason saveSettings() checks
      // it -- an import that silently fails to reach the server would
      // otherwise still claim success right before reloading the page.
      let serverSyncOk=true;
      try{
        const [whRes,ntRes]=await Promise.all([
          fetch('/webhook_settings',{method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({url:b.webhook_url||'', events:b.webhook_events||{}})}),
          fetch('/notify_settings',{method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({quiet_start:b.quiet_start||'', quiet_end:b.quiet_end||'', muted_projects:b.muted_projects||[], snitch_url:b.snitch_url||'', digest_cadence:b.digest_cadence||'weekly', backup_retention_days:b.backup_retention_days||14, agent_retention_hours:b.agent_retention_hours||12, project_budgets:b.project_budgets||{}})}),
        ]);
        serverSyncOk=whRes.ok&&ntRes.ok;
      }catch(e){ serverSyncOk=false; }
      log(serverSyncOk
        ? 'Settings imported — reloading to apply'
        : 'Settings imported locally, but syncing to the server failed -- webhook/budget/quiet-hours delivery may be running on stale config (re-save from Settings once the server is reachable)',
        serverSyncOk?'success':'error');
      setTimeout(()=>location.reload(), 800);
    }catch(e){
      log('Import failed: invalid settings file','error');
    }
    fileInput.value='';
  };
  reader.readAsText(file);
}

function copyMarkdownSummary(btn){
  if(!lastStatus) return;
  const agents=(lastStatus.agents||[]).filter(a=>!String(a.id||'').startsWith('hook_'));
  const running=agents.filter(a=>a.status==='running').length;
  const done=agents.filter(a=>a.status==='done').length;
  const errors=agents.filter(a=>a.status==='error').length;
  const totalTokens=agents.reduce((s,a)=>s+(a.tokens_used||0),0);
  const cost=agents.reduce((s,a)=>s+_agentCost(a),0).toFixed(4);
  const allTasks=agents.flatMap(a=>a.tasks||[]);
  const doneTasks=allTasks.filter(t=>t.done).length;
  const sessStart=lastStatus.started_at?parseTimeStr(lastStatus.started_at):null;
  const duration=sessStart?Math.round((Date.now()-sessStart)/1000):0;
  const durStr=duration>0?(duration<60?duration+'s':Math.floor(duration/60)+'m '+(duration%60)+'s'):'—';
  const now=new Date();
  const timeStr=`${pad(now.getHours())}:${pad(now.getMinutes())}`;

  let md=`## AOC Session — ${timeStr}\n\n`;
  md+=`- **Project**: ${lastStatus.project||'—'}\n`;
  md+=`- **Agents**: ${agents.length} (${done} done, ${running} running${errors?`, ${errors} error`:''}) \n`;
  md+=`- **Tasks**: ${doneTasks}/${allTasks.length} completed\n`;
  md+=`- **Duration**: ${durStr}\n`;
  if(totalTokens>0) md+=`- **Tokens**: ${Number(totalTokens).toLocaleString()} (~$${cost})\n`;
  /* per-session breakdown in markdown */
  const _mdSessMap={};
  agents.forEach(a=>{
    const sid=a.session_id||'default';
    if(!_mdSessMap[sid]) _mdSessMap[sid]={project:a.session_project||sid.slice(-6),agents:[],tasksDone:0,tasksTotal:0,tokens:0,cost:0,running:0};
    const _ms=_mdSessMap[sid];
    _ms.agents.push(a);
    if(a.status==='running'||a.status==='waiting') _ms.running++;
    (a.tasks||[]).forEach(t=>{_ms.tasksTotal++;if(t.done)_ms.tasksDone++;});
    _ms.tokens+=a.tokens_used||0;
    _ms.cost+=_agentCost(a);
  });
  const _mdSessions=Object.values(_mdSessMap);
  if(_mdSessions.length>1){
    md+=`\n### CLI Sessions (${_mdSessions.length})\n`;
    _mdSessions.forEach(_ms=>{
      const _pct=_ms.tasksTotal>0?Math.round(_ms.tasksDone/_ms.tasksTotal*100):0;
      const _cost=_ms.tokens>0?` · $${_ms.cost.toFixed(4)}`:'';
      const _status=_ms.running>0?`${_ms.running} running`:_ms.agents.every(a=>a.status==='done')?'done':'idle';
      md+=`- **${_ms.project}** — ${_ms.agents.length} agents · ${_ms.tasksDone}/${_ms.tasksTotal} tasks (${_pct}%) · ${_status}${_cost}\n`;
    });
  }
  md+=`\n### Agents\n`;
  agents.forEach(a=>{
    const icon=a.status==='done'?'✓':a.status==='error'?'✗':a.status==='running'?'▶':'·';
    const taskStr=(a.tasks||[]).filter(t=>t.done).length+'/'+((a.tasks||[]).length)+' tasks';
    const tokStr=a.tokens_used?` · ${Number(a.tokens_used).toLocaleString()} tokens`:'';
    const errStr=(a.status==='error'&&a.error_message)?` — ${a.error_message.slice(0,60)}`:'';
    md+=`- ${icon} **${a.name}** (${taskStr}${tokStr}${errStr})\n`;
  });
  navigator.clipboard.writeText(md).then(()=>{
    const orig=btn.textContent; btn.textContent='✓ COPIED';
    setTimeout(()=>btn.textContent=orig,2000);
    log('Markdown summary copied to clipboard','success');
  }).catch(()=>{ log('Clipboard copy failed','error'); });
}

/* ── dependency graph view ── */
function sColRgba(s){ return {running:'rgba(var(--c-rgb),.85)',done:'rgba(0,232,135,.85)',error:'rgba(255,51,85,.85)',waiting:'rgba(255,140,0,.85)'}[s]||'rgba(var(--c-rgb),.85)'; }

/* GRAPH/TREE are hand-built SVG with colors baked into inline `style=`/
   presentation attributes at render time -- a `body.light #graph-area svg
   {background:...}` stylesheet rule can never win against an inline style
   on the same element/property (inline style always outranks any
   non-!important stylesheet rule, regardless of selector specificity), so
   the theme has to be read here and baked into the right values up front
   instead. Shared by both views since they use the same dark-radar visual
   language and had the exact same bug. */
function _graphThemeColors(){
  if(document.body.classList.contains('light')) return {
    panelBg:'rgba(255,255,255,.55)', panelBorder:'rgba(0,0,0,.08)',
    nodeFill:'rgba(255,255,255,.85)', nodeStroke:'rgba(0,0,0,.08)',
    nodeText:'rgba(30,55,90,.7)', pctText:'rgba(20,60,95,.7)',
    edgeLine:'rgba(0,90,160,.32)', edgeText:'rgba(20,60,110,.6)',
    depText:'rgba(180,90,0,.55)', legendHint:'rgba(30,60,100,.5)',
    emptyText:'rgba(40,70,110,.5)',
    tooltipBg:'rgba(255,255,255,.97)', tooltipBorder:'rgba(0,0,0,.12)',
  };
  return {
    panelBg:'rgba(0,0,0,.14)', panelBorder:'rgba(255,255,255,.06)',
    nodeFill:'rgba(4,12,28,.9)', nodeStroke:'rgba(255,255,255,.04)',
    nodeText:'rgba(140,180,220,.65)', pctText:'rgba(160,200,230,.7)',
    edgeLine:'rgba(100,180,255,.3)', edgeText:'rgba(180,210,255,.55)',
    depText:'rgba(255,140,0,.4)', legendHint:'rgba(100,150,200,.35)',
    emptyText:'rgba(90,140,180,.3)',
    tooltipBg:'rgba(6,14,30,.96)', tooltipBorder:'rgba(var(--c-rgb),.2)',
  };
}
function renderGraph(data){
  const el=document.getElementById('graph-area');
  if(!el) return;
  if(!data){ el.innerHTML='<div style="color:var(--t3);font-family:var(--font2);font-size:10px;letter-spacing:.1em">WAITING FOR DATA</div>'; return; }
  const agents=(data.agents||[]).filter(a=>!String(a.id||'').startsWith('hook_'));
  if(!agents.length){
    el.innerHTML='<div style="color:var(--t3);font-family:var(--font2);font-size:10px;letter-spacing:.12em">NO AGENTS TO GRAPH</div>';
    return;
  }
  const tc=_graphThemeColors();
  const W=680, H=Math.max(360, agents.length>4?500:360);
  const cx=W/2, cy=H/2, R=Math.min(cx-90, cy-80, 170);
  const n=agents.length;
  const pos=agents.map((_,i)=>{
    if(n===1) return {x:cx,y:cy};
    const ang=(2*Math.PI*i/n)-Math.PI/2;
    return {x:Math.round(cx+R*Math.cos(ang)), y:Math.round(cy+R*Math.sin(ang))};
  });
  /* build edges: shared files + explicit depends_on */
  const edges=[];
  for(let i=0;i<n;i++) for(let j=i+1;j<n;j++){
    const ai=agents[i], aj=agents[j];
    const fa=new Set((ai.files_changed||[]).map(f=>f.path));
    const shared=(aj.files_changed||[]).filter(f=>fa.has(f.path)).map(f=>f.path);
    const iDepsJ=(ai.depends_on||[]).includes(aj.id);
    const jDepsI=(aj.depends_on||[]).includes(ai.id);
    if(shared.length||iDepsJ||jDepsI) edges.push({a:i,b:j,files:shared,iDepsJ,jDepsI});
  }
  /* SVG defs for arrow markers */
  const defs=`<defs>
    <marker id="arr-dep" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0,8 3,0 6" fill="rgba(255,140,0,.75)"/></marker>
    <filter id="glow"><feGaussianBlur in="SourceGraphic" stdDeviation="3" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  </defs>`;
  /* edges */
  const edgeSvg=edges.map((e,ei)=>{
    const a=pos[e.a], b=pos[e.b];
    const mx=Math.round((a.x+b.x)/2), my=Math.round((a.y+b.y)/2)-10;
    const sw=(Math.min(e.files.length*1.2+1.2,5)).toFixed(1);
    const isDep=e.iDepsJ||e.jDepsI;
    const stroke=isDep?'rgba(255,140,0,.55)':tc.edgeLine;
    const dash=isDep?'':'8 4';
    const marker=e.iDepsJ?'url(#arr-dep)':'';
    const markerS=e.jDepsI?'url(#arr-dep)':'';
    const safeA=escHtml(agents[e.a].id), safeB=escHtml(agents[e.b].id);
    return `<g onclick="_graphEdgeClick('${safeA}','${safeB}')" style="cursor:pointer">
  <line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="transparent" stroke-width="14"/>
  <line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="${stroke}" stroke-width="${sw}" ${dash?`stroke-dasharray="${dash}"`:''}${marker?` marker-end="${marker}"`:''}${markerS?` marker-start="${markerS}"`:''}/>
  ${e.files.length?`<text x="${mx}" y="${my}" text-anchor="middle" font-size="8" fill="${tc.edgeText}" font-family="Consolas,monospace">${e.files.length} shared</text>`:''}
  ${isDep?`<text x="${mx}" y="${my+10}" text-anchor="middle" font-size="7" fill="rgba(255,160,60,.7)" font-family="Consolas,monospace">DEPENDS</text>`:''}
</g>`;
  }).join('\n');
  /* nodes */
  const nodeSvg=agents.map((a,i)=>{
    const {x,y}=pos[i];
    const rgba=sColRgba(a.status);
    const lbl=_deriveUnit(a).slice(0,4);
    const name=a.name.length>22?a.name.slice(0,21)+'…':a.name;
    const pct=a.tasks.length?Math.round(a.tasks.filter(t=>t.done).length/a.tasks.length*100):0;
    const r=28;
    const isActive=_adpAgentId===a.id;
    const connCount=edges.filter(e=>e.a===i||e.b===i).length;
    const pts=Array.from({length:6},(_,k)=>{
      const ag=k*Math.PI/3-Math.PI/6;
      return `${(x+r*Math.cos(ag)).toFixed(0)},${(y+r*Math.sin(ag)).toFixed(0)}`;
    }).join(' ');
    const arcR=r+8, arcC=(2*Math.PI*arcR).toFixed(1), arcD=(pct/100*2*Math.PI*arcR).toFixed(1);
    const sid=escHtml(a.id);
    return `<g onclick="openAgentDetail('${sid}',null)" onmouseover="_graphNodeHover(event,'${sid}')" onmouseout="_graphNodeOut()" style="cursor:pointer${isActive?';filter:drop-shadow(0 0 10px '+rgba+')':''}">
  ${a.status==='running'?`<circle cx="${x}" cy="${y}" r="${arcR+6}" fill="none" stroke="${rgba}" stroke-width="1" opacity=".25" style="animation:pulse 2s infinite"/>`:''}
  <polygon points="${pts}" fill="${isActive?rgba.replace(',1)',',.18)').replace('.85)',',.18)'):tc.nodeFill}" stroke="${rgba}" stroke-width="${isActive?2.8:1.8}"/>
  <circle cx="${x}" cy="${y}" r="${arcR}" fill="none" stroke="${tc.nodeStroke}" stroke-width="2.5"/>
  <circle cx="${x}" cy="${y}" r="${arcR}" fill="none" stroke="${rgba}" stroke-width="2.5" opacity="${isActive?.95:.55}" stroke-dasharray="${arcD} ${arcC}" stroke-linecap="round" transform="rotate(-90 ${x} ${y})"/>
  <text x="${x}" y="${y-5}" text-anchor="middle" font-family="Consolas,monospace" font-size="11" font-weight="700" fill="${rgba}">${lbl}</text>
  <text x="${x}" y="${y+9}" text-anchor="middle" font-family="Consolas,monospace" font-size="9" fill="${tc.pctText}">${pct}%</text>
  <text x="${x}" y="${y+52}" text-anchor="middle" font-family="Inter,sans-serif" font-size="9" fill="${tc.nodeText}">${escHtml(name)}</text>
  ${connCount>0?`<text x="${x+r+2}" y="${y-r+4}" text-anchor="middle" font-family="Consolas,monospace" font-size="8" fill="${rgba}" opacity=".75">${connCount}</text>`:''}
</g>`;
  }).join('\n');
  /* legend */
  const legend=`
  <text x="10" y="${H-20}" font-family="Consolas,monospace" font-size="8" fill="${tc.legendHint}" letter-spacing=".05em">click node → detail panel · click edge → shared files</text>
  <line x1="10" y1="${H-10}" x2="40" y2="${H-10}" stroke="${tc.edgeLine}" stroke-width="1.5" stroke-dasharray="5 3"/>
  <text x="46" y="${H-7}" font-family="Consolas,monospace" font-size="7" fill="${tc.edgeText}">shared files</text>
  <line x1="110" y1="${H-10}" x2="140" y2="${H-10}" stroke="rgba(255,140,0,.55)" stroke-width="1.5" marker-end="url(#arr-dep)"/>
  <text x="146" y="${H-7}" font-family="Consolas,monospace" font-size="7" fill="${tc.depText}">depends_on</text>`;
  el.innerHTML=`<div style="position:relative;width:100%;display:flex;flex-direction:column;align-items:center;gap:10px">
<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:${W}px;background:${tc.panelBg};border-radius:14px;border:1px solid ${tc.panelBorder}">
${defs}
${edgeSvg}
${nodeSvg}
${legend}
${edges.length===0&&n>1?`<text x="${W/2}" y="${H/2+80}" text-anchor="middle" font-family="Consolas,monospace" font-size="9" fill="${tc.emptyText}" letter-spacing=".1em">NO SHARED FILES — AGENTS INDEPENDENT</text>`:''}
</svg>
<div id="graph-tooltip" style="display:none;position:fixed;z-index:5000;pointer-events:none;background:${tc.tooltipBg};border:1px solid ${tc.tooltipBorder};border-radius:10px;padding:10px 14px;max-width:240px;box-shadow:0 8px 32px rgba(0,0,0,.6)"></div>
</div>`;
}

function _graphNodeHover(event, agentId){
  if(!lastStatus) return;
  const a=(lastStatus.agents||[]).find(ag=>ag.id===agentId);
  if(!a) return;
  const tip=document.getElementById('graph-tooltip');
  if(!tip) return;
  const col=sCol(a.status);
  const stLabel=stxt(a);
  const dt=(a.tasks||[]).filter(t=>t.done).length, tt=(a.tasks||[]).length;
  const cost=a.tokens_used?`$${_agentCost(a).toFixed(4)}`:null;
  // Shared by GRAPH and TREE (_treeNodeHover wraps this), yet TREE -- the
  // one view that's literally about parent_id hierarchy -- never
  // mentioned it in its own hover tooltip; the layout showed it, the text
  // never did. Last surface in the same thread as features 106/107.
  const parentAg=_resolveParentAgent(a);
  const childAgs=_resolveChildAgents(a);
  tip.innerHTML=`<div style="font-weight:700;color:${col};margin-bottom:3px;font-size:12px">${escHtml(a.name)}</div>
<div style="color:${col};font-size:9px;font-family:var(--font2);letter-spacing:.08em;margin-bottom:5px">${stLabel}</div>
${a.description?`<div style="color:var(--t2);margin-bottom:5px;line-height:1.4;font-size:10px">${escHtml(a.description)}</div>`:''}
${a.model?`<div style="color:var(--t3);margin-bottom:5px;font-size:9px;font-family:var(--font2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(a.model)}</div>`:''}
${parentAg?`<div style="color:var(--t3);margin-bottom:5px;font-size:9px;font-family:var(--font2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">↳ spawned by ${escHtml(parentAg.name)}</div>`:''}
${childAgs.length?`<div style="color:var(--t3);margin-bottom:5px;font-size:9px;font-family:var(--font2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">spawned ${childAgs.length} subagent${childAgs.length!==1?'s':''} →</div>`:''}
<div style="font-family:var(--font2);font-size:9px;color:var(--t3);display:flex;gap:10px;flex-wrap:wrap">
  ${a.detected_via==='transcript'?`<span style="color:rgba(255,170,0,.95)">⚠ HOOK MISS</span>`:''}
  ${_stuckSecs(a)>300?`<span style="color:rgba(255,140,0,.95)">⚠ STUCK</span>`:''}
  ${a.subagent_type?`<span>${escHtml(a.subagent_type)}</span>`:''}
  ${a.tool_use_count!=null?`<span>${a.tool_use_count} tc</span>`:''}
  ${tt?`<span>TASKS ${dt}/${tt}</span>`:''}
  ${cost?`<span style="color:rgba(0,210,130,.8)">${cost}</span>`:''}
  ${(a.files_changed||[]).length?`<span>${a.files_changed.length} files</span>`:''}
</div>
${a.status==='error'&&a.error_message?`<div style="margin-top:6px;font-size:10px;color:rgba(255,100,120,.9);font-family:var(--font2);word-break:break-all">${escHtml(a.error_message.slice(0,100))}</div>`:''}
<div style="margin-top:6px;font-size:9px;color:var(--t3);font-family:var(--font2)">click to open detail panel</div>`;
  tip.style.display='block';
  const _move=e=>{
    const vw=window.innerWidth, vh=window.innerHeight;
    let lx=e.clientX+16, ly=e.clientY-20;
    if(lx+250>vw) lx=e.clientX-260;
    if(ly+180>vh) ly=e.clientY-180;
    tip.style.left=lx+'px'; tip.style.top=ly+'px';
  };
  _move(event);
  const svg=event.target.closest('svg');
  if(svg){ svg._gTipMove=_move; svg.addEventListener('mousemove',_move); }
}

function _graphNodeOut(){
  const tip=document.getElementById('graph-tooltip');
  if(tip) tip.style.display='none';
  const svg=document.querySelector('#graph-area svg');
  if(svg&&svg._gTipMove){ svg.removeEventListener('mousemove',svg._gTipMove); delete svg._gTipMove; }
}

function _graphEdgeClick(idA, idB){
  if(!lastStatus) return;
  const agA=(lastStatus.agents||[]).find(a=>a.id===idA);
  const agB=(lastStatus.agents||[]).find(a=>a.id===idB);
  if(!agA||!agB) return;
  const fa=new Set((agA.files_changed||[]).map(f=>f.path));
  const shared=(agB.files_changed||[]).filter(f=>fa.has(f.path)).map(f=>f.path);
  const depAB=(agA.depends_on||[]).includes(idB);
  const depBA=(agB.depends_on||[]).includes(idA);
  let msg=`${agA.name} ↔ ${agB.name}`;
  if(shared.length) msg+='\nShared: '+shared.join(', ');
  if(depAB) msg+=`\n${agA.name} depends on ${agB.name}`;
  if(depBA) msg+=`\n${agB.name} depends on ${agA.name}`;
  showToast('done', 'EDGE DETAIL', shared.length?`${shared.length} shared file${shared.length>1?'s':''}: ${shared.slice(0,3).join(', ')}${shared.length>3?` +${shared.length-3} more`:''}`:depAB||depBA?'Dependency link (no shared files)':'Connected');
  log(msg.replace(/\n/g,' · '),'info');
}


/* ── render agents ── */
function renderAgents(data){
  const mainArea=document.getElementById('main-area');
  let running=0, done=0, errors=0, totalT=0, doneT=0;

  /* filter out internal hook entries written by claude hooks */
  const agents=(data.agents||[]).filter(a=>!String(a.id||'').startsWith('hook_'));

  const cardsArea = document.getElementById('cards-area');

  /* idle state — only when no CLIs are connected at all */
  if(!data.session_active && agents.length===0 && (data.sessions_count||0)===0){
    if(!cardsArea.querySelector('.idle')){
      cardsArea.innerHTML=`
      <div class="idle">
        <div class="idle-hex">
          <svg viewBox="0 0 48 48">
            <polygon points="24,4 44,14 44,34 24,44 4,34 4,14" stroke-width=".8"/>
            <circle cx="24" cy="24" r="10" stroke-width=".8"/>
            <line x1="24" y1="4" x2="24" y2="44" stroke-width=".5"/>
            <line x1="4" y1="14" x2="44" y2="14" stroke-width=".5"/>
            <line x1="4" y1="34" x2="44" y2="34" stroke-width=".5"/>
          </svg>
        </div>
        <div class="idle-title">AOC ONLINE</div>
        <div class="idle-sub">Awaiting mission orders — no active agents</div>
      </div>`;
    }
    document.getElementById('s-run').textContent='00';
    document.getElementById('s-done').textContent='00';
    const _eEl=document.getElementById('s-errors'); if(_eEl){ _eEl.textContent='—'; _eEl.style.color='var(--t3)'; }
    document.getElementById('s-tasks').textContent='—';
    document.getElementById('s-files').textContent=pad(fcCount);
    return;
  }

  /* ensure agents grid exists — remove idle placeholder without wiping filter bars */
  const _idle=cardsArea.querySelector('.idle');
  if(_idle) _idle.remove();
  if(!document.getElementById('agents')){
    if(!document.getElementById('search-wrap')){
      const _sw=document.createElement('div');
      _sw.id='search-wrap'; _sw.className='search-wrap'; _sw.style.display='none';
      _sw.innerHTML=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg><input class="search-input" id="search-input" type="text" placeholder="Search agents..." oninput="setSearch(this.value)"><button class="search-clear" id="search-clear" onclick="setSearch('')">✕</button>`;
      cardsArea.insertBefore(_sw,cardsArea.firstChild);
    }
    if(!document.getElementById('session-tab-bar')){
      const _stb=document.createElement('div');
      _stb.id='session-tab-bar'; _stb.style.cssText='display:none;gap:4px;padding:0 0 6px;flex-wrap:wrap';
      const _swRef=document.getElementById('search-wrap');
      _swRef?_swRef.after(_stb):cardsArea.insertBefore(_stb,cardsArea.firstChild);
    }
    if(!document.getElementById('status-filter-bar')){
      const _sfb=document.createElement('div');
      _sfb.id='status-filter-bar'; _sfb.style.cssText='display:none;gap:6px;padding:0 0 8px;flex-wrap:wrap';
      const _ref=document.getElementById('session-tab-bar');
      _ref?_ref.after(_sfb):cardsArea.insertBefore(_sfb,cardsArea.firstChild);
    }
    const _ag=document.createElement('div');
    _ag.className='agents'; _ag.id='agents';
    cardsArea.appendChild(_ag);
  }

  const agentsEl=document.getElementById('agents');
  if(!agentsEl) return;

  /* show search bar + session/status filters only in the AGENTS view */
  const sw=document.getElementById('search-wrap');
  if(sw) sw.style.display=(currentView==='agents'&&agents.length>0)?'flex':'none';
  if(currentView==='cli'){
    const stb=document.getElementById('session-tab-bar'); if(stb) stb.style.display='none';
    const sfb=document.getElementById('status-filter-bar'); if(sfb) sfb.style.display='none';
  }

  if(currentView==='agents') renderSessionTabBar(data, agents);
  const sessionAgents = sessionFilter===null ? agents : agents.filter(a=>a.session_id===sessionFilter);
  renderStatusFilterBar(sessionAgents);
  const statusFiltered = statusFilter==='all' ? sessionAgents : sessionAgents.filter(a=>{
    if(statusFilter==='running') return a.status==='running'||a.status==='waiting';
    return a.status===statusFilter;
  });
  const _filtered = searchQuery
    ? statusFiltered.filter(a=>(a.name||'').toLowerCase().includes(searchQuery)||(a.description||'').toLowerCase().includes(searchQuery)||(a.session_project||'').toLowerCase().includes(searchQuery))
    : statusFiltered;
  const _sPri={running:0,waiting:1,error:2,done:3};
  const visibleAgents=[..._filtered].sort((a,b)=>{
    const pa=_pinnedIds.has(a.id)?-1:_sPri[a.status]??4;
    const pb=_pinnedIds.has(b.id)?-1:_sPri[b.status]??4;
    if(pa!==pb) return pa-pb;
    /* within same status: running → newest first; done → oldest first */
    const ta=parseTimeStr(a.started_at)||0, tb=parseTimeStr(b.started_at)||0;
    return a.status==='done' ? ta-tb : tb-ta;
  });

  agentsEl.classList.toggle('list-mode', listMode);
  /* re-applied every render since #agents.innerHTML rebuild below only
     replaces children, not this container — but idle-state resets do
     recreate #agents itself (see "ensure agents grid exists" above),
     so the density class can't just be set once at init/save time. */
  _applyDensity(_density);

  if(listMode){
    agentsEl.innerHTML=visibleAgents.map(a=>{
      const dt=(a.tasks||[]).filter(t=>t.done).length;
      const tt=(a.tasks||[]).length;
      const pct=tt>0?Math.round(dt/tt*100):0;
      if(a.status==='running'||a.status==='waiting') running++;
      if(a.status==='done') done++;
      if(a.status==='error') errors++;
      totalT+=tt; doneT+=dt;
      let elapsedStr='';
      if(a.started_at){
        const [_eh,_em,_es]=(a.started_at||'').split(':').map(Number);
        const startMs=new Date().setHours(_eh||0,_em||0,_es||0,0);
        let endMs=Date.now();
        if(a.status!=='running'&&a.completed_at){
          const [_ch,_cm,_cs]=(a.completed_at||'').split(':').map(Number);
          endMs=new Date().setHours(_ch||0,_cm||0,_cs||0,0);
        }
        const secs=Math.max(0,Math.floor((endMs-startMs)/1000));
        elapsedStr=secs<60?`${secs}s`:`${Math.floor(secs/60)}m ${secs%60}s`;
      }
      const col=sCol(a.status);
      const tokStr=a.tokens_used?`<span style="font-size:9px;font-family:var(--font2);color:rgba(0,210,130,.7);margin-left:4px">$${_agentCost(a).toFixed(4)}</span>`:'';
      const pinClass=_pinnedIds.has(a.id)?'style="border-left-color:rgba(255,200,60,.6)"':'';
      return `<div class="agent-row ${a.status}" ${pinClass}>
        <div class="ar-unit">${_deriveUnit(a)}</div>
        ${a.detected_via==='transcript'?`<span style="color:rgba(255,170,0,.95);font-size:10px;flex-shrink:0" title="${escHtml(_hookMissTitle(a))}">⚠</span>`:''}
        <div class="ar-name" title="${escHtml(a.description||a.name)}">${escHtml(a.name)}</div>
        ${tokStr}
        <div class="ar-pill ${a.status}">${stxt(a)}</div>
        <div class="ar-prog"><div class="ar-prog-fill" style="width:${pct}%;background:${col}"></div></div>
        <div class="ar-elapsed">${elapsedStr||'—'}</div>
        <button class="ar-dismiss" onclick="dismissAgent('${a.id}','${escHtml(a.machine||'')}')" title="Dismiss">×</button>
      </div>`;
    }).join('');
  } else {

  /* detect new agents and newly completed tasks before render */
  const _newThisRender = new Set(visibleAgents.filter(a=>!_knownAgentIds.has(a.id)).map(a=>a.id));
  const _newCompletedKeys = new Set();
  visibleAgents.forEach(a=>{
    (a.tasks||[]).forEach((t,i)=>{
      const key=`${a.id}:${i}`;
      if(t.done && !_completedTaskKeys.has(key)){ _newCompletedKeys.add(key); }
      if(t.done) _completedTaskKeys.add(key);
    });
  });
  /* clean up keys for removed agents */
  const _visibleIdSet=new Set(visibleAgents.map(a=>a.id));
  for(const id of _knownAgentIds){ if(!_visibleIdSet.has(id)){ _knownAgentIds.delete(id); for(const k of _completedTaskKeys){ if(k.startsWith(id+':')) _completedTaskKeys.delete(k); } } }

  /* Progress-bar width/arc don't ease via CSS transition on their own — every
     render fully replaces agentsEl.innerHTML, so a persisting agent's bar
     never has a "before" DOM state to animate from; it just snaps to the new
     value on the freshly-created node. Capture the old values here (before
     they're destroyed) so a FLIP-style patch after the rebuild can force a
     real transition — same trick renderKpi already uses for the cost gauge
     via a direct .style mutation on a persistent node (see cost-gauge-arc). */
  const _progBefore={};
  visibleAgents.forEach(a=>{
    if(_newThisRender.has(a.id)) return;  // no "before" state to ease from
    const old=document.getElementById('card-'+(a._domId||a.id));
    if(!old) return;
    const fill=old.querySelector('.prog-fill');
    const arc=old.querySelector('.arc-wrap circle:last-of-type');
    _progBefore[a._domId||a.id]={
      fillWidth: fill?fill.style.width:null,
      arcDash:   arc?arc.getAttribute('stroke-dasharray'):null,
    };
  });

  agentsEl.innerHTML=visibleAgents.map(a=>{
    const dt=(a.tasks||[]).filter(t=>t.done).length;
    const tt=(a.tasks||[]).length;
    const pct=tt>0?Math.round(dt/tt*100):0;
    if(a.status==='running'||a.status==='waiting') running++;
    if(a.status==='done') done++;
    if(a.status==='error') errors++;
    totalT+=tt; doneT+=dt;
    /* if scheduled for removal, show as still running (no done flash) */
    const isFading=_removingIds.has(a.id);
    const dispStatus=isFading?'running':a.status;
    const col=sCol(dispStatus);
    const {c,d}=arcDash(pct);
    const stLabel=stxt({status:dispStatus});
    /* JS-computed opacity: persists across innerHTML rebuilds (CSS animation resets on re-render) */
    const fadeOpacity = isFading ? Math.max(0, 1 - (Date.now()-(_fadingStartedAt[a.id]||Date.now()))/10000) : 1;

    /* live elapsed / total duration */
    let elapsedStr='';
    if(a.started_at){
      const [_eh,_em,_es]=(a.started_at||'').split(':').map(Number);
      const startMs=new Date().setHours(_eh||0,_em||0,_es||0,0);
      let endMs=Date.now();
      if(a.status!=='running'&&a.completed_at){
        const [_ch,_cm,_cs]=(a.completed_at||'').split(':').map(Number);
        endMs=new Date().setHours(_ch||0,_cm||0,_cs||0,0);
      }
      const secs=Math.max(0,Math.floor((endMs-startMs)/1000));
      elapsedStr=secs<60?`${secs}s`:`${Math.floor(secs/60)}m ${secs%60}s`;
    }

    const tasks=a.tasks.map((t,i)=>{
      const isNewDone=t.done&&_newCompletedKeys.has(`${a.id}:${i}`);
      return `<div class="task ${t.done?'d':'p'}">
        <span class="task-chk${isNewDone?' task-chk-pop':''}">${t.done?'✓':'·'}</span>
        <span class="task-lbl">${escHtml(t.label||'')}</span>
      </div>`;
    }).join('');

    /* token counter row */
    const tokensHtml = a.tokens_used
      ? (() => {
          const pct=_ctxPct(a);
          return `<div style="display:flex;align-items:center;gap:6px;margin-top:6px;padding:4px 8px;border-radius:6px;background:rgba(var(--c-rgb),.05);border:1px solid rgba(var(--c-rgb),.1);">
           <span style="font-size:10px;color:var(--t3);font-family:var(--font2);letter-spacing:.06em">TOKENS</span>
           <span style="font-size:11px;font-family:var(--font2);font-weight:700;color:var(--c)">${Number(a.tokens_used).toLocaleString()}</span>
           ${pct!==null?`<div style="flex:1;height:2px;border-radius:999px;background:rgba(255,255,255,.05);overflow:hidden;"><div style="height:100%;width:${pct}%;background:linear-gradient(90deg,var(--c2),var(--c));border-radius:999px;"></div></div>`:''}
         </div>`;
        })() : '';

    /* task velocity */
    const doneTasks=a.tasks.filter(t=>t.done&&t.ts);
    let velocityHtml='';
    if(doneTasks.length>=2&&a.started_at){
      const firstTs=doneTasks[0].ts, lastTs=doneTasks[doneTasks.length-1].ts;
      const [_vh,_vm,_vs]=(a.started_at||'').split(':').map(Number);
      const spanMin=(Date.now()-new Date().setHours(_vh||0,_vm||0,_vs||0,0))/60000||1;
      const tpm=(doneTasks.length/Math.max(spanMin,0.5)).toFixed(1);
      const remaining=a.tasks.filter(t=>!t.done).length;
      const etaMins=remaining>0?(remaining/parseFloat(tpm)).toFixed(0):0;
      velocityHtml=`<div style="font-size:10px;color:var(--t3);font-family:var(--font2);margin-top:3px;display:flex;gap:8px;">
        <span>⚡ ${tpm} tasks/min</span>${remaining>0&&etaMins>0?`<span>ETA ~${etaMins}m</span>`:''}
      </div>`;
    }

    /* error message drill-down + copy */
    const safeErr=escHtml(a.error_message||'');
    const errorHtml = (a.status==='error' && a.error_message)
      ? `<div style="margin-top:8px;padding:6px 10px;border-radius:8px;background:rgba(255,51,85,.06);border:1px solid rgba(255,51,85,.2);border-left:3px solid var(--r);" data-err="${safeErr}">
           <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
             <div style="font-size:10px;font-family:var(--font2);color:var(--r);letter-spacing:.08em;flex:1">ERROR</div>
             <button onclick="copyText(this,this.closest('[data-err]').dataset.err)" style="font-size:10px;padding:1px 8px;border-radius:4px;border:1px solid rgba(255,51,85,.3);background:rgba(255,51,85,.08);color:rgba(255,120,140,.8);cursor:pointer;font-family:var(--font)">copy</button>
             <button onclick="_copyErrorContext('${a.id}')" title="Copy project + task + error + log tail as one paste-ready block" style="font-size:10px;padding:1px 8px;border-radius:4px;border:1px solid rgba(255,51,85,.3);background:rgba(255,51,85,.08);color:rgba(255,120,140,.8);cursor:pointer;font-family:var(--font)">copy context</button>
           </div>
           <div style="font-size:11px;color:rgba(255,120,140,.85);font-family:var(--font2);line-height:1.5;word-break:break-all">${safeErr}</div>
         </div>` : '';

    /* file changes */
    const filesHtml = (a.files_changed||[]).length
      ? `<div style="display:flex;flex-direction:column;gap:2px;margin-top:6px;">${
          (a.files_changed||[]).map(f=>`<div class="fe ${f.type||'changed'}" onclick="showDiff('${escHtml(f.path.replace(/'/g,"\\'"))}')" style="cursor:pointer;" title="Click to see diff">
            <span class="fe-badge">${(f.type||'MOD').toUpperCase().slice(0,3)}</span>
            <span class="fe-name">${escHtml(f.path.split('/').pop())}</span>
            ${f.lines?`<span class="fe-lines">${f.lines}L</span>`:''}
          </div>`).join('')
        }</div>` : '';

    const agentLogs = (a.log||[]);
    const logHtml = agentLogs.length
      ? `<div class="log-toggle" id="lt-${a.id}" onclick="toggleAgentLog('${a.id}')">
           <svg viewBox="0 0 10 10"><path d="M3 2l4 3-4 3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
           LOGS <span style="margin-left:3px;opacity:.6">(${agentLogs.length})</span>
         </div>
         <div class="log-panel" id="lp-${a.id}">
           ${agentLogs.slice(-30).map(e=>
             `<div class="log-entry">${escHtml(String(e))}</div>`
           ).join('')}
         </div>`
      : '';

    const sessProj=a.session_project||'';
    const sessBadge=sessProj?`<div style="font-size:9px;font-family:var(--font2);color:var(--t3);letter-spacing:.06em;margin-bottom:4px;opacity:.7;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${sessProj}">// ${sessProj}</div>`:'';
    const isCollapsed=_collapsedIds.has(a.id);
    const isPinned=_pinnedIds.has(a.id);
    const isNewCard=_newThisRender.has(a.id);
    const domId=a._domId||a.id;  // machine-namespaced when merged from multiple AOC instances, else just a.id
    const isRemote=a._isLocal===false;
    return `<div id="card-${domId}" class="card ${dispStatus}${isFading?' agent-fading':''}${isCollapsed?' collapsed':''}${isPinned?' pinned':''}${isNewCard?' card-enter':''}" style="${isFading?`opacity:${fadeOpacity.toFixed(3)};`:''}" onclick="${isCollapsed?`toggleCollapse('${a.id}',event)`:''}">
      <button class="card-cmp-btn ${_compareIds.includes(a.id)?'selected':''}" data-id="${a.id}" onclick="toggleCompare('${a.id}',event)" title="${_compareIds.includes(a.id)?'Remove from compare':'Add to compare'}">⊞</button>
      <button class="card-pin-btn" onclick="togglePin('${a.id}',event)" title="${isPinned?'Unpin':'Pin to top'}">${isPinned?'◈':'◉'}</button>
      <button class="card-collapse-btn" onclick="toggleCollapse('${a.id}',event)" title="${isCollapsed?'Expand':'Collapse'}">▲</button>
      <button class="card-dismiss" onclick="dismissAgent('${a.id}','${escHtml(a.machine||'')}')" title="Dismiss">×</button>
      ${dispStatus==='running'?'<div class="sweep"></div>':''}
      ${sessBadge}
      <div class="card-head">
        <div class="unit-badge">${_deriveUnit(a)}</div>
        <div class="card-meta">
          <div class="card-name">${escHtml(a.name)}</div>
          <div class="card-desc">${isCollapsed?'':escHtml(a.description||'')}</div>
        </div>
        <div class="status-pill ${dispStatus}"><span class="sdot"></span>${stLabel}</div>
        ${isRemote?`<span class="hookmiss-badge" style="border-color:var(--c);color:var(--c);background:rgba(var(--c-rgb),.08)" title="From remote machine">⌘ ${escHtml(a.machine)}</span>`:''}
        ${a.detected_via==='transcript'?`<span class="hookmiss-badge" title="${escHtml(_hookMissTitle(a))}">⚠ HOOK MISS</span>`:''}
        ${a.tokens_used?`<span class="cost-badge" data-cost="${a.tokens_used}" id="cb-${domId}">$${_agentCost(a).toFixed(3)}</span>`:''}
        ${isCollapsed?`<span style="font-size:10px;font-family:var(--font2);color:var(--t3);margin-left:6px">${elapsedStr||''}</span>`:`<button class="card-info-btn" onclick="openAgentDetail('${a.id}',event)" title="Agent detail drill-down [click]">⊕ INFO</button>`}
      </div>
      <div class="card-body">
      ${(()=>{ const stuckSecs=_stuckSecs(a); return stuckSecs>300?`<div class="stuck-badge">⚠ STUCK — no progress for ${stuckSecs<3600?Math.floor(stuckSecs/60)+'m':Math.floor(stuckSecs/3600)+'h'}</div>`:''; })()}
      <div class="tasks">${tasks}</div>
      ${errorHtml}
      ${tokensHtml}
      ${velocityHtml}
      <div class="prog-row">
        <div class="arc-wrap">
          <svg width="40" height="40" viewBox="0 0 40 40" style="transform:rotate(-90deg)">
            <circle cx="20" cy="20" r="16" fill="none" stroke="rgba(255,255,255,.05)" stroke-width="2.5"/>
            <circle cx="20" cy="20" r="16" fill="none" stroke="${col}" stroke-width="2.5"
              stroke-dasharray="${d} ${c}" stroke-linecap="round"
              style="transition:stroke-dasharray .6s ease;filter:drop-shadow(0 0 4px ${col})"/>
          </svg>
          <div class="arc-pct">${pct}%</div>
        </div>
        <div class="prog-info">
          <div class="prog-track"><div class="prog-fill" style="width:${pct}%"></div></div>
          <div class="prog-lbl"><span>${dt} / ${tt} tasks</span><span style="font-family:var(--font2);color:${a.status==='running'?'var(--c)':a.status==='done'?'var(--g)':'var(--t3)'}">${elapsedStr||a.status.toUpperCase()}</span>${(a.files_changed||[]).length?`<span onclick="setView('files');event.stopPropagation()" style="font-family:var(--font2);font-size:10px;color:var(--t3);cursor:pointer;padding:1px 6px;border-radius:4px;border:1px solid rgba(255,255,255,.08);transition:color .2s" title="View file changes" onmouseover="this.style.color='var(--c)'" onmouseout="this.style.color='var(--t3)'">✎ ${(a.files_changed||[]).length}</span>`:''}</div>
        </div>
      </div>
      ${logHtml}
      </div>
    </div>`;
  }).join('');

  /* FLIP-patch: snap each surviving card's bar/arc back to its pre-rebuild
     value, force a reflow, then let the next frame ease to the real target —
     this is what actually makes the .prog-fill / arc-wrap circle CSS
     transitions (defined once, unchanged) visibly animate. */
  Object.keys(_progBefore).forEach(id=>{
    const before=_progBefore[id];
    const el=document.getElementById('card-'+id);  // id here is already the domId (see _progBefore's keys above)
    if(!el) return;
    const fill=el.querySelector('.prog-fill');
    if(fill && before.fillWidth!=null && before.fillWidth!==fill.style.width){
      const target=fill.style.width, origT=fill.style.transition;
      fill.style.transition='none';
      fill.style.width=before.fillWidth;
      void fill.offsetWidth;  // force reflow
      fill.style.transition=origT;
      requestAnimationFrame(()=>{ fill.style.width=target; });
    }
    const arc=el.querySelector('.arc-wrap circle:last-of-type');
    if(arc && before.arcDash!=null){
      const target=arc.getAttribute('stroke-dasharray');
      if(target!==before.arcDash){
        const origT=arc.style.transition;
        arc.style.transition='none';
        arc.setAttribute('stroke-dasharray', before.arcDash);
        void arc.getBoundingClientRect();  // force reflow for SVG
        arc.style.transition=origT;
        requestAnimationFrame(()=>{ arc.setAttribute('stroke-dasharray', target); });
      }
    }
  });
  } /* end else (card mode) */

  /* track known agent IDs for next render cycle */
  visibleAgents.forEach(a=>_knownAgentIds.add(a.id));

  /* ── CLI view: always render session cards ── */
  if(currentView==='cli'){
    const slist=(data.sessions_list||[]);
    const ghostCount=Math.max(0,(data.sessions_count||0)-slist.length);
    const ghosts=Array.from({length:ghostCount},()=>`<div class="card" style="opacity:.55">
      <div class="card-head">
        <div class="unit-badge" style="border-color:rgba(var(--c-rgb),.4);color:rgba(var(--c-rgb),.6);font-size:9px">CLI</div>
        <div class="card-meta">
          <div class="card-name">Claude Code</div>
          <div class="card-desc" style="font-size:10px;color:var(--t3)">Running — waiting for first message</div>
        </div>
        <div class="status-pill"><span></span>DETECTED</div>
      </div>
    </div>`);
    const _fmtModel=m=>{
      if(!m) return '';
      const _mn=m.replace(/^claude-/,'');
      const _map={'opus-4-5':'Opus 4.5','opus-4':'Opus 4','sonnet-4-6':'Sonnet 4.6','sonnet-4-5':'Sonnet 4.5','sonnet-4':'Sonnet 4','haiku-4-5':'Haiku 4.5','haiku-4':'Haiku 4','opus-3-5':'Opus 3.5','sonnet-3-5':'Sonnet 3.5','haiku-3-5':'Haiku 3.5','haiku-3':'Haiku 3','opus-3':'Opus 3'};
      if(_map[_mn]) return _map[_mn];
      // generic fallback: "sonnet-4-6" → "Sonnet 4.6"
      return _mn.replace(/-(\d+)-(\d+)$/,' $1.$2').replace(/-(\d+)$/,' $1').replace(/^\w/,c=>c.toUpperCase());
    };
    const _fmtRuntime=(first,last)=>{
      if(!first||!last) return '';
      try{
        const ms=new Date(last)-new Date(first);
        if(isNaN(ms)||ms<=0) return '';
        const s=Math.floor(ms/1000);
        if(s<60) return s+'s';
        const m=Math.floor(s/60), h=Math.floor(m/60);
        if(h>0) return h+'h '+(m%60)+'m';
        return m+'m '+(s%60)+'s';
      }catch(e){ return ''; }
    };
    const _fmtRelTime=ls=>{
      if(!ls) return '';
      // ls is "HH:MM:SS" string from server
      try{
        const [hh,mm,ss]=ls.split(':').map(Number);
        const now=new Date();
        let t=new Date(); t.setHours(hh,mm,ss,0);
        let diff=Math.floor((now-t)/1000);
        if(diff<0) diff+=86400; // next-day wrap
        if(diff<10) return 'just now';
        if(diff<60) return diff+'s ago';
        if(diff<3600) return Math.floor(diff/60)+'m ago';
        return Math.floor(diff/3600)+'h ago';
      }catch(e){ return ls; }
    };
    const cards=slist.map(s=>{
      const isActive=s.session_active!==false;
      const waitingOnYou=isActive&&!!s.waiting_on_you;
      const waitingDurStr=(waitingOnYou&&(s.waiting_secs||0)>=60)?' · '+_fmtDurationDHM(s.waiting_secs):'';
      const cwdBase=s.cwd?(s.cwd.replace(/\\/g,'/').split('/').filter(Boolean).pop()||''):'';
      const label=escHtml(s.display_name||s.project||cwdBase||'CLI Session');
      const sid6=s.id.slice(-6);
      const agentCount=(data.agents||[]).filter(a=>a.session_id===s.id).length;
      const agentBadge=agentCount>0?`<span style="font-size:9px;color:var(--c);margin-left:6px;opacity:.7">${agentCount} agent${agentCount!==1?'s':''}</span>`:'';
      const sDomId=s._domId||s.id;
      const sIsRemote=s._isLocal===false;
      const machineBadge=sIsRemote?`<span style="font-size:9px;color:var(--c);margin-left:6px;opacity:.7">⌘ ${escHtml(s.machine)}</span>`:'';
      // Model / meta badges
      const modelStr=_fmtModel(s.model||'');
      const versionStr=s.cc_version?`v${s.cc_version}`:'';
      const branchStr=s.git_branch?(s.pr_url
        ?`<a href="${escHtml(s.pr_url)}" target="_blank" onclick="event.stopPropagation()" style="color:inherit;text-decoration:underline;text-decoration-style:dotted" title="Open PR on GitHub">⎇ ${escHtml(s.git_branch)}</a>`
        :`⎇ ${escHtml(s.git_branch)}`):'';
      const metaParts=[branchStr,modelStr,versionStr].filter(Boolean);
      const metaLine=metaParts.length?`<div style="font-size:9px;color:var(--t3);font-family:var(--font2);margin-top:2px;letter-spacing:.04em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${metaParts.join(' · ')}</div>`:'';
      // Cost / tokens / runtime / rel-time
      const totalTok=(s.input_tokens||0)+(s.output_tokens||0);
      const costStr=`$${(s.estimated_cost||0).toFixed(4)}`;
      const tokStr=totalTok>0?`${(totalTok/1000).toFixed(1)}k tok`:'';
      const runtimeStr=_fmtRuntime(s.first_ts,s.last_ts);
      const relStr=_fmtRelTime(s.last_seen||'');
      const statsRow=[costStr,tokStr,runtimeStr,relStr].filter(x=>x&&x!='$0.0000').join(' · ');
      /* input/output/cache_read/cache_write_tokens + msg_count are all
         already tracked per session and shipped in /status, but were
         never actually shown anywhere -- cache tokens in particular
         matter for cost (cache reads are far cheaper than fresh input),
         so surface the full breakdown in a hover tooltip rather than
         cluttering the compact stats line itself. */
      const tokTitle=totalTok>0?_tokBreakdownStr(s)+(s.msg_count?' · '+s.msg_count+' msg':''):'';
      const statsLine=statsRow?`<div style="font-size:9px;color:var(--t2);font-family:var(--font2);margin-top:2px;opacity:.8;letter-spacing:.03em"${tokTitle?` title="${escHtml(tokTitle)}"`:''}>${statsRow}</div>`:'';
      const noteLine=s.note?`<div style="font-size:9px;color:var(--t3);font-family:var(--font2);margin-top:2px;opacity:.85;letter-spacing:.02em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${escHtml(s.note)}">📝 ${escHtml(s.note)}</div>`:'';
      const burnRates=isActive?_computeBurnRates(_tokenHistory[s.id]||[], Date.now()/1000):null;
      const burnBadge=(burnRates&&_isBurnSpike(burnRates[0],burnRates[1]))?`<div class="burn-badge">⚡ BURN SPIKE — ${Math.round(burnRates[0])} tok/min</div>`:'';
      const _actHist=_activityHistory[s.id]||[];
      const activitySparkline=_actHist.length<3?'':(()=>{
        const actColors={active:'var(--c)',waiting:'var(--o)',idle:'rgba(255,255,255,.1)'};
        const buckets=_bucketActivity(_actHist, Date.now()/1000);
        const segs=buckets.map(b=>`<div style="flex:1;height:7px;border-radius:1px;background:${b?actColors[b]:'rgba(255,255,255,.04)'}"></div>`).join('');
        return `<div style="display:flex;gap:1px;margin-top:5px" title="Activity over the last 2h (cyan=working, amber=waiting on you, grey=idle)">${segs}</div>`;
      })();
      return `<div class="card ${isActive?'running':''}" style="${isActive?'':'opacity:.4'}">
        <div class="${isActive?'sweep':''}"></div>
        <div class="card-head">
          <div class="unit-badge" style="border-color:var(--c);color:var(--c);font-size:9px">CLI</div>
          <div class="card-meta" style="min-width:0;flex:1">
            <div class="card-name" id="cli-name-${escHtml(sDomId)}" style="display:flex;align-items:center;gap:6px">
              ${label}
              ${agentBadge}
              ${machineBadge}
            </div>
            ${metaLine}
            ${statsLine}
            ${noteLine}
            ${burnBadge}
            ${activitySparkline}
          </div>
          <div class="status-pill ${isActive?(waitingOnYou?'waiting':'running'):''}" ${waitingOnYou?'title="Claude finished its last turn and is waiting for your next message"':''}><span class="${isActive?'sdot':''}"></span>${isActive?(waitingOnYou?'WAITING'+waitingDurStr:'ACTIVE'):'CLOSED'}</div>
          ${!sIsRemote?`<button onclick="event.stopPropagation();openNotesPanel('${escHtml(s.id)}')" title="${s.note?'Edit note':'Add note'}" style="background:none;border:none;color:${s.note?'var(--c)':'var(--t3)'};cursor:pointer;font-size:13px;padding:4px 6px;margin-left:4px;line-height:1;border-radius:4px;transition:color .2s" onmouseover="this.style.color='var(--c)'" onmouseout="this.style.color='${s.note?'var(--c)':'var(--t3)'}'">📝</button>`:''}
          <button onclick="event.stopPropagation();exportSessionDetail('${escHtml(s.id)}')" title="Export session as Markdown" style="background:none;border:none;color:var(--t3);cursor:pointer;font-size:13px;padding:4px 6px;margin-left:4px;line-height:1;border-radius:4px;transition:color .2s" onmouseover="this.style.color='var(--c)'" onmouseout="this.style.color='var(--t3)'">⇩</button>
          <button onclick="toggleSessionCompare('${escHtml(s.id)}',event)" title="${_sessCompareIds.includes(s.id)?'Remove from compare':'Add to compare (pick 2 sessions)'}" style="background:none;border:none;color:${_sessCompareIds.includes(s.id)?'var(--o)':'var(--t3)'};cursor:pointer;font-size:13px;padding:4px 6px;margin-left:4px;line-height:1;border-radius:4px;transition:color .2s" onmouseover="this.style.color='var(--o)'" onmouseout="this.style.color='${_sessCompareIds.includes(s.id)?'var(--o)':'var(--t3)'}'">⊞</button>
          ${isActive&&s.host_pid?`<button onclick="event.stopPropagation();_forceStopSession('${escHtml(s.id)}','${escHtml(s.project||s.cwd||'this session')}','${escHtml(s.machine||'')}')" title="Force stop this CLI session (kills its claude.exe process)" style="background:none;border:none;color:rgba(255,80,100,.7);cursor:pointer;font-size:13px;padding:4px 6px;margin-left:4px;line-height:1;border-radius:4px;transition:color .2s" onmouseover="this.style.color='var(--r)'" onmouseout="this.style.color='rgba(255,80,100,.7)'">⛔</button>`:''}
          ${!isActive&&!sIsRemote?`<button onclick="event.stopPropagation();_copyResumeCmd('${escHtml(s.id)}')" title="Copy resume command to clipboard" style="background:none;border:none;color:var(--t3);cursor:pointer;font-size:13px;padding:4px 6px;margin-left:4px;line-height:1;border-radius:4px;transition:color .2s" onmouseover="this.style.color='var(--c)'" onmouseout="this.style.color='var(--t3)'">⟲</button>`:''}
          ${!isActive?` <button onclick="event.stopPropagation();_dismissSession('${escHtml(s.id)}','${escHtml(s.machine||'')}')" title="Dismiss" style="background:none;border:none;color:var(--t3);cursor:pointer;font-size:13px;padding:4px 6px;margin-left:4px;line-height:1;border-radius:4px;transition:color .2s" onmouseover="this.style.color='var(--r)'" onmouseout="this.style.color='var(--t3)'">✕</button>`:''}
        </div>
      </div>`;
    });
    agentsEl.innerHTML=[...cards,...ghosts].join('');
  } else if(visibleAgents.length===0){
    /* AGENTS view, no agents → empty state */
    const hasAnyCLI=(data.sessions_count||0)>0||data.session_active;
    agentsEl.innerHTML=hasAnyCLI
      ?`<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;padding:40px 20px;color:var(--t3);text-align:center">
          <div style="font-family:var(--font2);font-size:11px;letter-spacing:.1em;opacity:.6">NO ACTIVE AGENTS</div>
          <div style="font-size:11px;opacity:.45">Switch to <button onclick="setView('cli')" style="background:none;border:none;color:var(--c);cursor:pointer;font-size:11px;padding:0;text-decoration:underline">CLI view</button> to see running sessions</div>
        </div>`
      :`<div style="display:flex;align-items:center;justify-content:center;padding:40px 20px;color:var(--t3)">
          <div style="font-family:var(--font2);font-size:11px;letter-spacing:.1em;opacity:.45">NO ACTIVE AGENTS</div>
        </div>`;
  }

  /* flash cost badges where tokens changed */
  requestAnimationFrame(()=>{
    (data.agents||[]).forEach(a=>{
      if(!a.tokens_used) return;
      const prev=_prevTokens[a.id];
      if(prev!==undefined && prev!==a.tokens_used){
        const el=document.getElementById('cb-'+(a._domId||a.id));
        if(el){ el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash'); }
      }
      _prevTokens[a.id]=a.tokens_used;
    });
  });

  function _statSet(id, val){
    const el=document.getElementById(id); if(!el) return;
    const newTxt=String(val);
    if(el.textContent===newTxt) return;
    el.classList.remove('stat-flip'); void el.offsetWidth;
    el.classList.add('stat-flip');
    el.textContent=newTxt;
  }
  _statSet('s-run', pad(running));
  _statSet('s-done', pad(done));
  const errEl=document.getElementById('s-errors');
  if(errEl){ const et=errors>0?pad(errors):'—'; if(errEl.textContent!==et){ errEl.classList.remove('stat-flip'); void errEl.offsetWidth; errEl.classList.add('stat-flip'); errEl.textContent=et; } errEl.style.color=errors>0?'var(--r)':'var(--t3)'; if(errEl.parentNode) errEl.parentNode.style.opacity=errors>0?'1':'.5'; }
  document.title=running>0?`(${running} running) A.O.C`:done>0?`(${done} done) A.O.C`:'A.O.C · idle';
  const _agentsBtn=document.getElementById('vt-agents');
  if(_agentsBtn) _agentsBtn.textContent=running>0?`AGENTS (${running})`:'AGENTS';
  document.getElementById('s-tasks').textContent=`${doneT}/${totalT}`;
  const _fcSet=new Set(); agents.forEach(a=>(a.files_changed||[]).forEach(f=>_fcSet.add(f.path))); fcCount=_fcSet.size;
  document.getElementById('s-files').textContent=pad(fcCount);
  const cliCount=data.sessions_count||0;
  const cliEl=document.getElementById('s-clis');
  if(cliEl) _statSet('s-clis', String(cliCount).padStart(2,'0'));
  const _cliBtn=document.getElementById('vt-cli');
  if(_cliBtn) _cliBtn.textContent=cliCount>0?`CLI (${cliCount})`:'CLI';
  if(currentView==='timeline') renderTimeline(data);
}

/* ── diff to detect changes and log them ── */
function diffStatus(prev,next){
  if(!prev||!prev.agents) return;

  /* detect new session */
  if(prev.started_at && next.started_at && prev.started_at !== next.started_at){
    log('NEW MISSION SESSION STARTED','success');
    return;
  }

  const _prevMap=Object.fromEntries((prev.agents||[]).map(a=>[a.id,a]));
  (next.agents||[]).forEach(a=>{
    const o=_prevMap[a.id];
    if(!o) return;
    const tag=_deriveUnit(a);
    if(o.status!==a.status){
      if(a.status==='done')    log(`MISSION COMPLETE`,'success',tag);
      if(a.status==='running') log(`ACTIVATED`,'info',tag);
      if(a.status==='error')   log(`SYSTEM FAILURE — ${a.error_message||'unknown error'}`,'error',tag);
      if(a.status==='waiting') log(`AWAITING ORDERS`,'warn',tag);
    }
    (a.tasks||[]).forEach((task,j)=>{
      if((o.tasks||[])[j]&&!(o.tasks||[])[j].done&&task.done)
        log(task.label,'info',tag);
    });
    if(a.log&&o.log) a.log.slice(o.log.length).forEach(m=>log(m,'info',tag));
  });
}

/* ── clock ── */
function updateClock(){
  const d=new Date();
  document.getElementById('clock').textContent=`${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  document.getElementById('cdate').textContent=`${d.getFullYear()}.${pad(d.getMonth()+1)}.${pad(d.getDate())}`;
}
setInterval(updateClock,1000); updateClock();

/* ── session elapsed counter ── */
let _sessStartMs = null;
function _updateElapsed(){
  const el=document.getElementById('s-elapsed');
  if(!el) return;
  if(!_sessStartMs){ el.textContent='—'; return; }
  const secs=Math.max(0,Math.floor((Date.now()-_sessStartMs)/1000));
  if(secs<60) el.textContent=secs+'s';
  else if(secs<3600) el.textContent=Math.floor(secs/60)+'m '+(secs%60)+'s';
  else el.textContent=Math.floor(secs/3600)+'h '+Math.floor((secs%3600)/60)+'m';
}
setInterval(_updateElapsed,1000);

/* ── reset session ── */
async function resetSession(){
  if(!confirm('Reset session? Agent history will be cleared from the monitor.')) return;
  try {
    await fetch('/reset', {method:'POST'});
    lastStatus=null;
    logs=[];
    log('SESSION RESET — AWAITING NEW ORDERS','warn');
  } catch(e){
    log('RESET FAILED: '+e.message,'error');
  }
}

let _lastAuditData = null;
let _auditTab = 'log';
function setAuditTab(tab){
  _auditTab = tab;
  ['log','files'].forEach(t=>{
    const el=document.getElementById('audt-'+t);
    if(el){ el.classList.toggle('active', t===tab); el.setAttribute('aria-selected',t===tab); }
  });
  renderAuditLog();
}

/* ── audit log ── */
function eventClass(line){
  const m=line.match(/\|\s+(\w+)\s+\|/);
  return m?m[1]:'';
}
async function renderAuditLog(){
  const listEl=document.getElementById('audit-list');
  const dlBtn=document.getElementById('btn-dl');
  const filesEl=document.getElementById('audit-files');
  const fnameEl=document.getElementById('al-fname');
  const fcEl=document.getElementById('audit-file-changes');

  /* update FILES tab label with count from lastStatus */
  const _auAgents=lastStatus?(lastStatus.agents||[]).filter(a=>!String(a.id||'').startsWith('hook_')):[];
  const _auSeen=new Set(); const _auFiles=[];
  _auAgents.forEach(a=>{
    (a.files_changed||[]).forEach(f=>{
      if(!_auSeen.has(f.path)){ _auSeen.add(f.path); _auFiles.push({...f, agent: a.unit||a.name||String(a.id).slice(-4).toUpperCase()}); }
    });
  });
  const ftEl=document.getElementById('audt-files'); if(ftEl) ftEl.textContent=`FILES (${_auFiles.length})`;

  if(_auditTab==='files'){
    /* FILES tab — show agent file changes */
    listEl.style.display='none'; listEl.innerHTML='';
    filesEl.style.display='none';
    if(dlBtn) dlBtn.style.display='none';
    if(fnameEl) fnameEl.textContent='';
    if(fcEl){
      fcEl.style.display='flex';
      if(!_auFiles.length){
        fcEl.innerHTML='<div style="color:var(--t3);font-family:var(--font2);font-size:11px;text-align:center;padding:50px 0;letter-spacing:.08em">// no file changes this session</div>';
      } else {
        fcEl.innerHTML=_auFiles.map(f=>`
          <div class="fe ${f.type==='new'?'new':'changed'}" onclick="showDiff('${escHtml(f.path.replace(/'/g,"\\'"))}');event.stopPropagation()" style="cursor:pointer;padding:10px 12px;border-radius:8px">
            <span class="fe-badge">${f.type==='new'?'NEW':'MOD'}</span>
            <span class="fe-name" style="flex:1;font-size:12px">${escHtml(f.path)}</span>
            ${f.lines?`<span class="fe-lines">${f.lines}L</span>`:''}
            <span style="font-size:9px;color:var(--t3);font-family:var(--font2);opacity:.6;flex-shrink:0">${escHtml(f.agent)}</span>
          </div>`).join('');
      }
    }
    return;
  }

  /* LOG tab — existing fetch + render */
  if(fcEl){ fcEl.style.display='none'; fcEl.innerHTML=''; }
  listEl.style.display='';
  try{
    const d=await fetch('/auditlog').then(r=>r.json());
    _lastAuditData=d;

    if(d.current){
      /* active session — show lines + download button */
      fnameEl.textContent=d.current;
      const auditLines = d.lines.slice(-20);
      listEl.innerHTML=auditLines.slice().reverse().map(l=>
        `<div class="al ${eventClass(l)}">${l}</div>`
      ).join('');
      dlBtn.href='/logs/'+encodeURIComponent(d.current);
      dlBtn.style.display='inline-flex';
      filesEl.style.display='none';
    } else {
      /* no active session — show log file list */
      fnameEl.textContent='NO ACTIVE SESSION';
      listEl.innerHTML='';
      dlBtn.style.display='none';
      if(d.files && d.files.length>0){
        filesEl.style.display='flex';
        filesEl.innerHTML=d.files.map(f=>
          `<a class="audit-file-link" href="/logs/${encodeURIComponent(f)}" target="_blank">${f}</a>`
        ).join('');
      } else {
        filesEl.style.display='flex';
        filesEl.innerHTML='<span style="font-size:9px;color:var(--t3)">// no logs</span>';
      }
    }
  }catch(e){}
}

/* ── trend chart: thin line + area, hover crosshair/tooltip, single series
   (no legend needed -- the title above it names the series). Values only,
   never color alone: the tooltip always shows the exact date + number. ── */
let _trendChartSeq=0;
function _renderTrendChart(containerEl, points, opts){
  const id='tc'+(_trendChartSeq++);
  const color=opts.color||'var(--c)';
  const fmt=opts.formatValue||(v=>String(v));
  const W=600,H=64,PAD=4;
  if(!points.length){
    containerEl.innerHTML=`<div class="trend-title">${escHtml(opts.label||'')}</div><div style="font-family:var(--font2);font-size:10px;color:var(--t3);padding:10px 0">// no data yet</div>`;
    return;
  }
  const vals=points.map(p=>p.value);
  const maxV=Math.max(...vals,1), minV=Math.min(...vals,0);
  const range=(maxV-minV)||1;
  const n=points.length;
  const xAt=i=>n>1?PAD+(W-PAD*2)*i/(n-1):W/2;
  const yAt=v=>PAD+(H-PAD*2)*(1-(v-minV)/range);
  const coords=points.map((p,i)=>[xAt(i),yAt(p.value)]);
  const linePath=coords.map(([x,y],i)=>`${i===0?'M':'L'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const areaPath=`${linePath} L${coords[n-1][0].toFixed(1)},${H-PAD} L${coords[0][0].toFixed(1)},${H-PAD} Z`;
  containerEl.innerHTML=`
    <div class="trend-title">${escHtml(opts.label||'')}</div>
    <div style="position:relative">
      <svg class="trend-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" id="${id}">
        <defs><linearGradient id="${id}-g" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${color}" stop-opacity="0.28"/>
          <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
        </linearGradient></defs>
        <path d="${areaPath}" fill="url(#${id}-g)" stroke="none"/>
        <path d="${linePath}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <circle cx="${coords[0][0]}" cy="${coords[0][1]}" r="2.5" fill="${color}"/>
        <circle cx="${coords[n-1][0]}" cy="${coords[n-1][1]}" r="2.5" fill="${color}"/>
        <line class="trend-hover-line" id="${id}-hl" x1="0" y1="0" x2="0" y2="${H}" style="display:none"/>
        <circle class="trend-hover-dot" id="${id}-hd" r="3.5" fill="${color}" stroke="var(--bg)" stroke-width="1.5" style="display:none"/>
        <rect x="0" y="0" width="${W}" height="${H}" fill="transparent" id="${id}-hit"/>
      </svg>
      <div class="trend-tip" id="${id}-tip"></div>
    </div>`;
  const svg=document.getElementById(id), hit=document.getElementById(id+'-hit');
  const hl=document.getElementById(id+'-hl'), hd=document.getElementById(id+'-hd'), tip=document.getElementById(id+'-tip');
  function show(i){
    const [x,y]=coords[i];
    hl.setAttribute('x1',x); hl.setAttribute('x2',x); hl.style.display='';
    hd.setAttribute('cx',x); hd.setAttribute('cy',y); hd.style.display='';
    tip.style.display='block';
    tip.style.left=(x/W*100)+'%'; tip.style.top=(y/H*100)+'%';
    tip.textContent=`${points[i].date} — ${fmt(points[i].value)}`;
  }
  function hide(){ hl.style.display='none'; hd.style.display='none'; tip.style.display='none'; }
  hit.addEventListener('mousemove',e=>{
    const rect=svg.getBoundingClientRect();
    const relX=(e.clientX-rect.left)/rect.width*W;
    let best=0,bestD=Infinity;
    coords.forEach(([x],i)=>{ const d=Math.abs(x-relX); if(d<bestD){bestD=d;best=i;} });
    show(best);
  });
  hit.addEventListener('mouseleave',hide);
}

/* Simple run-rate cost forecast: (cost so far this month / days elapsed)
   x days remaining, added to cost so far -- pure arithmetic over
   analytics' by_day array (last 30 days), no new query. Shared by
   renderHistory's analytics view and the Cost/Budget settings pane so
   this projection is computed in exactly one place. */
/* Auto-generated callouts for the top of History -> Analytics -- every
   number here already exists elsewhere on the page (retry_patterns,
   common_errors, hook_misses/total agents), but finding the single most
   important one meant scrolling through every section in turn. Pure and
   deterministic (no randomness, no LLM call) so the same data always
   produces the same callouts -- a fixed, small rule set, not an
   open-ended "summarize this" prompt. */
function _buildInsights({totalAgents, hookMisses, commonErrors, retryPatterns}){
  const out=[];
  if(retryPatterns && retryPatterns.length){
    const r=retryPatterns[0];
    out.push({icon:'⚠',text:`"${r.name}" has been retried ${r.retries}× across ${r.sessions} session${r.sessions!==1?'s':''}`});
  }
  if(commonErrors && commonErrors.length && commonErrors[0].occurrences>=3){
    const e=commonErrors[0];
    out.push({icon:'🔴',text:`"${(e.error_msg||'').slice(0,80)}" has recurred ${e.occurrences} times`});
  }
  if(totalAgents>0 && hookMisses>0){
    const pct=Math.round(hookMisses/totalAgents*100);
    if(pct>=10) out.push({icon:'⚠',text:`${pct}% of agent starts were only caught by the transcript fallback, not Claude Code's own hook`});
  }
  return out;
}
function _computeMonthProjection(byDay){
  const now=new Date();
  const monthPrefix=now.toISOString().slice(0,7);
  const costSoFar=(byDay||[]).filter(r=>(r.date||'').startsWith(monthPrefix)).reduce((s,r)=>s+(r.cost||0),0);
  const daysElapsed=now.getDate();
  const daysInMonth=new Date(now.getFullYear(),now.getMonth()+1,0).getDate();
  const daysRemaining=daysInMonth-daysElapsed;
  const avgDaily=daysElapsed>0?costSoFar/daysElapsed:0;
  const projectedMonthEnd=costSoFar+avgDaily*daysRemaining;
  return {costSoFar,projectedMonthEnd};
}
/* Per-project counterpart to the global "projected month-end spend" line
   above -- project_budgets (Settings -> COST) was write-only until now,
   a JSON textarea with no feedback about where each listed project
   actually stands against its own number. by_day_project already has
   every project's daily cost with no cap, so each configured project's
   own rows just get run through the exact same _computeMonthProjection
   math, one project at a time. */
function _perProjectBudgetSummary(projectBudgets,byDayProject){
  return Object.entries(projectBudgets||{}).map(([project,budget])=>{
    const rows=(byDayProject||[]).filter(r=>r.project===project);
    const {costSoFar,projectedMonthEnd}=_computeMonthProjection(rows);
    return {project, budget:+budget||0, costSoFar, projectedMonthEnd,
             pctUsed: budget>0?costSoFar/budget*100:0};
  });
}
function _renderProjectBudgetSummary(byDayProject){
  const el=document.getElementById('st-project-budget-summary');
  if(!el) return;
  const rows=_perProjectBudgetSummary(_projectBudgets, byDayProject||[]);
  const fmt=c=>'$'+(+c).toFixed(2);
  el.innerHTML=rows.map(r=>{
    const over=r.budget>0&&r.projectedMonthEnd>r.budget;
    const near=!over&&r.pctUsed>=80;
    const color=over?'var(--r)':near?'rgba(255,170,0,.9)':'var(--t3)';
    return `<div style="display:flex;justify-content:space-between;gap:8px"><span style="color:var(--t2)">${escHtml(r.project)}</span><span style="color:${color}">${fmt(r.costSoFar)} / ${fmt(r.budget)} — projected ${fmt(r.projectedMonthEnd)}</span></div>`;
  }).join('');
}

/* Formats /analytics' hook_miss_concurrency (already computed server-side
   by _db_analytics, previously only ever shown buried inside HISTORY →
   ANALYTICS) into one short line for the INFRA settings tab -- surfaces
   whether the PreToolUse hook drops more often under concurrent-session
   load without needing to open that tab. Pure/testable: takes the object
   as-is, no DOM/fetch inside. */
function _formatHookReliability(hmc){
  hmc = hmc || {};
  const miss = hmc.hook_miss, normal = hmc.normal;
  if(!miss && !normal) return 'No data yet.';
  const missN = miss ? miss.n : 0, normalN = normal ? normal.n : 0;
  const total = missN + normalN;
  if(!total) return 'No data yet.';
  const missPct = (missN/total*100).toFixed(1);
  const parts = [`${missPct}% hook misses (${missN}/${total})`];
  if(miss && normal && miss.avg_concurrent_sessions != null && normal.avg_concurrent_sessions != null){
    parts.push(`avg concurrency: ${miss.avg_concurrent_sessions} (miss) vs ${normal.avg_concurrent_sessions} (normal)`);
  }
  return parts.join(' · ');
}

/* ── history view ── */
let _histTab='sessions'; // 'sessions' | 'analytics'
let _selectedProjectTrend=''; // project name whose own cost trend is pinned open in Analytics, '' = none
function _selectProjectTrend(project){
  _selectedProjectTrend=(_selectedProjectTrend===project)?'':project; // clicking the same project again closes it
  renderHistory();
}
let _selectedModelTrend=''; // same idea as _selectedProjectTrend, one dimension over (model instead of project)
function _selectModelTrend(model){
  _selectedModelTrend=(_selectedModelTrend===model)?'':model;
  renderHistory();
}
let _selectedSubagentTypeTrend=''; // same idea, one more dimension over (subagent_type instead of model)
function _selectSubagentTypeTrend(subagentType){
  _selectedSubagentTypeTrend=(_selectedSubagentTypeTrend===subagentType)?'':subagentType;
  renderHistory();
}
let _selectedFileTrend=''; // same idea, one more dimension over (file path instead of model) -- only ever the top-20 file_hotspots paths, see by_day_file_hotspots' own backend comment
function _selectFileTrend(path){
  _selectedFileTrend=(_selectedFileTrend===path)?'':path;
  renderHistory();
}
async function renderHistory(){
  _histLastRender=Date.now();
  const el=document.getElementById('history-area');
  if(!el) return;
  const fmtCost=_fmtCost;
  const fmtTok=t=>t?Number(t).toLocaleString():'—';
  const fmtDur=_fmtDurShort;

  el.innerHTML=`<div style="max-width:900px;width:100%;margin:0 auto;display:flex;flex-direction:column;height:100%;min-height:0">
    <div class="hist-toolbar">
      <span class="hist-title">SESSION HISTORY</span>
      <div class="adp-tabs" style="border:none;margin-left:8px">
        <div class="adp-tab ${_histTab==='sessions'?'active':''}" onclick="_histTab='sessions';renderHistory()">SESSIONS</div>
        <div class="adp-tab ${_histTab==='analytics'?'active':''}" onclick="_histTab='analytics';renderHistory()">ANALYTICS</div>
      </div>
      ${_histTab==='sessions'?`<input type="text" id="hist-search-input" placeholder="Filter by project, id, or tag..." value="${escHtml(_histSearchQuery)}" oninput="setHistSearch(this.value)" style="font-family:var(--font2);font-size:11px;padding:4px 10px;border-radius:7px;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.03);color:var(--t);margin-left:10px;width:150px">
      <input type="date" id="hist-search-from" value="${escHtml(_histSearchFrom)}" oninput="setHistDateFrom(this.value)" title="From date" style="font-family:var(--font2);font-size:11px;padding:4px 8px;border-radius:7px;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.03);color:var(--t);margin-left:6px">
      <input type="date" id="hist-search-to" value="${escHtml(_histSearchTo)}" oninput="setHistDateTo(this.value)" title="To date" style="font-family:var(--font2);font-size:11px;padding:4px 8px;border-radius:7px;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.03);color:var(--t);margin-left:6px">`:''}
      ${_histTab==='analytics'?(()=>{
        /* href, not fetch() -- so the token has to ride in the query string
           the same way the tunnel URL / WebSocket URL already do (see
           window._AOC_TOKEN usages above), or this 401s the moment it's
           opened through a cloudflared tunnel instead of localhost */
        const tok=window._AOC_TOKEN?encodeURIComponent(window._AOC_TOKEN):'';
        const monthFrom=new Date().toISOString().slice(0,8)+'01';
        const today=new Date().toISOString().slice(0,10);
        const q=(extra)=>tok?extra+(extra.includes('?')?'&':'?')+'token='+tok:extra;
        if(!window._AOC_IS_PRO){
          return `<span style="font-family:var(--font2);font-size:9px;color:var(--t3);margin-left:10px" title="CSV export is a Pro feature -- Settings -> LICENSE to upgrade">🔒 CSV export (PRO)</span>`;
        }
        return `
        <a href="${q(`/export_costs.csv?from=${monthFrom}&to=${today}`)}" class="adp-btn" style="text-decoration:none;font-size:9px;padding:2px 10px;margin-left:10px" title="Download this month's cost-by-project report as CSV">⬇ THIS MONTH</a>
        <a href="${q('/export_costs.csv')}" class="adp-btn" style="text-decoration:none;font-size:9px;padding:2px 10px;margin-left:6px" title="Download all-time cost-by-project report as CSV">⬇ ALL TIME</a>
        <span style="font-family:var(--font2);font-size:9px;color:var(--t3);margin-left:10px">BY MODEL</span>
        <a href="${q(`/export_costs_by_model.csv?from=${monthFrom}&to=${today}`)}" class="adp-btn" style="text-decoration:none;font-size:9px;padding:2px 10px;margin-left:6px" title="Download this month's cost-by-model report as CSV">⬇ THIS MONTH</a>
        <a href="${q('/export_costs_by_model.csv')}" class="adp-btn" style="text-decoration:none;font-size:9px;padding:2px 10px;margin-left:6px" title="Download all-time cost-by-model report as CSV">⬇ ALL TIME</a>
        <span style="font-family:var(--font2);font-size:9px;color:var(--t3);margin-left:10px">BY AGENT TYPE</span>
        <a href="${q(`/export_costs_by_subagent_type.csv?from=${monthFrom}&to=${today}`)}" class="adp-btn" style="text-decoration:none;font-size:9px;padding:2px 10px;margin-left:6px" title="Download this month's cost-by-agent-type report as CSV">⬇ THIS MONTH</a>
        <a href="${q('/export_costs_by_subagent_type.csv')}" class="adp-btn" style="text-decoration:none;font-size:9px;padding:2px 10px;margin-left:6px" title="Download all-time cost-by-agent-type report as CSV">⬇ ALL TIME</a>`;
      })():''}
      <div style="flex:1"></div>
      ${lastStatus&&lastStatus.last_autosave?`<span style="font-family:var(--font2);font-size:9px;color:var(--t3);letter-spacing:.06em" title="Auto-saved every 5 min">⟳ ${(()=>{const d=Math.round((Date.now()/1000-lastStatus.last_autosave)/60);return d<1?'just now':d+'m ago';})()}</span>`:''}
      <button class="adp-btn" onclick="fetch('/history/save_current',{method:'GET'}).then(()=>renderHistory())" style="font-size:9px;padding:2px 10px">⊕ SAVE NOW</button>
    </div>
    <div class="hist-body" id="hist-body"><div style="text-align:center;padding:40px;color:var(--t3);font-family:var(--font2);font-size:10px;letter-spacing:.1em">Loading...</div></div>
  </div>`;

  const body=document.getElementById('hist-body');
  if(_histTab==='analytics'){
    try{
      const localAnalytics=await fetch('/analytics').then(r=>r.json());
      const remoteAnalytics=await _fetchRemoteJSON('/analytics');
      const d=_mergeAnalytics(localAnalytics,remoteAnalytics);
      const t=d.total||{};
      const byDay=d.by_day||[];
      const byProj=d.by_project||[];
      const byDayProj=d.by_day_project||[];
      const byModel=d.by_model||[];
      const byDayModel=d.by_day_model||[];
      const bySubagentType=d.by_subagent_type||[];
      const byTag=d.by_tag||[];
      const byDaySubagentType=d.by_day_subagent_type||[];
      const byDayHook=d.by_day_hook_reliability||[];
      const fileHotspots=d.file_hotspots||[];
      const byDayFileHotspots=d.by_day_file_hotspots||[];
      const byFileType=d.by_file_type||[];
      const tagCloud=d.tag_cloud||[];
      const slowestAgents=d.slowest_agents||[];
      const commonErrors=d.common_errors||[];
      const hookMisses=d.hook_misses||0;
      const retryCount=d.retry_count||0;
      const retryPatterns=d.retry_patterns||[];
      const hmc=d.hook_miss_concurrency||{};
      const maxProjCost=byProj.reduce((m,r)=>Math.max(m,r.cost||0),0)||1;
      const maxModelCost=byModel.reduce((m,r)=>Math.max(m,r.cost||0),0)||1;
      const maxSubagentTypeCost=bySubagentType.reduce((m,r)=>Math.max(m,r.cost||0),0)||1;
      const maxTagCost=byTag.reduce((m,r)=>Math.max(m,r.cost||0),0)||1;
      const maxFileChanges=fileHotspots.reduce((m,r)=>Math.max(m,r.changes||0),0)||1;
      const maxFileTypeChanges=byFileType.reduce((m,r)=>Math.max(m,r.changes||0),0)||1;
      const maxTagCount=tagCloud.reduce((m,r)=>Math.max(m,r.count||0),0)||1;
      const maxAgentDuration=slowestAgents.reduce((m,r)=>Math.max(m,r.duration_s||0),0)||1;
      const maxErrorOccurrences=commonErrors.reduce((m,r)=>Math.max(m,r.occurrences||0),0)||1;
      const maxRetries=retryPatterns.reduce((m,r)=>Math.max(m,r.retries||0),0)||1;
      const {costSoFar:_costSoFar,projectedMonthEnd:_projectedMonthEnd}=_computeMonthProjection(byDay);
      body.innerHTML=`
        <div class="hist-analytics">
          <div class="hist-kpi"><div class="hk-val">${t.sessions||0}</div><div class="hk-lbl">Sessions</div></div>
          <div class="hist-kpi"><div class="hk-val" style="color:rgba(0,232,135,.9)">${fmtCost(t.cost)}</div><div class="hk-lbl">Total cost</div></div>
          <div class="hist-kpi"><div class="hk-val">${fmtTok(t.tokens)}</div><div class="hk-lbl">Total tokens</div></div>
          <div class="hist-kpi" title="${t.done||0} completed successfully"><div class="hk-val">${t.agents||0}</div><div class="hk-lbl">Agents run</div></div>
          <div class="hist-kpi"><div class="hk-val" style="color:${t.errors>0?'var(--r)':'var(--t3)'}">${t.errors||0}</div><div class="hk-lbl">Errors</div></div>
          <div class="hist-kpi"><div class="hk-val">${t.files||0}</div><div class="hk-lbl">Files changed</div></div>
          <div class="hist-kpi" title="Agents whose start was only ever caught by the transcript fallback -- Claude Code's own PreToolUse hook never fired for them"><div class="hk-val" style="color:${hookMisses>0?'rgba(255,170,0,.95)':'var(--t3)'}">${hookMisses}</div><div class="hk-lbl">Hook misses</div></div>
          <div class="hist-kpi" title="An agent errored, then a same-named agent ran again in that same session -- exact-name match, same session only"><div class="hk-val" style="color:${retryCount>0?'rgba(255,170,0,.95)':'var(--t3)'}">${retryCount}</div><div class="hk-lbl">Retries</div></div>
        </div>
        ${_buildInsights({totalAgents:t.agents||0, hookMisses, commonErrors, retryPatterns}).map(i=>`
        <div style="font-family:var(--font2);font-size:10px;color:var(--t2);padding:4px 8px;margin-bottom:4px;border-radius:7px;background:rgba(255,170,0,.06);border:1px solid rgba(255,170,0,.18)">${i.icon} ${escHtml(i.text)}</div>`).join('')}
        ${hmc.hook_miss&&hmc.normal?`
        <div style="font-family:var(--font2);font-size:10px;color:var(--t3);padding:2px 2px 8px" title="Tests whether the hook drops PreToolUse more often when multiple CLI sessions are active at once">
          Avg concurrent sessions when an agent started: <span style="color:rgba(255,170,0,.9)">${hmc.hook_miss.avg_concurrent_sessions} (hook miss, n=${hmc.hook_miss.n})</span> vs <span style="color:var(--t2)">${hmc.normal.avg_concurrent_sessions} (normal, n=${hmc.normal.n})</span>
        </div>`:''}
        ${_costSoFar>0?`
        <div style="font-family:var(--font2);font-size:10px;color:var(--t3);padding:2px 2px 8px" title="Simple run-rate projection: (cost so far this month / days elapsed) x days remaining, added to cost so far">
          This month so far: <span style="color:var(--t2)">${fmtCost(_costSoFar)}</span> — projected month-end: <span style="color:rgba(255,170,0,.9)">${fmtCost(_projectedMonthEnd)}</span>
        </div>`:''}
        ${byDay.length?`
        <div class="trend-wrap" id="trend-tokens"></div>
        <div class="trend-wrap" id="trend-cost"></div>
        <div class="trend-wrap" id="trend-errors"></div>
        <div class="trend-wrap" id="trend-duration"></div>
        <div class="trend-wrap" id="trend-waiting"></div>`:''}
        ${byDayHook.length?`
        <div class="trend-wrap" id="trend-hook-reliability"></div>`:''}
        ${byDay.length?`
        <div class="trend-wrap" id="trend-task-completion"></div>`:''}
        ${byProj.length?`
        <div class="hist-section">COST BY PROJECT <span style="font-weight:400;color:var(--t3);font-size:8px;letter-spacing:0;text-transform:none">(click a project for its own trend)</span></div>
        <div class="hist-bar-wrap">${byProj.map(r=>`
          <div class="hist-bar-row" onclick="_selectProjectTrend('${(r.project||'').replace(/'/g,"\\'")}')" style="cursor:pointer;${_selectedProjectTrend===r.project?'background:rgba(var(--c-rgb),.08);border-radius:6px':''}">
            <span class="hist-bar-lbl" style="max-width:110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r.project||'')}">${escHtml(r.project||'—')}</span>
            <div class="hist-bar-track"><div class="hist-bar-fill" style="width:${((r.cost||0)/maxProjCost*100).toFixed(1)}%;background:rgba(0,232,135,.65)"></div></div>
            <span class="hist-bar-val">${fmtCost(r.cost)}</span>
            <span style="font-family:var(--font2);font-size:9px;color:var(--t3);min-width:64px;text-align:right" title="${r.done||0} done, ${r.errors||0} errors${_taskCompletionPct(r)!=null?` · ${_taskCompletionPct(r)}% tasks done`:''}${r.waiting_on_you_s?` · Claude waited on you ${fmtDur(r.waiting_on_you_s)} total`:''}">${r.sessions}sess / ${r.agents}ag${r.errors?` · <span style="color:var(--r)">${r.errors}err</span>`:''}</span>
          </div>`).join('')}</div>
        ${_selectedProjectTrend&&byDayProj.some(r=>r.project===_selectedProjectTrend)?`
        <div class="trend-wrap" id="trend-selected-proj"></div>
        <div class="trend-wrap" id="trend-selected-proj-tokens"></div>`:''}`:''}
        ${byModel.length?`
        <div class="hist-section">COST BY MODEL <span style="font-weight:400;color:var(--t3);font-size:8px;letter-spacing:0;text-transform:none">(click a model for its own trend)</span></div>
        <div class="hist-bar-wrap">${byModel.map(r=>`
          <div class="hist-bar-row" onclick="_selectModelTrend('${(r.model||'').replace(/'/g,"\\'")}')" style="cursor:pointer;${_selectedModelTrend===r.model?'background:rgba(var(--c-rgb),.08);border-radius:6px':''}">
            <span class="hist-bar-lbl" style="max-width:110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r.model||'')}">${escHtml(r.model||'—')}</span>
            <div class="hist-bar-track"><div class="hist-bar-fill" style="width:${((r.cost||0)/maxModelCost*100).toFixed(1)}%;background:rgba(0,232,135,.65)"></div></div>
            <span class="hist-bar-val">${fmtCost(r.cost)}</span>
            <span style="font-family:var(--font2);font-size:9px;color:var(--t3);min-width:88px;text-align:right" title="${r.done||0} done, ${r.errors||0} errors${_tokPerCall(r)!=null?` · ${_tokPerCall(r).toLocaleString()} tok/call`:''}${r.avg_duration_s!=null?` · avg ${fmtDur(r.avg_duration_s)}`:''}">${r.agents}ag / ${fmtTok(r.tokens)}${_computeSuccessRate(r.agents,r.done)!=null?` · ${_computeSuccessRate(r.agents,r.done)}%`:''}${r.errors?` · <span style="color:var(--r)">${r.errors}err</span>`:''}</span>
          </div>`).join('')}</div>
        ${_selectedModelTrend&&byDayModel.some(r=>r.model===_selectedModelTrend)?`
        <div class="trend-wrap" id="trend-selected-model"></div>
        <div class="trend-wrap" id="trend-selected-model-tokens"></div>`:''}`:''}
        ${bySubagentType.length?`
        <div class="hist-section">COST BY AGENT TYPE <span style="font-weight:400;color:var(--t3);font-size:8px;letter-spacing:0;text-transform:none">(click a type for its own trend)</span></div>
        <div class="hist-bar-wrap">${bySubagentType.map(r=>`
          <div class="hist-bar-row" onclick="_selectSubagentTypeTrend('${(r.subagent_type||'').replace(/'/g,"\\'")}')" style="cursor:pointer;${_selectedSubagentTypeTrend===r.subagent_type?'background:rgba(var(--c-rgb),.08);border-radius:6px':''}">
            <span class="hist-bar-lbl" style="max-width:110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r.subagent_type||'')}">${escHtml(r.subagent_type||'—')}</span>
            <div class="hist-bar-track"><div class="hist-bar-fill" style="width:${((r.cost||0)/maxSubagentTypeCost*100).toFixed(1)}%;background:rgba(0,232,135,.65)"></div></div>
            <span class="hist-bar-val">${fmtCost(r.cost)}</span>
            <span style="font-family:var(--font2);font-size:9px;color:var(--t3);min-width:64px;text-align:right" title="${r.done||0} done, ${r.errors||0} errors${_tokPerCall(r)!=null?` · ${_tokPerCall(r).toLocaleString()} tok/call`:''}">${r.agents}ag / ${fmtTok(r.tokens)}${r.errors?` · <span style="color:var(--r)">${r.errors}err</span>`:''}</span>
          </div>`).join('')}</div>
        ${_selectedSubagentTypeTrend&&byDaySubagentType.some(r=>r.subagent_type===_selectedSubagentTypeTrend)?`
        <div class="trend-wrap" id="trend-selected-subagent-type"></div>
        <div class="trend-wrap" id="trend-selected-subagent-type-tokens"></div>`:''}`:''}
        ${byTag.length?`
        <div class="hist-section">COST BY TAG <span style="font-weight:400;color:var(--t3);font-size:8px;letter-spacing:0;text-transform:none">(a session with multiple tags counts toward each)</span></div>
        <div class="hist-bar-wrap">${byTag.map(r=>`
          <div class="hist-bar-row">
            <span class="hist-bar-lbl" style="max-width:110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r.tag||'')}">${escHtml(r.tag||'')}</span>
            <div class="hist-bar-track"><div class="hist-bar-fill" style="width:${((r.cost||0)/maxTagCost*100).toFixed(1)}%;background:rgba(0,232,135,.65)"></div></div>
            <span class="hist-bar-val">${fmtCost(r.cost)}</span>
            <span style="font-family:var(--font2);font-size:9px;color:var(--t3);min-width:64px;text-align:right" title="${r.done||0} done, ${r.errors||0} errors">${r.sessions}sess / ${r.agents}ag${r.errors?` · <span style="color:var(--r)">${r.errors}err</span>`:''}</span>
          </div>`).join('')}</div>`:''}
        ${fileHotspots.length?`
        <div class="hist-section">TOP FILES <span style="font-weight:400;color:var(--t3);font-size:8px;letter-spacing:0;text-transform:none">(click a file for its trend, ⇄ for its diff)</span></div>
        <div class="hist-bar-wrap">${fileHotspots.map(r=>`
          <div class="hist-bar-row" onclick="_selectFileTrend('${(r.path||'').replace(/'/g,"\\'")}')" style="cursor:pointer;${_selectedFileTrend===r.path?'background:rgba(var(--c-rgb),.08);border-radius:6px':''}">
            <span class="hist-bar-lbl" style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;direction:rtl;text-align:left" title="${escHtml(r.path||'')}">${escHtml(r.path||'—')}</span>
            <div class="hist-bar-track"><div class="hist-bar-fill" style="width:${((r.changes||0)/maxFileChanges*100).toFixed(1)}%;background:rgba(0,232,135,.65)"></div></div>
            <span class="hist-bar-val">${r.changes} change${r.changes!==1?'s':''}</span>
            <span style="font-family:var(--font2);font-size:9px;color:var(--t3);min-width:88px;text-align:right" title="${r.sessions} distinct session${r.sessions!==1?'s':''}">${r.sessions}sess · ${r.total_lines||0}L</span>
            <span onclick="showDiff('${(r.path||'').replace(/'/g,"\\'")}');event.stopPropagation()" title="View diff" style="cursor:pointer;padding:0 2px;opacity:.6">⇄</span>
          </div>`).join('')}</div>
        ${_selectedFileTrend&&byDayFileHotspots.some(r=>r.path===_selectedFileTrend)?`
        <div class="trend-wrap" id="trend-selected-file"></div>`:''}`:''}
        ${byFileType.length?`
        <div class="hist-section">FILE TYPES</div>
        <div class="hist-bar-wrap">${byFileType.map(r=>`
          <div class="hist-bar-row">
            <span class="hist-bar-lbl" style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r.ext||'')}">${r.ext==='(no extension)'?escHtml(r.ext):'.'+escHtml(r.ext||'')}</span>
            <div class="hist-bar-track"><div class="hist-bar-fill" style="width:${((r.changes||0)/maxFileTypeChanges*100).toFixed(1)}%;background:rgba(0,232,135,.65)"></div></div>
            <span class="hist-bar-val">${r.changes} change${r.changes!==1?'s':''}</span>
            <span style="font-family:var(--font2);font-size:9px;color:var(--t3);min-width:88px;text-align:right" title="${r.sessions} distinct session${r.sessions!==1?'s':''}">${r.sessions}sess · ${r.total_lines||0}L</span>
          </div>`).join('')}</div>`:''}
        ${tagCloud.length?`
        <div class="hist-section">TAGS <span style="font-weight:400;color:var(--t3);font-size:8px;letter-spacing:0;text-transform:none">(click a tag to filter Sessions by it)</span></div>
        <div class="hist-bar-wrap">${tagCloud.map(r=>`
          <div class="hist-bar-row" onclick="_selectTagFilter('${(r.tag||'').replace(/'/g,"\\'")}')" style="cursor:pointer">
            <span class="hist-bar-lbl" style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r.tag||'')}">${escHtml(r.tag||'')}</span>
            <div class="hist-bar-track"><div class="hist-bar-fill" style="width:${((r.count||0)/maxTagCount*100).toFixed(1)}%;background:rgba(0,232,135,.65)"></div></div>
            <span class="hist-bar-val">${r.count} session${r.count!==1?'s':''}</span>
          </div>`).join('')}</div>`:''}
        ${slowestAgents.length?`
        <div class="hist-section">SLOWEST AGENTS <span style="font-weight:400;color:var(--t3);font-size:8px;letter-spacing:0;text-transform:none">(click to open its session)</span></div>
        <div class="hist-bar-wrap">${slowestAgents.map(r=>`
          <div class="hist-bar-row" ${r._isLocal!==false
            ?`onclick="_jumpToSessionHistory('${(r.session_id||'').replace(/'/g,"\\'")}')" style="cursor:pointer"`
            :`style="cursor:default"`}>
            <span class="hist-bar-lbl" style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r.name||r.agent_id||'')}">${escHtml(r.name||r.agent_id||'—')}</span>
            <div class="hist-bar-track"><div class="hist-bar-fill" style="width:${((r.duration_s||0)/maxAgentDuration*100).toFixed(1)}%;background:${r.status==='error'?'rgba(255,51,85,.6)':'rgba(0,232,135,.65)'}"></div></div>
            <span class="hist-bar-val">${fmtDur(r.duration_s)}</span>
            <span style="font-family:var(--font2);font-size:9px;color:var(--t3);min-width:64px;text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r.project||'')}${r.machine?' · '+escHtml(r.machine):''}">${r.subagent_type?escHtml(r.subagent_type)+' · ':''}${escHtml(r.project||'—')}${r.status==='error'?' · <span style="color:var(--r)">err</span>':''}</span>
          </div>`).join('')}</div>`:''}
        ${commonErrors.length?`
        <div class="hist-section">COMMON ERRORS <span style="font-weight:400;color:var(--t3);font-size:8px;letter-spacing:0;text-transform:none">(exact-text matches; click to open the most recent occurrence)</span></div>
        <div class="hist-bar-wrap">${commonErrors.map(r=>`
          <div class="hist-bar-row" ${r.last_session_id
            ?`onclick="_jumpToSessionHistory('${(r.last_session_id||'').replace(/'/g,"\\'")}')" style="cursor:pointer"`
            :`style="cursor:default"`}>
            <span class="hist-bar-lbl" style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r.error_msg||'')}">${escHtml((r.error_msg||'').slice(0,80))}</span>
            <div class="hist-bar-track"><div class="hist-bar-fill" style="width:${((r.occurrences||0)/maxErrorOccurrences*100).toFixed(1)}%;background:rgba(255,51,85,.55)"></div></div>
            <span class="hist-bar-val">${r.occurrences}×</span>
            <span style="font-family:var(--font2);font-size:9px;color:var(--t3);min-width:64px;text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r.project||'')}">${escHtml(r.project||'—')}</span>
          </div>`).join('')}</div>`:''}
        ${retryPatterns.length?`
        <div class="hist-section">RETRIED TASKS <span style="font-weight:400;color:var(--t3);font-size:8px;letter-spacing:0;text-transform:none">(exact-name match, same session)</span></div>
        <div class="hist-bar-wrap">${retryPatterns.map(r=>`
          <div class="hist-bar-row">
            <span class="hist-bar-lbl" style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r.name||'')}">${escHtml(r.name||'')}</span>
            <div class="hist-bar-track"><div class="hist-bar-fill" style="width:${((r.retries||0)/maxRetries*100).toFixed(1)}%;background:rgba(255,170,0,.55)"></div></div>
            <span class="hist-bar-val">${r.retries}×</span>
            <span style="font-family:var(--font2);font-size:9px;color:var(--t3);min-width:64px;text-align:right" title="${r.sessions} distinct session${r.sessions!==1?'s':''}${r.last_error?` · ${escHtml(r.last_error)}`:''}">${r.sessions}sess</span>
          </div>`).join('')}</div>`:''}
        ${byProj.length&&byDayProj.length?`
        <div class="hist-section">TOP PROJECTS — COST TREND</div>
        ${byProj.slice(0,3).map((p,i)=>`<div class="trend-wrap" id="trend-proj-${i}"></div>`).join('')}`:''}
      `;
      if(byDay.length){
        // by_day is ORDER BY date DESC from the backend -- reverse to
        // chronological (oldest→newest, left-to-right) for the trend charts.
        const chrono=byDay.slice().reverse();
        // Config-array + loop, matching the same shape the small-multiples
        // per-project trend loop below already uses for repeated charts.
        [
          {id:'trend-tokens',   field:'tokens',        label:'TOKENS BY DAY',                color:'var(--c)', fmt:v=>fmtTok(v)},
          {id:'trend-cost',     field:'cost',           label:'COST BY DAY',                   color:'var(--g)', fmt:v=>fmtCost(v)},
          {id:'trend-errors',   field:'errors',         label:'ERRORS BY DAY',                 color:'var(--r)', fmt:v=>String(v)},
          {id:'trend-duration', field:'avg_duration_s', label:'AVG SESSION DURATION BY DAY',   color:'var(--o)', fmt:v=>fmtDur(v)},
          {id:'trend-waiting',  field:'waiting_on_you_s', label:'TIME CLAUDE WAITED ON YOU BY DAY', color:'rgba(255,170,0,.9)', fmt:v=>fmtDur(v)},
        ].forEach(cfg=>{
          _renderTrendChart(document.getElementById(cfg.id),
            chrono.map(r=>({date:r.date, value:r[cfg.field]||0})),
            {label:`${cfg.label} (last ${chrono.length} days)`, color:cfg.color, formatValue:cfg.fmt});
        });
      }
      if(byDayHook.length){
        // Day-level view of the same hook_misses/hook_miss_concurrency data
        // already shown above as an all-time total -- shows whether the
        // PreToolUse hook's reliability is trending better or worse, not
        // just its cumulative miss count.
        const chronoHook=byDayHook.slice().reverse();
        _renderTrendChart(document.getElementById('trend-hook-reliability'),
          chronoHook.map(r=>({date:r.date, value:r.total>0?(r.misses||0)/r.total*100:0})),
          {label:`HOOK MISS RATE BY DAY (last ${chronoHook.length} days)`, color:'rgba(255,170,0,.9)', formatValue:v=>v.toFixed(1)+'%'});
      }
      if(byDay.length){
        // task_done/task_total were already summed into by_day for the
        // session-list rows' own task-completion chips, but never divided
        // against each other into a trend -- same derived-percentage shape
        // as the hook-reliability trend just above.
        const chronoTasks=byDay.slice().reverse();
        _renderTrendChart(document.getElementById('trend-task-completion'),
          chronoTasks.map(r=>({date:r.date, value:(r.task_total||0)>0?(r.task_done||0)/r.task_total*100:0})),
          {label:`TASK COMPLETION RATE BY DAY (last ${chronoTasks.length} days)`, color:'rgba(0,232,135,.9)', formatValue:v=>v.toFixed(1)+'%'});
      }
      if(_selectedProjectTrend){
        // byDayProj already covers every project with no cap (only the
        // small-multiples view below is capped to the top 3) -- clicking
        // any COST BY PROJECT row can pin open that project's own trend,
        // not just whichever 3 happen to be the biggest spenders.
        const el=document.getElementById('trend-selected-proj');
        if(el){
          const rows=byDayProj.filter(r=>r.project===_selectedProjectTrend); // already date ASC
          _renderTrendChart(el, rows.map(r=>({date:r.date, value:r.cost||0})),
            {label:`${_selectedProjectTrend} — COST TREND (selected)`, color:'var(--c)', formatValue:v=>fmtCost(v)});
          // by_day_project's SQL row carries `tokens` alongside `cost` (same
          // query, same rows) but only the cost side ever got a chart here --
          // tokens survives the multi-machine merge in _mergeAnalytics just
          // fine, it just had no render slot.
          const elTok=document.getElementById('trend-selected-proj-tokens');
          if(elTok) _renderTrendChart(elTok, rows.map(r=>({date:r.date, value:r.tokens||0})),
            {label:`${_selectedProjectTrend} — TOKENS TREND (selected)`, color:'var(--o)', formatValue:v=>fmtTok(v)});
        }
      }
      if(_selectedModelTrend){
        const el=document.getElementById('trend-selected-model');
        if(el){
          const rows=byDayModel.filter(r=>r.model===_selectedModelTrend); // already date ASC
          _renderTrendChart(el, rows.map(r=>({date:r.date, value:r.cost||0})),
            {label:`${_selectedModelTrend} — COST TREND (selected)`, color:'var(--c)', formatValue:v=>fmtCost(v)});
          const elTok=document.getElementById('trend-selected-model-tokens');
          if(elTok) _renderTrendChart(elTok, rows.map(r=>({date:r.date, value:r.tokens||0})),
            {label:`${_selectedModelTrend} — TOKENS TREND (selected)`, color:'var(--o)', formatValue:v=>fmtTok(v)});
        }
      }
      if(_selectedSubagentTypeTrend){
        const el=document.getElementById('trend-selected-subagent-type');
        if(el){
          const rows=byDaySubagentType.filter(r=>r.subagent_type===_selectedSubagentTypeTrend); // already date ASC
          _renderTrendChart(el, rows.map(r=>({date:r.date, value:r.cost||0})),
            {label:`${_selectedSubagentTypeTrend} — COST TREND (selected)`, color:'var(--c)', formatValue:v=>fmtCost(v)});
          const elTok=document.getElementById('trend-selected-subagent-type-tokens');
          if(elTok) _renderTrendChart(elTok, rows.map(r=>({date:r.date, value:r.tokens||0})),
            {label:`${_selectedSubagentTypeTrend} — TOKENS TREND (selected)`, color:'var(--o)', formatValue:v=>fmtTok(v)});
        }
      }
      if(_selectedFileTrend){
        const el=document.getElementById('trend-selected-file');
        if(el){
          const rows=byDayFileHotspots.filter(r=>r.path===_selectedFileTrend); // already date ASC
          _renderTrendChart(el, rows.map(r=>({date:r.date, value:r.changes||0})),
            {label:`${_selectedFileTrend} — CHANGES TREND (selected)`, color:'var(--c)', formatValue:v=>String(v)});
        }
      }
      if(byProj.length&&byDayProj.length){
        // Small multiples rather than one multi-line chart -- keeps each
        // project's own scale readable and avoids needing a 4th+ categorical
        // hue beyond the app's 3 established brand accent colors.
        // Deliberately cost-only, unlike the single-selection trend above --
        // giving each of the top 3 a 2nd tokens chart would double this
        // preview section to 6 charts for a glance-only overview; tokens are
        // one click away via _selectProjectTrend on any of these projects.
        const projColors=['var(--c)','var(--g)','var(--o)'];
        byProj.slice(0,3).forEach((p,i)=>{
          const rows=byDayProj.filter(r=>r.project===p.project); // already date ASC
          _renderTrendChart(document.getElementById(`trend-proj-${i}`),
            rows.map(r=>({date:r.date, value:r.cost||0})),
            {label:`${p.project} — cost trend`, color:projColors[i], formatValue:v=>fmtCost(v)});
        });
      }
    }catch(e){ body.innerHTML='<div style="color:var(--r);padding:20px;font-family:var(--font2);font-size:10px">Error loading analytics</div>'; }
  } else {
    try{
      const localSessions=await fetch('/history').then(r=>r.json());
      const remoteSessions=await _fetchRemoteJSON('/history');
      _histSessionsCache=_mergeHistorySessions(localSessions,remoteSessions);
      _renderHistSessionsBody();
    }catch(e){ body.innerHTML='<div style="color:var(--r);padding:20px;font-family:var(--font2);font-size:10px">Error loading history</div>'; }
  }
}

let _histSessionsCache=null;
let _histSearchQuery='';
let _histSearchFrom='';
let _histSearchTo='';
let _histSearchGen=0;
let _histDetailTags=[]; // currently-displayed session detail's tags, so _historyTagAdd/Remove don't need to scrape the DOM
let _histUntaggedOnly=false;
function _toggleUntaggedFilter(){ _histUntaggedOnly=!_histUntaggedOnly; _renderHistSessionsBody(); }
async function _renderHistSessionsBody(){
  // Separate from renderHistory() on purpose: renderHistory() rebuilds the
  // whole toolbar (including the search input itself) every call, which
  // would reset the input's cursor/focus on every keystroke if the search
  // handler called it directly -- same reason the main agent search
  // (setSearch) re-renders only #agents, never its own toolbar.
  const body=document.getElementById('hist-body');
  if(!body) return;
  const q=_histSearchQuery;
  // A query or date range reaches past the local cache (which only ever
  // holds /history's 100-most-recent rows) via the server-side
  // /history/search endpoint instead of filtering that cache -- an
  // additive capability, not a replacement of the fast "browsing the
  // most recent sessions, no filter" path below.
  const hasFilter=!!(q||_histSearchFrom||_histSearchTo);
  const myGen=++_histSearchGen;
  let sessions;
  if(hasFilter){
    try{
      const params=new URLSearchParams();
      if(q) params.set('q',q);
      if(_histSearchFrom) params.set('from',_histSearchFrom);
      if(_histSearchTo) params.set('to',_histSearchTo);
      const qstr='/history/search?'+params.toString();
      // Same-shaped merge as the unfiltered /history path above (line
      // ~6554-6555) -- without this, a search would only ever cover this
      // machine's own DB, silently dropping remote-machine history that
      // the unfiltered view already includes.
      const [localSessions,remoteSessions]=await Promise.all([
        fetch(qstr).then(r=>r.json()).catch(()=>[]),
        _fetchRemoteJSON(qstr),
      ]);
      sessions=_mergeHistorySessions(localSessions,remoteSessions);
    }catch(e){ sessions=[]; }
    if(myGen!==_histSearchGen) return; // a newer search superseded this one mid-flight
  }else{
    sessions=_histSessionsCache||[];
  }
  if(!sessions.length){
    body.innerHTML=hasFilter
      ?'<div style="color:var(--t3);font-family:var(--font2);font-size:11px;text-align:center;padding:60px 0;letter-spacing:.1em">// no sessions match your search</div>'
      :'<div style="color:var(--t3);font-family:var(--font2);font-size:11px;text-align:center;padding:60px 0;letter-spacing:.1em">// no history yet — sessions are saved on reset</div>';
    return;
  }
  const untaggedCount=sessions.filter(s=>!(s.tags&&s.tags.length)).length;
  if(_histUntaggedOnly) sessions=sessions.filter(s=>!(s.tags&&s.tags.length));
  const untaggedBar=untaggedCount>0?`
    <div style="font-size:10px;font-family:var(--font2);color:var(--t3);padding:0 0 8px;display:flex;align-items:center;gap:6px">
      <span>🏷 ${untaggedCount} untagged session${untaggedCount!==1?'s':''}</span>
      <button class="adp-btn" onclick="_toggleUntaggedFilter()" style="font-size:9px;padding:1px 8px">${_histUntaggedOnly?'SHOW ALL':'SHOW ONLY THESE'}</button>
    </div>`:'';
  if(!sessions.length){
    body.innerHTML=untaggedBar+'<div style="color:var(--t3);font-family:var(--font2);font-size:11px;text-align:center;padding:60px 0;letter-spacing:.1em">// no untagged sessions</div>';
    return;
  }
  const fmtCost=_fmtCost;
  const fmtTok=t=>t?Number(t).toLocaleString():'—';
  const fmtDur=_fmtDurShort;
  // Retrospective counterpart to the live cost_spike webhook: that one only
  // ever fires a one-time alert while a session is still running, so once
  // it closes there was no lasting record of which past sessions were
  // outliers. Reuses the exact same predicate/baseline (_isCostSpike,
  // _projectAvgCosts) against whatever KPI-bar analytics fetch already
  // happens to be cached -- local-only (not merged across Remote Machines,
  // unlike the session list itself), so this only ever marks local rows;
  // skipped entirely (no badge, not a crash) if that cache hasn't
  // populated yet.
  const projectAvgCosts=_projectAvgCosts((_kpiAnalytics||{}).by_project);
  const grouped={};
  sessions.forEach(s=>{ const d=s.date||'—'; if(!grouped[d]) grouped[d]=[]; grouped[d].push(s); });
  body.innerHTML=untaggedBar+Object.entries(grouped).map(([date,rows])=>`
    <div class="hist-section">${date}</div>
    ${rows.map(s=>{
      const isRemote=s._isLocal===false;
      const rm=isRemote?(_remoteMachines.find(m=>m.name===s.machine)||{}):null;
      const onclick=isRemote
        ?`showHistoryDetail('${s.id}','${escHtml(rm.url||'')}','${escHtml(rm.token||'')}')`
        :`showHistoryDetail('${s.id}')`;
      const projectAvg=isRemote?0:(projectAvgCosts[s.project]||0);
      const isOutlier=_isCostSpike(s.cost||0,projectAvg);
      return `
      <div class="hist-row" onclick="${onclick}">
        <span class="hr-date">${s.started_at||'—'} – ${s.ended_at||'—'}</span>
        <span class="hr-project">${escHtml(s.project||'(no project)')}${isRemote?` <span style="color:var(--c);opacity:.7">⌘ ${escHtml(s.machine)}</span>`:''}${(s.tags||[]).map(t=>`<button class="le-tag" style="margin-left:6px" onclick="event.stopPropagation();setHistSearch('${escHtml(t).replace(/'/g,"\\'")}')" title="Filter History by this tag">${escHtml(t)}</button>`).join('')}</span>
        <span class="hr-chips">
          <span class="hist-chip ok">${s.done||0}✓ ${s.agents||0}ag</span>
          ${s.task_total>0?`<span class="hist-chip cost">${s.task_done||0}/${s.task_total}t</span>`:''}
          ${s.errors>0?`<span class="hist-chip err">${s.errors}✗</span>`:''}
          ${isOutlier?`<span class="hookmiss-badge" title="${fmtCost(s.cost)} vs. this project's own ${fmtCost(projectAvg)} average session cost">⚡ OUTLIER</span>`:''}
          ${s.cost?`<span class="hist-chip cost">${fmtCost(s.cost)}</span>`:''}
          ${s.tokens?`<span class="hist-chip tok">${fmtTok(s.tokens)}</span>`:''}
          <span style="font-family:var(--font2);font-size:9px;color:var(--t3)">${fmtDur(s.duration_s)}</span>
        </span>
      </div>`;}).join('')}
  `).join('');
}
function setHistSearch(val){
  _histSearchQuery=val.toLowerCase();
  const inp=document.getElementById('hist-search-input');
  if(inp && inp.value!==val) inp.value=val;
  _renderHistSessionsBody();
}
function setHistDateFrom(val){ _histSearchFrom=val; _renderHistSessionsBody(); }
function setHistDateTo(val){ _histSearchTo=val; _renderHistSessionsBody(); }

/* ── self-diagnostics view ── */
async function renderDiag(){
  const el=document.getElementById('diag-area');
  if(!el) return;
  const fmtBytes=b=>{
    if(b==null) return '—';
    if(b>=1e9) return (b/1e9).toFixed(2)+' GB';
    if(b>=1e6) return (b/1e6).toFixed(1)+' MB';
    if(b>=1e3) return (b/1e3).toFixed(1)+' KB';
    return b+' B';
  };
  const fmtUptime=s=>_fmtDurationDHM(s);
  try{
    const [d,backups]=await Promise.all([
      fetch('/diag').then(r=>r.json()),
      fetch('/backups').then(r=>r.json()).catch(()=>[]),
    ]);
    const fmtDate=t=>t?new Date(t*1000).toLocaleString():'—';
    const backupRows=(backups||[]).map(b=>`
      <div style="display:flex;align-items:center;gap:8px;padding:4px 0;border-bottom:1px solid rgba(255,255,255,.05);font-family:var(--font2);font-size:10px">
        <span style="flex:1;color:${b.corrupt?'var(--r)':'var(--t2)'}">${escHtml(b.filename)}${b.corrupt?' ⚠ CORRUPT':''}</span>
        <span style="color:var(--t3)">${fmtBytes(b.size_bytes)}</span>
        <span style="color:var(--t3)">${fmtDate(b.mtime)}</span>
        ${b.corrupt?'':`<button class="adp-btn" onclick="_restoreBackup('${escHtml(b.filename)}')" style="font-size:9px;padding:2px 10px;color:var(--r)">RESTORE</button>`}
      </div>`).join('');
    el.innerHTML=`
      <div style="font-size:10px;font-family:var(--font2);color:var(--t3);margin-bottom:2px">
        AOC's own process health -- not your agents or CLI sessions. For watchdog/sentinel status and hook reliability, see
        <a href="#" onclick="openSettings();setSettingsTab('infra');return false" style="color:var(--c);text-decoration:underline;text-decoration-style:dotted">Settings → INFRA →</a>
      </div>
      <div class="hist-section">PROCESS</div>
      <div class="hist-analytics">
        <div class="hist-kpi"><div class="hk-val">${fmtUptime(d.uptime_s)}</div><div class="hk-lbl">Uptime</div></div>
        <div class="hist-kpi"><div class="hk-val">${d.thread_count??'—'}</div><div class="hk-lbl">Threads</div></div>
        <div class="hist-kpi"><div class="hk-val">${fmtBytes(d.memory_rss_bytes)}</div><div class="hk-lbl">Memory (RSS)</div></div>
      </div>
      <div class="hist-section">DATABASE</div>
      <div class="hist-analytics">
        <div class="hist-kpi"><div class="hk-val">${fmtBytes(d.db_size_bytes)}</div><div class="hk-lbl">history.db size</div></div>
        <div class="hist-kpi"><div class="hk-val">${d.backups_count??'—'}</div><div class="hk-lbl">Backups</div></div>
      </div>
      <div style="font-size:9px;font-family:var(--font2);color:var(--t3);margin-bottom:6px">
        Restoring overwrites the live history.db (this only affects Session History/Analytics, not the live dashboard). A safety copy of whatever's currently live is taken automatically first.
      </div>
      <div id="backup-restore-msg" style="font-size:10px;font-family:var(--font2);margin-bottom:6px"></div>
      ${backupRows||'<div style="font-size:10px;color:var(--t3);font-family:var(--font2)">// no backups yet</div>'}
    `;
  }catch(e){ el.innerHTML='<div style="color:var(--r);padding:20px;font-family:var(--font2);font-size:10px">Error loading diagnostics</div>'; }
}

async function _restoreBackup(filename){
  if(!confirm(`Restore history.db from "${filename}"?\n\nThe current database will be safety-copied first, then overwritten. This affects Session History/Analytics only, not the live dashboard.`)) return;
  const msgEl=document.getElementById('backup-restore-msg');
  if(msgEl){ msgEl.style.color='var(--t3)'; msgEl.textContent='Restoring…'; }
  try{
    const r=await fetch('/restore_backup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filename})});
    const result=await r.json();
    if(result.ok){
      if(msgEl){ msgEl.style.color='var(--g)'; msgEl.textContent=`✓ Restored from ${filename} (previous DB saved as ${result.pre_restore_backup})`; }
      log(`Restored history.db from ${filename}`,'success');
    } else {
      if(msgEl){ msgEl.style.color='var(--r)'; msgEl.textContent=`✗ ${result.error||'restore failed'}`; }
      log('Restore failed: '+(result.error||'unknown error'),'error');
    }
    renderDiag();
  }catch(e){
    if(msgEl){ msgEl.style.color='var(--r)'; msgEl.textContent='✗ request failed: '+e.message; }
  }
}

async function showHistoryDetail(sid,baseUrl,token){
  try{
    const url=baseUrl?(baseUrl+'/history/'+sid+(token?'?token='+encodeURIComponent(token):'')):('/history/'+sid);
    const d=await fetch(url).then(r=>r.json());
    const snap=d.snapshot||{};
    const agents=d.agents||[];
    const files=d.files||[];
    const tags=d.tags||[];
    _histDetailTags=tags;
    const isRemote=!!baseUrl;
    const fmtCost=_fmtCost;
    const fmtDur=_fmtDurShort;
    const col=st=>({done:'var(--g)',error:'var(--r)',running:'var(--c)',waiting:'var(--o)'})[st]||'var(--t3)';
    const body=document.getElementById('hist-body');
    body.innerHTML=`
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
        <button class="adp-btn" onclick="renderHistory()" style="font-size:9px;padding:2px 10px">← BACK</button>
        <span style="font-family:var(--font2);font-size:11px;color:var(--c);letter-spacing:.1em">${escHtml(snap.project||'Session '+sid)}</span>
        <span style="font-family:var(--font2);font-size:9px;color:var(--t3)">${snap.started_at||''}</span>
        ${d.cc_version?`<span style="font-family:var(--font2);font-size:9px;color:var(--t3)" title="Claude Code version">v${escHtml(d.cc_version)}</span>`:''}
      </div>
      <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:12px">
        ${tags.map(t=>isRemote
          ?`<span class="le-tag" title="${escHtml(t)}">${escHtml(t)}</span>`
          :`<button class="le-tag" onclick="_historyTagRemove('${sid}','${escHtml(t).replace(/'/g,"\\'")}')" aria-label="Remove tag ${escHtml(t)}" title="Click to remove">${escHtml(t)} ✕</button>`
        ).join('')}
        ${isRemote?'':`<input type="text" id="hist-tag-input" placeholder="+ tag" maxlength="30" style="font-family:var(--font2);font-size:9px;padding:2px 8px;border-radius:5px;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.03);color:var(--t);width:70px" onkeydown="if(event.key==='Enter'&&this.value.trim()){_historyTagAdd('${sid}',this.value.trim());this.value='';}">`}
      </div>
      ${agents.length?`
      <div class="hist-section">AGENTS (${agents.length})</div>
      ${agents.map(a=>{
        // parent_id (persisted alongside model/detected_via, feature 102)
        // was never read back here either -- resolve within this same
        // session's agent list, same shape _renderAdp/_agentToMarkdown
        // already resolve it from the live agents list.
        const parent=a.parent_id?agents.find(ag=>ag.agent_id===a.parent_id):null;
        const children=agents.filter(ag=>ag.parent_id===a.agent_id);
        return `<div class="hist-row" style="cursor:default">
        <span style="font-family:var(--font2);font-size:11px;color:${col(a.status)};min-width:36px">${a.unit||a.agent_id.slice(-4).toUpperCase()}</span>
        <span style="flex:1;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${parent?`<span style="color:var(--t3)" title="Spawned by ${escHtml(parent.name||'')}">↳ </span>`:''}${escHtml(a.name||'')}</span>
        ${children.length?`<span style="font-size:9px;color:var(--t3);font-family:var(--font2)" title="${escHtml(children.map(c=>c.name||'').join(', '))}">${children.length} subagent${children.length!==1?'s':''}↓</span>`:''}
        ${a.model?`<span style="font-size:9px;color:var(--t3);font-family:var(--font2);max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(a.model)}">${escHtml(a.model)}</span>`:''}
        ${a.subagent_type?`<span style="font-size:9px;color:rgba(var(--c-rgb),.75);font-family:var(--font2);background:rgba(var(--c-rgb),.08);border-radius:4px;padding:1px 6px" title="Subagent type">${escHtml(a.subagent_type)}</span>`:''}
        ${a.tool_use_count!=null?`<span style="font-size:9px;color:var(--t3);font-family:var(--font2)" title="Tool calls">${a.tool_use_count} tc</span>`:''}
        ${a.detected_via==='transcript'?`<span class="hookmiss-badge" title="${escHtml(_hookMissTitle(a))}">⚠ HOOK MISS</span>`:''}
        <span class="hist-chip ${a.status==='done'?'ok':a.status==='error'?'err':'cost'}">${a.status}</span>
        ${a.cost?`<span class="hist-chip cost">${fmtCost(a.cost)}</span>`:''}
        <span style="font-family:var(--font2);font-size:9px;color:var(--t3)">${fmtDur(a.duration_s)}</span>
        <span style="font-family:var(--font2);font-size:9px;color:var(--t3)">${a.task_done}/${a.task_total}t</span>
      </div>${a.status==='error'&&a.error_msg?`<div style="margin:-2px 0 4px;padding:6px 10px 6px 46px;font-size:10px;color:rgba(255,120,140,.85);font-family:var(--font2);line-height:1.4;white-space:pre-wrap;word-break:break-all;max-height:120px;overflow-y:auto">${escHtml(a.error_msg)}</div>`:''}`;
      }).join('')}`:''}
      ${files.length?`
      <div class="hist-section">FILES CHANGED (${files.length})</div>
      <div style="display:flex;flex-direction:column;gap:3px">${files.slice(0,30).map(f=>`
        <div class="fe ${f.type==='new'?'new':'changed'}" style="padding:4px 8px;border-radius:6px">
          <span class="fe-badge">${f.type==='new'?'NEW':'MOD'}</span>
          <span class="fe-name" style="font-size:11px">${escHtml(f.path)}</span>
          ${f.lines?`<span class="fe-lines">${f.lines}L</span>`:''}
          <span style="font-size:9px;color:var(--t3);font-family:var(--font2)">${f.agent_id.slice(-4).toUpperCase()}</span>
        </div>`).join('')}${files.length>30?`<div style="font-size:10px;color:var(--t3);padding:3px">+${files.length-30} more</div>`:''}</div>`:''}
    `;
  }catch(e){ log('Error loading session detail: '+e.message,'error'); }
}
async function _historySetTags(sid,tags){
  try{
    const r=await fetch('/history_tags',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({session_id:sid,tags})});
    const result=await r.json();
    if(!result.ok) log('Failed to save tags — session not found','error');
  }catch(e){
    log('Failed to save tags: '+e.message,'error');
  }
  await showHistoryDetail(sid);
}
async function _historyTagAdd(sid,tag){ await _historySetTags(sid,[..._histDetailTags,tag]); }
async function _historyTagRemove(sid,tag){ await _historySetTags(sid,_histDetailTags.filter(t=>t!==tag)); }

/* ── KPI bar ── */
/* pure so it's directly testable -- see tests/js/success_rate.test.js */
function _computeSuccessRate(todayAgents, todayDone){
  return todayAgents>0 ? Math.round((todayDone||0)/todayAgents*100) : null;
}
let _kpiAnalytics = null;
let _kpiLastFetch = 0;
let _prevCost = -1;
let _budgetAlerted = false; // fires _browserNotify once per crossing, not every render tick
async function renderKpi(sr){
  const now = Date.now();
  if(now - _kpiLastFetch > 30000){
    _kpiLastFetch = now;
    try{ _kpiAnalytics = await fetch('/analytics').then(r=>r.json()); }catch(e){}
  }
  const agents = sr.agents||[];
  const runCount = agents.filter(a=>a.status==='running'||a.status==='waiting').length;
  const fmtDur = s=>_fmtDurShort(s,true);
  const fmtCost = c=>c!=null&&c>0?'$'+(+c).toFixed(3):'$0';
  const td = (_kpiAnalytics||{}).today||{};
  const total = (_kpiAnalytics||{}).total||{};
  const todayAgents = td.agents!=null ? td.agents : null;
  const todayDone   = td.done!=null   ? td.done   : null;
  const todayErrors = td.errors!=null ? td.errors  : null;
  const successRate = _computeSuccessRate(todayAgents, todayDone);
  const el = id=>document.getElementById(id);
  const set = (id,val,cls='')=>{ const e=el(id); if(e){ e.textContent=val; e.className='kpi-val'+(cls?' '+cls:''); }};
  const sessionCount = (sr.sessions_list||[]).filter(s=>s.session_active).length || (sr.sessions_count||0);
  const errCount = agents.filter(a=>a.status==='error').length;
  set('kpi-running', runCount, runCount>0?'green':'');
  set('kpi-sessions', sessionCount||'—', sessionCount>0?'cyan':'');
  const sessEl=el('kpi-sessions');
  // avg_duration_s sits right next to agents/done/errors in the same `today`
  // DB row (all from _db_analytics), but unlike its siblings it was never
  // read anywhere -- the SESSIONS tile's title was a static "Active CLI
  // sessions" string that never got rewritten, mirroring the kpi-errors
  // tooltip pattern just below.
  if(sessEl && sessEl.parentElement) sessEl.parentElement.title = td.avg_duration_s!=null
    ? `Active CLI sessions — avg session duration today: ${_fmtDurationDHM(td.avg_duration_s)}`
    : 'Active CLI sessions';
  const errEl2=el('kpi-errors');
  if(errEl2){
    errEl2.textContent=errCount||'—';
    errEl2.style.color=errCount>0?'var(--r)':'var(--t3)';
    /* errCount only covers agents still visible on CARDS -- older sessions
       swept out of the live view don't count. todayErrors (from /analytics,
       a straight DB sum) is the true today-wide total, so surface it here
       rather than adding a whole new KPI card just for one number. */
    if(errEl2.parentElement) errEl2.parentElement.title = todayErrors!=null
      ? `Agents with errors (visible now) — ${todayErrors} today across all sessions`
      : 'Agents with errors';
  }
  const succEl=el('kpi-success');
  if(succEl){
    succEl.textContent=successRate!=null?successRate+'%':'—';
    succEl.style.color=successRate==null?'var(--t3)':successRate>=90?'rgba(0,232,135,.9)':successRate>=70?'var(--o)':'var(--r)';
  }
  set('kpi-total', total.agents!=null?total.agents:'—');
  /* ── cost gauge — sums real per-model estimated_cost from transcript scanner ── */
  const sessionCost = (sr.sessions_list||[]).reduce((s,sess)=>s+(sess.estimated_cost||0),0);
  const totalTokens = (sr.sessions_list||[]).reduce((s,sess)=>s+(sess.input_tokens||0)+(sess.output_tokens||0),0);
  const arcLen = 88;
  const pct = _budgetLimit>0 ? Math.min(sessionCost/_budgetLimit,1) : 0;
  const arcOffset = arcLen * (1 - pct);
  const arcColor = pct>0.9?'#ff5050':pct>0.65?'#ffa040':'rgba(0,210,130,.85)';
  const gaugeArc = el('cost-gauge-arc');
  if(gaugeArc){ gaugeArc.style.strokeDashoffset=arcOffset; gaugeArc.style.stroke=arcColor; }
  const tokFmt = totalTokens>=1e6?(totalTokens/1e6).toFixed(2)+'M':totalTokens>=1000?Math.round(totalTokens/1000)+'K':String(totalTokens||0);
  const textColor = sessionCost>0?(pct>0.9?'#ff5050':pct>0.65?'#ffa040':'rgba(0,232,135,.9)'):'rgba(255,255,255,.3)';
  const costEl = el('kpi-cost');
  if(costEl){ costEl.textContent='$'+sessionCost.toFixed(3); costEl.setAttribute('fill',textColor); }
  const tokEl = el('cost-gauge-tok');
  if(tokEl) tokEl.textContent=totalTokens>0?tokFmt+' tok':'— tok';
  if(sessionCost>_prevCost&&_prevCost>=0){
    const svg=el('cost-gauge-svg');
    if(svg){ svg.classList.remove('gflash'); void svg.offsetWidth; svg.classList.add('gflash'); }
  }
  _prevCost=sessionCost;
  /* budget alert -- fires once per crossing (not every ~poll tick while over
     budget), re-arms once cost drops back under the limit (new session/reset) */
  if(_budgetLimit>0&&sessionCost>=_budgetLimit&&!_budgetAlerted){
    _budgetAlerted=true;
    _browserNotify('AOC — Budget exceeded', `Session cost $${sessionCost.toFixed(3)} has reached your $${_budgetLimit.toFixed(2)} budget`);
  } else if(sessionCost<_budgetLimit){
    _budgetAlerted=false;
  }
  /* Per-project budget alerts moved server-side (_webhook_notify_worker's
     budget_alert check, monthly month-to-date spend against
     _notify_settings["project_budgets"]) -- fires toast + webhook and
     works with no browser tab open, unlike this tab-open-only
     _browserNotify path it replaced. _projectBudgets is still read here
     only by _renderProjectBudgetSummary's visual indicator in Settings. */
  _renderTopProjects(_kpiAnalytics);
}

/* Top projects by cost today -- pure function, takes by_day_project (an
   /analytics field renderKpi already fetches every 30s into _kpiAnalytics
   for the KPI bar, so this needs no extra fetch of its own) plus today's
   date string, and returns the top N {project,cost} pairs for today only.
   Visible at a glance on CARDS instead of only inside HISTORY → ANALYTICS. */
function _topProjectsToday(byDayProject, todayStr, topN=3){
  const todayRows=(byDayProject||[]).filter(r=>r.date===todayStr && (r.cost||0)>0);
  return todayRows
    .map(r=>({project:r.project||'(none)', cost:r.cost||0}))
    .sort((a,b)=>b.cost-a.cost)
    .slice(0,topN);
}
/* Week-over-week cost trend -- same last-7-vs-preceding-7 by_day slicing
   as _build_digest_summary's own math server-side (see that function's
   docstring for the "array position, not calendar-exact" caveat), just
   computed here client-side off the same by_day the KPI bar's own 30s
   /analytics fetch already carries. The weekly digest already surfaces
   this exact number, but only once a week in a toast/webhook -- this
   makes it visible live, at a glance, without waiting for that schedule
   or opening History -> Analytics. */
function _computeWeekOverWeekCost(byDay){
  const rows=byDay||[];
  const last7=rows.slice(0,7);
  const prev7=rows.slice(7,14);
  const cost=last7.reduce((s,r)=>s+(r.cost||0),0);
  const prevCost=prev7.reduce((s,r)=>s+(r.cost||0),0);
  const pctChange=prevCost>0?((cost-prevCost)/prevCost*100):null;
  return {cost, prevCost, pctChange};
}
function _renderTopProjects(analytics){
  const bar=document.getElementById('top-projects-bar');
  if(!bar) return;
  const todayStr=new Date().toISOString().slice(0,10);
  const top=_topProjectsToday((analytics||{}).by_day_project, todayStr);
  const wow=_computeWeekOverWeekCost((analytics||{}).by_day);
  const wowHtml=wow.pctChange!=null
    ?`<span title="Last 7 days: $${wow.cost.toFixed(2)} — preceding 7 days: $${wow.prevCost.toFixed(2)}">7d cost <span style="color:${wow.pctChange>=0?'rgba(255,120,140,.9)':'rgba(0,232,135,.9)'}">${wow.pctChange>=0?'▲':'▼'} ${Math.abs(wow.pctChange).toFixed(0)}%</span> vs prior wk</span>`
    :'';
  if(!top.length && !wowHtml){ bar.style.display='none'; bar.innerHTML=''; return; }
  bar.style.display='flex';
  const topHtml=top.length?`<span style="letter-spacing:.1em;opacity:.7">TOP TODAY</span>`+top.map((r,i)=>
    `<span title="${escHtml(r.project)}">${i+1}. ${escHtml(r.project.length>18?r.project.slice(0,18)+'…':r.project)} <span style="color:rgba(0,232,135,.85)">$${r.cost.toFixed(2)}</span></span>`
  ).join('<span style="opacity:.3">·</span>'):'';
  bar.innerHTML=[wowHtml, topHtml].filter(Boolean).join('<span style="opacity:.3">·</span>');
}

/* ── poll ── */
/* single place that applies a fresh status payload to the UI — used to be
   duplicated between an initial poll() and a separate setInterval body that
   had quietly drifted (missing _tunnelUpdateUI, for one) */
let _histLastRender=0;
let _diagLastRender=0;
function _applyStatus(sr){
  sr=_mergeRemoteData(sr);
  diffStatusWithRemove(lastStatus,sr);
  checkStaleAgents(sr);
  lastStatus=sr;
  _sessStartMs=sr.session_active&&sr.started_at?parseTimeStr(sr.started_at):null;
  if(!sr.session_active) document.getElementById('s-elapsed').textContent='—';
  renderAgents(sr);
  renderKpi(sr);
  _tunnelUpdateUI(sr.tunnel);
  _selfUpdateUpdateUI(sr.self_update);
  _infraUpdateUI(sr.infra_health);
  _updateSweepButton(sr);
  if(_adpAgentId) _renderAdp();
  if(currentView==='summary')  renderSummary(sr);
  if(currentView==='graph')    renderGraph(sr);
  if(currentView==='heat')     renderHeatmap(sr);
  if(currentView==='tree')     renderTree(sr);
  if(currentView==='timeline') renderTimeline(sr);
  if(currentView==='history'&&Date.now()-_histLastRender>10000){ _histLastRender=Date.now(); renderHistory(); }
  if(currentView==='diag'&&Date.now()-_diagLastRender>10000){ _diagLastRender=Date.now(); renderDiag(); }
  if(_rightTab==='files') _renderRpFiles(sr);
  if(_rightTab==='audit') _renderRpAudit();
  if(_rightTab==='errors') _renderRpErrors();
  if(_cmpOpen) _renderCompare();
  if(_sessCmpOpen) _renderSessionCompare();
}

/* ── multi-machine merge ──
   Pure function: local sr + whatever's cached from configured remotes ->
   one combined object shaped exactly like _build_status_payload()'s own
   output, so every render function above (and every notification-diff
   path) accepts it completely unchanged. No-op when no remotes are
   configured, so single-machine behavior is byte-for-byte identical to
   before this feature existed. */
function _mergeRemoteData(sr){
  if(!_remoteMachines.length) return sr;
  const localName=_localMachineName||'local';
  const sessions_list=(sr.sessions_list||[]).map(s=>({...s, machine:localName, _isLocal:true, _domId:'local_'+s.id}));
  const agents=(sr.agents||[]).map(a=>({...a, machine:localName, _isLocal:true, _domId:'local_'+a.id}));
  /* also merge the raw sessions dict -- checkStaleAgents() looks up
     data.sessions[agent.session_id].last_seen_epoch directly (not via
     sessions_list, which doesn't carry that field) to decide whether a
     2h+ "running" agent is actually abandoned; without this, every remote
     agent would look abandoned immediately since its session_id would
     never be found in the local machine's own sessions dict. */
  const mergedSessionsDict={...(sr.sessions||{})};
  _remoteMachines.forEach((m,idx)=>{
    const cached=_remoteCache[idx];
    if(!cached||!cached.ok||!cached.data) return;
    const name=m.name||('machine'+idx);
    (cached.data.sessions_list||[]).forEach(s=>sessions_list.push({...s, machine:name, _isLocal:false, _domId:'m'+idx+'_'+s.id}));
    (cached.data.agents||[]).forEach(a=>agents.push({...a, machine:name, _isLocal:false, _domId:'m'+idx+'_'+a.id}));
    Object.assign(mergedSessionsDict, cached.data.sessions||{});
  });
  return {...sr, sessions_list, agents, sessions:mergedSessionsDict, sessions_count:sessions_list.length};
}

function _pollRemoteMachines(){
  _remoteMachines.forEach((m,idx)=>{
    if(!m.url) return;
    const url=m.url+'/status'+(m.token?('?token='+encodeURIComponent(m.token)):'');
    fetch(url,{signal:AbortSignal.timeout(4000)})
      .then(r=>r.ok?r.json():Promise.reject(new Error('HTTP '+r.status)))
      .then(data=>{ _remoteCache[idx]={data, ok:true, lastFetch:Date.now()}; })
      .catch(()=>{ _remoteCache[idx]={..._remoteCache[idx], ok:false, lastFetch:Date.now()}; });
  });
}
setInterval(_pollRemoteMachines, 4000);

/* ── routing a mutation (Force Stop / Dismiss) to the right machine ──
   Every merged agent/session card carries a.machine/s.machine (the name
   typed into Settings -> Remote Machines, or _localMachineName for this
   machine's own -- see _mergeRemoteData above). _machineFor resolves
   that name back to the {url,token} needed to reach it; null means
   "local", so _apiUrl's callers don't need their own local/remote
   branch -- a null machine just returns the plain relative path exactly
   as every call site already did before Remote Machines existed. */
function _machineFor(name){
  if(!name || name===_localMachineName) return null;
  return _remoteMachines.find(m=>m.name===name) || null;
}
function _apiUrl(machine, path){
  if(!machine || !machine.url) return path;
  const sep=path.includes('?')?'&':'?';
  return machine.url+path+(machine.token?(sep+'token='+encodeURIComponent(machine.token)):'');
}

/* ── multi-machine merge: History/Analytics ──
   Separate from the live-dashboard merge above on purpose: History and
   Analytics are fetched on-demand only when that tab is actually open
   (same as they already were before remotes existed), not on the constant
   4s poll -- there's no need to keep remote SQLite aggregates warm in the
   background just because a tab isn't showing them. */
async function _fetchRemoteJSON(path){
  return Promise.all(_remoteMachines.map(m=>{
    if(!m.url) return null;
    const sep=path.includes('?')?'&':'?';
    const url=m.url+path+(m.token?(sep+'token='+encodeURIComponent(m.token)):'');
    return fetch(url,{signal:AbortSignal.timeout(4000)}).then(r=>r.ok?r.json():null).catch(()=>null);
  }));
}

function _mergeAnalytics(local,remoteDataList){
  if(!_remoteMachines.length) return local;
  const sumInto=(dst,src)=>{ for(const k of ['sessions','tokens','cost','agents','done','errors','files','tool_use_count','task_done','task_total','waiting_on_you_s']){ if(src[k]!=null) dst[k]=(dst[k]||0)+src[k]; } };
  const total={...(local.total||{})};
  let hookMisses=local.hook_misses||0;
  let retryCount=local.retry_count||0;
  const retryPatternsMap=new Map((local.retry_patterns||[]).map(r=>[r.name,{...r}]));
  const byDayMap=new Map((local.by_day||[]).map(r=>[r.date,{...r}]));
  const byProjMap=new Map((local.by_project||[]).map(r=>[r.project,{...r}]));
  const byDayProjMap=new Map((local.by_day_project||[]).map(r=>[r.date+'|'+r.project,{...r}]));
  const byModelMap=new Map((local.by_model||[]).map(r=>[r.model,{...r}]));
  const byDayModelMap=new Map((local.by_day_model||[]).map(r=>[r.date+'|'+r.model,{...r}]));
  const bySubagentTypeMap=new Map((local.by_subagent_type||[]).map(r=>[r.subagent_type,{...r}]));
  const byDaySubagentTypeMap=new Map((local.by_day_subagent_type||[]).map(r=>[r.date+'|'+r.subagent_type,{...r}]));
  const byDayHookMap=new Map((local.by_day_hook_reliability||[]).map(r=>[r.date,{...r}]));
  const fileHotspotsMap=new Map((local.file_hotspots||[]).map(r=>[r.path,{...r}]));
  const byDayFileHotspotsMap=new Map((local.by_day_file_hotspots||[]).map(r=>[r.date+'|'+r.path,{...r}]));
  const fileTypeMap=new Map((local.by_file_type||[]).map(r=>[r.ext,{...r}]));
  const tagCloudMap=new Map((local.tag_cloud||[]).map(r=>[r.tag,{...r}]));
  const byTagMap=new Map((local.by_tag||[]).map(r=>[r.tag,{...r}]));
  let slowestAgents=(local.slowest_agents||[]).map(r=>({...r, _isLocal:true}));
  const commonErrorsMap=new Map((local.common_errors||[]).map(r=>[r.error_msg,{...r}]));
  remoteDataList.forEach((rd,idx)=>{
    if(!rd) return;
    sumInto(total, rd.total||{});
    hookMisses+=rd.hook_misses||0;
    retryCount+=rd.retry_count||0;
    (rd.retry_patterns||[]).forEach(r=>{
      const ex=retryPatternsMap.get(r.name);
      if(ex){ ex.retries=(ex.retries||0)+(r.retries||0); ex.sessions=(ex.sessions||0)+(r.sessions||0); }
      else retryPatternsMap.set(r.name,{...r});
    });
    (rd.by_day||[]).forEach(r=>{ const ex=byDayMap.get(r.date); ex?sumInto(ex,r):byDayMap.set(r.date,{...r}); });
    (rd.by_project||[]).forEach(r=>{ const ex=byProjMap.get(r.project); ex?sumInto(ex,r):byProjMap.set(r.project,{...r}); });
    (rd.by_day_project||[]).forEach(r=>{
      const key=r.date+'|'+r.project;
      const ex=byDayProjMap.get(key);
      if(ex){ ex.tokens=(ex.tokens||0)+(r.tokens||0); ex.cost=(ex.cost||0)+(r.cost||0); }
      else byDayProjMap.set(key,{...r});
    });
    (rd.by_model||[]).forEach(r=>{ const ex=byModelMap.get(r.model); ex?sumInto(ex,r):byModelMap.set(r.model,{...r}); });
    (rd.by_day_model||[]).forEach(r=>{
      const key=r.date+'|'+r.model;
      const ex=byDayModelMap.get(key);
      if(ex){ ex.tokens=(ex.tokens||0)+(r.tokens||0); ex.cost=(ex.cost||0)+(r.cost||0); }
      else byDayModelMap.set(key,{...r});
    });
    (rd.by_subagent_type||[]).forEach(r=>{ const ex=bySubagentTypeMap.get(r.subagent_type); ex?sumInto(ex,r):bySubagentTypeMap.set(r.subagent_type,{...r}); });
    (rd.by_day_subagent_type||[]).forEach(r=>{
      const key=r.date+'|'+r.subagent_type;
      const ex=byDaySubagentTypeMap.get(key);
      if(ex){ ex.tokens=(ex.tokens||0)+(r.tokens||0); ex.cost=(ex.cost||0)+(r.cost||0); }
      else byDaySubagentTypeMap.set(key,{...r});
    });
    (rd.by_day_hook_reliability||[]).forEach(r=>{
      const ex=byDayHookMap.get(r.date);
      if(ex){ ex.misses=(ex.misses||0)+(r.misses||0); ex.total=(ex.total||0)+(r.total||0); }
      else byDayHookMap.set(r.date,{...r});
    });
    (rd.file_hotspots||[]).forEach(r=>{
      const ex=fileHotspotsMap.get(r.path);
      if(ex){ ex.changes=(ex.changes||0)+(r.changes||0); ex.sessions=(ex.sessions||0)+(r.sessions||0); ex.total_lines=(ex.total_lines||0)+(r.total_lines||0); }
      else fileHotspotsMap.set(r.path,{...r});
    });
    (rd.by_day_file_hotspots||[]).forEach(r=>{
      const key=r.date+'|'+r.path;
      const ex=byDayFileHotspotsMap.get(key);
      if(ex){ ex.changes=(ex.changes||0)+(r.changes||0); }
      else byDayFileHotspotsMap.set(key,{...r});
    });
    (rd.by_file_type||[]).forEach(r=>{
      const ex=fileTypeMap.get(r.ext);
      if(ex){ ex.changes=(ex.changes||0)+(r.changes||0); ex.sessions=(ex.sessions||0)+(r.sessions||0); ex.total_lines=(ex.total_lines||0)+(r.total_lines||0); }
      else fileTypeMap.set(r.ext,{...r});
    });
    (rd.tag_cloud||[]).forEach(r=>{
      const ex=tagCloudMap.get(r.tag);
      if(ex){ ex.count=(ex.count||0)+(r.count||0); }
      else tagCloudMap.set(r.tag,{...r});
    });
    (rd.by_tag||[]).forEach(r=>{ const ex=byTagMap.get(r.tag); ex?sumInto(ex,r):byTagMap.set(r.tag,{...r}); });
    // A leaderboard of individual agent runs, not a summable aggregate --
    // concat local+remote (each machine's own top 20) and re-sort/re-slice
    // to the merged top 20, rather than the sumInto-by-key pattern every
    // other by_* breakdown above uses. Tagged with the machine name (same
    // idea as _mergeHistorySessions) since a row here identifies one
    // specific agent run, not an aggregate bucket -- unlike _isLocal,
    // machine name has no meaning for a purely local dashboard, so it's
    // only set once remotes actually exist (this whole function already
    // early-returns before reaching here otherwise).
    const machineName=(_remoteMachines[idx]&&_remoteMachines[idx].name)||('machine'+idx);
    slowestAgents=slowestAgents.concat((rd.slowest_agents||[]).map(r=>({...r, _isLocal:false, machine:machineName})));
    (rd.common_errors||[]).forEach(r=>{
      const ex=commonErrorsMap.get(r.error_msg);
      // Occurrence count sums across machines; last_session_id/project keep
      // whichever machine's row is already there rather than trying to
      // compare recency across machines (no timestamp in this payload to
      // compare against) -- click-through still lands on a real, valid
      // occurrence of the error either way.
      if(ex){ ex.occurrences=(ex.occurrences||0)+(r.occurrences||0); }
      else commonErrorsMap.set(r.error_msg,{...r});
    });
  });
  slowestAgents=slowestAgents.sort((a,b)=>(b.duration_s||0)-(a.duration_s||0)).slice(0,20);
  return {
    ...local, total, hook_misses:hookMisses, retry_count:retryCount,
    retry_patterns:[...retryPatternsMap.values()].sort((a,b)=>(b.retries||0)-(a.retries||0)),
    by_day:[...byDayMap.values()].sort((a,b)=>a.date<b.date?1:-1),
    by_project:[...byProjMap.values()].sort((a,b)=>(b.cost||0)-(a.cost||0)),
    by_day_project:[...byDayProjMap.values()].sort((a,b)=>a.date<b.date?-1:1),
    by_model:[...byModelMap.values()].sort((a,b)=>(b.cost||0)-(a.cost||0)),
    by_day_model:[...byDayModelMap.values()].sort((a,b)=>a.date<b.date?-1:1),
    by_subagent_type:[...bySubagentTypeMap.values()].sort((a,b)=>(b.cost||0)-(a.cost||0)),
    by_day_subagent_type:[...byDaySubagentTypeMap.values()].sort((a,b)=>a.date<b.date?-1:1),
    by_day_hook_reliability:[...byDayHookMap.values()].sort((a,b)=>a.date<b.date?-1:1),
    file_hotspots:[...fileHotspotsMap.values()].sort((a,b)=>(b.changes||0)-(a.changes||0)),
    by_day_file_hotspots:[...byDayFileHotspotsMap.values()].sort((a,b)=>a.date<b.date?-1:1),
    by_file_type:[...fileTypeMap.values()].sort((a,b)=>(b.changes||0)-(a.changes||0)),
    tag_cloud:[...tagCloudMap.values()].sort((a,b)=>(b.count||0)-(a.count||0)),
    by_tag:[...byTagMap.values()].sort((a,b)=>(b.cost||0)-(a.cost||0)),
    slowest_agents:slowestAgents,
    common_errors:[...commonErrorsMap.values()].sort((a,b)=>(b.occurrences||0)-(a.occurrences||0)),
  };
}

function _mergeHistorySessions(local,remoteDataList){
  if(!_remoteMachines.length) return local;
  const merged=(local||[]).map(s=>({...s, machine:_localMachineName||'local', _isLocal:true}));
  remoteDataList.forEach((rd,idx)=>{
    if(!rd) return;
    const name=(_remoteMachines[idx]&&_remoteMachines[idx].name)||('machine'+idx);
    rd.forEach(s=>merged.push({...s, machine:name, _isLocal:false}));
  });
  merged.sort((a,b)=>(b.started_at||'').localeCompare(a.started_at||''));
  return merged;
}

/* ── live updates via SSE — replaces the old 500ms setInterval poll ── */
let _es=null;
let _esReconnectDelay=1000;   /* ms, capped exponential backoff */
let _esReconnectTimer=null;
function _connectEvents(){
  /* WebSocket, not EventSource/SSE — SSE never got forwarded at all through
     the Cloudflare quick tunnel (chunked responses arrived as 0 bytes even
     after 35s), while WebSocket already reliably survives the same tunnel
     (proven by the terminal). Unlike EventSource, WebSocket has no built-in
     retry, so reconnect is hand-rolled below. */
  if(_esReconnectTimer){ clearTimeout(_esReconnectTimer); _esReconnectTimer=null; }
  if(_es){ try{ _es.onclose=null; _es.close(); }catch(e){} }
  const proto=location.protocol==='https:'?'wss':'ws';
  const url=`${proto}://${location.host}/events`+(window._AOC_TOKEN?'?token='+encodeURIComponent(window._AOC_TOKEN):'');
  _es=new WebSocket(url);
  _es.onopen=()=>{ pollFails=0; _setConnStatus(true); _esReconnectDelay=1000; };
  _es.onmessage=(ev)=>{
    try{
      const sr=JSON.parse(ev.data);
      pollFails=0; _setConnStatus(true);
      _applyStatus(sr);
    }catch(e){ log('status WS parse error: '+e.message,'error'); }
  };
  _es.onerror=()=>{ /* onclose fires right after and owns state + reconnect */ };
  _es.onclose=()=>{
    pollFails++;
    if(pollFails>=2) _setConnStatus(false);
    _esReconnectTimer=setTimeout(_connectEvents, _esReconnectDelay);
    _esReconnectDelay=Math.min(_esReconnectDelay*1.5, 15000);
  };
}

/* ── auto-remove done agents; error agents stay sticky ── */
const _removingIds = new Set();
const _fadingStartedAt = {};  /* agentId → epoch ms when fade began */
const _MIN_VISIBLE_MS = 15000; /* agent stays visible at least 15s from started_at */
function scheduleRemove(agentId, status){
  if(status==='error') return;
  if(_removingIds.has(agentId)) return;
  _removingIds.add(agentId);
  _fadingStartedAt[agentId] = Date.now();  /* JS-computed fade; persists across innerHTML rebuilds */
  /* respect minimum visibility — delay removal so agent was visible for at least 15s */
  const agent=(lastStatus?.agents||[]).find(a=>a.id===agentId);
  const startMs=agent?.started_at?parseTimeStr(agent.started_at):Date.now();
  const alreadyVisibleMs=Date.now()-startMs;
  const delay=Math.max(10000, _MIN_VISIBLE_MS-alreadyVisibleMs);
  setTimeout(async ()=>{
    /* cancel removal if agent went back to running */
    const cur=(lastStatus?.agents||[]).find(a=>a.id===agentId);
    if(cur && cur.status==='running'){
      _removingIds.delete(agentId);
      delete _fadingStartedAt[agentId];
      return;
    }
    try{
      await fetch('/history/save_current');
      await fetch('/remove',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:agentId})});
    }catch(e){}
    _removingIds.delete(agentId);
    delete _fadingStartedAt[agentId];
  }, delay);
}

/* ── auto-remove stale "running" agents (started > 20min ago, never finished) ── */
function checkStaleAgents(data){
  const now=Date.now();
  (data.agents||[]).filter(a=>!String(a.id||'').startsWith('hook_')).forEach(a=>{
    if(a.status!=='running') return;
    if(!a.started_at) return;
    const [_sh,_sm,_ss]=(a.started_at||'').split(':').map(Number);
    const startMs=new Date().setHours(_sh||0,_sm||0,_ss||0,0);
    if(isNaN(startMs)) return;
    const age=(now-startMs)/1000;
    if(age<=7200) return;  /* not old enough to even consider yet */
    /* A 2h+ "running" agent isn't necessarily stuck — it might just be a slow
       task under a session the user is still actively working in. Only treat
       it as abandoned if its OWNING SESSION also looks dead (no heartbeat in
       30min): that's the actual signal something got orphaned (e.g. AOC
       restarted mid-task and will never see the real completion event),
       not merely "this took a while". Without this check, any long-running
       agent got force-marked done/removed purely by age, even while its CLI
       was still live and the agent still genuinely working. */
    const sess=a.session_id?(data.sessions||{})[a.session_id]:null;
    const sessLastSeen=sess?sess.last_seen_epoch:null;
    const sessLooksAbandoned=!sessLastSeen||(now/1000-sessLastSeen)>1800;
    if(sessLooksAbandoned) scheduleRemove(a.id,'done');
  });
}

/* ── stuck detection ── track last task-progress time per agent */
const _lastProgressAt = {};  /* agentId → epoch ms of last task completion */
const _stuckNotified = new Set(); /* agents already notified about being stuck */
function _stuckSecs(a){ const lp=_lastProgressAt[a.id]; return lp&&a.status==='running'?Math.floor((Date.now()-lp)/1000):0; }

/* ── token-burn anomaly detection ── mirrors the server-side check in
   _webhook_notify_worker so a tab-open user gets the badge/sound
   immediately, not just whenever webhooks happen to be configured. */
const _tokenHistory = {};   /* sessionId → [[epoch_s, cumulative_tokens], ...], trimmed to last 10 min */
const _burnNotified = new Set(); /* sessions already notified about a burn spike */
const _waitingNotified = new Set(); /* sessions already notified about waiting too long */
const _costSpikeNotified = new Set(); /* sessions already notified about a cost spike */

/* ── session activity sparkline ── purely live/in-memory, same choice as
   the burn-spike detector above: no persisted history exists for this,
   and this is a lightweight visualization, not a record worth persisting. */
const _activityHistory = {}; /* sessionId → [[epoch_s, 'active'|'waiting'|'idle'], ...], trimmed to last 2h */

/* extend diffStatus to schedule removal + audio + conflict */
const _origDiff = diffStatus;
const _prevStatuses = {};
const _prevSessionActive = {};
const _sessionAutoDismissTimers = {};
function diffStatusWithRemove(prev, next){
  _origDiff(prev, next);
  /* auto-dismiss sessions that just closed -- NOT for remote (merged-in)
     sessions, matching the same _isLocal gate the manual dismiss buttons
     already use (lines ~3890/5415): a remote session's id doesn't exist
     in this machine's own sessions dict, so dismissing it here would
     create a bogus local entry keyed by a foreign id. */
  (next.sessions_list||[]).forEach(s=>{
    if(s._isLocal===false) return;
    const wasActive=_prevSessionActive[s.id];
    const nowActive=s.session_active!==false;
    if(wasActive===true && !nowActive && !_sessionAutoDismissTimers[s.id]){
      /* 90s, not 5s: a session can look momentarily "inactive" from
         nothing more than a >5min gap between hook heartbeats (one long
         tool call, a long subagent task, stepping away briefly) -- both
         _build_status_payload's live session_active computation and
         _transcript_scanner_worker's own staleness check treat that as
         inactive by design. The old 5s window gave a same-session
         reactivation (next scanner tick at 30s, or the next real
         heartbeat) essentially no chance to arrive before the dismiss
         fired -- this is what caused every "why did this session
         randomly get dismissed while I was still using it" recurrence. */
      _sessionAutoDismissTimers[s.id]=setTimeout(()=>{
        delete _sessionAutoDismissTimers[s.id];
        _dismissSession(s.id);
        if(sessionFilter===s.id) setSessionFilter(null);
      }, 90000);
    } else if(nowActive && _sessionAutoDismissTimers[s.id]){
      /* session came back before the timer fired -- cancel it. Mirrors
         scheduleRemove's own "re-check before committing" pattern
         (~6220-6245); this timer previously had no equivalent guard at
         all, so it always fired regardless of what happened in between. */
      clearTimeout(_sessionAutoDismissTimers[s.id]);
      delete _sessionAutoDismissTimers[s.id];
    }
    _prevSessionActive[s.id]=nowActive;

    const nowS=Date.now()/1000;
    // activity sparkline sample -- taken regardless of active/inactive
    // (unlike the token-burn tracking below, which only matters while
    // active) so the sparkline can actually show when a session went idle.
    const activityState=!nowActive?'idle':(s.waiting_on_you?'waiting':'active');
    const ahist=_activityHistory[s.id]=_activityHistory[s.id]||[];
    ahist.push([nowS,activityState]);
    const acutoff=nowS-7200;
    while(ahist.length&&ahist[0][0]<acutoff) ahist.shift();

    if(!nowActive){ delete _tokenHistory[s.id]; _burnNotified.delete(s.id); _waitingNotified.delete(s.id); _costSpikeNotified.delete(s.id); return; }

    // Waiting-too-long nudge: mirrors the server-side check in
    // _webhook_notify_worker, kept independent of the burn-rate block
    // below so it isn't accidentally skipped for a session with under
    // 5min of token-history samples.
    if((s.waiting_secs||0)>7200){
      if(!_waitingNotified.has(s.id)){
        _waitingNotified.add(s.id);
        if(!_isNotifySuppressed(s.project)){ _playSound('waiting_nudge'); _fireWaitingWebhook(s); }
      }
    }else{
      _waitingNotified.delete(s.id);
    }

    // Cost-spike: mirrors the server-side check in _webhook_notify_worker
    // (_is_cost_spike/_project_avg_costs), same independent placement as
    // waiting_nudge above -- not gated behind the burn-rate history check
    // below. _kpiAnalytics.by_project is already fetched every 30s by
    // renderKpi for the KPI bar, reused here rather than fetching again.
    const projectAvgCost=_projectAvgCosts((_kpiAnalytics||{}).by_project)[s.project]||0;
    const sessionCost=s.estimated_cost||0;
    if(_isCostSpike(sessionCost,projectAvgCost)){
      if(!_costSpikeNotified.has(s.id)){
        _costSpikeNotified.add(s.id);
        if(!_isNotifySuppressed(s.project)){ _playSound('cost_spike'); _fireCostSpikeWebhook(s,sessionCost,projectAvgCost); }
      }
    }else{
      _costSpikeNotified.delete(s.id);
    }

    const total=(s.input_tokens||0)+(s.output_tokens||0);
    const hist=_tokenHistory[s.id]=_tokenHistory[s.id]||[];
    hist.push([nowS,total]);
    const cutoff=nowS-600;
    while(hist.length&&hist[0][0]<cutoff) hist.shift();
    const rates=_computeBurnRates(hist,nowS);
    if(rates){
      const [recentRate,avgRate]=rates;
      if(_isBurnSpike(recentRate,avgRate)){
        if(!_burnNotified.has(s.id)){
          _burnNotified.add(s.id);
          if(!_isNotifySuppressed(s.project)){ _playSound('burn_spike'); _fireBurnWebhook(s,recentRate,avgRate); }
        }
      }else{
        _burnNotified.delete(s.id);
      }
    }
  });
  const agents=(next.agents||[]).filter(a=>!String(a.id||'').startsWith('hook_'));
  const prevAgents=Object.fromEntries((prev&&prev.agents||[]).map(a=>[a.id,a]));
  agents.forEach(a=>{
    const wasStatus=_prevStatuses[a.id];
    const suppressed=_isNotifySuppressed(a.session_project);
    if(wasStatus && wasStatus!==a.status){
      if(a.status==='done'){
        if(!suppressed){
          _playSound('done');
          showToast('done','MISSION COMPLETE',a.name);
          _browserNotify('AOC — Mission Complete', a.name);
          _fireWebhook('done', a);
        }
      }
      if(a.status==='error'){
        if(!suppressed){
          _playSound('error');
          showToast('error','SYSTEM FAILURE',a.error_message||a.name);
          _browserNotify('AOC — System Failure', a.name+(a.error_message?': '+a.error_message.slice(0,60):''));
          _fireWebhook('error', a);
        }
      }
      if(a.status==='running'&&wasStatus&&wasStatus!=='running'){
        if(!suppressed) _playSound('running');
      }
    }
    /* track task progress for stuck detection */
    const prevA=prevAgents[a.id];
    if(prevA){
      const prevDone=((prevA.tasks||[]).filter(t=>t.done).length);
      const curDone=((a.tasks||[]).filter(t=>t.done).length);
      if(curDone>prevDone) _lastProgressAt[a.id]=Date.now();
    }
    if(a.status==='running' && !_lastProgressAt[a.id]){
      _lastProgressAt[a.id]=parseTimeStr(a.started_at)||Date.now();
    }
    /* stuck sound — fire once when agent crosses 5min no-progress threshold */
    if(a.status==='running' && _lastProgressAt[a.id] && !_stuckNotified.has(a.id)){
      if(Date.now()-_lastProgressAt[a.id]>300000){
        _stuckNotified.add(a.id);
        if(!suppressed){ _playSound('stuck'); _fireWebhook('stuck', a); }
      }
    }
    if(a.status!=='running') _stuckNotified.delete(a.id);
    _prevStatuses[a.id]=a.status;
    if(a.status==='done'||a.status==='error') scheduleRemove(a.id, a.status);
  });
  renderConflicts(agents);
}

/* ── dynamic favicon ── */
(function(){
  const _cv=document.createElement('canvas');
  _cv.width=32; _cv.height=32;
  let _lastFavStatus='';
  // Canvas fillStyle can't parse var(--c) directly (it needs a resolved
  // color string), so 'running' reads the live --c-rgb custom property at
  // draw time instead of a hardcoded hex -- this is what makes the favicon
  // track the user's chosen accent color instead of always being cyan.
  function _favColors(status){
    if(status==='running'){
      const rgb=getComputedStyle(document.documentElement).getPropertyValue('--c-rgb').trim() || '0,196,232';
      return {solid:`rgb(${rgb})`, faint:`rgba(${rgb},0.2)`};
    }
    const solid={done:'#00e887',error:'#ff3355',idle:'#1a3050'}[status]||'#1a3050';
    return {solid, faint:solid+'33'};
  }
  function _drawFavicon(status){
    if(status===_lastFavStatus) return;
    _lastFavStatus=status;
    const ctx=_cv.getContext('2d');
    ctx.clearRect(0,0,32,32);
    const {solid:col, faint}=_favColors(status);
    ctx.beginPath();
    for(let i=0;i<6;i++){
      const a=Math.PI/180*(i*60-30);
      const x=16+13*Math.cos(a), y=16+13*Math.sin(a);
      i===0?ctx.moveTo(x,y):ctx.lineTo(x,y);
    }
    ctx.closePath();
    ctx.fillStyle=faint;
    ctx.fill();
    ctx.strokeStyle=col;
    ctx.lineWidth=2;
    ctx.stroke();
    /* center dot */
    ctx.beginPath();
    ctx.arc(16,16,4,0,Math.PI*2);
    ctx.fillStyle=col;
    ctx.fill();
    /* update favicon */
    let link=document.querySelector("link[rel*='icon']");
    if(!link){ link=document.createElement('link'); link.rel='icon'; document.head.appendChild(link); }
    link.href=_cv.toDataURL('image/png');
  }
  function _updateFavicon(){
    if(!lastStatus){ _drawFavicon('idle'); return; }
    const agents=(lastStatus.agents||[]).filter(a=>!String(a.id||'').startsWith('hook_'));
    const running=agents.some(a=>a.status==='running'||a.status==='waiting');
    const errors=agents.some(a=>a.status==='error');
    const done=agents.length>0&&agents.every(a=>a.status==='done');
    _drawFavicon(errors?'error':running?'running':done?'done':'idle');
  }
  setInterval(_updateFavicon,3000);
  _updateFavicon();
})();

/* ── PWA installability ── manifest link is set dynamically (not a static
   <link> tag) so a tunneled/remote session's token rides along into the
   manifest fetch, and from there into the icon fetch and the installed
   icon's start_url -- same "carry the token through every resource URL
   when tunneled" approach already used elsewhere in this app. */
(function(){
  const tokSuffix=window._AOC_TOKEN?('?token='+encodeURIComponent(window._AOC_TOKEN)):'';
  const link=document.createElement('link');
  link.rel='manifest';
  link.href='/manifest.json'+tokSuffix;
  document.head.appendChild(link);
  if('serviceWorker' in navigator){
    navigator.serviceWorker.register('/sw.js'+tokSuffix).catch(()=>{});
  }
})();

/* ── browser notifications ── */
let _notifPermission = (typeof Notification !== 'undefined') ? Notification.permission : 'denied';
function _requestNotifPermission(){
  if(typeof Notification === 'undefined') return;
  if(_notifPermission==='default') Notification.requestPermission().then(p=>{ _notifPermission=p; });
}
function _browserNotify(title, body){
  if(!('Notification' in window) || Notification.permission !== 'granted') return;
  if(!document.hidden) return; /* only when window not focused */
  try{ new Notification(title, {body, silent:false}); }catch(e){}
}

/* ── TERMINAL — multi-session ── */
/* ── TERMINAL: multi-session, grid, rename, completion notifications ── */
const _tSess={};   /* id → {term,ws,fit,cell,xtermEl,title,status,isClaude,badge,busy,lastOut} */
let _tActive=null, _tNext=1, _tXtermLoaded=false, _tGridMode=false;

const _XTERM_OPTS={
  theme:{background:'#0d1117',foreground:'#c8e6ff',cursor:'#00e887',cursorAccent:'#0d1117',
         selection:'rgba(var(--c-rgb),.22)',black:'#0d1117',brightBlack:'#3a4450'},
  fontFamily:"Consolas,'Cascadia Code','Courier New',monospace",
  fontSize:13, lineHeight:1.4, cursorBlink:true, convertEol:true, scrollback:8000,
};

function _initTerm(){
  if(_tXtermLoaded){ if(!_tActive)_termNew(); else{ const s=_tSess[_tActive]; if(s?.fit)s.fit.fit(); } return; }
  const lnk=document.createElement('link');
  lnk.rel='stylesheet';
  lnk.href='https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.min.css';
  document.head.appendChild(lnk);
  const s1=document.createElement('script'); s1.src='https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js';
  s1.onload=()=>{
    const s2=document.createElement('script'); s2.src='https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js';
    s2.onload=()=>{ _tXtermLoaded=true; _termNew(); };
    document.head.appendChild(s2);
  };
  document.head.appendChild(s1);
}

function _termNew(autoCmd, isClaude){
  if(!window.Terminal) return;
  const id='t'+(_tNext++);
  const titleDefault=isClaude?'◈ claude':'shell '+(_tNext-1);

  /* cell wrapper (single mode: fills area; grid mode: grid cell) */
  const cell=document.createElement('div');
  cell.className='tg-cell';

  /* mini header (visible only in grid mode) */
  const hdr=document.createElement('div');
  hdr.className='tg-hdr'+(isClaude?' claude-hdr':'');
  hdr.innerHTML=`<span class="tg-title" ondblclick="_termRenameInline('${id}',this)" title="Double-click to rename">${escHtml(titleDefault)}</span>
    <button onclick="_termClose('${id}',event)" style="background:none;border:none;cursor:pointer;color:var(--t3);font-size:11px;padding:0;line-height:1;opacity:.5" title="Close">×</button>`;
  cell.appendChild(hdr);

  /* xterm inner container */
  const xtermEl=document.createElement('div');
  xtermEl.className='tg-inner';
  cell.appendChild(xtermEl);

  const area=document.getElementById('xterm-area');
  if(_tGridMode){
    /* grid mode: all cells always visible */
    cell.classList.add('grid-cell');
    if(isClaude) cell.classList.add('tg-claude');
    hdr.style.display='flex';
    area.appendChild(cell);
  } else {
    /* single mode: hide others, show this one full-size */
    Object.values(_tSess).forEach(s=>{ s.cell.style.display='none'; });
    cell.style.cssText='display:flex;flex-direction:column;flex:1;min-height:0;';
    area.appendChild(cell);
  }

  const term=new Terminal(_XTERM_OPTS);
  const fit=new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(xtermEl);
  term.focus();
  fit.fit();
  new ResizeObserver(()=>{ if(currentView==='term'&&(_tGridMode||_tActive===id)){try{fit.fit();}catch(_){}} }).observe(xtermEl);

  /* wss:// required when the page itself loaded over https (Cloudflare tunnel) —
     browsers block insecure ws:// from a secure origin. The token must ride along
     as a query param since the WebSocket API can't set custom headers. */
  const _wsProto=location.protocol==='https:'?'wss':'ws';
  const _wsAuth=window._AOC_TOKEN?('?token='+encodeURIComponent(window._AOC_TOKEN)):'';
  const ws=new WebSocket(`${_wsProto}://${location.host}/terminal/ws${_wsAuth}`);
  ws.binaryType='arraybuffer';
  const sess={term,ws,fit,cell,xtermEl,title:titleDefault,status:'connecting',isClaude:!!isClaude,badge:0,busy:false,lastOut:0};
  _tSess[id]=sess;
  _tActive=id;
  _renderTermTabs();

  ws.onopen=()=>{
    sess.status='connected'; _renderTermTabs();
    term.writeln(isClaude
      ?'\x1b[32m◈ Starting Claude CLI...\x1b[0m\r\n'
      :'\x1b[32m✓ Git Bash — type \x1b[96mclaude\x1b[32m for a new Claude\x1b[0m\r\n');
    if(autoCmd) setTimeout(()=>{ if(ws.readyState===1) ws.send(autoCmd+'\r'); }, 1200);
  };
  ws.onmessage=e=>{
    if(!_tSess[id]) return;
    const d=e.data instanceof ArrayBuffer?new Uint8Array(e.data):e.data;
    term.write(d);
    /* completion detection: track idle → busy → idle transitions */
    sess.lastOut=Date.now();
    if(!sess.busy) sess.busy=true;
  };
  ws.onclose=()=>{ sess.status='closed'; _renderTermTabs(); term.writeln('\r\n\x1b[33m[Closed]\x1b[0m'); };
  ws.onerror=()=>{ sess.status='error'; _renderTermTabs(); };
  term.onData(d=>{ if(ws.readyState===1)ws.send(d); sess.lastOut=Date.now(); });
  term.onResize(({rows,cols})=>{ if(ws.readyState===1)ws.send('\x00resize:'+rows+':'+cols); });
}

/* Periodic check: if a session was busy and went idle for >1.5s → task done notification */
setInterval(()=>{
  const now=Date.now();
  Object.entries(_tSess).forEach(([id,s])=>{
    if(s.busy && now-s.lastOut>1500){
      s.busy=false;
      /* only notify if this session is not the active one OR we're on a different view */
      if(id!==_tActive || currentView!=='term'){
        s.badge++;
        showToast('info','Terminal: \x22'+s.title+'\x22 — command finished');
        _renderTermTabs();
      }
    }
  });
},500);

function _termSwitch(id){
  const s=_tSess[id]; if(!s) return;
  s.badge=0;  /* clear badge on switch */
  if(_tGridMode){
    /* in grid mode just change focus highlight */
    Object.values(_tSess).forEach(ss=>ss.cell.classList.remove('tg-focused'));
    s.cell.classList.add('tg-focused');
    _tActive=id;
    s.term.focus();
    _renderTermTabs();
    return;
  }
  Object.entries(_tSess).forEach(([sid,ss])=>{ ss.cell.style.display=sid===id?'flex':'none'; });
  _tActive=id;
  setTimeout(()=>{ s.fit.fit(); s.term.focus(); },50);
  _renderTermTabs();
}

function _termClose(id, e){
  if(e) e.stopPropagation();
  const s=_tSess[id]; if(!s) return;
  try{s.ws.close();}catch(_){}
  try{s.term.dispose();}catch(_){}
  s.cell.remove(); delete _tSess[id];
  const ids=Object.keys(_tSess);
  if(_tActive===id) _tActive=ids.length?ids[ids.length-1]:null;
  if(_tGridMode){
    /* in grid mode: refit all remaining terminals after DOM reflow */
    setTimeout(()=>{ Object.values(_tSess).forEach(ss=>{try{ss.fit.fit();}catch(_){}}); },80);
    _renderTermTabs();
  } else {
    if(_tActive) _termSwitch(_tActive); else _renderTermTabs();
  }
}

function _termRename(id){
  const s=_tSess[id]; if(!s) return;
  const nv=prompt('Rename terminal:',s.title);
  if(nv!==null&&nv.trim()){
    s.title=nv.trim();
    const hdrTitle=s.cell.querySelector('.tg-title');
    if(hdrTitle) hdrTitle.textContent=s.title;
    _renderTermTabs();
  }
}

function _termRenameInline(id, el){
  const s=_tSess[id]; if(!s) return;
  const old=s.title;
  el.contentEditable='true'; el.focus();
  const sel=window.getSelection(); const rng=document.createRange();
  rng.selectNodeContents(el); sel.removeAllRanges(); sel.addRange(rng);
  const done=()=>{
    el.contentEditable='false';
    const nv=el.textContent.trim()||old;
    s.title=nv; el.textContent=nv;
    _renderTermTabs();
  };
  el.onblur=done;
  el.onkeydown=ev=>{ if(ev.key==='Enter'||ev.key==='Escape'){ev.preventDefault();el.blur();} };
}

function _termToggleGrid(){
  _tGridMode=!_tGridMode;
  const area=document.getElementById('xterm-area');
  if(_tGridMode){
    area.classList.add('grid-mode');
    /* show all cells as grid cells */
    Object.entries(_tSess).forEach(([id,s])=>{
      s.cell.style.cssText='';
      s.cell.classList.add('grid-cell');
      if(s.isClaude) s.cell.classList.add('tg-claude');
      s.cell.querySelector('.tg-hdr').style.display='flex';
    });
    if(_tActive&&_tSess[_tActive]) _tSess[_tActive].cell.classList.add('tg-focused');
    setTimeout(()=>{ Object.values(_tSess).forEach(s=>{try{s.fit.fit();}catch(_){}}); },120);
  } else {
    area.classList.remove('grid-mode');
    Object.entries(_tSess).forEach(([id,s])=>{
      s.cell.classList.remove('grid-cell','tg-claude','tg-focused');
      s.cell.querySelector('.tg-hdr').style.display='none';
      s.cell.style.cssText=id===_tActive?'display:flex;flex-direction:column;flex:1;min-height:0;':'display:none;';
    });
    if(_tActive&&_tSess[_tActive]) setTimeout(()=>{ _tSess[_tActive].fit.fit(); _tSess[_tActive].term.focus(); },120);
  }
  _renderTermTabs();
}

function _renderTermTabs(){
  const bar=document.getElementById('term-tabs'); if(!bar) return;
  const sIcon={connected:'●',closed:'○',connecting:'◌',error:'✕'};
  const sCol ={connected:'var(--g)',closed:'var(--t3)',connecting:'var(--o)',error:'var(--r)'};
  bar.innerHTML=Object.entries(_tSess).map(([id,s])=>`
    <div class="t-tab${id===_tActive?' active':''}${s.isClaude?' claude-tab':''}" onclick="_termSwitch('${id}')">
      <span style="color:${sCol[s.status]||'var(--t3)'};font-size:9px">${sIcon[s.status]||'?'}</span>
      <span ondblclick="_termRename('${id}')" title="Double-click to rename">${escHtml(s.title)}</span>
      ${s.badge?`<span class="t-badge">${s.badge}</span>`:''}
      <button class="t-close" onclick="_termClose('${id}',event)" title="Close">×</button>
    </div>`).join('')+
  `<button class="t-tab-add" onclick="_termNew()" title="New shell">+</button>
   <button class="t-grid-btn${_tGridMode?' active':''}" onclick="_termToggleGrid()" title="${_tGridMode?'Single view':'Grid view (3×3)'}">${_tGridMode?'▣':'⊞'}</button>`;
}

/* public API */
function _termNew_pub(){ _termNew(); }
function _termClaude(){
  /* full path from /status so bash finds claude regardless of its PATH */
  const bin=(lastStatus&&lastStatus.claude_bin)||'claude';
  _termNew(bin, true);
}
function _termClearActive(){
  const s=_tActive&&_tSess[_tActive]; if(!s) return;
  /* send cls to shell to clear its viewport, then clear xterm scrollback */
  if(s.ws.readyState===1) s.ws.send('clear\r');
  setTimeout(()=>{ try{s.term.clear();}catch(_){} },150);
}
function _termKillActive(){ if(_tActive) _termClose(_tActive); }

/* git status in toolbar */
function _termUpdateGit(){
  /* session_project is just basename(cwd) (see /update's cwd_name derivation in
     monitor.py) -- not a real filesystem path, so passing it to /git's `git -C`
     always failed silently (empty branch). The real path lives on the matching
     entry in sessions_list, not on the agent object itself. */
  const runningAgent=(lastStatus?.agents||[]).find(a=>a.status==='running');
  const sess=runningAgent&&(lastStatus?.sessions_list||[]).find(s=>s.id===runningAgent.session_id);
  const cwd=sess?.cwd||null;
  if(!cwd){ const el=document.getElementById('term-git'); if(el)el.textContent=''; return; }
  fetch('/git?cwd='+encodeURIComponent(cwd)).then(r=>r.json()).then(d=>{
    const el=document.getElementById('term-git'); if(!el)return;
    if(d.branch) el.textContent='⎇ '+d.branch+(d.changes?' ['+d.changes+']':'');
    else el.textContent='';
  }).catch(()=>{});
}

/* ── boot ── */
const boot=[
  ['INITIALIZING A.O.C INTERFACE...','info'],
  ['CONNECTING TO ORCHESTRATOR...','info'],
  ['WAITING FOR AGENT STATUS...','info'],
  ['REAL-TIME AGENT TRACKING ENABLED','success'],
];
boot.forEach(([m,t],i)=>setTimeout(()=>log(m,t),i*250));
_loadPrefs();
// Unconditional (not just inside _loadPrefs' own `if(p.theme)` branch) so
// a first-run OS-detected _theme actually reaches the DOM (body class +
// toggle button icon) even when there's no saved preference to load yet
// -- without this call, a light-OS user's first visit would still
// render dark, since nothing else ever applies the initial value.
_applyTheme();
_requestNotifPermission();
_loadSoundPrefs();
_connectEvents();
let _gitLastPoll=0;
setInterval(()=>{ if(currentView==='term'&&Date.now()-_gitLastPoll>10000){ _gitLastPoll=Date.now(); _termUpdateGit(); } },5000);
renderAuditLog();

/* ── agent detail panel ── */
let _adpAgentId = null;
let _adpTab = 'log';
let _adpCopyData = '';

function openAgentDetail(id, event){
  if(event) event.stopPropagation();
  _adpAgentId = id;
  _adpTab = 'log';
  const ov = document.getElementById('agent-detail-overlay');
  if(ov) ov.classList.add('open');
  ['log','tasks','files'].forEach(t=>{
    const el=document.getElementById('adpt-'+t);
    if(el) el.classList.toggle('active', t==='log');
  });
  _renderAdp();
}

function closeAgentDetail(){
  const ov = document.getElementById('agent-detail-overlay');
  if(ov) ov.classList.remove('open');
  _adpAgentId = null;
}

function setAdpTab(tab){
  _adpTab = tab;
  ['log','tasks','files'].forEach(t=>{
    const el=document.getElementById('adpt-'+t);
    if(el){ el.classList.toggle('active', t===tab); el.setAttribute('aria-selected',t===tab); }
  });
  _renderAdpBody();
}

function _renderAdp(){
  if(!_adpAgentId || !lastStatus) return;
  const a=(lastStatus.agents||[]).find(ag=>ag.id===_adpAgentId);
  if(!a){ closeAgentDetail(); return; }
  const col=sCol(a.status);
  const stLabel=stxt(a);
  let elapsedStr='';
  if(a.started_at){
    const startMs=parseTimeStr(a.started_at);
    let endMs=Date.now();
    if(a.status!=='running'&&a.completed_at) endMs=parseTimeStr(a.completed_at);
    const secs=Math.max(0,Math.floor((endMs-startMs)/1000));
    elapsedStr=secs<60?secs+'s':Math.floor(secs/60)+'m '+(secs%60)+'s';
  }
  // input/output/cache_read/cache_write_tokens are all tracked per agent
  // (same fields the session-card tooltip already surfaces at the session
  // level) but only their sum (tokens_used) was ever shown here -- cache
  // tokens matter for cost, so break it down in a hover tooltip on the
  // existing cost badge. Only present on the hook's tool_response when it
  // happens to include usage+resolvedModel (not guaranteed every time), so
  // check for actual presence rather than defaulting to 0 -- otherwise a
  // missing breakdown would misleadingly render as "Input: 0 · Output: 0 …"
  // instead of just omitting the tooltip. The 4 fields are always set
  // together as one group (see aoc_hook.py's usage_fields / monitor.py's
  // _apply_agent_update), so checking one is enough.
  const hasTokBreakdown=a.input_tokens!=null;
  const tokBreakdownParts=[];
  if(hasTokBreakdown) tokBreakdownParts.push(_tokBreakdownStr(a));
  // tool_use_count is a separate signal from tokens_used (a high token
  // count with few tool calls behaves very differently from a low count
  // with many) and isn't gated behind the same usage+resolvedModel
  // condition as the breakdown above, so it's tracked independently.
  if(a.tool_use_count!=null) tokBreakdownParts.push(`${a.tool_use_count} tool call${a.tool_use_count!==1?'s':''}`);
  const tokBreakdown=tokBreakdownParts.join(' · ');
  const tokStr=a.tokens_used?`<span style="font-size:10px;color:rgba(0,210,130,.8);font-family:var(--font2)"${tokBreakdown?` title="${escHtml(tokBreakdown)}"`:''}>$${_agentCost(a).toFixed(4)}</span>`:'';
  // token_limit already draws a context-window progress bar on the agent
  // card (renderAgents' tokensHtml) but was never surfaced here -- same
  // field, compact badge form for the header row instead of a full bar.
  const ctxPct=_ctxPct(a);
  const ctxStr=ctxPct!==null?`<span style="font-size:9px;color:var(--t3);font-family:var(--font2)" title="Context window used: ${Number(a.tokens_used).toLocaleString()} / ${Number(a.token_limit).toLocaleString()} tokens">${ctxPct}% ctx</span>`:'';
  // Raw model string, same truncate-with-title-tooltip treatment as the
  // COST BY MODEL bar labels in History -> Analytics -- this is the one
  // model field on an agent, it was tracked and costed server-side but
  // never actually shown anywhere on the live agent itself until now.
  const modelStr=a.model?`<span style="font-size:9px;color:var(--t3);font-family:var(--font2);max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(a.model)}">${escHtml(a.model)}</span>`:'';
  // subagent_type (e.g. "general-purpose", "Explore", "Plan") was sitting
  // in tool_input at hook registration time but never captured before --
  // the only way to tell agents apart was their free-text description.
  const subagentTypeStr=a.subagent_type?`<span style="font-size:9px;color:rgba(var(--c-rgb),.75);font-family:var(--font2);background:rgba(var(--c-rgb),.08);border-radius:4px;padding:1px 6px" title="Subagent type">${escHtml(a.subagent_type)}</span>`:'';
  // detected_via==='transcript' (this agent's PreToolUse hook never fired,
  // caught by the transcript fallback instead) already surfaces as a
  // HOOK MISS badge on the card, the AGENTS-tab row, SUMMARY, Compare,
  // the GRAPH/TREE tooltip and History Detail -- this panel was the one
  // per-agent surface still missing it.
  const hookMissStr=a.detected_via==='transcript'?`<span class="hookmiss-badge" title="${escHtml(_hookMissTitle(a))}">⚠ HOOK MISS</span>`:'';
  // parent_id (this agent was itself spawned by another subagent, see
  // TREE view / _extract_child_agent_ids) was only ever consumed by
  // _buildTree's own layout code -- nowhere else told you which agent
  // spawned this one. Clickable straight to the parent's own detail
  // panel, same pattern as the rest of this header's badges.
  const parentAgent=_resolveParentAgent(a);
  const parentStr=parentAgent?`<span style="font-size:9px;color:var(--t3);font-family:var(--font2);cursor:pointer" onclick="openAgentDetail('${escHtml(parentAgent.id)}',null)" title="Spawned by this agent — click to open">↳ ${escHtml(parentAgent.name)}</span>`:'';
  // Reverse direction of the same thread -- TREE already draws a childCount
  // badge (${childCount}↓) on a parent node, but nothing told you that from
  // an orchestrator agent's own detail panel.
  const childAgents=_resolveChildAgents(a);
  const childStr=childAgents.length?`<span style="font-size:9px;color:var(--t3);font-family:var(--font2);cursor:pointer" onclick="openAgentDetail('${escHtml(childAgents[0].id)}',null)" title="${escHtml(childAgents.map(c=>c.name).join(', '))}">spawned ${childAgents.length} subagent${childAgents.length!==1?'s':''} →</span>`:'';
  // Same _stuckSecs() signal the card already showed (>300s since this
  // running agent's last task completion) -- the detail panel, the surface
  // you'd actually click into to investigate why an agent looks frozen,
  // never carried it.
  const stuckSecs=_stuckSecs(a);
  const stuckStr=stuckSecs>300?`<div class="stuck-badge">⚠ STUCK — no progress for ${stuckSecs<3600?Math.floor(stuckSecs/60)+'m':Math.floor(stuckSecs/3600)+'h'}</div>`:'';
  const isRunning=a.status==='running'||a.status==='waiting';
  const liveBadge=isRunning?`<div class="live-badge" style="font-size:9px;letter-spacing:.07em;margin-left:auto;margin-right:4px"><div class="dot"></div>LIVE</div>`:'';
  const tasks=a.tasks||[];
  const activeTask=isRunning?tasks.find(t=>!t.done):null;
  const activeTaskHtml=activeTask?`<div style="display:flex;align-items:center;gap:6px;margin-top:6px;padding:5px 9px;border-radius:7px;background:rgba(var(--c-rgb),.06);border:1px solid rgba(var(--c-rgb),.18)">
    <span style="font-size:9px;font-family:var(--font2);color:rgba(var(--c-rgb),.7);letter-spacing:.09em;flex-shrink:0">► NOW</span>
    <span style="font-size:11px;color:var(--c);font-family:var(--font2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(activeTask.label||'')}</span>
  </div>`:'';
  const hdrEl=document.getElementById('adp-hdr');
  if(hdrEl) hdrEl.innerHTML=`
    <div class="unit-badge" style="border-color:${col};color:${col};background:rgba(var(--c-rgb),.07);margin-top:2px">${_deriveUnit(a)}</div>
    <div style="flex:1;min-width:0">
      <div style="font-size:13px;font-weight:700;color:${col};margin-bottom:3px;line-height:1.3">${escHtml(a.name)}</div>
      ${a.description?`<div style="font-size:11px;color:var(--t2);line-height:1.45;margin-bottom:5px">${escHtml(a.description)}</div>`:''}
      <div style="display:flex;align-items:center;gap:7px;flex-wrap:wrap">
        <div class="status-pill ${a.status}" style="font-size:9px;padding:2px 7px"><span class="sdot"></span>${stLabel}</div>
        ${elapsedStr?`<span style="font-size:10px;font-family:var(--font2);color:var(--t3)">${elapsedStr}</span>`:''}
        ${tokStr}
        ${ctxStr}
        ${modelStr}
        ${subagentTypeStr}
        ${hookMissStr}
        ${parentStr}
        ${childStr}
      </div>
      ${stuckStr}
      ${activeTaskHtml}
    </div>
    ${liveBadge}
    <button class="adp-btn" onclick="exportAgentDetail()" title="Export agent report to clipboard [E]" style="padding:3px 10px;font-size:9px;margin-right:4px;flex-shrink:0">⎘ MD</button>
    <button class="adp-close" onclick="closeAgentDetail()" aria-label="Close">✕</button>
  `;
  const logCount=(a.log||[]).length;
  const taskCount=(a.tasks||[]).length;
  const fileCount=(a.files_changed||[]).length;
  const lt=document.getElementById('adpt-log'); if(lt) lt.textContent=`LOG (${logCount})`;
  const tt=document.getElementById('adpt-tasks'); if(tt) tt.textContent=`TASKS (${taskCount})`;
  const ft=document.getElementById('adpt-files'); if(ft) ft.textContent=`FILES (${fileCount})`;
  _renderAdpBody();
}

function _renderAdpBody(){
  if(!_adpAgentId || !lastStatus) return;
  const a=(lastStatus.agents||[]).find(ag=>ag.id===_adpAgentId);
  if(!a) return;
  const bodyEl=document.getElementById('adp-body');
  if(!bodyEl) return;
  const scrollTop=bodyEl.scrollTop;

  if(_adpTab==='log'){
    const entries=a.log||[];
    _adpCopyData=entries.join('\n');
    if(!entries.length){
      bodyEl.innerHTML='<div style="color:var(--t3);font-family:var(--font2);font-size:11px;text-align:center;padding:50px 0;letter-spacing:.08em">// no log entries</div>';
      return;
    }
    bodyEl.innerHTML=`<div class="adp-sec">LOG MESSAGES<button class="adp-btn" onclick="copyText(this,_adpCopyData)">COPY ALL</button></div>`+
      entries.slice().reverse().map(e=>`<div class="adp-log-line">${escHtml(String(e))}</div>`).join('');
  } else if(_adpTab==='tasks'){
    const tasks=a.tasks||[];
    const done=tasks.filter(t=>t.done).length;
    if(!tasks.length){
      bodyEl.innerHTML='<div style="color:var(--t3);font-family:var(--font2);font-size:11px;text-align:center;padding:50px 0;letter-spacing:.08em">// no tasks defined</div>';
      return;
    }
    const errHtml=(a.status==='error'&&a.error_message)?`<div style="margin-bottom:8px;padding:7px 10px;border-radius:8px;background:rgba(255,51,85,.06);border:1px solid rgba(255,51,85,.2);border-left:3px solid var(--r)"><div style="font-size:9px;font-family:var(--font2);color:var(--r);letter-spacing:.08em;margin-bottom:3px">ERROR DETAIL</div><div style="font-size:11px;color:rgba(255,120,140,.85);font-family:var(--font2);line-height:1.5;word-break:break-all">${escHtml(a.error_message)}</div></div>`:'';
    bodyEl.innerHTML=`<div class="adp-sec">TASKS — ${done}/${tasks.length} COMPLETE</div>`+errHtml+
      tasks.map(t=>`<div class="adp-task-row ${t.done?'done':'pend'}">
        <span style="flex-shrink:0">${t.done?'✓':'·'}</span>
        <span style="flex:1">${escHtml(t.label||'')}</span>
        ${t.completed_at?`<span style="font-size:9px;font-family:var(--font2);color:var(--t3);flex-shrink:0">${t.completed_at}</span>`:''}
      </div>`).join('');
  } else if(_adpTab==='files'){
    const files=a.files_changed||[];
    if(!files.length){
      bodyEl.innerHTML='<div style="color:var(--t3);font-family:var(--font2);font-size:11px;text-align:center;padding:50px 0;letter-spacing:.08em">// no file changes</div>';
      return;
    }
    bodyEl.innerHTML=`<div class="adp-sec">FILES CHANGED — ${files.length}</div>`+
      files.map(f=>`<div class="fe ${f.type==='new'?'new':'changed'}" onclick="showDiff('${escHtml(f.path.replace(/'/g,"\\'"))}');closeAgentDetail()" style="cursor:pointer" title="Click for diff — closes panel">
        <span class="fe-badge">${f.type==='new'?'NEW':'MOD'}</span>
        <span class="fe-name" title="${escHtml(f.path)}">${escHtml(f.path)}</span>
        ${f.lines?`<span class="fe-lines">${f.lines}L</span>`:''}
      </div>`).join('');
  }

  bodyEl.scrollTop=scrollTop;
}

/* Builds one agent's Markdown block -- shared by exportAgentDetail (a
   standalone report, headingPrefix defaults to its original "# AGENT: "
   H1) and exportSessionDetail (embedded under a session's own H1, passes
   "### " instead) so this formatting logic isn't duplicated a third
   time. The internal Tasks/Error/Files/Log sub-headings stay fixed at
   "## " either way -- keeps exportAgentDetail's own output byte-for-byte
   unchanged after this refactor, at the minor cost of those sub-sections
   not perfectly nesting under a session doc's own headings. */
// parentName is pre-resolved by the caller (exportAgentDetail/
// exportSessionDetail, where the full agents list is in scope) rather
// than looked up in here off a.parent_id -- this function stays a pure
// function of its own arguments, same as every other field it reads,
// so it's testable in isolation without needing lastStatus defined.
function _agentToMarkdown(a,headingPrefix='# AGENT: ',parentName=null,childNames=null){
  const tasksDone=(a.tasks||[]).filter(t=>t.done).length;
  const tasksTotal=(a.tasks||[]).length;
  const pct=tasksTotal>0?Math.round(tasksDone/tasksTotal*100):0;
  const cost=a.tokens_used?_agentCost(a).toFixed(4):null;
  const stLabel=stxt(a);
  let md=`${headingPrefix}${a.name}\n`;
  md+=`**Status:** ${stLabel}\n`;
  if(a.session_project) md+=`**Project:** ${a.session_project}\n`;
  if(a.started_at) md+=`**Started:** ${a.started_at}\n`;
  if(a.completed_at) md+=`**Completed:** ${a.completed_at}\n`;
  if(a.started_at){
    const startMs=parseTimeStr(a.started_at);
    let endMs=Date.now();
    if(a.status!=='running'&&a.completed_at) endMs=parseTimeStr(a.completed_at);
    const secs=Math.max(0,Math.floor((endMs-startMs)/1000));
    md+=`**Duration:** ${secs<60?secs+'s':Math.floor(secs/60)+'m '+(secs%60)+'s'}\n`;
  }
  if(a.tokens_used) md+=`**Tokens:** ${Number(a.tokens_used).toLocaleString()}${cost?' ($'+cost+')':''}\n`;
  if(a.token_limit&&a.tokens_used) md+=`**Context used:** ${_ctxPct(a)}% (${Number(a.tokens_used).toLocaleString()} / ${Number(a.token_limit).toLocaleString()})\n`;
  if(a.model) md+=`**Model:** ${a.model}\n`;
  if(a.subagent_type) md+=`**Subagent type:** ${a.subagent_type}\n`;
  if(a.tool_use_count!=null) md+=`**Tool calls:** ${a.tool_use_count}\n`;
  if(a.detected_via==='transcript') md+=`**Detected via:** transcript fallback (hook miss)${a.concurrent_sessions!=null?` — ${a.concurrent_sessions} other session${a.concurrent_sessions!==1?'s':''} active at the time`:''}\n`;
  const stuckSecs=_stuckSecs(a);
  if(stuckSecs>300) md+=`**Stuck:** no progress for ${stuckSecs<3600?Math.floor(stuckSecs/60)+'m':Math.floor(stuckSecs/3600)+'h'}\n`;
  if(parentName) md+=`**Spawned by:** ${parentName}\n`;
  if(childNames&&childNames.length) md+=`**Spawned subagents:** ${childNames.join(', ')}\n`;
  if(a.description) md+=`**Description:** ${a.description}\n`;
  md+='\n';
  if(tasksTotal){
    md+=`## Tasks (${tasksDone}/${tasksTotal} — ${pct}%)\n`;
    (a.tasks||[]).forEach(t=>{
      md+=`- [${t.done?'x':' '}] ${t.label||''}`;
      if(t.completed_at) md+=` _(${t.completed_at})_`;
      md+='\n';
    });
    md+='\n';
  }
  if(a.status==='error'&&a.error_message){
    md+=`## Error\n\`\`\`\n${a.error_message}\n\`\`\`\n\n`;
  }
  const files=a.files_changed||[];
  if(files.length){
    md+=`## Files Changed (${files.length})\n`;
    files.forEach(f=>{ md+=`- ${f.type==='new'?'NEW':'MOD'} \`${f.path}\`${f.lines?' ('+f.lines+'L)':''}\n`; });
    md+='\n';
  }
  const logEntries=a.log||[];
  if(logEntries.length){
    md+=`## Log (${logEntries.length} entries)\n\`\`\`\n`;
    logEntries.forEach(e=>{ md+=String(e)+'\n'; });
    md+='```\n';
  }
  return md;
}
function _copyMarkdownToClipboard(md,label){
  const _doToast=()=>showToast('done','EXPORTED',label+' copied to clipboard');
  navigator.clipboard.writeText(md).then(_doToast).catch(()=>{
    const ta=document.createElement('textarea');
    ta.value=md; ta.style.cssText='position:fixed;top:-9999px;left:-9999px';
    document.body.appendChild(ta); ta.select(); document.execCommand('copy');
    document.body.removeChild(ta); _doToast();
  });
}
function exportAgentDetail(){
  if(!_adpAgentId || !lastStatus) return;
  const a=(lastStatus.agents||[]).find(ag=>ag.id===_adpAgentId);
  if(!a) return;
  const parent=_resolveParentAgent(a);
  const children=_resolveChildAgents(a).map(ag=>ag.name);
  _copyMarkdownToClipboard(_agentToMarkdown(a,undefined,parent?.name,children),'Agent report');
}
function exportSessionDetail(sessionId){
  if(!lastStatus) return;
  const sess=(lastStatus.sessions_list||[]).find(s=>s.id===sessionId);
  const agents=(lastStatus.agents||[]).filter(a=>a.session_id===sessionId);
  const label=sess?(sess.display_name||sess.project||sessionId.slice(-6)):sessionId.slice(-6);
  const tasksDone=agents.reduce((s,a)=>s+((a.tasks||[]).filter(t=>t.done).length),0);
  const tasksTotal=agents.reduce((s,a)=>s+((a.tasks||[]).length),0);
  const totalTokens=agents.reduce((s,a)=>s+(a.tokens_used||0),0);
  const totalCost=agents.reduce((s,a)=>s+_agentCost(a),0);
  let md=`# SESSION: ${label}\n`;
  if(sess){
    if(sess.project) md+=`**Project:** ${sess.project}\n`;
    if(sess.git_branch) md+=`**Branch:** ${sess.git_branch}\n`;
    if(sess.cc_version) md+=`**CC Version:** ${sess.cc_version}\n`;
    if(sess.note) md+=`**Note:** ${sess.note}\n`;
  }
  md+=`**Agents:** ${agents.length}\n`;
  if(tasksTotal) md+=`**Tasks:** ${tasksDone}/${tasksTotal}\n`;
  if(totalTokens>0) md+=`**Tokens:** ${Number(totalTokens).toLocaleString()} (~$${totalCost.toFixed(4)})\n`;
  md+='\n';
  if(agents.length){
    md+=`## Agents\n\n`;
    agents.forEach(a=>{
      const parent=_resolveParentAgent(a);
      const children=_resolveChildAgents(a).map(ag=>ag.name);
      md+=_agentToMarkdown(a,'### ',parent?.name,children)+'\n';
    });
  }else{
    md+='_No agents recorded for this session._\n';
  }
  _copyMarkdownToClipboard(md,'Session report');
}

/* ── notification history panel ── */
function _updateNotifBadge(){
  const badge=document.getElementById('notif-badge');
  if(!badge) return;
  if(_notifUnread>0){
    badge.style.display='flex';
    badge.textContent=_notifUnread>99?'99+':String(_notifUnread);
  } else {
    badge.style.display='none';
  }
}

function _renderNotifPanel(){
  const listEl=document.getElementById('notif-list');
  const cntEl=document.getElementById('notif-hdr-count');
  if(!listEl) return;
  const n=_notifHistory.length;
  if(cntEl) cntEl.textContent=n?`${n} event${n!==1?'s':''}`:' ';
  if(!n){
    listEl.innerHTML='<div style="text-align:center;padding:60px 20px;color:var(--t3);font-family:var(--font2);font-size:11px;letter-spacing:.08em">// no events yet</div>';
    return;
  }
  listEl.innerHTML=[..._notifHistory].reverse().map(e=>
    `<div class="notif-entry ${e.type}">
      <span class="notif-entry-time">${e.t}</span>
      ${e.tag?`<span class="notif-entry-tag">${escHtml(e.tag)}</span>`:''}
      <span class="notif-entry-msg">${escHtml(e.msg)}</span>
    </div>`
  ).join('');
}

function openNotifPanel(){
  _notifOpen=true;
  _notifUnread=0;
  _updateNotifBadge();
  const ov=document.getElementById('notif-overlay');
  if(ov) ov.classList.add('open');
  _renderNotifPanel();
}

function closeNotifPanel(){
  _notifOpen=false;
  const ov=document.getElementById('notif-overlay');
  if(ov) ov.classList.remove('open');
}

function clearNotifHistory(){
  _notifHistory=[];
  _notifUnread=0;
  _updateNotifBadge();
  _renderNotifPanel();
}

/* ── heatmap view ── */
let _heatMode = 'tasks'; /* 'tasks' | 'tokens' */
let _heatBinSec = 30;

function _heatBinOptions(rangeSec){
  if(rangeSec<=120) return [15,30];
  if(rangeSec<=600) return [30,60];
  if(rangeSec<=1800) return [60,120];
  return [120,300];
}

function renderHeatmap(data){
  const el=document.getElementById('heat-area');
  if(!el) return;
  const agents=(data.agents||[]).filter(a=>!String(a.id||'').startsWith('hook_')&&a.started_at);
  if(!agents.length){
    el.innerHTML='<div style="color:var(--t3);text-align:center;padding:60px;font-family:var(--font2);font-size:10px;letter-spacing:.12em">NO AGENT DATA</div>';
    return;
  }
  const now=Date.now();
  let minT=Infinity, maxT=-Infinity;
  agents.forEach(a=>{
    const s=parseTimeStr(a.started_at); if(s&&s<minT) minT=s;
    const e=parseTimeStr(a.completed_at)||now; if(e>maxT) maxT=e;
  });
  maxT=Math.max(maxT,now);
  const rangeSec=Math.max((maxT-minT)/1000,60);

  /* auto-pick bin size if needed */
  const binOptions=_heatBinOptions(rangeSec);
  if(!binOptions.includes(_heatBinSec)) _heatBinSec=binOptions[0];
  const binMs=_heatBinSec*1000;
  const totalBins=Math.min(Math.ceil((maxT-minT)/binMs),80);
  const effectiveBinMs=Math.ceil((maxT-minT)/totalBins);

  /* per-agent per-bin values */
  const rows=agents.map(a=>{
    const sT=parseTimeStr(a.started_at)||minT;
    const eT=parseTimeStr(a.completed_at)||(a.status==='running'||a.status==='waiting'?now:sT+5000);
    const bins=[];
    for(let b=0;b<totalBins;b++){
      const bStart=minT+b*effectiveBinMs;
      const bEnd=bStart+effectiveBinMs;
      const active=sT<bEnd&&eT>bStart;
      if(!active){ bins.push({v:0,active:false,tasks:[]}); continue; }
      if(_heatMode==='tasks'){
        const tasksDone=(a.tasks||[]).filter(t=>{
          if(!t.done||!t.completed_at) return false;
          const tt=parseTimeStr(t.completed_at);
          return tt&&tt>=bStart&&tt<bEnd;
        });
        bins.push({v:tasksDone.length,active:true,tasks:tasksDone.map(t=>t.label||'task')});
      } else {
        /* tokens: approximate proportional to time overlap */
        const overlap=Math.min(eT,bEnd)-Math.max(sT,bStart);
        const dur=Math.max(eT-sT,1);
        const tokEst=Math.round((a.tokens_used||0)*(overlap/dur));
        bins.push({v:tokEst,active:true,tasks:[]});
      }
    }
    return {a,bins,sT,eT};
  });

  /* compute max value across all bins for normalization */
  const allVals=rows.flatMap(r=>r.bins.map(b=>b.v));
  const maxVal=Math.max(...allVals,1);

  /* cell color function */
  function cellColor(b){
    if(!b.active) return 'transparent';
    if(b.v===0) return 'rgba(255,255,255,.04)';
    const t=b.v/maxVal;
    if(_heatMode==='tasks'){
      const a=0.15+t*0.85;
      return `rgba(var(--c-rgb),${a.toFixed(2)})`;
    } else {
      const a=0.12+t*0.85;
      return `rgba(0,232,135,${a.toFixed(2)})`;
    }
  }

  /* axis labels: show every Nth bin */
  const labelEvery=Math.max(1,Math.ceil(totalBins/10));
  const axisLabels=Array.from({length:totalBins},(_,b)=>{
    if(b%labelEvery!==0) return '';
    const t=new Date(minT+b*effectiveBinMs);
    return `${String(t.getHours()).padStart(2,'0')}:${String(t.getMinutes()).padStart(2,'0')}`;
  });

  /* bin size selector */
  const binOptHtml=binOptions.map(s=>`<button class="heat-mode-btn${_heatBinSec===s?' active':''}" onclick="_heatSetBin(${s})">${s<60?s+'s':Math.round(s/60)+'m'}</button>`).join('');
  const modeHtml=`
    <button class="heat-mode-btn${_heatMode==='tasks'?' active':''}" onclick="_heatSetMode('tasks')">TASKS</button>
    <button class="heat-mode-btn${_heatMode==='tokens'?' active':''}" onclick="_heatSetMode('tokens')">TOKENS</button>
    <div style="flex:1"></div>
    <span style="color:var(--t3);margin-right:4px">BIN</span>${binOptHtml}
    <span style="color:var(--t3);margin-left:8px">${totalBins} bins · ${_heatBinSec<60?_heatBinSec+'s':Math.round(_heatBinSec/60)+'m'} each</span>`;

  /* legend scale */
  const scaleSteps=6;
  const scaleHtml=Array.from({length:scaleSteps},(_,i)=>{
    const t=i/(scaleSteps-1);
    const col=_heatMode==='tasks'?`rgba(var(--c-rgb),${(0.15+t*0.85).toFixed(2)})`:`rgba(0,232,135,${(0.12+t*0.85).toFixed(2)})`;
    return `<div class="heat-legend-cell" style="background:${col}"></div>`;
  }).join('');

  /* tooltip helper via title attribute — simple approach */
  const gridHtml=rows.map(row=>{
    const col=sCol(row.a.status);
    const cellsHtml=row.bins.map((b,bi)=>{
      const bg=cellColor(b);
      const bStart=new Date(minT+bi*effectiveBinMs);
      const bEnd=new Date(minT+(bi+1)*effectiveBinMs);
      const timeStr=`${String(bStart.getHours()).padStart(2,'0')}:${String(bStart.getMinutes()).padStart(2,'0')}:${String(bStart.getSeconds()).padStart(2,'0')}–${String(bEnd.getHours()).padStart(2,'0')}:${String(bEnd.getMinutes()).padStart(2,'0')}:${String(bEnd.getSeconds()).padStart(2,'0')}`;
      const tipContent=b.active?(b.tasks.length?`${timeStr}\\n${b.tasks.map(t=>'✓ '+t).join('\\n')}`:b.v>0?`${timeStr}\\n~${b.v.toLocaleString()} tokens`:`${timeStr}\\nrunning (no tasks)`):timeStr+' (inactive)';
      const statusBorder=row.a.status==='error'&&parseTimeStr(row.a.completed_at)>=minT+bi*effectiveBinMs&&parseTimeStr(row.a.completed_at)<minT+(bi+1)*effectiveBinMs?`outline:1px solid var(--r);`:'';
      const doneBorder=row.a.status==='done'&&parseTimeStr(row.a.completed_at)>=minT+bi*effectiveBinMs&&parseTimeStr(row.a.completed_at)<minT+(bi+1)*effectiveBinMs?`outline:1px solid var(--g);`:'';
      return `<div class="heat-cell" style="background:${bg};${statusBorder||doneBorder}" title="${escHtml(tipContent)}"></div>`;
    }).join('');
    return `<div class="heat-row">
      <div class="heat-label" style="color:${col}" onclick="openAgentDetail('${escHtml(row.a.id)}',null)" title="${escHtml(row.a.name)}">${escHtml(row.a.name.length>18?row.a.name.slice(0,17)+'…':row.a.name)}</div>
      <div class="heat-cells">${cellsHtml}</div>
    </div>`;
  }).join('');

  const axisHtml=`<div class="heat-axis">${axisLabels.map(l=>`<div class="heat-axis-lbl">${l}</div>`).join('')}</div>`;

  el.innerHTML=`<div style="max-width:960px;width:100%">
  <div class="heat-toolbar">${modeHtml}</div>
  ${axisHtml}
  <div class="heat-grid">${gridHtml}</div>
  <div class="heat-legend">
    <span>LOW</span>
    <div class="heat-legend-scale">${scaleHtml}</div>
    <span>HIGH</span>
    <span style="margin-left:16px;color:var(--t3)">${_heatMode==='tasks'?'color = tasks completed per bin':'color = estimated tokens per bin'}</span>
  </div>
  </div>`;
}

function _heatSetMode(m){ _heatMode=m; if(lastStatus) renderHeatmap(lastStatus); }
function _heatSetBin(s){ _heatBinSec=s; if(lastStatus) renderHeatmap(lastStatus); }

/* ── TREE VIEW ── */
function _buildTree(agents){
  const childMap = {};
  const agentById = {};
  agents.forEach(a => { agentById[a.id]=a; childMap[a.id]=[]; });
  agents.forEach(a => {
    if(a.parent_id && agentById[a.parent_id]) childMap[a.parent_id].push(a.id);
  });
  const roots = agents.filter(a => !a.parent_id || !agentById[a.parent_id]);
  const depth = {};
  const queue = roots.map(r => ({id:r.id, d:0}));
  while(queue.length){
    const {id,d} = queue.shift();
    depth[id] = d;
    (childMap[id]||[]).forEach(cid => queue.push({id:cid, d:d+1}));
  }
  return {childMap, agentById, roots, depth};
}

function _layoutTree(agents, childMap, roots, depth){
  const NODE_H = 80;
  const LEVEL_W = 200;
  const NODE_R = 28;
  const pos = {};
  const yCounters = {};
  function assignPos(id, d){
    const children = childMap[id]||[];
    if(!children.length){
      const y = (yCounters[d]||0);
      yCounters[d] = y + NODE_H;
      pos[id] = {x: d*LEVEL_W + NODE_R + 20, y: y + NODE_R + 10};
      return;
    }
    children.forEach(cid => assignPos(cid, d+1));
    const childYs = children.map(cid => pos[cid].y);
    pos[id] = {x: d*LEVEL_W + NODE_R + 20, y: (Math.min(...childYs)+Math.max(...childYs))/2};
  }
  roots.forEach(r => assignPos(r.id, depth[r.id]||0));
  return pos;
}

function renderTree(data){
  const el = document.getElementById('tree-area');
  if(!el) return;
  const tc=_graphThemeColors();
  const agents = (data&&data.agents||[]).filter(a => !String(a.id||'').startsWith('hook_'));
  if(!agents.length){
    el.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100%;font-family:Consolas,monospace;font-size:13px;color:${tc.emptyText};letter-spacing:.08em">NO AGENTS</div>`;
    return;
  }
  const {childMap, agentById, roots, depth} = _buildTree(agents);
  const pos = _layoutTree(agents, childMap, roots, depth);
  const allPos = Object.values(pos);
  if(!allPos.length){
    el.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100%;font-family:Consolas,monospace;font-size:13px;color:${tc.emptyText};letter-spacing:.08em">NO AGENTS</div>`;
    return;
  }
  const W = Math.max(...allPos.map(p=>p.x)) + 80;
  const H = Math.max(...allPos.map(p=>p.y)) + 80;
  const edgeSvg = agents.flatMap(a =>
    (childMap[a.id]||[]).map(cid => {
      const p = pos[a.id], c = pos[cid];
      if(!p||!c) return '';
      const midX = (p.x+c.x)/2;
      const col = sCol(agentById[cid]&&agentById[cid].status||'done');
      return `<path d="M${p.x} ${p.y} C${midX} ${p.y} ${midX} ${c.y} ${c.x} ${c.y}" stroke="${col}" stroke-width="1.5" fill="none" opacity=".45" stroke-dasharray="5 3"/>`;
    })
  ).join('');
  const nodeSvg = agents.map(a => {
    const px = (pos[a.id]||{x:0,y:0}).x;
    const py = (pos[a.id]||{x:0,y:0}).y;
    const rgba = sColRgba(a.status);
    const lbl = _deriveUnit(a).slice(0,4);
    const name = a.name.length>16 ? a.name.slice(0,15)+'…' : a.name;
    const pct = a.tasks&&a.tasks.length ? Math.round(a.tasks.filter(t=>t.done).length/a.tasks.length*100) : 0;
    const r = 24;
    const isActive = _adpAgentId === a.id;
    const pts = Array.from({length:6},(_,k)=>{
      const ag = k*Math.PI/3-Math.PI/6;
      return `${(px+r*Math.cos(ag)).toFixed(0)},${(py+r*Math.sin(ag)).toFixed(0)}`;
    }).join(' ');
    const arcR = r+7;
    const arcC = (2*Math.PI*arcR).toFixed(1);
    const arcD = (pct/100*2*Math.PI*arcR).toFixed(1);
    const childCount = (childMap[a.id]||[]).length;
    const sid = escHtml(a.id);
    return `<g onclick="openAgentDetail('${sid}',null)" onmouseover="_treeNodeHover(event,'${sid}')" onmouseout="_graphNodeOut()" style="cursor:pointer${isActive?`;filter:drop-shadow(0 0 8px ${rgba})`:''}">
  ${a.status==='running'?`<circle cx="${px}" cy="${py}" r="${arcR+5}" fill="none" stroke="${rgba}" stroke-width="1" opacity=".2" style="animation:pulse 2s infinite"/>`:``}
  <polygon points="${pts}" fill="${isActive?rgba.replace('.85)',',.15)').replace(',1)',',.15)'):tc.nodeFill}" stroke="${rgba}" stroke-width="${isActive?2.5:1.6}"/>
  <circle cx="${px}" cy="${py}" r="${arcR}" fill="none" stroke="${tc.nodeStroke}" stroke-width="2"/>
  <circle cx="${px}" cy="${py}" r="${arcR}" fill="none" stroke="${rgba}" stroke-width="2" opacity="${isActive?.9:.5}" stroke-dasharray="${arcD} ${arcC}" stroke-linecap="round" transform="rotate(-90 ${px} ${py})"/>
  <text x="${px}" y="${py-4}" text-anchor="middle" font-family="Consolas,monospace" font-size="10" font-weight="700" fill="${rgba}">${lbl}</text>
  <text x="${px}" y="${py+8}" text-anchor="middle" font-family="Consolas,monospace" font-size="8" fill="${tc.pctText}">${pct}%</text>
  <text x="${px}" y="${py+40}" text-anchor="middle" font-family="Inter,sans-serif" font-size="9" fill="${tc.nodeText}">${escHtml(name)}</text>
  ${childCount>0?`<text x="${px+r}" y="${py-r+4}" text-anchor="middle" font-family="Consolas,monospace" font-size="7" fill="${rgba}" opacity=".8">${childCount}↓</text>`:''}
</g>`;
  }).join('');
  const hasHierarchy = agents.some(a => a.parent_id && agentById[a.parent_id]);
  // Stale wording predates feature 102 (parent_id is now derived
  // automatically from nested subagent transcript scans, not something
  // a dev "adds" -- see _extract_child_agent_ids in hooks/aoc_hook.py),
  // back when this really was an unfinished, opt-in protocol field. This
  // flat view is the correct, expected state whenever no subagent in the
  // session spawned another subagent itself, not a broken one.
  const hint = !hasHierarchy ? `<text x="${(W/2).toFixed(0)}" y="${H+20}" text-anchor="middle" font-family="Consolas,monospace" font-size="8" fill="${tc.legendHint}" letter-spacing=".08em">no nested subagent delegation in this session</text>` : '';
  el.innerHTML = `<div style="overflow:auto;width:100%"><svg viewBox="0 0 ${Math.max(W,400)} ${Math.max(H,200)+30}" style="min-width:${Math.max(W,400)}px;background:${tc.panelBg};border-radius:14px;border:1px solid ${tc.panelBorder}">${edgeSvg}${nodeSvg}${hint}</svg></div>`;
}

function _treeNodeHover(event, agentId){
  _graphNodeHover(event, agentId);
}

/* ── fullscreen ── */
function toggleFullscreen(){
  const btn=document.getElementById('btn-fs');
  if(!document.fullscreenElement){
    document.documentElement.requestFullscreen().catch(()=>{});
    if(btn) btn.textContent='⛶';
  } else {
    document.exitFullscreen().catch(()=>{});
  }
}
document.addEventListener('fullscreenchange',()=>{
  const btn=document.getElementById('btn-fs');
  if(btn) btn.textContent=document.fullscreenElement?'⊡':'⛶';
  if(btn) btn.title=document.fullscreenElement?'Exit fullscreen [Z]':'Fullscreen [Z]';
  if(btn) btn.style.color=document.fullscreenElement?'var(--c)':'';
});

/* ── session notes ── */
let _notesOpen=false;
let _notesSessionId=null;  // null = editing the global scratchpad note; else a per-session note
function openNotesPanel(sessionId){
  _notesOpen=true;
  _notesSessionId=sessionId||null;
  document.getElementById('notes-overlay').classList.add('open');
  setTimeout(()=>{ const ta=document.getElementById('notes-ta'); if(ta) ta.focus(); },220);
  const titleLbl=document.getElementById('notes-title-lbl');
  const subLbl=document.getElementById('notes-subtitle-lbl');
  const ta=document.getElementById('notes-ta');
  if(_notesSessionId){
    const sess=(lastStatus&&lastStatus.sessions_list||[]).find(s=>s.id===_notesSessionId);
    if(ta) ta.value=(sess&&sess.note)||'';
    if(titleLbl) titleLbl.textContent='SESSION NOTE';
    if(subLbl) subLbl.textContent=(sess&&(sess.display_name||sess.project))||'this CLI session';
  }else{
    if(lastStatus&&lastStatus.session_note!=null){ if(ta) ta.value=lastStatus.session_note||''; }
    if(titleLbl) titleLbl.textContent='SESSION NOTES';
    if(subLbl) subLbl.textContent='saved to session history';
  }
}
function closeNotesPanel(){
  _notesOpen=false;
  document.getElementById('notes-overlay').classList.remove('open');
}
async function saveNotes(){
  const ta=document.getElementById('notes-ta');
  const note=ta?ta.value:'';
  try{
    if(_notesSessionId){
      await fetch('/session_note',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:_notesSessionId,note})});
      if(lastStatus){
        const sess=(lastStatus.sessions_list||[]).find(s=>s.id===_notesSessionId);
        if(sess) sess.note=note;
      }
      renderAgents(lastStatus);  // refresh the card's note preview snippet
    }else{
      await fetch('/notes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({note})});
      if(lastStatus) lastStatus.session_note=note;
    }
    const msg=document.getElementById('notes-saved-msg');
    if(msg){ msg.style.display='block'; setTimeout(()=>msg.style.display='none',2000); }
    log('Note saved','info');
  }catch(e){ log('Failed to save note: '+e.message,'error'); }
}
function clearNotes(){
  const ta=document.getElementById('notes-ta');
  if(ta) ta.value='';
  saveNotes();
}

/* ── settings ── */
const _ACCENT_VARS = {
  cyan:   { c:'0,196,232', c2:'0,150,200' },
  purple: { c:'140,80,255', c2:'100,60,220' },
  green:  { c:'0,220,120', c2:'0,170,90' },
  orange: { c:'255,140,40', c2:'220,100,20' },
  pink:   { c:'255,80,180', c2:'220,50,150' },
};
let _settingsOpen = false;
let _costRate = parseFloat(localStorage.getItem('aoc_cost_rate') || '9');
/* Real per-model cost (set server-side in /update from the hook's granular
   usage+model, monitor.py's _calc_cost — the same math the KPI gauge already
   trusts) when available, falling back to the flat _costRate guess for
   agents/history predating that fix. Function-hoisted so it's safe to use
   from code defined earlier in this file than _costRate's declaration. */
function _agentCost(a){
  return a.estimated_cost!=null ? a.estimated_cost : (a.tokens_used||0)/1_000_000*_costRate;
}
let _budgetLimit = parseFloat(localStorage.getItem('aoc_budget') || '0');
let _projectBudgets = (()=>{ try{ return JSON.parse(localStorage.getItem('aoc_project_budgets')||'{}'); }catch(e){ return {}; } })();
let _webhookUrl = localStorage.getItem('aoc_webhook_url') || '';
let _webhookEvents = JSON.parse(localStorage.getItem('aoc_webhook_events') || '{"done":true,"error":true,"stuck":false,"burn_spike":false,"weekly_digest":false,"waiting_nudge":false,"cost_spike":false,"budget_alert":false}');
let _quietStart = localStorage.getItem('aoc_quiet_start') || '';
let _quietEnd = localStorage.getItem('aoc_quiet_end') || '';
let _mutedProjects = (()=>{ try{ return JSON.parse(localStorage.getItem('aoc_muted_projects')||'[]'); }catch(e){ return []; } })();
let _snitchUrl = localStorage.getItem('aoc_snitch_url') || '';
let _digestCadence = localStorage.getItem('aoc_digest_cadence') || 'weekly';
let _backupRetentionDays = parseInt(localStorage.getItem('aoc_backup_retention_days') || '14', 10) || 14;
let _agentRetentionHours = parseInt(localStorage.getItem('aoc_agent_retention_hours') || '12', 10) || 12;
async function _snitchPingNow(){
  const v=n=>document.getElementById(n);
  const url=v('st-snitch-url')?.value?.trim()||'';
  const msgEl=document.getElementById('st-snitch-status');
  if(!url){ if(msgEl){msgEl.style.color='var(--r)';msgEl.textContent='✗ no URL entered';} return; }
  if(msgEl){msgEl.style.color='var(--t3)';msgEl.textContent='…pinging';}
  try{
    const r=await fetch('/snitch_ping_now',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});
    const result=await r.json();
    if(msgEl) result.ok?(msgEl.style.color='var(--g)',msgEl.textContent='✓ ping sent'):(msgEl.style.color='var(--r)',msgEl.textContent='✗ '+(result.error||'failed'));
  }catch(e){
    if(msgEl){msgEl.style.color='var(--r)';msgEl.textContent='✗ request failed: '+e.message;}
  }
}
function _isNotifySuppressed(project){
  /* Mirrors monitor.py's _notification_suppressed exactly -- same two
     checks (explicit per-project mute, then quiet-hours window with
     overnight wraparound), kept in sync by hand since the client and the
     two server-side workers (_headless_notify_worker, _webhook_notify_worker)
     all need to agree on "should this notify right now". */
  if(project && _mutedProjects.includes(project)) return true;
  if(!_quietStart || !_quietEnd) return false;
  const now = new Date();
  const hhmm = String(now.getHours()).padStart(2,'0')+':'+String(now.getMinutes()).padStart(2,'0');
  return _quietStart <= _quietEnd ? (hhmm >= _quietStart && hhmm < _quietEnd) : (hhmm >= _quietStart || hhmm < _quietEnd);
}

/* ── multi-machine view ──
   Client-side only, by design: each remote is just another AOC instance's
   own unmodified /status endpoint (already CORS-open, already token-gated),
   fetched and merged in this tab. Zero backend changes on either machine. */
let _remoteMachines = (()=>{ if(!window._AOC_IS_PRO) return []; try{ return JSON.parse(localStorage.getItem('aoc_remote_machines')||'[]'); }catch(e){ return []; } })();
let _remoteMachinesDraft = [];  // settings-panel working copy; committed to _remoteMachines only on Save (Cancel discards, matching every other settings field's contract)
let _localMachineName = localStorage.getItem('aoc_local_machine_name') || '';
let _remoteCache = {};  // idx -> {data, ok, lastFetch}

function _renderRemoteMachinesRows(){
  const el=document.getElementById('remote-machines-list');
  if(!el) return;
  if(!_remoteMachinesDraft.length){
    el.innerHTML='<div style="font-size:9px;color:var(--t3);font-family:var(--font2)">No machines added.</div>';
    return;
  }
  el.innerHTML=_remoteMachinesDraft.map((m,i)=>`
    <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
      <input class="settings-input" style="flex:1;min-width:90px" placeholder="Name (e.g. Laptop)" value="${escHtml(m.name||'')}" oninput="_updateRemoteMachine(${i},'name',this.value)">
      <input class="settings-input" style="flex:2;min-width:140px" placeholder="http://192.168.1.50:5151" value="${escHtml(m.url||'')}" oninput="_updateRemoteMachine(${i},'url',this.value)">
      <input class="settings-input" type="password" style="flex:1;min-width:70px" placeholder="token" value="${escHtml(m.token||'')}" oninput="_updateRemoteMachine(${i},'token',this.value)">
      <button class="adp-btn" onclick="_testRemoteMachine(${i})" style="font-size:9px;padding:3px 8px;white-space:nowrap">TEST</button>
      <button class="adp-btn" onclick="_removeRemoteMachine(${i})" style="font-size:9px;padding:3px 8px;color:var(--r)">✕</button>
      <span id="rm-test-msg-${i}" style="font-size:9px;font-family:var(--font2);width:100%"></span>
    </div>`).join('');
}
function _addRemoteMachineRow(){
  _remoteMachinesDraft.push({name:'', url:'', token:''});
  _renderRemoteMachinesRows();
}
function _removeRemoteMachine(i){
  _remoteMachinesDraft.splice(i,1);
  _renderRemoteMachinesRows();
}
function _updateRemoteMachine(i,field,val){
  if(_remoteMachinesDraft[i]) _remoteMachinesDraft[i][field]=val;
}
async function _testRemoteMachine(i){
  const m=_remoteMachinesDraft[i]; if(!m) return;
  const msgEl=document.getElementById('rm-test-msg-'+i);
  const setMsg=(color,text)=>{ if(msgEl){ msgEl.style.color=color; msgEl.textContent=text; } };
  if(!m.url||!m.url.trim()){ setMsg('var(--r)','✗ missing URL'); return; }
  setMsg('var(--t3)','…testing');
  try{
    const base=m.url.trim().replace(/\/$/,'');
    const url=base+'/status'+(m.token?('?token='+encodeURIComponent(m.token)):'');
    const r=await fetch(url,{signal:AbortSignal.timeout(4000)});
    if(r.status===401){ setMsg('var(--r)','✗ 401 — generate a token on that machine (REMOTE ACCESS)'); return; }
    if(!r.ok){ setMsg('var(--r)','✗ HTTP '+r.status); return; }
    const d=await r.json();
    setMsg('var(--g)',`✓ ${(d.sessions_list||[]).length} sessions, ${(d.agents||[]).length} agents`);
  }catch(e){
    setMsg('var(--r)','✗ unreachable (check URL/network)');
  }
}

let _accentName = localStorage.getItem('aoc_accent') || 'cyan';
let _density = localStorage.getItem('aoc_density') || 'normal';

function _applyAccent(name){
  const v=_ACCENT_VARS[name];
  if(!v) return;
  const r=document.documentElement.style;
  r.setProperty('--c', `rgba(${v.c},1)`);
  r.setProperty('--c2', `rgba(${v.c2},1)`);
  r.setProperty('--c-rgb', v.c);
  r.setProperty('--c2-rgb', v.c2);
  document.querySelectorAll('.accent-swatch').forEach(s=>s.classList.toggle('active', s.dataset.accent===name));
}
function _applyDensity(d){
  const cards = document.getElementById('agents');
  if(cards){
    cards.style.gap = d==='compact'?'6px':d==='comfortable'?'18px':'10px';
    cards.classList.toggle('density-compact', d==='compact');
    cards.classList.toggle('density-comfortable', d==='comfortable');
  }
}
function setAccent(name){ _accentName=name; _applyAccent(name); }

/* ── auth panel helpers ── */
async function _authRefresh(){
  try{
    const d=await fetch('/auth/info').then(r=>r.json());
    const lbl=document.getElementById('auth-status-lbl');
    const urlRow=document.getElementById('auth-url-row');
    const urlInput=document.getElementById('auth-url-input');
    const tokenDisplay=document.getElementById('auth-token-display');
    const genBtn=document.getElementById('auth-gen-btn');
    const delBtn=document.getElementById('auth-del-btn');
    const note=document.getElementById('auth-note');
    if(!lbl) return;
    if(d.enabled){
      lbl.textContent=`Remote access ENABLED — ${d.ip}:5151`;
      lbl.style.color='var(--g)';
      urlRow.style.display='flex';
      if(urlInput) urlInput.value=d.url||'';
      if(tokenDisplay) tokenDisplay.textContent=d.token||'';
      if(genBtn) genBtn.textContent='↻ REGENERATE';
      if(delBtn) delBtn.style.display='inline-flex';
      if(note) note.textContent='Share the URL above with any device on your local network.';
    } else {
      lbl.textContent='Remote access DISABLED — localhost only';
      lbl.style.color='var(--t3)';
      if(urlRow) urlRow.style.display='none';
      if(genBtn) genBtn.textContent='⚡ GENERATE TOKEN';
      if(delBtn) delBtn.style.display='none';
      if(note) note.textContent='Generate a token to enable access from phone or another PC on the same network. Restart the monitor after first enable.';
    }
  }catch(e){}
}
async function _authGenerate(){
  try{
    await fetch('/auth/token',{method:'POST'});
    await _authRefresh();
    showToast('info','Network access token generated — restart monitor to apply bind change');
  }catch(e){ showToast('error','Failed to generate token'); }
}
async function _authDisable(){
  if(!confirm('Disable remote access? Anyone with the URL will lose access.')) return;
  try{
    await fetch('/auth/token',{method:'DELETE'});
    await _authRefresh();
    showToast('info','Remote access disabled');
  }catch(e){}
}
function _authCopyUrl(){
  const el=document.getElementById('auth-url-input');
  if(!el) return;
  navigator.clipboard.writeText(el.value).then(()=>showToast('info','URL copied to clipboard')).catch(()=>{ el.select(); document.execCommand('copy'); showToast('info','URL copied'); });
}

function _copyResumeCmd(id){
  const cmd='claude --resume '+id;
  navigator.clipboard.writeText(cmd).then(()=>showToast('info','Resume command copied',cmd)).catch(()=>{
    const ta=document.createElement('textarea');
    ta.value=cmd; ta.style.position='fixed'; ta.style.opacity='0';
    document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
    showToast('info','Resume command copied',cmd);
  });
}

/* Bundles a failed agent's project/task/error/log-tail into one paste-ready
   block -- the "copy" button on an error card only ever copied the bare
   error_message, which usually isn't enough context to share/debug without
   also pasting the project and what the agent was doing. Pure/testable:
   takes the agent object directly, no DOM/clipboard access inside. */
function _buildErrorContext(agent){
  if(!agent) return '';
  const lines=[];
  lines.push(`Agent: ${agent.name||'(unnamed)'}`);
  if(agent.session_project) lines.push(`Project: ${agent.session_project}`);
  lines.push(`Status: ${agent.status||'unknown'}`);
  if(agent.description) lines.push(`Task: ${agent.description}`);
  const tasks=agent.tasks||[];
  if(tasks.length){
    const done=tasks.filter(t=>t.done).length;
    lines.push(`Subtasks: ${done}/${tasks.length} done`);
  }
  if(agent.error_message) lines.push(`Error: ${agent.error_message}`);
  const log=(agent.log||[]).slice(-10);
  if(log.length){
    lines.push('--- Log tail (last '+log.length+') ---');
    log.forEach(e=>lines.push(String(e)));
  }
  return lines.join('\n');
}
function _copyErrorContext(id){
  const agent=(lastStatus&&lastStatus.agents||[]).find(a=>a.id===id);
  const text=_buildErrorContext(agent);
  if(!text) return;
  navigator.clipboard.writeText(text).then(()=>showToast('info','Error context copied')).catch(()=>{
    const ta=document.createElement('textarea');
    ta.value=text; ta.style.position='fixed'; ta.style.opacity='0';
    document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
    showToast('info','Error context copied');
  });
}

function setSettingsTab(name){
  document.querySelectorAll('.settings-pane').forEach(el=>el.style.display=el.dataset.pane===name?'':'none');
  document.querySelectorAll('.settings-tab').forEach(el=>{ el.classList.toggle('active',el.dataset.pane===name); el.setAttribute('aria-selected',el.dataset.pane===name); });
}
function openSettings(){
  _settingsOpen=true;
  document.getElementById('settings-overlay').classList.add('open');
  setSettingsTab('cost');  // always reset to the first tab -- simpler than persisting last-used tab
  _authRefresh();
  _renderLicenseUI();
  /* populate inputs */
  const v=n=>document.getElementById(n);
  if(v('st-cost-rate')) v('st-cost-rate').value=_costRate;
  if(v('st-budget'))    v('st-budget').value=_budgetLimit||'';
  if(v('st-project-budgets')) v('st-project-budgets').value=Object.keys(_projectBudgets).length?JSON.stringify(_projectBudgets,null,2):'';
  if(v('st-webhook'))   v('st-webhook').value=_webhookUrl;
  if(v('st-wh-done'))   v('st-wh-done').checked=!!_webhookEvents.done;
  if(v('st-wh-error'))  v('st-wh-error').checked=!!_webhookEvents.error;
  if(v('st-wh-stuck'))  v('st-wh-stuck').checked=!!_webhookEvents.stuck;
  if(v('st-wh-burn'))   v('st-wh-burn').checked=!!_webhookEvents.burn_spike;
  if(v('st-wh-digest')) v('st-wh-digest').checked=!!_webhookEvents.weekly_digest;
  if(v('st-wh-waiting')) v('st-wh-waiting').checked=!!_webhookEvents.waiting_nudge;
  if(v('st-wh-cost')) v('st-wh-cost').checked=!!_webhookEvents.cost_spike;
  if(v('st-quiet-start')) v('st-quiet-start').value=_quietStart;
  if(v('st-quiet-end'))   v('st-quiet-end').value=_quietEnd;
  if(v('st-muted-projects')) v('st-muted-projects').value=_mutedProjects.join(', ');
  if(v('st-snitch-url'))  v('st-snitch-url').value=_snitchUrl;
  if(v('st-digest-cadence')) v('st-digest-cadence').value=_digestCadence;
  if(v('st-backup-retention')) v('st-backup-retention').value=_backupRetentionDays;
  if(v('st-agent-retention')) v('st-agent-retention').value=_agentRetentionHours;
  if(v('st-local-machine-name')) v('st-local-machine-name').value=_localMachineName;
  _remoteMachinesDraft=_remoteMachines.map(m=>({...m}));  // working copy so Cancel discards edits, matching every other settings field
  _renderRemoteMachinesRows();
  if(v('st-density'))   v('st-density').value=_density;
  _applyAccent(_accentName);
  /* Sync webhook config from the server (source of truth for the background
     delivery worker) -- best-effort, falls back to whatever localStorage
     already populated above if this fails or the panel closes first. */
  fetch('/webhook_settings').then(r=>r.json()).then(cfg=>{
    if(!_settingsOpen) return;
    if(cfg && typeof cfg.url==='string'){
      _webhookUrl=cfg.url; _webhookEvents=cfg.events||_webhookEvents;
      if(v('st-webhook'))  v('st-webhook').value=_webhookUrl;
      if(v('st-wh-done'))  v('st-wh-done').checked=!!_webhookEvents.done;
      if(v('st-wh-error')) v('st-wh-error').checked=!!_webhookEvents.error;
      if(v('st-wh-stuck')) v('st-wh-stuck').checked=!!_webhookEvents.stuck;
      if(v('st-wh-burn'))  v('st-wh-burn').checked=!!_webhookEvents.burn_spike;
      if(v('st-wh-digest')) v('st-wh-digest').checked=!!_webhookEvents.weekly_digest;
      if(v('st-wh-waiting')) v('st-wh-waiting').checked=!!_webhookEvents.waiting_nudge;
      if(v('st-wh-cost')) v('st-wh-cost').checked=!!_webhookEvents.cost_spike;
      if(v('st-wh-budget')) v('st-wh-budget').checked=!!_webhookEvents.budget_alert;
    }
  }).catch(()=>{});
  /* Same idea for quiet hours / muted projects -- server is the source of
     truth for the two headless workers, localStorage above is just the
     fallback if this fetch loses the race with the panel closing. */
  fetch('/notify_settings').then(r=>r.json()).then(cfg=>{
    if(!_settingsOpen) return;
    if(cfg){
      _quietStart=cfg.quiet_start||''; _quietEnd=cfg.quiet_end||''; _mutedProjects=cfg.muted_projects||[]; _snitchUrl=cfg.snitch_url||''; _digestCadence=cfg.digest_cadence||'weekly'; _backupRetentionDays=cfg.backup_retention_days||14; _agentRetentionHours=cfg.agent_retention_hours||12;
      _projectBudgets=cfg.project_budgets||{};
      if(v('st-project-budgets')) v('st-project-budgets').value=Object.keys(_projectBudgets).length?JSON.stringify(_projectBudgets,null,2):'';
      _renderProjectBudgetSummary((_kpiAnalytics||{}).by_day_project);
      if(v('st-quiet-start')) v('st-quiet-start').value=_quietStart;
      if(v('st-quiet-end'))   v('st-quiet-end').value=_quietEnd;
      if(v('st-muted-projects')) v('st-muted-projects').value=_mutedProjects.join(', ');
      if(v('st-snitch-url'))  v('st-snitch-url').value=_snitchUrl;
      if(v('st-digest-cadence')) v('st-digest-cadence').value=_digestCadence;
      if(v('st-backup-retention')) v('st-backup-retention').value=_backupRetentionDays;
      if(v('st-agent-retention')) v('st-agent-retention').value=_agentRetentionHours;
    }
  }).catch(()=>{});
  /* Projected month-end spend -- reuses _computeMonthProjection (the same
     run-rate math the History/Analytics view already computes), just
     fetched fresh here since Settings has its own lifecycle from that
     view. Best-effort like the syncs above: silently leaves the em-dash
     placeholder if this fails or the panel closes first. */
  fetch('/analytics').then(r=>r.json()).then(a=>{
    if(!_settingsOpen) return;
    const el=v('st-projected-spend');
    if(!el) return;
    const {costSoFar,projectedMonthEnd}=_computeMonthProjection(a.by_day||[]);
    const fmt=c=>'$'+(+c).toFixed(2);
    el.textContent=costSoFar>0?`${fmt(costSoFar)} so far → projected ${fmt(projectedMonthEnd)}`:'—';
    const hrEl=v('infra-hook-reliability');
    if(hrEl) hrEl.textContent=_formatHookReliability(a.hook_miss_concurrency);
    _renderProjectBudgetSummary(a.by_day_project);
  }).catch(()=>{});
  /* Backup summary for the retention setting above -- same /backups list
     the DIAG view's restore picker already uses, just totalled here
     instead of rendered per-row. */
  fetch('/backups').then(r=>r.json()).then(list=>{
    if(!_settingsOpen) return;
    const el=v('infra-backup-summary');
    if(!el) return;
    if(!list.length){ el.textContent='No backups yet.'; return; }
    const totalBytes=list.reduce((s,b)=>s+(b.size_bytes||0),0);
    const totalMb=(totalBytes/1e6).toFixed(1);
    const corruptCount=list.filter(b=>b.corrupt).length;
    el.textContent=`${list.length} backup${list.length!==1?'s':''} · ${totalMb} MB total`+(corruptCount?` · ${corruptCount} flagged corrupt`:'');
  }).catch(()=>{});
  /* Agent-retention summary: how many currently-tracked agents are already
     done/error and therefore subject to the auto-clear worker -- reuses
     lastStatus rather than a fresh fetch since it's already kept live. */
  (()=>{
    const el=v('infra-agent-retention-summary');
    if(!el) return;
    const agents=(lastStatus&&lastStatus.agents)||[];
    if(!agents.length){ el.textContent='No tracked agents right now.'; return; }
    const finished=agents.filter(a=>a.status==='done'||a.status==='error').length;
    el.textContent=`${agents.length} tracked agent${agents.length!==1?'s':''} · ${finished} done/error (auto-cleared after ${_agentRetentionHours}h)`;
  })();
}
function closeSettings(){
  _settingsOpen=false;
  document.getElementById('settings-overlay').classList.remove('open');
}
async function saveSettings(){
  const v=n=>document.getElementById(n);
  _costRate = parseFloat(v('st-cost-rate')?.value) || 9;
  _budgetLimit = parseFloat(v('st-budget')?.value) || 0;
  const pbRaw = v('st-project-budgets')?.value?.trim() || '';
  if(pbRaw){
    try{
      const parsed = JSON.parse(pbRaw);
      if(parsed && typeof parsed==='object' && !Array.isArray(parsed)){
        _projectBudgets = parsed;
      } else {
        log('Per-project budgets: must be a JSON object, ignoring','error');
      }
    }catch(e){
      log('Per-project budgets: invalid JSON, ignoring (kept previous value)','error');
    }
  } else {
    _projectBudgets = {};
  }
  _webhookUrl = v('st-webhook')?.value?.trim() || '';
  _webhookEvents = {
    done:  v('st-wh-done')?.checked ?? true,
    error: v('st-wh-error')?.checked ?? true,
    stuck: v('st-wh-stuck')?.checked ?? false,
    burn_spike: v('st-wh-burn')?.checked ?? false,
    weekly_digest: v('st-wh-digest')?.checked ?? false,
    waiting_nudge: v('st-wh-waiting')?.checked ?? false,
    cost_spike: v('st-wh-cost')?.checked ?? false,
    budget_alert: v('st-wh-budget')?.checked ?? false,
  };
  _quietStart = v('st-quiet-start')?.value || '';
  _quietEnd = v('st-quiet-end')?.value || '';
  _mutedProjects = (v('st-muted-projects')?.value || '').split(',').map(s=>s.trim()).filter(Boolean);
  _snitchUrl = v('st-snitch-url')?.value?.trim() || '';
  _digestCadence = v('st-digest-cadence')?.value || 'weekly';
  _backupRetentionDays = Math.max(1, Math.min(365, parseInt(v('st-backup-retention')?.value, 10) || 14));
  _agentRetentionHours = Math.max(1, Math.min(23, parseInt(v('st-agent-retention')?.value, 10) || 12));
  _localMachineName = v('st-local-machine-name')?.value?.trim() || '';
  _remoteMachines = _remoteMachinesDraft
    .filter(m=>m.url&&m.url.trim())
    .map(m=>({name:(m.name||'').trim(), url:m.url.trim().replace(/\/$/,''), token:(m.token||'').trim()}));
  _remoteCache = {};  // config just changed -- drop stale cached data, next poll tick repopulates
  _density = v('st-density')?.value || 'normal';
  /* persist */
  localStorage.setItem('aoc_cost_rate', _costRate);
  localStorage.setItem('aoc_budget', _budgetLimit);
  localStorage.setItem('aoc_project_budgets', JSON.stringify(_projectBudgets));
  localStorage.setItem('aoc_webhook_url', _webhookUrl);
  localStorage.setItem('aoc_webhook_events', JSON.stringify(_webhookEvents));
  localStorage.setItem('aoc_quiet_start', _quietStart);
  localStorage.setItem('aoc_quiet_end', _quietEnd);
  localStorage.setItem('aoc_muted_projects', JSON.stringify(_mutedProjects));
  localStorage.setItem('aoc_snitch_url', _snitchUrl);
  localStorage.setItem('aoc_digest_cadence', _digestCadence);
  localStorage.setItem('aoc_backup_retention_days', _backupRetentionDays);
  localStorage.setItem('aoc_agent_retention_hours', _agentRetentionHours);
  localStorage.setItem('aoc_local_machine_name', _localMachineName);
  localStorage.setItem('aoc_remote_machines', JSON.stringify(_remoteMachines));
  /* Also persist server-side so _webhook_notify_worker can deliver events
     even when no browser tab is open at all -- localStorage above only
     helps the JS-side _fireWebhook, which needs an open tab to run.
     Awaited (not fire-and-forget) and checked for .ok specifically so a
     failure here -- server briefly unreachable, a validation error -- is
     surfaced instead of silently claiming "Settings saved" while the
     headless webhook/budget/quiet-hours delivery this exists for is left
     running on stale server-side config with zero indication anything
     went wrong. */
  let serverSyncOk=true;
  try{
    const [whRes,ntRes]=await Promise.all([
      fetch('/webhook_settings',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({url:_webhookUrl, events:_webhookEvents})}),
      fetch('/notify_settings',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({quiet_start:_quietStart, quiet_end:_quietEnd, muted_projects:_mutedProjects, snitch_url:_snitchUrl, digest_cadence:_digestCadence, backup_retention_days:_backupRetentionDays, agent_retention_hours:_agentRetentionHours, project_budgets:_projectBudgets})}),
    ]);
    serverSyncOk=whRes.ok&&ntRes.ok;
  }catch(e){ serverSyncOk=false; }
  localStorage.setItem('aoc_accent', _accentName);
  localStorage.setItem('aoc_density', _density);
  _applyAccent(_accentName);
  _applyDensity(_density);
  _renderProjectBudgetSummary((_kpiAnalytics||{}).by_day_project);
  const msg=document.getElementById('st-saved-msg');
  if(msg){ msg.style.display='block'; setTimeout(()=>msg.style.display='none',2000); }
  if(serverSyncOk){
    log('Settings saved','info');
  } else {
    log('Settings saved locally, but syncing to the server failed -- webhook/budget/quiet-hours delivery may be running on stale config until this succeeds (try Save again)','error');
  }
}
/* ── Tunnel UI (Cloudflare / ngrok) ── */
let _tunnelProvider='cloudflare';
let _lastTunnelInfo=null;
function _tunnelSetProvider(p){
  _tunnelProvider=p;
  const cfBtn=document.getElementById('tunnel-provider-cf'), ngBtn=document.getElementById('tunnel-provider-ngrok');
  if(cfBtn) cfBtn.style.background=p==='cloudflare'?'rgba(var(--c-rgb),.18)':'';
  if(ngBtn) ngBtn.style.background=p==='ngrok'?'rgba(var(--c-rgb),.18)':'';
  const descCf=document.getElementById('tunnel-desc-cf'), descNg=document.getElementById('tunnel-desc-ngrok');
  if(descCf) descCf.style.display=p==='cloudflare'?'':'none';
  if(descNg) descNg.style.display=p==='ngrok'?'':'none';
  _tunnelUpdateUI(_lastTunnelInfo);
}
async function _tunnelSaveNgrokToken(){
  const input=document.getElementById('tunnel-ngrok-token-input');
  const msg=document.getElementById('tunnel-ngrok-auth-msg');
  const token=input?.value?.trim();
  if(!token) return;
  if(msg){ msg.textContent='Saving…'; msg.style.color='var(--t3)'; }
  const r=await fetch('/tunnel/ngrok_authtoken',{method:'POST',body:JSON.stringify({authtoken:token})}).then(r=>r.json()).catch(()=>({ok:false,error:'network error'}));
  if(input) input.value='';  // don't leave the secret sitting in the DOM
  if(msg){
    msg.textContent=r.ok?'Token saved ✓':('Error: '+(r.error||'?'));
    msg.style.color=r.ok?'rgba(0,232,135,.9)':'var(--r)';
  }
}
function _tunnelUpdateUI(tunnel){
  _lastTunnelInfo=tunnel;
  if(!tunnel) return;
  const lbl=document.getElementById('tunnel-status-lbl');
  const startBtn=document.getElementById('tunnel-start-btn');
  const stopBtn=document.getElementById('tunnel-stop-btn');
  const urlRow=document.getElementById('tunnel-url-row');
  const urlInput=document.getElementById('tunnel-url-input');
  const dlRow=document.getElementById('tunnel-dl-row');
  const dlBar=document.getElementById('tunnel-dl-bar');
  const dlLbl=document.getElementById('tunnel-dl-lbl');
  const badge=document.getElementById('tunnel-badge');
  const badgeLbl=document.getElementById('tunnel-badge-lbl');
  const tokenNote=document.getElementById('tunnel-token-note');
  const ngrokAuthRow=document.getElementById('tunnel-ngrok-auth-row');
  const ngrokMissingRow=document.getElementById('tunnel-ngrok-missing');

  const s=tunnel.status;
  const url=tunnel.url||'';
  const idle=s==='off'||s==='error';
  /* while a tunnel is starting/ready, reflect which provider is actually running,
     not just the locally-selected one */
  const activeProvider=idle?_tunnelProvider:(tunnel.provider||_tunnelProvider);

  if(lbl) lbl.textContent='Status: '+({'off':'OFF','starting':'Starting…','downloading':'Downloading cloudflared…','ready':'ACTIVE','error':'ERROR'}[s]||s).toUpperCase();
  if(lbl) lbl.style.color=s==='ready'?'rgba(0,232,135,.9)':s==='error'?'var(--r)':s==='off'?'var(--t3)':'rgba(var(--c-rgb),.8)';
  /* ngrok_found was already computed and shipped in /status but never
     read here -- the UI used to show the "enter your authtoken" box
     regardless of whether ngrok.exe was even installed, which is
     misleading (typing a token in that case can't help). Distinguish
     the two failure modes: binary missing vs. binary present but
     unauthenticated. */
  const ngrokBinaryMissing=activeProvider==='ngrok'&&idle&&!tunnel.ngrok_found;
  const ngrokNotReady=activeProvider==='ngrok'&&idle&&!tunnel.ngrok_authtoken_ok;
  if(startBtn){
    startBtn.style.display=idle?'':'none';
    startBtn.disabled=ngrokNotReady;
    startBtn.style.opacity=ngrokNotReady?'.4':'';
    startBtn.title=ngrokBinaryMissing?'ngrok.exe not found':(ngrokNotReady?'ngrok authtoken not configured':'');
  }
  if(stopBtn)  stopBtn.style.display=idle?'none':'';
  if(urlRow)   urlRow.style.display=s==='ready'?'flex':'none';
  if(dlRow)    dlRow.style.display=s==='downloading'?'flex':'none';
  if(ngrokMissingRow) ngrokMissingRow.style.display=ngrokBinaryMissing?'flex':'none';
  if(ngrokAuthRow) ngrokAuthRow.style.display=(idle&&_tunnelProvider==='ngrok'&&!tunnel.ngrok_authtoken_ok&&tunnel.ngrok_found)?'flex':'none';
  if(url&&urlInput){ urlInput.value=url+(window._AOC_TOKEN?'?token='+encodeURIComponent(window._AOC_TOKEN):''); }
  if(dlBar)    dlBar.style.width=(tunnel.dl_pct||0)+'%';
  if(dlLbl)    dlLbl.textContent='Downloading cloudflared… '+(tunnel.dl_pct||0)+'%';
  if(tokenNote) tokenNote.textContent=window._AOC_TOKEN||'(none)';
  /* provider toggle disabled while a tunnel is starting/ready — stop it first */
  const provRow=document.getElementById('tunnel-provider-row');
  if(provRow) provRow.style.opacity=idle?'':'.4';
  const cfBtn=document.getElementById('tunnel-provider-cf'), ngBtn=document.getElementById('tunnel-provider-ngrok');
  if(cfBtn) cfBtn.disabled=!idle;
  if(ngBtn) ngBtn.disabled=!idle;
  /* header badge */
  if(badge) badge.style.display=s==='ready'?'flex':'none';
  if(badgeLbl&&url) badgeLbl.textContent=url.replace('https://','');
  const errDetail=document.getElementById('tunnel-error-detail');
  if(errDetail){
    errDetail.style.display=(s==='error'&&tunnel.error_detail)?'':'none';
    errDetail.textContent=tunnel.error_detail||'';
  }
}
function _selfUpdateUpdateUI(su){
  const badge=document.getElementById('selfupdate-badge');
  if(!badge) return;
  if(!su||!su.stale){ badge.style.display='none'; return; }
  badge.style.display='flex';
  /* checked_at was already computed and shipped alongside stale/behind_by
     but never read here -- the self-update worker only checks every 15min,
     so knowing how fresh this specific reading is matters. */
  const checkedAgo=_fmtEpochAgo(su.checked_at);
  badge.title=su.behind_by+' commit'+(su.behind_by!==1?'s':'')+' behind origin/master — run git pull'+(checkedAgo?' (checked '+checkedAgo+')':'');
}
/* pure so it's directly testable -- see tests/js/fmt_epoch_ago.test.js.
   watchdog/sentinel last_log_ts (both Python epoch seconds) were already
   computed and shipped in /status's infra_health, but _infraUpdateUI only
   ever showed the raw log line text, never how stale it is. */
function _fmtEpochAgo(epochSeconds){
  if(epochSeconds==null) return '';
  const diff=Math.floor(Date.now()/1000-epochSeconds);
  if(diff<10) return 'just now'; // also swallows small negative diffs from clock skew
  if(diff<60) return diff+'s ago';
  if(diff<3600) return Math.floor(diff/60)+'m ago';
  if(diff<86400) return Math.floor(diff/3600)+'h ago';
  return Math.floor(diff/86400)+'d ago';
}
function _infraUpdateUI(infra){
  if(!infra) return;
  const wd=infra.watchdog||{}, sn=infra.sentinel||{};
  const badge=document.getElementById('infra-badge');
  const badgeLbl=document.getElementById('infra-badge-lbl');
  const unhealthy=!wd.pid_alive||sn.stale;
  if(badge) badge.style.display=unhealthy?'flex':'none';
  if(badgeLbl) badgeLbl.textContent=!wd.pid_alive?'WATCHDOG DOWN':'SENTINEL STALE';

  const wdDot=document.getElementById('infra-wd-dot');
  const wdStatus=document.getElementById('infra-wd-status');
  const wdDetail=document.getElementById('infra-wd-detail');
  if(wdDot) wdDot.style.background=wd.pid_alive?'var(--g)':'var(--r)';
  if(wdStatus){ wdStatus.textContent=wd.pid_alive?'RUNNING':'NOT RUNNING'; wdStatus.style.color=wd.pid_alive?'var(--g)':'var(--r)'; }
  if(wdDetail){
    const restarts=wd.restarts_recent||0;
    const wdAgo=_fmtEpochAgo(wd.last_log_ts);
    wdDetail.textContent=(restarts>0?restarts+' restart'+(restarts!==1?'s':'')+' recently — ':'')+(wd.last_log_line||'no log yet')+(wdAgo?' ('+wdAgo+')':'');
  }

  const snDot=document.getElementById('infra-sn-dot');
  const snStatus=document.getElementById('infra-sn-status');
  const snDetail=document.getElementById('infra-sn-detail');
  if(snDot) snDot.style.background=sn.stale?'var(--r)':'var(--g)';
  if(snStatus){ snStatus.textContent=sn.stale?'STALE':'OK'; snStatus.style.color=sn.stale?'var(--r)':'var(--g)'; }
  if(snDetail){
    const snAgo=_fmtEpochAgo(sn.last_log_ts);
    snDetail.textContent=(sn.last_log_line||'no log yet')+(snAgo?' ('+snAgo+')':'');
  }

  const uptimeEl=document.getElementById('infra-uptime');
  if(uptimeEl) uptimeEl.textContent=_fmtDurationDHM(infra.monitor_uptime_s);

  const fmtBreakdown=counts=>{
    const total=Object.values(counts||{}).reduce((a,b)=>a+b,0);
    if(!total) return '0';
    const parts=Object.entries(counts).sort((a,b)=>b[1]-a[1]).map(([k,v])=>`${k}:${v}`).join(', ');
    return `${total} (${parts})`;
  };
  const alertEl=document.getElementById('infra-alert-audit');
  if(alertEl) alertEl.textContent=fmtBreakdown(infra.alert_audit_7d);
  const killEl=document.getElementById('infra-kill-audit');
  if(killEl) killEl.textContent=fmtBreakdown(infra.kill_audit_7d);
}
function _renderLicenseUI(){
  const badge=document.getElementById('license-tier-badge');
  const emailNote=document.getElementById('license-email-note');
  const activeRow=document.getElementById('license-active-row');
  const inactiveRow=document.getElementById('license-inactive-row');
  const isPro=!!window._AOC_IS_PRO;
  if(badge){
    badge.textContent=isPro?'PRO':'FREE';
    badge.style.background=isPro?'rgba(0,232,135,.12)':'rgba(255,255,255,.06)';
    badge.style.color=isPro?'rgba(0,232,135,.9)':'var(--t3)';
  }
  if(emailNote) emailNote.textContent=isPro&&window._AOC_LICENSE_EMAIL?window._AOC_LICENSE_EMAIL:'';
  if(activeRow)   activeRow.style.display=isPro?'flex':'none';
  if(inactiveRow) inactiveRow.style.display=isPro?'none':'flex';
  /* Pro-gated controls elsewhere in Settings: lock instead of hide, so a
     Free user can see what they'd unlock rather than wondering if the
     button is just missing */
  const startBtn=document.getElementById('tunnel-start-btn');
  if(startBtn){ startBtn.disabled=startBtn.disabled||!isPro; if(!isPro) startBtn.title='Pro feature -- Settings -> LICENSE to upgrade'; }
  const addMachineBtn=document.getElementById('btn-add-remote-machine');
  if(addMachineBtn){ addMachineBtn.disabled=!isPro; addMachineBtn.title=isPro?'':'Pro feature -- Settings -> LICENSE to upgrade'; addMachineBtn.style.opacity=isPro?'':'.4'; }
}
async function _activateLicense(){
  const input=document.getElementById('license-key-input');
  const msg=document.getElementById('license-msg');
  const key=input?.value?.trim();
  if(!key) return;
  if(msg){ msg.textContent='Activating…'; msg.style.color='var(--t3)'; }
  const r=await fetch('/license',{method:'POST',body:JSON.stringify({key})}).then(r=>r.json()).catch(()=>({ok:false,error:'network error'}));
  if(r.ok){
    window._AOC_IS_PRO=true;
    window._AOC_LICENSE_EMAIL=r.email||null;
    if(input) input.value='';
    if(msg){ msg.textContent='Activated -- reloading…'; msg.style.color='rgba(0,232,135,.9)'; }
    setTimeout(()=>location.reload(), 600);
  } else if(msg){
    msg.textContent='Error: '+(r.error||'invalid key');
    msg.style.color='var(--r)';
  }
}
async function _deactivateLicense(){
  await fetch('/license',{method:'DELETE'});
  location.reload();
}
async function _tunnelStart(){
  const lbl=document.getElementById('tunnel-status-lbl');
  if(lbl) lbl.textContent='Status: STARTING…';
  const errDetail=document.getElementById('tunnel-error-detail');
  if(errDetail) errDetail.style.display='none';
  const r=await fetch('/tunnel/start',{method:'POST',body:JSON.stringify({provider:_tunnelProvider})}).then(r=>r.json()).catch(()=>null);
  /* tunnel auto-provisions a token server-side if none existed; adopt it now so
     this already-open tab keeps authenticating once the localhost bypass is
     disabled for tunnel-exposed traffic (see _check_auth in monitor.py) */
  if(r&&r.token&&r.token!==window._AOC_TOKEN){
    window._AOC_TOKEN=r.token;
    /* the status WebSocket was opened with the old (possibly empty) token in
       its URL — a WebSocket can't have its URL updated in place, so reopen */
    _connectEvents();
  }
}
async function _tunnelStop(){
  await fetch('/tunnel/stop',{method:'POST'});
  if(document.getElementById('tunnel-status-lbl'))
    document.getElementById('tunnel-status-lbl').textContent='Status: OFF';
}
function _tunnelCopyUrl(){
  const u=document.getElementById('tunnel-url-input');
  if(!u) return;
  navigator.clipboard?.writeText(u.value).then(()=>log('Tunnel URL copied','success')).catch(()=>{u.select();document.execCommand('copy');log('URL copied','success');});
}

async function testWebhook(){
  const url=document.getElementById('st-webhook')?.value?.trim();
  const st=document.getElementById('st-wh-status');
  if(!url){ if(st) st.textContent='No URL set'; return; }
  if(st) st.textContent='Sending...';
  try{
    await fetch('/webhook_test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});
    if(st){ st.textContent='✓ Sent'; st.style.color='var(--g)'; }
  }catch(e){
    if(st){ st.textContent='✗ Failed'; st.style.color='var(--r)'; }
  }
}
async function _fireWebhook(event, agent){
  if(!_webhookUrl) return;
  if(!_webhookEvents[event]) return;
  try{
    // Mirror _webhook_notify_worker's agent_payload field-for-field (monitor.py
    // ~1262-1269) -- that's the server-side path that fires when no browser
    // tab is open, and it was enriched with project/model/error_message so an
    // external consumer (Slack/Discord/a bot) doesn't have to open the
    // dashboard to know what an event was even about. This client-only path
    // (fires while a tab IS open) never got the same fields, so the exact
    // same event for the exact same agent produced a strictly worse payload
    // depending purely on whether a tab happened to be open.
    const agentPayload={id:agent.id,name:agent.name,status:agent.status,
      cost:agent.tokens_used?+_agentCost(agent).toFixed(4):0,
      project:agent.session_project||'', model:agent.model||''};
    if(event==='error'&&agent.error_message) agentPayload.error_message=agent.error_message.slice(0,500);
    fetch('/webhook_fire',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      url:_webhookUrl, event, agent:agentPayload
    })});
  }catch(e){}
}
/* Same job as _compute_burn_rates/_is_burn_spike in monitor.py -- kept in
   sync by hand (same as the stuck-detector logic, which also lives
   independently in both languages) rather than sharing code across a
   Python/JS boundary that doesn't otherwise exist in this app. */
function _computeBurnRates(history,now,recentWindowS=180,minHistoryS=300){
  if(history.length<2) return null;
  const [oldestTs,oldestTok]=history[0];
  const [newestTs,newestTok]=history[history.length-1];
  const observedS=newestTs-oldestTs;
  if(observedS<minHistoryS) return null;
  const avgRate=(newestTok-oldestTok)/(observedS/60);
  const recentCutoff=now-recentWindowS;
  const recent=history.filter(([t])=>t>=recentCutoff);
  if(recent.length<2) return null;
  const [rOldestTs,rOldestTok]=recent[0];
  const [rNewestTs,rNewestTok]=recent[recent.length-1];
  const rObservedS=rNewestTs-rOldestTs;
  if(rObservedS<=0) return null;
  const recentRate=(rNewestTok-rOldestTok)/(rObservedS/60);
  return [recentRate,avgRate];
}
function _isBurnSpike(recentRate,avgRate,minFloor=5000,multiplier=3){
  return avgRate>0 && recentRate>multiplier*avgRate && recentRate>minFloor;
}
/* Same job as _is_cost_spike/_project_avg_costs in monitor.py -- kept in
   sync by hand, same reasoning as _computeBurnRates/_isBurnSpike above. */
function _isCostSpike(sessionCost,projectAvgCost,minFloor=1,multiplier=3){
  return projectAvgCost>0 && sessionCost>multiplier*projectAvgCost && sessionCost>minFloor;
}
function _projectAvgCosts(byProject){
  const out={};
  (byProject||[]).forEach(row=>{
    const project=row.project, sessions=row.sessions||0;
    if(!project||sessions<=0) return;
    out[project]=(row.cost||0)/sessions;
  });
  return out;
}
/* Bucket a session's raw (epoch_s, state) activity samples into
   `numBuckets` fixed-width time slices over the last `windowS` seconds,
   for the CLI card's activity sparkline. Each bucket carries forward the
   most recently seen state (samples are sparse relative to a bucket's
   width) -- a bucket with no new sample simply repeats the prior one.
   Buckets before any sample exists are null ("no data yet"). */
function _bucketActivity(history,now,numBuckets=20,windowS=7200){
  const bucketSize=windowS/numBuckets;
  const buckets=new Array(numBuckets).fill(null);
  let lastKnown=null, idx=0;
  for(let i=0;i<numBuckets;i++){
    const bEnd=now-windowS+(i+1)*bucketSize;
    while(idx<history.length && history[idx][0]<bEnd){
      lastKnown=history[idx][1];
      idx++;
    }
    buckets[i]=lastKnown;
  }
  return buckets;
}
async function _fireBurnWebhook(sess,recentRate,avgRate){
  if(!_webhookUrl) return;
  if(!_webhookEvents.burn_spike) return;
  try{
    fetch('/webhook_fire',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      url:_webhookUrl, event:'burn_spike',
      session:{id:sess.id, project:sess.project, display_name:sess.display_name,
               recent_rate:Math.round(recentRate), avg_rate:Math.round(avgRate)}
    })});
  }catch(e){}
}
async function _fireWaitingWebhook(sess){
  if(!_webhookUrl) return;
  if(!_webhookEvents.waiting_nudge) return;
  try{
    fetch('/webhook_fire',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      url:_webhookUrl, event:'waiting_nudge',
      session:{id:sess.id, project:sess.project, display_name:sess.display_name,
               waiting_secs:sess.waiting_secs}
    })});
  }catch(e){}
}
async function _fireCostSpikeWebhook(sess,cost,projectAvgCost){
  if(!_webhookUrl) return;
  if(!_webhookEvents.cost_spike) return;
  try{
    fetch('/webhook_fire',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      url:_webhookUrl, event:'cost_spike',
      session:{id:sess.id, project:sess.project, display_name:sess.display_name,
               cost:+cost.toFixed(4), project_avg_cost:+projectAvgCost.toFixed(4)}
    })});
  }catch(e){}
}
/* init on page load */
(()=>{ _applyAccent(_accentName); _applyDensity(_density); })();

/* ── keyboard shortcuts ── */
const _KB_GROUPS = [
  { title:'VIEWS', items:[
    ['C','Agents'],['B','CLI'],['T','Timeline'],['S','Summary'],['G','Graph'],
    ['X','Heatmap'],['W','Tree'],['V','History'],['K','Health'],['M','Term'],
  ]},
  { title:'RIGHT PANEL', items:[
    ['I','Panel → Files tab'],['U','Panel → Audit tab'],
  ]},
  { title:'PANELS & UI', items:[
    ['P','Toggle sidebar'],['N','Notification history'],
    ['O','Session notes'],[',','Settings'],
    ['Z','Fullscreen toggle'],['H','Toggle dark / light theme'],['?','Toggle this help'],
    ['Ctrl+K','Command palette'],
  ]},
  { title:'AGENTS', items:[
    ['F','Focus agent search'],['L','Toggle list mode'],
    ['D','Collapse done agents'],['A','Expand all agents'],
    ['E','Export agent detail (MD)'],
  ]},
  { title:'SESSIONS', items:[
    ['0','All sessions'],['1 – 5','Jump to CLI session'],['R','Reset session'],
  ]},
  { title:'TIMELINE ZOOM', items:[
    ['+','Zoom in'],['−','Zoom out'],['0','Fit / reset zoom'],
  ]},
];
let _kbOpen=false;
function _toggleKbHelp(){
  _kbOpen=!_kbOpen;
  const ov=document.getElementById('kb-overlay');
  if(!ov) return;
  ov.classList.toggle('open',_kbOpen);
  if(_kbOpen){
    const body=document.getElementById('kb-body');
    if(body&&!body.dataset.rendered){
      body.dataset.rendered='1';
      const half=Math.ceil(_KB_GROUPS.length/2);
      const cols=[_KB_GROUPS.slice(0,half),_KB_GROUPS.slice(half)];
      body.innerHTML=cols.map(col=>`<div>${col.map(g=>
        `<div class="kb-group">
          <div class="kb-group-title">${g.title}</div>
          ${g.items.map(([k,d])=>`<div class="kb-row"><kbd class="kb-key">${k}</kbd><span class="kb-desc">${d}</span></div>`).join('')}
        </div>`
      ).join('')}</div>`).join('');
    }
  }
}

/* ── command palette ── */
let _paletteOpen=false;
let _paletteSelIdx=0;
let _paletteResults=[];
function _paletteFilter(candidates,query){
  const q=(query||'').trim().toLowerCase();
  if(!q) return candidates.slice(0,8);
  return candidates.filter(c=>
    c.label.toLowerCase().includes(q)||(c.sub||'').toLowerCase().includes(q)
  ).slice(0,8);
}
function _paletteCandidates(){
  const out=[];
  (lastStatus&&lastStatus.sessions_list||[]).forEach(s=>{
    out.push({ label:s.display_name||s.project||'CLI Session', sub:'Session',
      action:()=>{ setView('agents'); setSessionFilter(s.id); } });
  });
  (lastStatus&&lastStatus.agents||[]).filter(a=>!String(a.id||'').startsWith('hook_')).forEach(a=>{
    out.push({ label:a.name||a.id, sub:'Agent · '+(a.status||''),
      action:()=>{ setView('agents'); openAgentDetail(a.id); } });
  });
  [['cost','Settings → Cost'],['notify','Settings → Notify'],['look','Settings → Look'],
   ['access','Settings → Access'],['tunnel','Settings → Tunnel'],['remote','Settings → Remote'],
   ['infra','Settings → Infra']].forEach(([pane,label])=>{
    out.push({ label, sub:'Settings', action:()=>{ openSettings(); setSettingsTab(pane); } });
  });
  [['agents','View → Agents'],['cli','View → CLI'],['timeline','View → Timeline'],['summary','View → Summary'],
   ['graph','View → Graph'],['heat','View → Heatmap'],['tree','View → Tree'],
   ['history','View → History'],['diag','View → Health'],['term','View → Terminal']].forEach(([v,label])=>{
    out.push({ label, sub:'View', action:()=>{ setView(v); } });
  });
  return out;
}
function openPalette(){
  _paletteOpen=true;
  const ov=document.getElementById('palette-overlay');
  const input=document.getElementById('palette-input');
  if(ov) ov.classList.add('open');
  if(input){ input.value=''; setTimeout(()=>input.focus(),50); }
  _paletteRender('');
}
function closePalette(){
  _paletteOpen=false;
  const ov=document.getElementById('palette-overlay');
  if(ov) ov.classList.remove('open');
}
function _paletteRender(query){
  _paletteResults=_paletteFilter(_paletteCandidates(),query);
  _paletteSelIdx=0;
  const el=document.getElementById('palette-results');
  if(!el) return;
  if(!_paletteResults.length){ el.innerHTML='<div class="palette-empty">No matches</div>'; return; }
  el.innerHTML=_paletteResults.map((c,i)=>
    `<div class="palette-item${i===0?' active':''}" onclick="_paletteActivate(${i})">
      <span class="pi-label">${escHtml(c.label)}</span>
      <span class="pi-sub">${escHtml(c.sub||'')}</span>
    </div>`
  ).join('');
}
function _paletteActivate(idx){
  const c=_paletteResults[idx];
  if(!c) return;
  closePalette();
  try{ c.action(); }catch(e){}
}
function _paletteMove(delta){
  if(!_paletteResults.length) return;
  _paletteSelIdx=(_paletteSelIdx+delta+_paletteResults.length)%_paletteResults.length;
  document.querySelectorAll('#palette-results .palette-item').forEach((el,i)=>el.classList.toggle('active',i===_paletteSelIdx));
}
function _paletteInputKeydown(e){
  if(e.key==='ArrowDown'){ e.preventDefault(); _paletteMove(1); }
  else if(e.key==='ArrowUp'){ e.preventDefault(); _paletteMove(-1); }
  else if(e.key==='Enter'){ e.preventDefault(); _paletteActivate(_paletteSelIdx); }
  else if(e.key==='Escape'){ e.preventDefault(); closePalette(); }
}
document.addEventListener('keydown',e=>{
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){
    e.preventDefault();
    _paletteOpen?closePalette():openPalette();
  }
});
document.addEventListener('keydown',e=>{
  if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA'||e.metaKey||e.ctrlKey||e.altKey) return;
  if(e.key==='Escape'){ if(_kbOpen){ _toggleKbHelp(); return; } }
  switch(e.key.toUpperCase()){
    case 'C': setView('agents');   log('View → AGENTS','info');   break;
    case 'B': setView('cli');      log('View → CLI','info');      break;
    case 'T': setView('timeline'); log('View → TIMELINE','info'); break;
    case 'S': setView('summary');  log('View → SUMMARY','info');  break;
    case 'G': setView('graph');    log('View → GRAPH','info');    break;
    case 'X': setView('heat');     log('View → HEAT','info');     break;
    case 'K': setView('diag');     log('View → HEALTH','info');   break;
    case 'W': setView('tree');     log('View → TREE','info');     break;
    case 'I': setRightTab('files'); log('Panel → FILES','info');  break;
    case 'U': setRightTab('audit'); log('Panel → AUDIT','info');  break;
    case 'V': setView('history');  log('View → HISTORY','info'); break;
    case 'M': setView('term');     log('View → TERM','info');    break;
    case 'R': resetSession();      break;
    case '?': _toggleKbHelp();    break;
    case 'F': { const si=document.getElementById('search-input'); if(si){si.focus();e.preventDefault();} break; }
    case 'L': toggleListMode(); break;
    case 'D': collapseAllDone(); break;
    case 'A': expandAll(); break;
    case 'P': toggleRightPanel(); break;
    case 'N': _notifOpen?closeNotifPanel():openNotifPanel(); break;
    case 'O': _notesOpen?closeNotesPanel():openNotesPanel(); break;
    case 'Z': toggleFullscreen(); break;
    case ',': _settingsOpen?closeSettings():openSettings(); break;
    case 'E': if(_adpAgentId) exportAgentDetail(); break;
    case 'H': toggleTheme(); break;
    case '+': case '=': if(currentView==='timeline'){ _tlZoomIn(); e.preventDefault(); } break;
    case '-': if(currentView==='timeline'){ _tlZoomOut(); e.preventDefault(); } break;
    case '0': { if(currentView==='timeline'){ _tlZoomReset(); } else { setSessionFilter(null); log('Session → ALL','info'); } break; }
    case '1': case '2': case '3': case '4': case '5': {
      if(lastStatus){
        const now=Date.now(); const cutoff=now-1800000;
        const sess=(lastStatus.sessions_list||[]);
        const idx=parseInt(e.key)-1;
        if(idx<sess.length){ setSessionFilter(sess[idx].id); log(`Session → ${sess[idx].project||sess[idx].id.slice(-6)}`,'info'); }
      }
      break;
    }
    case 'TAB': {
      e.preventDefault();
      const _views=['agents','cli','timeline','summary','graph','heat','diag','tree','history','term'];
      const _ci=_views.indexOf(currentView);
      setView(_views[(_ci+1)%_views.length]);
      break;
    }
  }
});
/* Escape: close overlays in priority order */
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'){
    if(_paletteOpen){ closePalette(); e.preventDefault(); return; }
    if(_kbOpen){ _toggleKbHelp(); e.preventDefault(); return; }
    if(_cmpOpen){ closeCompare(); e.preventDefault(); return; }
    if(_sessCmpOpen){ closeSessionCompare(); e.preventDefault(); return; }
    if(_notifOpen){ closeNotifPanel(); e.preventDefault(); return; }
    if(_notesOpen){ closeNotesPanel(); e.preventDefault(); return; }
    if(_settingsOpen){ closeSettings(); e.preventDefault(); return; }
    if(_adpAgentId){ closeAgentDetail(); e.preventDefault(); return; }
    const si=document.getElementById('search-input');
    if(si&&document.activeElement===si){ setSearch(''); si.blur(); e.preventDefault(); }
  }
});
/* role="tab" elements are <div>s, not native <button>s -- give them the
   keyboard behavior a real ARIA tab pattern requires: Enter/Space
   activates the focused tab, Left/Right roves focus within the same
   tablist (wrapping), matching what a screen reader user expects from
   the role it's being told these elements have. */
document.addEventListener('keydown',e=>{
  const el=e.target;
  if(!el.matches||!el.matches('[role="tab"]')) return;
  if(e.key==='Enter'||e.key===' '){ e.preventDefault(); el.click(); return; }
  if(e.key==='ArrowRight'||e.key==='ArrowLeft'){
    const tabs=Array.from(el.closest('[role="tablist"]').querySelectorAll('[role="tab"]'));
    const i=tabs.indexOf(el);
    if(i===-1) return;
    e.preventDefault();
    const next=tabs[(i+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length];
    next.focus();
    next.click();
  }
});
log('Shortcuts: C B T S G X W I U = views · R = reset · F = search · Ctrl+K = palette · ? = help','info');
</script>
</body>
</html>"""


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
         "note": v.get("note", ""),
         "pr_url": _pr_link_cache_snapshot.get(v.get("git_branch", ""), {}).get("url"),
         "waiting_secs": _compute_waiting_secs(v, now_epoch)}
        for k, v in sessions_from_dict.items()
    ]
    # sessions_count: floor at the real claude.exe process count (background
    # scanner, _claude_proc_worker, polls tasklist every 2s). Hook-tracked
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
    data["infra_health"] = _build_infra_health()
    return data


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # enables keep-alive, eliminates TIME_WAIT storm
    def log_message(self, fmt, *args):
        pass

    def _check_auth(self) -> bool:
        """Return True if request is authorized. Localhost always allowed, UNLESS a
        Cloudflare tunnel is active: cloudflared forwards public traffic to this
        server over a local connection, so every tunnel request also looks like it
        came from 127.0.0.1. The localhost bypass must not apply in that case, or
        the tunnel would grant unauthenticated internet access (incl. the terminal)."""
        client_ip = self.client_address[0]
        tunnel_exposed = _tunnel_status[0] in ("starting", "ready")
        if client_ip in ("127.0.0.1", "::1", "localhost") and not tunnel_exposed:
            return True
        if not _auth_token:
            return False  # non-localhost blocked when no token is set
        import hmac
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        # hmac.compare_digest rather than == -- a plain string comparison
        # short-circuits on the first mismatched character, which is a
        # timing side-channel in principle (impractical to actually exploit
        # over a real network against a 24-byte random token, but this is
        # the free, correct way to compare a secret and there's no reason
        # not to).
        if hmac.compare_digest(qs.get("token", [""])[0], _auth_token):
            return True
        if hmac.compare_digest(self.headers.get("X-AOC-Token", ""), _auth_token):
            return True
        return False

    def _serve_csv_download(self, csv_text, filename):
        if not _is_pro():
            self._serve(403, "application/json",
                        json.dumps({"ok": False, "error": "CSV export is a Pro feature. Settings -> LICENSE to upgrade."}).encode())
            return
        csv_body = csv_text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(csv_body))
        self.end_headers()
        self.wfile.write(csv_body)

    def _get_csv_date_range(self):
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        return (qs.get("from") or [""])[0], (qs.get("to") or [""])[0]

    def _get_auditlog(self):
        files = _log.list_logs()
        file_meta = []
        for fname in files:
            fpath = os.path.join(LOGS_DIR, fname)
            try:
                size = os.path.getsize(fpath)
            except Exception:
                size = 0
            file_meta.append({"name": fname, "size": size})
        payload = {
            "lines": _log.read_tail(200),
            "current": os.path.basename(_log.current_log_path() or ""),
            "files": files,
            "file_meta": file_meta,
        }
        self._serve(200, "application/json", json.dumps(payload, ensure_ascii=False).encode())

    def _get_diff(self):
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        rel = qs.get("file", [""])[0]
        diff_text = ""
        if rel:
            try:
                base = _get_project_dir() or AOC_DIR
                # absolute path passed directly
                if os.path.isabs(rel):
                    full_path = rel
                    cwd = os.path.dirname(rel)
                    git_rel = os.path.basename(rel)
                else:
                    full_path = os.path.join(base, rel.replace("/", os.sep))
                    cwd = base
                    git_rel = rel
                # Require the resolved path to actually stay inside the current
                # project dir (or AOC's own dir as fallback) — an absolute `rel`
                # was otherwise accepted as-is, letting an authenticated request
                # read any file the process can (e.g. ?file=C:\Users\x\.ssh\id_rsa).
                base_real = os.path.realpath(base)
                full_real = os.path.realpath(full_path)
                if full_real != base_real and not full_real.startswith(base_real + os.sep):
                    diff_text = f"(access denied: {rel} is outside the project directory)"
                else:
                    # 1) unstaged changes
                    r = _run(["git", "diff", "HEAD", "--", git_rel], cwd=cwd, timeout=5)
                    if r.stdout.strip():
                        diff_text = r.stdout
                    else:
                        # 2) last commit changes for this file
                        r2 = _run(["git", "log", "-1", "--pretty=format:", "-p", "--", git_rel], cwd=cwd, timeout=5)
                        if r2.stdout.strip():
                            diff_text = r2.stdout
                    if not diff_text.strip():
                        # 3) fallback: show full file content
                        if os.path.exists(full_path):
                            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                                lines = f.readlines()
                            diff_text = f"--- /dev/null\n+++ {rel}\n@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{l}" for l in lines)
                        else:
                            diff_text = f"(file not found: {rel})"
            except Exception as e:
                diff_text = f"Error: {e}"
        else:
            diff_text = "No file specified"
        self._serve(200, "application/json",
                    json.dumps({"diff": diff_text}, ensure_ascii=False).encode())

    def _get_git(self):
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        cwd = qs.get('cwd', [AOC_DIR])[0]
        # cwd is a request param, not a trusted internal path -- the one
        # legitimate caller (_termUpdateGit) only ever passes a cwd
        # already sitting on a currently-tracked session (see that JS
        # function's own comment), so require it to actually be one of
        # those rather than running `git -C <anything>` against whatever
        # directory an authenticated request names, which would let it
        # probe branch/status for any directory this process can read.
        try:
            _known_cwds = {AOC_DIR} | {
                s.get("cwd") for s in _load_status().get("sessions", {}).values() if s.get("cwd")
            }
        except Exception:
            _known_cwds = {AOC_DIR}
        if cwd not in _known_cwds:
            self._serve(200, 'application/json', b'{"branch":null,"changes":0}')
            return
        try:
            branch = _run(['git','-C',cwd,'rev-parse','--abbrev-ref','HEAD'], timeout=5).stdout.strip()
            status_out = _run(['git','-C',cwd,'status','--short'], timeout=5).stdout.strip()
            changes = len([l for l in status_out.splitlines() if l.strip()])
            self._serve(200,'application/json',json.dumps(
                {'branch':branch,'changes':changes,'status':status_out[:500]},ensure_ascii=False).encode())
        except Exception:
            self._serve(200,'application/json',b'{"branch":null,"changes":0}')

    def _get_log_file(self):
        fname = os.path.basename(self.path[6:])
        fpath = os.path.join(LOGS_DIR, fname)
        if fname and os.path.exists(fpath) and fname.endswith(".log"):
            with open(fpath, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", len(content))
            self.end_headers()
            self.wfile.write(content)
        else:
            self._serve(404, "text/plain", b"Log not found")

    def do_GET(self):
        if not self._check_auth():
            self._serve(401, "text/plain", b"Unauthorized - token required")
            return
        path_no_qs = self.path.split("?")[0]
        if path_no_qs in ("/", "/index.html"):
            html = HTML.replace("__AOC_TOKEN_PLACEHOLDER__", _auth_token or "") \
                       .replace("__AOC_IS_PRO_PLACEHOLDER__", "true" if _is_pro() else "false")
            self._serve(200, "text/html; charset=utf-8", html.encode())
        elif path_no_qs == "/icon.svg":
            self._serve(200, "image/svg+xml", _ICON_SVG.encode())
        elif path_no_qs == "/manifest.json":
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            tok = (qs.get("token") or [""])[0]
            manifest = _build_manifest(tok)
            self._serve(200, "application/json", json.dumps(manifest).encode(), no_cache=True)
        elif path_no_qs == "/sw.js":
            # Minimal service worker: no offline caching (this is a live
            # dashboard, a stale cached snapshot would be actively
            # misleading), just enough of a fetch handler for PWA
            # installability criteria on Android/Chromium.
            sw = "self.addEventListener('fetch', () => {});"
            self._serve(200, "application/javascript", sw.encode(), no_cache=True)
        elif path_no_qs == "/auth/info":
            self._serve(200, "application/json", json.dumps(_auth_info(), ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/license":
            self._serve(200, "application/json", json.dumps(_license_info(), ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/status":
            try:
                data = _build_status_payload()
                self._serve(200, "application/json", json.dumps(data, ensure_ascii=False).encode(), no_cache=True)
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())
        elif path_no_qs == "/metrics":
            try:
                data = _build_status_payload()
                analytics = _db_analytics()
                body = _render_prometheus_metrics(data, analytics)
                self._serve(200, "text/plain", body.encode(), no_cache=True)
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())
        elif path_no_qs == "/events":
            # WebSocket push: replaces the old 500ms client-side poll loop.
            if self.headers.get('Upgrade', '').lower() == 'websocket':
                _handle_events_ws(self)
            else:
                self._serve(400, "text/plain", b"WebSocket required")
        elif path_no_qs == "/history":
            sessions = _db_get_sessions(100)
            self._serve(200, "application/json", json.dumps(sessions, ensure_ascii=False).encode())
        elif path_no_qs == "/history/search":
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            query = (qs.get("q") or [""])[0]
            date_from = (qs.get("from") or [""])[0]
            date_to = (qs.get("to") or [""])[0]
            sessions = _db_search_sessions(query, date_from, date_to)
            self._serve(200, "application/json", json.dumps(sessions, ensure_ascii=False).encode(), no_cache=True)
        elif path_no_qs == "/history/save_current":
            try:
                current = _load_status()
                _db_save_session(current)
                self._serve(200, "application/json", b'{"ok":true}')
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())
        elif path_no_qs.startswith("/history/"):
            sid = path_no_qs[len("/history/"):]
            detail = _db_get_session_detail(sid)
            self._serve(200, "application/json", json.dumps(detail, ensure_ascii=False).encode())
        elif path_no_qs == "/analytics":
            data = _db_analytics()
            self._serve(200, "application/json", json.dumps(data, ensure_ascii=False).encode())
        elif path_no_qs == "/export_costs.csv":
            date_from, date_to = self._get_csv_date_range()
            self._serve_csv_download(_export_costs_csv(date_from, date_to),
                                      f"aoc_costs_{date_from or 'all'}_{date_to or 'all'}.csv")
        elif path_no_qs == "/export_costs_by_model.csv":
            date_from, date_to = self._get_csv_date_range()
            self._serve_csv_download(_export_costs_by_model_csv(date_from, date_to),
                                      f"aoc_costs_by_model_{date_from or 'all'}_{date_to or 'all'}.csv")
        elif path_no_qs == "/export_costs_by_subagent_type.csv":
            date_from, date_to = self._get_csv_date_range()
            self._serve_csv_download(_export_costs_by_subagent_type_csv(date_from, date_to),
                                      f"aoc_costs_by_agent_type_{date_from or 'all'}_{date_to or 'all'}.csv")
        elif path_no_qs == "/diag":
            data = _get_diag_info()
            self._serve(200, "application/json", json.dumps(data, ensure_ascii=False).encode())
        elif path_no_qs == "/backups":
            self._serve(200, "application/json", json.dumps(_list_backups(), ensure_ascii=False).encode())
        elif path_no_qs == "/webhook_settings":
            self._serve(200, "application/json", json.dumps(_webhook_settings, ensure_ascii=False).encode())
        elif path_no_qs == "/notify_settings":
            self._serve(200, "application/json", json.dumps(_notify_settings, ensure_ascii=False).encode())
        elif path_no_qs == "/errors":
            data = _db_get_errors(100)
            self._serve(200, "application/json", json.dumps(data, ensure_ascii=False).encode())
        elif path_no_qs == "/auditlog":
            self._get_auditlog()
        elif self.path.startswith("/diff"):
            self._get_diff()
        elif self.path.startswith("/git"):
            self._get_git()
        elif path_no_qs == "/terminal/ws":
            if self.headers.get('Upgrade','').lower() == 'websocket':
                _handle_terminal_ws(self)
            else:
                self._serve(400, "text/plain", b"WebSocket required")
        elif self.path.startswith("/logs/"):
            self._get_log_file()
        else:
            self._serve(404, "text/plain", b"Not found")

    def do_DELETE(self):
        if not self._check_auth():
            self._serve(401, "text/plain", b"Unauthorized"); return
        # Stripped once here (mirroring do_GET's path_no_qs) rather than at
        # each elif -- a Remote Machine call always carries `?token=...`
        # (see README's Security model: the auth token travels as a query
        # param, not a header, for any cross-origin request), so matching
        # against the raw self.path used to either 404 on every remote
        # DELETE or, worse for /session/<id>, silently glue the query
        # string onto the extracted id and dismiss/create the wrong
        # session entry instead of failing loudly.
        path_no_qs = self.path.split("?")[0]
        if path_no_qs == "/auth/token":
            _delete_auth_token()
            self._serve(200, "application/json", json.dumps(_auth_info()).encode())
        elif path_no_qs == "/license":
            _delete_license()
            self._serve(200, "application/json", json.dumps(_license_info()).encode())
        elif path_no_qs.startswith("/session/"):
            sid = path_no_qs[len("/session/"):]
            try:
                from urllib.parse import unquote
                sid = unquote(sid)
                with _status_lock:
                    status = _load_status()
                    sessions = status.get("sessions", {})
                    if sid in sessions:
                        sessions[sid]["dismissed"] = True
                        sessions[sid]["session_active"] = False
                    else:
                        sessions[sid] = {"dismissed": True, "session_active": False}
                    status["sessions"] = sessions
                    _save_status(status)
                self._serve(200, "application/json", b'{"ok":true}')
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())
        else:
            self._serve(404, "text/plain", b"Not found")

    def do_OPTIONS(self):
        """CORS preflight. `_serve()` already sends
        Access-Control-Allow-Origin on every real response, which is
        enough for a cross-origin GET (Remote Machines' own /status poll)
        or a bodyless POST -- browsers only send those straight through.
        DELETE and a POST with a JSON body are not "simple requests"
        though, so the browser sends this OPTIONS preflight first and
        withholds do_DELETE/do_POST entirely until it sees the right
        headers back here -- without this handler, BaseHTTPRequestHandler
        has no do_OPTIONS and falls back to a 501, which is exactly what
        silently made Force Stop/Dismiss no-ops against a merged-in
        Remote Machine (see README's Remote Machines section) despite
        those endpoints working fine called locally the whole time.
        No auth check here: a preflight carries no body and the actual
        token still travels as `?token=` on the real request that
        follows, checked by that request's own do_POST/do_DELETE exactly
        as before -- this only stops the browser from blocking that
        request before it's ever sent."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _post_update(self, body):
        try:
            update = json.loads(body)
            with _status_lock:
                status = _load_status()
                now = _now_ts()
                now_epoch = time.time()

                # ── multi-session tracking ──
                session_id = update.get("session_id", "default")
                cwd = update.get("cwd", "")
                sessions = status.setdefault("sessions", {})
                if session_id not in sessions:
                    sessions[session_id] = {}
                # Dismiss is meant for CLOSED sessions (the ✕ button only shows when
                # session_active===false). If a live heartbeat reports the session
                # active again, the user is back in that same CLI — un-hide it instead
                # of leaving it permanently gone.
                if sessions[session_id].get("dismissed") and update.get("session_active"):
                    sessions[session_id]["dismissed"] = False
                sessions[session_id]["last_seen"] = now
                sessions[session_id]["last_seen_epoch"] = now_epoch
                if cwd:
                    cwd = _fix_mojibake(cwd)
                    sessions[session_id]["cwd"] = cwd
                    cwd_name = os.path.basename(cwd.rstrip("/\\")) or cwd
                    if not sessions[session_id].get("project"):
                        sessions[session_id]["project"] = cwd_name
                if "project" in update and update["project"]:
                    sessions[session_id]["project"] = update["project"]
                if "session_active" in update:
                    sessions[session_id]["session_active"] = update["session_active"]
                if "display_name" in update:
                    sessions[session_id]["display_name"] = _fix_mojibake(update["display_name"])
                if update.get("host_pid"):
                    sessions[session_id]["host_pid"] = update["host_pid"]
                if "waiting_on_you" in update:
                    # Real-time signal from the Stop/UserPromptSubmit heartbeat hook:
                    # Stop means Claude just finished a turn and control is back with
                    # the human; UserPromptSubmit means the human just replied. Lower
                    # latency than the transcript scanner (which also computes this,
                    # as a fallback for sessions where the hook silently misses).
                    _accumulate_waiting_time(sessions[session_id], update["waiting_on_you"], now_epoch)
                    sessions[session_id]["waiting_on_you"] = update["waiting_on_you"]
                # mark session as having had agents (used for sessions_count)
                if update.get("agents"):
                    sessions[session_id]["has_agents"] = True

                # ── auto-expire sessions inactive >30min ──
                cutoff = now_epoch - 1800
                for sid in list(sessions.keys()):
                    if sessions[sid].get("dismissed"):
                        continue  # keep dismissed sessions so they stay hidden
                    if sessions[sid].get("last_seen_epoch", now_epoch) < cutoff:
                        del sessions[sid]
                        status["agents"] = [
                            a for a in status.get("agents", [])
                            if a.get("session_id") != sid
                        ]

                session_project = sessions.get(session_id, {}).get("project", "")

                # ── update session-level fields ──
                for key in ("session_active", "project", "started_at"):
                    if key in update:
                        status[key] = update[key]

                # ── upsert agents ──
                for au in update.get("agents", []):
                    _apply_agent_update(status, au, session_id, session_project, now)

                _save_status(status)
            self._serve(200, "application/json", b'{"ok":true}')
        except Exception as e:
            self._serve(500, "text/plain", str(e).encode())

    def _post_notify_settings(self, body):
        try:
            data = json.loads(body)
            quiet_start = str(data.get("quiet_start", ""))[:5]
            quiet_end = str(data.get("quiet_end", ""))[:5]
            muted = data.get("muted_projects") or []
            if not isinstance(muted, list):
                muted = []
            muted = [str(p)[:100] for p in muted if str(p).strip()][:50]
            snitch_url = str(data.get("snitch_url", ""))[:500]
            digest_cadence = data.get("digest_cadence", "weekly")
            if digest_cadence not in ("daily", "weekly", "off"):
                digest_cadence = "weekly"
            backup_retention_days = _clamp_backup_retention_days(data.get("backup_retention_days", 14))
            agent_retention_hours = _clamp_agent_retention_hours(data.get("agent_retention_hours", 12))
            project_budgets = _sanitize_project_budgets(data.get("project_budgets") or {})
            _save_notify_settings({
                "quiet_start": quiet_start,
                "quiet_end": quiet_end,
                "muted_projects": muted,
                "snitch_url": snitch_url,
                "digest_cadence": digest_cadence,
                "backup_retention_days": backup_retention_days,
                "agent_retention_hours": agent_retention_hours,
                "project_budgets": project_budgets,
            })
            self._serve(200, "application/json", b'{"ok":true}')
        except Exception as e:
            self._serve(500, "text/plain", str(e).encode())

    def _post_tunnel_start(self, body):
        if not _is_pro():
            self._serve(403, "application/json",
                        json.dumps({"ok": False, "error": "Remote access (tunnel) is a Pro feature. Settings -> LICENSE to upgrade."}).encode())
            return
        if _tunnel_status[0] in ("starting", "downloading", "ready"):
            self._serve(200, "application/json",
                        json.dumps({"ok": True, "status": _tunnel_status[0], "token": _auth_token}).encode())
        else:
            try:
                provider = json.loads(body or b"{}").get("provider", "cloudflare")
            except Exception:
                provider = "cloudflare"
            if provider not in ("cloudflare", "ngrok"):
                provider = "cloudflare"
            # A tunnel with no auth token would be wide open to the internet
            # (the localhost bypass is disabled once tunnel_exposed is true) —
            # always provision one first so exposure never happens unauthenticated.
            if not _auth_token:
                _save_auth_token(_secrets.token_urlsafe(24))
            _tunnel_provider[0] = provider
            if provider == "ngrok":
                _start_ngrok_worker_thread()
            else:
                _start_tunnel_worker_thread()
            self._serve(200, "application/json",
                        json.dumps({"ok": True, "status": "starting", "token": _auth_token}).encode())

    def _post_tunnel_ngrok_authtoken(self, body):
        try:
            authtoken = str(json.loads(body).get("authtoken", "")).strip()
            ng = _find_ngrok()
            if not ng:
                self._serve(200, "application/json", json.dumps({"ok": False, "error": "ngrok.exe not found"}).encode())
            elif not authtoken:
                self._serve(200, "application/json", json.dumps({"ok": False, "error": "empty authtoken"}).encode())
            else:
                import subprocess as _sp
                flags = _sp.CREATE_NO_WINDOW if os.name == "nt" else 0
                r = _sp.run([ng, "config", "add-authtoken", authtoken],
                            capture_output=True, timeout=10, creationflags=flags)
                if r.returncode == 0:
                    _ngrok_authtoken_cache[1] = 0.0  # force re-check on next /status poll
                    self._serve(200, "application/json", b'{"ok":true}')
                else:
                    self._serve(200, "application/json",
                                 json.dumps({"ok": False, "error": r.stderr.decode("utf-8", "replace")[:300]}).encode())
        except Exception as e:
            self._serve(500, "text/plain", str(e).encode())

    def _post_reset(self):
        try:
            # Every other read-modify-write of status.json (see /update, the
            # transcript scanner) holds _status_lock across the whole
            # read...save span; this one didn't. A hook /update landing
            # concurrently with a user-triggered reset could read status
            # between this read and the _save_status(idle) below, save its
            # own update, and then have it silently wiped out by idle's
            # unconditional "agents": [] a moment later.
            with _status_lock:
                # snapshot current session to history DB before wiping
                current = _load_status()
                if current.get("session_active") or any(
                    a.get("status") in ("done","error")
                    for a in current.get("agents",[])
                    if not str(a.get("id","")).startswith("hook_")
                ):
                    _db_save_session(current)
                _log.session_reset()
                global _prev_session_active
                with _prev_session_active_lock:
                    _prev_session_active = False
                idle = {
                    "started_at": None,
                    "session_active": False,
                    "project": "",
                    "agents": [],
                    "sessions": current.get("sessions", {}),
                }
                _save_status(idle)
                _autosave_id[0] = None   # new session gets a fresh autosave ID
                _autosave_time[0] = 0
                try:
                    os.remove(AUTOSAVE_ID_FILE)
                except Exception:
                    pass
            self._serve(200, "application/json", b'{"ok":true}')
        except Exception as e:
            self._serve(500, "text/plain", str(e).encode())

    def _post_kill_session(self, body):
        try:
            data = json.loads(body)
            sid = str(data.get("session_id", ""))
            remote_addr = self.client_address[0] if self.client_address else ""
            outcome = _kill_session_core(sid, remote_addr)
            code, payload = _KILL_OUTCOME_RESPONSES[outcome]
            self._serve(code, "application/json", payload)
        except Exception as e:
            self._serve(500, "text/plain", str(e).encode())

    def do_POST(self):
        if not self._check_auth():
            self._serve(401, "text/plain", b"Unauthorized"); return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        # Stripped once here (mirroring do_GET's path_no_qs) -- every route
        # below used to match against the raw self.path, which worked by
        # coincidence only because a same-origin call never carried a query
        # string. A Remote Machine call always does (the auth token travels
        # as `?token=...`, see README's Security model), so every one of
        # these routes 404'd for a remote caller until this was stripped.
        path_no_qs = self.path.split("?")[0]

        if path_no_qs == "/update":
            self._post_update(body)

        elif path_no_qs == "/remove":
            try:
                data = json.loads(body)
                aid = data.get("id")
                with _status_lock:
                    status = _load_status()
                    status["agents"] = [a for a in status.get("agents", []) if a["id"] != aid]
                    if not status["agents"]:
                        status["session_active"] = False
                    _save_status(status)
                self._serve(200, "application/json", b'{"ok":true}')
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())

        elif path_no_qs == "/clear_done":
            try:
                with _status_lock:
                    status = _load_status()
                    status["agents"] = [a for a in status.get("agents", []) if a.get("status") not in ("done",)]
                    if not any(a for a in status["agents"] if not str(a.get("id","")).startswith("hook_")):
                        status["session_active"] = False
                    _save_status(status)
                self._serve(200, "application/json", b'{"ok":true}')
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())

        elif path_no_qs == "/notes":
            try:
                data = json.loads(body)
                note = str(data.get("note", ""))[:2000]
                with _status_lock:
                    st = _load_status()
                    st["session_note"] = note
                    _save_status(st)
                self._serve(200, "application/json", b'{"ok":true}')
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())

        elif path_no_qs == "/session_note":
            # Per-session note, distinct from the single global "session_note"
            # above -- field name is "note" (not "session_note") specifically
            # to avoid confusion between the two.
            try:
                data = json.loads(body)
                session_id = data.get("session_id", "")
                note = data.get("note", "")
                with _status_lock:
                    st = _load_status()
                    if _set_session_note(st, session_id, note):
                        _save_status(st)
                self._serve(200, "application/json", b'{"ok":true}')
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())

        elif path_no_qs == "/history_tags":
            # Edits an already-saved History row directly (_db_set_session_tags),
            # not the live status dict -- see that function's own docstring
            # for why this differs from /session_note.
            try:
                data = json.loads(body)
                session_id = str(data.get("session_id", ""))
                ok = _db_set_session_tags(session_id, data.get("tags") or [])
                self._serve(200 if ok else 404, "application/json",
                            json.dumps({"ok": ok}).encode())
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())

        elif path_no_qs == "/webhook_test":
            try:
                import urllib.request as _ur
                data = json.loads(body)
                url = str(data.get("url", ""))[:500]
                payload = json.dumps({"source": "AOC", "event": "test", "message": "AOC webhook test"}).encode()
                req = _ur.Request(url, data=payload, method="POST",
                                  headers={"Content-Type": "application/json"})
                _ur.urlopen(req, timeout=5)
                self._serve(200, "application/json", b'{"ok":true}')
            except Exception as e:
                self._serve(200, "application/json", json.dumps({"ok": False, "error": str(e)}).encode())

        elif path_no_qs == "/webhook_fire":
            try:
                data = json.loads(body)
                url = str(data.get("url", ""))[:500]
                _fire_webhook(url, data)
                self._serve(200, "application/json", b'{"ok":true}')
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())

        elif path_no_qs == "/webhook_settings":
            try:
                data = json.loads(body)
                url = str(data.get("url", ""))[:500]
                if url and not _is_pro():
                    self._serve(403, "application/json",
                                json.dumps({"ok": False, "error": "Webhook delivery is a Pro feature. Settings -> LICENSE to upgrade."}).encode())
                    return
                events = data.get("events") or {}
                _save_webhook_settings({
                    "url": url,
                    "events": _sanitize_webhook_events(events),
                })
                self._serve(200, "application/json", b'{"ok":true}')
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())

        elif path_no_qs == "/notify_settings":
            self._post_notify_settings(body)

        elif path_no_qs == "/snitch_ping_now":
            try:
                data = json.loads(body)
                url = str(data.get("url", "") or _notify_settings.get("snitch_url", ""))[:500]
                if not url:
                    self._serve(400, "application/json", b'{"ok":false,"error":"no URL configured"}')
                else:
                    _snitch_ping(url)
                    self._serve(200, "application/json", b'{"ok":true}')
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())

        elif path_no_qs == "/restore_backup":
            try:
                data = json.loads(body)
                result = _restore_backup(str(data.get("filename", "")))
                self._serve(200 if result.get("ok") else 400, "application/json",
                            json.dumps(result, ensure_ascii=False).encode())
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())

        elif path_no_qs == "/tunnel/start":
            self._post_tunnel_start(body)

        elif path_no_qs == "/tunnel/stop":
            _stop_tunnel()
            self._serve(200, "application/json", b'{"ok":true}')

        elif path_no_qs == "/tunnel/ngrok_authtoken":
            self._post_tunnel_ngrok_authtoken(body)

        elif path_no_qs == "/reset":
            self._post_reset()

        elif path_no_qs == "/kill_session":
            self._post_kill_session(body)

        elif path_no_qs == "/auth/token":
            try:
                new_token = _secrets.token_urlsafe(24)
                _save_auth_token(new_token)
                self._serve(200, "application/json", json.dumps(_auth_info(), ensure_ascii=False).encode())
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())

        elif path_no_qs == "/license":
            try:
                data = json.loads(body)
                key = (data.get("key") or "").strip()
                payload = _verify_license(key) if key else None
                if not payload:
                    self._serve(400, "application/json", json.dumps({"ok": False, "error": "Invalid license key"}).encode())
                else:
                    _save_license(key)
                    self._serve(200, "application/json", json.dumps({"ok": True, **_license_info()}, ensure_ascii=False).encode())
            except Exception as e:
                self._serve(500, "text/plain", str(e).encode())

        else:
            self._serve(404, "text/plain", b"Not found")

    def _serve(self, code, ct, body, no_cache=False):
        self.send_response(code)
        if ct == "application/json":
            ct = "application/json; charset=utf-8"
        self.send_header("Content-Type", ct)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        if no_cache:
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def _start_server():
    """Start the HTTP server in the current thread (blocking)."""
    class _Server(ThreadingHTTPServer):
        allow_reuse_address = True
    _Server(("0.0.0.0", PORT), Handler).serve_forever()


def _make_tray_icon():
    """Build a 64x64 AOC hexagon tray icon using Pillow."""
    from PIL import Image, ImageDraw
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy, r = size // 2, size // 2, size // 2 - 4
    import math
    pts = [(cx + r * math.cos(math.radians(a - 90)), cy + r * math.sin(math.radians(a - 90)))
           for a in range(0, 360, 60)]
    d.polygon(pts, fill=(0, 196, 232, 220), outline=(0, 232, 200, 255))
    d.ellipse([cx - 12, cy - 12, cx + 12, cy + 12], fill=(0, 130, 160, 200))
    return img


_STARTUP_LNK = os.path.join(
    os.environ.get("APPDATA", ""),
    "Microsoft", "Windows", "Start Menu", "Programs", "Startup", "AOC Monitor.lnk"
)

def _is_startup_enabled():
    return os.path.exists(_STARTUP_LNK)

def _toggle_startup():
    if _is_startup_enabled():
        try: os.remove(_STARTUP_LNK)
        except Exception: pass
    else:
        try:
            import winshell  # optional; fall back to VBScript
        except ImportError:
            winshell = None
        if winshell:
            with winshell.shortcut(_STARTUP_LNK) as sc:
                sc.path = r"C:\Users\marek\AppData\Local\Programs\Python\Python39\pythonw.exe"
                sc.arguments = f'"{os.path.abspath(__file__)}" --app'
                sc.working_directory = AOC_DIR
                sc.description = "AOC Agent Operations Center"
        else:
            # Use VBScript as fallback (always available on Windows)
            vbs = f"""
Set WS = CreateObject("WScript.Shell")
Set sc = WS.CreateShortcut("{_STARTUP_LNK}")
sc.TargetPath = "C:\\Users\\marek\\AppData\\Local\\Programs\\Python\\Python39\\pythonw.exe"
sc.Arguments = Chr(34) & "{os.path.abspath(__file__)}" & Chr(34) & " --app"
sc.WorkingDirectory = "{AOC_DIR}"
sc.Description = "AOC Agent Operations Center"
sc.Save
""".strip()
            tmp_vbs = os.path.join(os.environ.get("TEMP", AOC_DIR), "_aoc_startup.vbs")
            with open(tmp_vbs, "w") as f:
                f.write(vbs)
            subprocess.run(["cscript", "//nologo", tmp_vbs], creationflags=_NO_WINDOW)
            try: os.remove(tmp_vbs)
            except Exception: pass


_AOC_AUMID = "AOC.AgentOperationsCenter"
_AOC_SHORTCUT_NAME = "AOC Agent Operations Center.lnk"

def _register_aumid_shortcut():
    """One-time (idempotent -- checks Test-Path first, same as the shortcut
    check here) registration of AOC's own AppUserModelID via a Start Menu
    shortcut, so _show_native_toast's notifications carry AOC's own name/icon
    instead of borrowing powershell.exe's. WshShell.CreateShortcut can't set
    the System.AppUserModel.ID property -- only IShellLinkW's IPropertyStore
    can -- hence the COM interop via Add-Type. Best-effort: any failure here
    just means _show_native_toast keeps using the powershell.exe AUMID
    fallback, which already works fine, so this never blocks startup."""
    try:
        shortcut_path = os.path.join(
            os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", _AOC_SHORTCUT_NAME
        )
        if os.path.exists(shortcut_path):
            return
        ps_script = r'''
$AppId = "''' + _AOC_AUMID + r'''"
$ShortcutPath = "''' + shortcut_path.replace("\\", "\\\\") + r'''"

Add-Type @"
using System;
using System.Runtime.InteropServices;

[ComImport, Guid("00021401-0000-0000-C000-000000000046")]
internal class CShellLink { }

[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("000214F9-0000-0000-C000-000000000046")]
internal interface IShellLinkW {
    void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszFile, int cchMaxPath, IntPtr pfd, uint fFlags);
    void GetIDList(out IntPtr ppidl);
    void SetIDList(IntPtr pidl);
    void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszName, int cchMaxName);
    void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
    void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszDir, int cchMaxPath);
    void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
    void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszArgs, int cchMaxPath);
    void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
    void GetHotkey(out short pwHotkey);
    void SetHotkey(short wHotkey);
    void GetShowCmd(out int piShowCmd);
    void SetShowCmd(int iShowCmd);
    void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszIconPath, int cchIconPath, out int piIcon);
    void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
    void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, uint dwReserved);
    void Resolve(IntPtr hwnd, uint fFlags);
    void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
}

[StructLayout(LayoutKind.Sequential, Pack = 4)]
internal struct PROPERTYKEY {
    public Guid fmtid;
    public int pid;
    public PROPERTYKEY(Guid fmtid, int pid) { this.fmtid = fmtid; this.pid = pid; }
}

[StructLayout(LayoutKind.Explicit)]
internal struct PROPVARIANT_UNION {
    [FieldOffset(0)] public IntPtr pwszVal;
}

[StructLayout(LayoutKind.Sequential)]
internal struct PROPVARIANT {
    public ushort vt;
    public ushort wReserved1;
    public ushort wReserved2;
    public ushort wReserved3;
    public PROPVARIANT_UNION union;

    public static PROPVARIANT FromString(string s) {
        PROPVARIANT pv = new PROPVARIANT();
        pv.vt = 31; // VT_LPWSTR
        pv.union.pwszVal = Marshal.StringToCoTaskMemUni(s);
        return pv;
    }
    public void Clear() {
        if (union.pwszVal != IntPtr.Zero) { Marshal.FreeCoTaskMem(union.pwszVal); union.pwszVal = IntPtr.Zero; }
    }
}

[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")]
internal interface IPropertyStore {
    int GetCount(out uint propertyCount);
    int GetAt(uint propertyIndex, out PROPERTYKEY key);
    int GetValue(ref PROPERTYKEY key, out PROPVARIANT pv);
    int SetValue(ref PROPERTYKEY key, ref PROPVARIANT pv);
    int Commit();
}

[ComImport, Guid("0000010b-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IPersistFile {
    void GetClassID(out Guid pClassID);
    int IsDirty();
    void Load([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, uint dwMode);
    void Save([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, bool fRemember);
    void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string pszFileName);
    void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string ppszFileName);
}

public class ShortcutHelper {
    static readonly PROPERTYKEY PKEY_AppUserModel_ID = new PROPERTYKEY(new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), 5);

    public static void CreateWithAumid(string shortcutPath, string targetPath, string aumid, string description) {
        IShellLinkW link = (IShellLinkW)new CShellLink();
        link.SetPath(targetPath);
        link.SetArguments("");
        link.SetDescription(description);

        IPropertyStore propStore = (IPropertyStore)link;
        PROPVARIANT pv = PROPVARIANT.FromString(aumid);
        PROPERTYKEY key = PKEY_AppUserModel_ID;
        propStore.SetValue(ref key, ref pv);
        propStore.Commit();
        pv.Clear();

        IPersistFile file = (IPersistFile)link;
        file.Save(shortcutPath, true);
    }
}
"@

if (-not (Test-Path $ShortcutPath)) {
    [ShortcutHelper]::CreateWithAumid($ShortcutPath, "$PSHOME\powershell.exe", $AppId, "AOC - Agent Operations Center")
}
'''
        tmp_ps1 = os.path.join(os.environ.get("TEMP", AOC_DIR), "_aoc_register_aumid.ps1")
        try:
            with open(tmp_ps1, "w", encoding="utf-8") as f:
                f.write(ps_script)
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", tmp_ps1],
                creationflags=_NO_WINDOW, timeout=15,
            )
        finally:
            try:
                os.remove(tmp_ps1)
            except Exception:
                pass
    except Exception as e:
        _log_bg_error("_register_aumid_shortcut", e)


def _show_native_toast(title: str, message: str):
    """Fire a real Windows notification without any extra dependency (no
    pystray/pywebview needed -- those only run in --app/tray mode, but the
    watchdog actually runs monitor.py --headless, where _tray_monitor's own
    icon.notify() below never executes at all).

    Tried System.Windows.Forms.NotifyIcon.ShowBalloonTip first (needs no
    AppUserModelID) -- live-tested, confirmed it does NOT actually show
    anything on this machine (likely Windows deprioritizing/dropping legacy
    balloon tips outright, not a Focus Assist block -- ToastEnabled=1, no
    quiet-hours flag set). Switched to the real WinRT toast API instead.
    First used powershell.exe's own already-registered AUMID (live-tested,
    worked) as a fallback-safe default; now prefers AOC's own AUMID
    (_register_aumid_shortcut, called once at startup) for AOC's own
    name/icon on the notification instead of PowerShell's -- also
    live-tested and confirmed showing distinctly from the borrowed one."""
    try:
        def xml_esc(s: str) -> str:
            return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        def ps_esc(s: str) -> str:
            return str(s).replace("'", "''")
        title_x = ps_esc(xml_esc(title))
        msg_x = ps_esc(xml_esc(message))
        # Use AOC's own AUMID only if its shortcut actually exists -- checked
        # at call time (cheap stat), not assumed from _register_aumid_shortcut
        # having been *attempted*, since that call is itself best-effort and
        # could have failed. Falls back to the always-registered
        # powershell.exe AUMID so a failed registration never regresses
        # notifications back to completely silent.
        aumid_shortcut = os.path.join(
            os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", _AOC_SHORTCUT_NAME
        )
        aumid = _AOC_AUMID if os.path.exists(aumid_shortcut) else \
            r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
        # activationType="protocol" is fully OS-handled (opens the URL
        # directly) -- no click-handler code needed on either end, whether
        # the user clicks the toast body itself (launch=) or the explicit
        # button (the <action> below).
        dashboard_url = f"http://localhost:{PORT}"
        toast_xml = (
            f'<toast activationType="protocol" launch="{dashboard_url}">'
            f'<visual><binding template="ToastGeneric"><text>{title_x}</text><text>{msg_x}</text></binding></visual>'
            f'<actions><action activationType="protocol" content="Open Dashboard" arguments="{dashboard_url}"/></actions>'
            f'</toast>'
        )
        ps = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
            "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null; "
            f"$xml = New-Object Windows.Data.Xml.Dom.XmlDocument; "
            f"$xml.LoadXml('{toast_xml}'); "
            "$toast = New-Object Windows.UI.Notifications.ToastNotification $xml; "
            f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{aumid}').Show($toast)"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", ps],
            creationflags=_NO_WINDOW,
        )
    except Exception as e:
        _log_bg_error("_show_native_toast", e)


def _headless_notify_worker():
    """Same running->done/error transition detection as _tray_monitor below,
    but using _show_native_toast instead of pystray's icon.notify() -- for
    --headless (the mode actually run by the watchdog) and plain CLI mode,
    neither of which have a pystray Icon event loop to call .notify() on."""
    import urllib.request
    _prev = {}
    while True:
        try:
            time.sleep(3)
            raw = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/status", timeout=2).read()
            d = json.loads(raw)
            agents = {a["id"]: a for a in (d.get("agents") or []) if not str(a.get("id", "")).startswith("hook_")}
            for aid, a in agents.items():
                old = _prev.get(aid, {})
                if old.get("status") == "running" and not _notification_suppressed(a.get("session_project", "")):
                    if a.get("status") == "done":
                        _show_native_toast(f"✓ {a.get('name','Agent')[:40]}", "Completed successfully")
                    elif a.get("status") == "error":
                        # "Agent encountered an error" told you nothing an
                        # error toast couldn't already imply from its own ✗ --
                        # the actual error_message was sitting right there on
                        # `a` unused, same gap the done/error webhook payload
                        # had (see _webhook_notify_worker).
                        _show_native_toast(f"✗ {a.get('name','Agent')[:40]}",
                                            (a.get("error_message") or "Agent encountered an error")[:180])
            _prev = agents
        except Exception as e:
            _log_bg_error("_headless_notify_worker", e)


def _run_app_mode():
    """System tray + pywebview native window mode."""
    import pystray, webview

    URL = f"http://127.0.0.1:{PORT}"
    _win = [None]   # mutable reference to pywebview window

    def open_window():
        if _win[0] is not None:
            try:
                _win[0].show()
                return
            except Exception:
                pass
        w = webview.create_window(
            "AOC — Agent Operations Center",
            URL,
            width=1280, height=800,
            min_size=(900, 600),
            background_color="#050815",
        )
        _win[0] = w
        webview.start(gui="edgechromium", debug=False)
        _win[0] = None   # window closed

    def on_tray_click(icon, item):
        threading.Thread(target=open_window, daemon=True).start()

    def on_quit(icon, item):
        icon.stop()
        _release_monitor_lock()  # os._exit below skips atexit -- release explicitly
        os._exit(0)

    def on_toggle_startup(icon, item):
        _toggle_startup()
        icon.update_menu()

    def startup_checked(item):
        return _is_startup_enabled()

    menu = pystray.Menu(
        pystray.MenuItem("Open Dashboard", on_tray_click, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Start with Windows", on_toggle_startup, checked=startup_checked),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit AOC", on_quit),
    )
    icon = pystray.Icon("AOC", _make_tray_icon(), "AOC — Agent Operations Center", menu)

    def _tray_monitor():
        import urllib.request, json as _json
        _prev = {}
        while True:
            try:
                time.sleep(3)
                raw = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/status", timeout=2).read()
                d = _json.loads(raw)
                agents = {a["id"]: a for a in (d.get("agents") or []) if not str(a.get("id","")).startswith("hook_")}
                running = sum(1 for a in agents.values() if a.get("status") in ("running","waiting"))
                icon.title = f"AOC — {running} running" if running else "AOC — Agent Operations Center"
                for aid, a in agents.items():
                    old = _prev.get(aid, {})
                    if old.get("status") == "running" and not _notification_suppressed(a.get("session_project", "")):
                        if a.get("status") == "done":
                            try: icon.notify(f"✓ {a.get('name','Agent')[:40]}", "Completed successfully")
                            except Exception: pass
                        elif a.get("status") == "error":
                            # Same fix as _headless_notify_worker's own error
                            # branch above -- error_message was available but
                            # unused, so --app mode's tray balloon was just as
                            # content-free as --headless's toast used to be.
                            try: icon.notify(f"✗ {a.get('name','Agent')[:40]}",
                                              (a.get("error_message") or "Agent encountered an error")[:180])
                            except Exception: pass
                _prev = agents
            except Exception as e:
                _log_bg_error("_tray_monitor", e)

    threading.Thread(target=_tray_monitor, daemon=True).start()
    # Open window immediately on first launch
    threading.Thread(target=open_window, daemon=True).start()
    icon.run()


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    headless = "--headless" in args
    app_mode = "--app" in args or (not headless and "pythonw" in sys.executable.lower())

    # ── single-instance guard ────────────────────────────────────────────────
    # ThreadingHTTPServer below sets allow_reuse_address=True (SO_REUSEADDR) so a
    # restart can quickly rebind past TIME_WAIT — but on Windows, SO_REUSEADDR
    # also lets a *second* process bind the same port while the first is still
    # actively listening, instead of just tolerating TIME_WAIT like on POSIX.
    # A watchdog restart racing a manual restart can silently spawn a second
    # (third, fourth...) monitor.py that coexists on the same port with its own
    # separate in-memory state — observed today: 5 stray processes accumulated
    # after repeated restarts, with requests routed unpredictably between them
    # (stale session data winning a race, a tunnel-stop call landing on a
    # process that never started the tunnel it was asked to stop).
    #
    # This used to be a urllib probe against /status: refuse to start if
    # something's already answering. That check itself raced its own
    # subject — it ran before *this* process bound its own listening
    # socket, so a second instance starting during the several-hundred-ms
    # (sometimes multi-second, see the /status-readiness poll below) window
    # between "process A passed the check" and "process A is actually
    # listening" would also see nothing answering and proceed. A PID file
    # written with O_CREAT|O_EXCL is atomic at the OS level (the create
    # itself fails if the file exists, no separate check-then-write gap to
    # race), closing that window instead of narrowing it. Stale-lock
    # detection reuses _pid_exe_name, the same technique watchdog.py's own
    # lock file (watchdog.pid) uses, so a PID recycled by an unrelated
    # process after a crash doesn't wedge every future start.
    _MONITOR_PID_FILE = os.path.join(AOC_DIR, "monitor.pid")

    def _acquire_monitor_lock(max_attempts: int = 5) -> bool:
        for _ in range(max_attempts):
            try:
                fd = os.open(_MONITOR_PID_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w") as f:
                    f.write(str(os.getpid()))
                return True
            except FileExistsError:
                try:
                    with open(_MONITOR_PID_FILE) as f:
                        old_pid = int(f.read().strip())
                except Exception:
                    old_pid = None  # unreadable/corrupt -- treat as stale below
                if old_pid and old_pid != os.getpid() and _pid_exe_name(old_pid) in ("python.exe", "pythonw.exe"):
                    return False  # a real instance is holding the lock
                # Stale lock (holder gone, or its PID got recycled by something
                # else entirely) -- clear it and retry rather than wedging
                # every future start the way watchdog.py's own docstring warns
                # its equivalent check could.
                try:
                    os.remove(_MONITOR_PID_FILE)
                except Exception:
                    return False
        return False  # kept losing the race across every retry -- don't guess

    def _release_monitor_lock() -> None:
        try:
            with open(_MONITOR_PID_FILE) as f:
                if int(f.read().strip()) == os.getpid():
                    os.remove(_MONITOR_PID_FILE)
        except Exception:
            pass

    if not _acquire_monitor_lock():
        print(f"AOC is already running on port {PORT} — not starting a second instance.")
        sys.exit(0)
    atexit.register(_release_monitor_lock)

    # We're about to become the sole active instance — reap any tunnel child a
    # previous, now-dead instance left running (see _reap_stray_tunnel_process).
    _reap_stray_tunnel_process()

    if "--tunnel" in args:
        _start_tunnel_worker_thread()

    # Start HTTP server in background thread (all modes)
    srv_thread = threading.Thread(target=_start_server, daemon=True)
    srv_thread.start()

    # Wait for server to be ready
    import urllib.request
    for _ in range(20):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/status", timeout=1)
            break
        except Exception:
            time.sleep(0.3)

    if headless:
        # Pure headless: server runs forever, no UI
        threading.Thread(target=_register_aumid_shortcut, daemon=True).start()
        threading.Thread(target=_headless_notify_worker, daemon=True).start()
        srv_thread.join()
    elif app_mode:
        # System tray + pywebview native window
        _run_app_mode()
    else:
        # CLI mode: open browser tab
        threading.Thread(target=_register_aumid_shortcut, daemon=True).start()
        threading.Thread(target=_headless_notify_worker, daemon=True).start()
        print(f"\n  AOC — Agent Operations Center -> http://localhost:{PORT}")
        print("  Ctrl+C to stop\n")
        threading.Thread(
            target=lambda: (time.sleep(0.5), webbrowser.open(f"http://localhost:{PORT}")),
            daemon=True,
        ).start()
        srv_thread.join()
