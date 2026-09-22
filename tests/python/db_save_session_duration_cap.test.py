"""Tests _db_save_session's duration_s "midnight crossover" sanity cap,
extracted straight from monitor.py. started_at/completed_at are bare
HH:MM:SS strings with no date component -- a negative raw diff is
assumed to mean "wrapped past midnight within the same session," but two
timestamps that are actually multiple calendar days apart with a merely
similar time-of-day (e.g. a stale started_at picked up by a re-running
autosave) produces the exact same small-negative raw diff, and the
correction then reports a bogus ~86400s duration. This is exactly what
the slowest_agents leaderboard (History -> Analytics, added the same day
as this fix) surfaced live the first time anything looked at individual
durations instead of only ever averaging them. Runs against a real
scratch SQLite DB, same convention as export_costs_by_model.test.py."""
import sys, os, sqlite3, threading, tempfile, shutil, json, time
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_db_save_session_duration_cap_")
DB_FILE = os.path.join(SCRATCH, "history.db")

conn = sqlite3.connect(DB_FILE)
conn.executescript("""
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY, project TEXT, orchestrator TEXT, date TEXT,
        started_at TEXT, ended_at TEXT, duration_s INTEGER DEFAULT 0,
        agents INTEGER DEFAULT 0, done INTEGER DEFAULT 0, errors INTEGER DEFAULT 0,
        tokens INTEGER DEFAULT 0, cost REAL DEFAULT 0,
        task_done INTEGER DEFAULT 0, task_total INTEGER DEFAULT 0,
        file_count INTEGER DEFAULT 0, snapshot TEXT, cc_version TEXT, waiting_on_you_s INTEGER DEFAULT 0
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

ns = exec_functions(["_db_conn", "_correct_hms_diff", "_db_save_session", "_MAX_PLAUSIBLE_DURATION_S"], {
    "sqlite3": sqlite3, "os": os, "json": json, "datetime": datetime, "time": time,
    "DB_FILE": DB_FILE, "_db_lock": threading.Lock(),
    "_now_ts": lambda: "10:00:00",  # stubbed -- see each case below for why
})
_db_save_session = ns["_db_save_session"]
_correct_hms_diff = ns["_correct_hms_diff"]

c = Checker()

# _correct_hms_diff in isolation -- extracted out of _db_save_session's two
# near-identical inline copies (session-level dur_s, per-agent a_dur) during
# a simplification pass, so this is now directly testable on its own.
c.check("_correct_hms_diff: positive same-day diff passes through unchanged", _correct_hms_diff(1800) == 1800)
c.check("_correct_hms_diff: negative diff gets the +86400 midnight-crossover correction (23:50->00:10, 20 real min)", _correct_hms_diff(-85200) == 1200)
c.check("_correct_hms_diff: a corrected value past the plausibility ceiling is zeroed (e.g. -600 corrects to 85800, past the 12h cap)", _correct_hms_diff(-600) == 0)
c.check("_correct_hms_diff: a positive value past the ceiling is zeroed too (no crossover needed)", _correct_hms_diff(50000) == 0)
c.check("_correct_hms_diff: exactly at the ceiling is kept, not zeroed (inclusive boundary)",
        _correct_hms_diff(ns["_MAX_PLAUSIBLE_DURATION_S"]) == ns["_MAX_PLAUSIBLE_DURATION_S"])
c.check("_correct_hms_diff: zero stays zero", _correct_hms_diff(0) == 0)

def read_session(sid):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT duration_s FROM sessions WHERE id=?", (sid,)).fetchone()
    conn.close()
    return row["duration_s"] if row else None

def read_agent(sid, aid):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT duration_s FROM agents WHERE session_id=? AND agent_id=?", (sid, aid)).fetchone()
    conn.close()
    return row["duration_s"] if row else None

# 1. Session-level: started_at (10:00:05) is 5s AFTER the stubbed ended_at
# (10:00:00) -- raw diff -5s, midnight-crossover correction would report
# ~86395s. Must be capped to 0, not stored as a bogus near-24h duration.
status1 = {
    "started_at": "10:00:05",
    "agents": [{"id": "a1", "status": "done"}],
}
_db_save_session(status1, sid="sess_artifact")
c.check("session-level duration_s capped to 0 for an implausible near-24h correction",
        read_session("sess_artifact") == 0)

# 2. Same artifact pattern at the per-agent level.
status2 = {
    "started_at": "09:00:00",
    "agents": [{"id": "a2", "status": "done", "started_at": "11:00:05", "completed_at": "11:00:00"}],
}
_db_save_session(status2, sid="sess_agent_artifact")
c.check("per-agent duration_s capped to 0 for the same implausible correction",
        read_agent("sess_agent_artifact", "a2") == 0)

# 3. A genuine, plausible duration (well under the cap) must be stored
# as-is, not zeroed out by an overly-aggressive cap.
status3 = {
    "started_at": "09:00:00",
    "agents": [{"id": "a3", "status": "done", "started_at": "09:00:00", "completed_at": "09:30:00"}],
}
_db_save_session(status3, sid="sess_normal")
c.check("session-level duration_s stored normally when plausible (ended_at stub 10:00:00, started 09:00:00 = 3600s)",
        read_session("sess_normal") == 3600)
c.check("per-agent duration_s stored normally when plausible (30 min)",
        read_agent("sess_normal", "a3") == 1800)

# 4. A real (non-artifact) midnight crossover -- started late at night,
# completed just after midnight, well under the plausibility ceiling --
# must still get the correction applied, not be zeroed.
status4 = {
    "started_at": "09:00:00",
    "agents": [{"id": "a4", "status": "done", "started_at": "23:50:00", "completed_at": "00:10:00"}],
}
_db_save_session(status4, sid="sess_real_crossover")
c.check("a real midnight crossover (23:50 -> 00:10, 20 real minutes) still corrects to 1200s, not zeroed",
        read_agent("sess_real_crossover", "a4") == 1200)

shutil.rmtree(SCRATCH, ignore_errors=True)
c.finish()
