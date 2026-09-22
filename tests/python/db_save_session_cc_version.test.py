"""Tests _db_save_session's cc_version persistence, plus _db_get_sessions/
_db_get_session_detail reading it back, extracted straight from monitor.py.
cc_version was tracked live (per-CLI-session, via the transcript scanner
backfilling it from the transcript's own metadata) but never persisted --
Session Compare/export could show it for the CURRENT session only, and it
was gone for good the moment a session got saved to history.db or reset.
Runs against a real scratch SQLite DB, same convention as
db_save_session_duration_cap.test.py."""
import sys, os, sqlite3, threading, tempfile, shutil, json, time
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_db_save_session_cc_version_")
DB_FILE = os.path.join(SCRATCH, "history.db")

conn = sqlite3.connect(DB_FILE)
conn.executescript("""
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY, project TEXT, orchestrator TEXT, date TEXT,
        started_at TEXT, ended_at TEXT, duration_s INTEGER DEFAULT 0,
        agents INTEGER DEFAULT 0, done INTEGER DEFAULT 0, errors INTEGER DEFAULT 0,
        tokens INTEGER DEFAULT 0, cost REAL DEFAULT 0,
        task_done INTEGER DEFAULT 0, task_total INTEGER DEFAULT 0,
        file_count INTEGER DEFAULT 0, snapshot TEXT, cc_version TEXT, waiting_on_you_s INTEGER DEFAULT 0, tags TEXT DEFAULT ''
    );
    CREATE TABLE agents (
        rowid_ INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, agent_id TEXT,
        name TEXT, unit TEXT, status TEXT, started_at TEXT, completed_at TEXT,
        duration_s INTEGER DEFAULT 0, tokens INTEGER DEFAULT 0, cost REAL DEFAULT 0,
        task_done INTEGER DEFAULT 0, task_total INTEGER DEFAULT 0,
        file_count INTEGER DEFAULT 0, error_msg TEXT,
        detected_via TEXT, concurrent_sessions INTEGER, model TEXT, subagent_type TEXT,
        tool_use_count INTEGER, parent_id TEXT,
        UNIQUE(session_id, agent_id)
    );
    CREATE TABLE file_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT, agent_id TEXT, path TEXT, type TEXT, lines INTEGER DEFAULT 0
    );
""")
conn.commit()
conn.close()

ns = exec_functions(
    ["_db_conn", "_correct_hms_diff", "_db_save_session", "_db_get_sessions", "_db_get_session_detail", "_MAX_PLAUSIBLE_DURATION_S", "_tags_list"],
    {
        "sqlite3": sqlite3, "os": os, "json": json, "datetime": datetime, "time": time,
        "DB_FILE": DB_FILE, "_db_lock": threading.Lock(),
        "_now_ts": lambda: "10:30:00",
    },
)
_db_save_session = ns["_db_save_session"]
_db_get_sessions = ns["_db_get_sessions"]
_db_get_session_detail = ns["_db_get_session_detail"]

c = Checker()

# 1. cc_version picked up from status["sessions"][X]["cc_version"] (the only
# place it's ever actually set live -- see the transcript-scanner worker)
status_with_version = {
    "started_at": "10:00:00",
    "agents": [{"id": "a1", "status": "done"}],
    "sessions": {"cli_sess_1": {"cc_version": "2.1.0", "project": "AOC"}},
}
_db_save_session(status_with_version, sid="sess_with_version")
row = _db_get_sessions()[0]
c.check("cc_version persisted from status.sessions[X].cc_version", row["cc_version"] == "2.1.0")

# 2. No cc_version anywhere in status["sessions"] -> stored as empty string,
# not None/missing (matches every other text column's "" default here)
status_no_version = {
    "started_at": "10:00:00",
    "agents": [{"id": "a2", "status": "done"}],
    "sessions": {"cli_sess_2": {"project": "AOC"}},
}
_db_save_session(status_no_version, sid="sess_no_version")
row2 = next(r for r in _db_get_sessions() if r["id"] == "sess_no_version")
c.check("no cc_version anywhere -> stored as empty string, not None", row2["cc_version"] == "")

# 3. No "sessions" key in status at all -> doesn't crash, same empty-string result
status_missing_sessions_key = {
    "started_at": "10:00:00",
    "agents": [{"id": "a3", "status": "done"}],
}
_db_save_session(status_missing_sessions_key, sid="sess_missing_key")
row3 = next(r for r in _db_get_sessions() if r["id"] == "sess_missing_key")
c.check("missing 'sessions' key entirely doesn't crash, still empty string", row3["cc_version"] == "")

# 4. Multiple concurrent CLI sessions, only one has cc_version set -- picks
# that one (same "whichever is set" simplification project/started_at
# already make for a multi-session snapshot)
status_multi = {
    "started_at": "10:00:00",
    "agents": [{"id": "a4", "status": "done"}],
    "sessions": {
        "cli_a": {"project": "AOC"},
        "cli_b": {"cc_version": "2.0.5", "project": "PHANTOM AI"},
    },
}
_db_save_session(status_multi, sid="sess_multi")
row4 = next(r for r in _db_get_sessions() if r["id"] == "sess_multi")
c.check("multi-session snapshot picks whichever session has cc_version set", row4["cc_version"] == "2.0.5")

# 5. _db_get_session_detail also surfaces cc_version (not just _db_get_sessions)
detail = _db_get_session_detail("sess_with_version")
c.check("_db_get_session_detail returns cc_version alongside the snapshot", detail["cc_version"] == "2.1.0")
detail_missing = _db_get_session_detail("sess_no_version")
c.check("_db_get_session_detail returns empty string, not None, when unset", detail_missing["cc_version"] == "")
detail_nonexistent = _db_get_session_detail("does_not_exist")
c.check("_db_get_session_detail on an unknown sid returns empty string, doesn't crash", detail_nonexistent.get("cc_version") == "")

shutil.rmtree(SCRATCH, ignore_errors=True)
c.finish()
