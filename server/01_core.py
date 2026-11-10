# AOC server, part 01: imports, paths, settings, Claude Code version tracking.
# monitor.py exec()s server/NN_*.py in number order into ONE shared
# namespace (its own) -- not an importable module: names from earlier
# parts are plain globals here, and __file__ is monitor.py.
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
#
# A second, throwaway instance (a preview of a branch, a fork's worktree, a
# test) sets AOC_PORT + AOC_DATA_DIR instead of patching this file. Any port
# other than 5151 -- or AOC_PREVIEW=1 -- is a preview: it never shows a
# toast, sends a phone push or fires a webhook (_win_toast, _push_send_one,
# _fire_webhook check AOC_PREVIEW), so trying a branch can't ping you.
def _env_port(raw, default=5151) -> int:
    """Pure: AOC_PORT -> a usable TCP port, else the default."""
    try:
        p = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return p if 1024 <= p <= 65535 else default

AOC_DATA_DIR = os.environ.get("AOC_DATA_DIR") or os.path.join(os.environ.get("LOCALAPPDATA", AOC_DIR), "AOC")
os.makedirs(AOC_DATA_DIR, exist_ok=True)
STATUS_FILE = os.path.join(AOC_DATA_DIR, "monitor-status.json")
LOGS_DIR    = os.path.join(AOC_DATA_DIR, "logs")
DB_FILE     = os.path.join(AOC_DATA_DIR, "history.db")
KNOWN_VERSIONS_FILE = os.path.join(AOC_DATA_DIR, "known_cc_versions.json")
KILL_AUDIT_LOG = os.path.join(LOGS_DIR, "kill_audit.log")
ALERT_AUDIT_LOG = os.path.join(LOGS_DIR, "alert_audit.log")
PORT = _env_port(os.environ.get("AOC_PORT"))
AOC_PREVIEW = PORT != 5151 or os.environ.get("AOC_PREVIEW") == "1"

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
           fill="#5b9be033" stroke="#5b9be0" stroke-width="2"/>
  <circle cx="16" cy="16" r="4" fill="#5b9be0"/>
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
        "background_color": "#1b2624",
        "theme_color": "#16201e",
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

def _iso_to_epoch(iso_ts):
    """Transcript UTC ISO timestamp -> epoch seconds, or None."""
    try:
        dt = datetime.strptime(str(iso_ts).split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
        return float(calendar.timegm(dt.timetuple()))
    except Exception:
        return None

def _transcript_session_times(first_ts: str, last_ts: str):
    """(date, started_at, ended_at, duration_s) for a History row, from a
    transcript's first/last UTC ISO timestamps, in local wall-clock time like
    every other row. The scanner used to slice the raw UTC string instead, so
    plain CLI sessions (no subagents, never saved by _db_save_session) showed
    a start time hours off, a date that could be the wrong day, and no end
    or duration at all. Unparseable input gives empty strings / 0 so the
    caller can keep whatever the row already had."""
    def _epoch(iso):
        try:
            dt = datetime.strptime(str(iso).split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
            return calendar.timegm(dt.timetuple())
        except Exception:
            return None
    e1, e2 = _epoch(first_ts), _epoch(last_ts)
    if e1 is None:
        return "", "", "", 0
    date = time.strftime("%Y-%m-%d", time.localtime(e1))
    started = time.strftime("%H:%M:%S", time.localtime(e1))
    if e2 is None or e2 < e1:
        return date, started, "", 0
    return date, started, time.strftime("%H:%M:%S", time.localtime(e2)), int(e2 - e1)

def _db_upsert_transcript_session(c, sid, project, first_ts, last_ts,
                                  tokens=None, cost=None, cc_version="", title=""):
    """Create/refresh a History row from a CLI session's transcript. tokens/
    cost None = keep the row's (the startup backfill only repairs times and
    title). Never blanks a value it can't derive; duration only grows, so a
    longer span already recorded by _db_save_session isn't shortened; tags
    and a user-edited project are never touched.

    The start never moves later: a transcript can be rewritten (compaction,
    resume) so its first line may postdate the real start the row already
    holds -- the earlier of the two wins and the duration counts from it.
    Rows the old scanner wrote (recognisable by ended_at IS NULL: it never
    set one) hold that start as raw UTC, so it's read back as UTC."""
    prev = c.execute("SELECT date, started_at, ended_at, duration_s FROM sessions WHERE id=?", (sid,)).fetchone()
    if prev and prev[0] and prev[1]:
        try:
            st = time.strptime(f"{prev[0]} {prev[1]}", "%Y-%m-%d %H:%M:%S")
            prev_epoch = calendar.timegm(st) if prev[2] is None else time.mktime(st)
            prev_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(prev_epoch))
            first_ts = min(first_ts, prev_iso) if _transcript_session_times(first_ts, "")[0] else prev_iso
        except (ValueError, OverflowError):
            pass
    # The end never moves earlier either: after a restart the scanner can
    # briefly hold an older last_ts than the one already stored. The stored
    # span is start + duration_s (ended_at alone has no date to compare).
    if prev and (prev[3] or 0) > 0:
        _, _, _, dur0 = _transcript_session_times(first_ts, last_ts)
        if prev[3] > dur0:
            try:
                e_start = calendar.timegm(time.strptime(str(first_ts).split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S"))
                last_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(e_start + prev[3]))
            except (ValueError, OverflowError):
                pass
    date, started, ended, dur = _transcript_session_times(first_ts, last_ts)
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    c.execute("""
        INSERT INTO sessions (id, project, date, started_at, ended_at, duration_s,
                              tokens, cost, cc_version, title)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            tokens     = COALESCE(?, tokens),
            cost       = COALESCE(?, cost),
            project    = CASE WHEN project = '' OR project IS NULL
                              THEN excluded.project ELSE project END,
            date       = CASE WHEN ? = '' THEN date ELSE excluded.date END,
            started_at = CASE WHEN excluded.started_at = '' THEN started_at ELSE excluded.started_at END,
            ended_at   = CASE WHEN COALESCE(excluded.ended_at, '') = '' THEN ended_at ELSE excluded.ended_at END,
            duration_s = MAX(COALESCE(duration_s, 0), excluded.duration_s),
            cc_version = COALESCE(NULLIF(excluded.cc_version, ''), cc_version),
            title      = COALESCE(NULLIF(excluded.title, ''), title)
    """, (sid, project or "", date, started, ended or None, dur,
          tokens or 0, cost or 0.0, cc_version or "", title or "",
          tokens, cost, started))

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
            "the transcript/hook format may have changed.",
            kind="cc_version",
        )
    except Exception:
        pass  # _show_native_toast defined later in module; see docstring above for why this is safe to ignore

_TASK_NOTIF_RE = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)
_TASK_TAG_RE = re.compile(r"<(tool-use-id|status|summary|result)>(.*?)</\1>", re.S)
_TASK_USAGE_RE = re.compile(r"<subagent_tokens>(\d+)</subagent_tokens>")
