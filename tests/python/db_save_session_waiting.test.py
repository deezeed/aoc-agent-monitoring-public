"""Tests _db_save_session's waiting_on_you_s persistence, extracted
straight from monitor.py. Companion to accumulate_waiting_time.test.py:
that test covers the state-machine that builds up each session's
waiting_on_you_accum_s live; this one covers folding it (plus any
still-open wait period) into the sessions.waiting_on_you_s column at
save time, the step that makes the WAITING ON YOU history trend possible.
Runs against a real scratch SQLite DB, same convention as
db_save_session_duration_cap.test.py."""
import sys, os, sqlite3, threading, tempfile, shutil, json
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_db_save_session_waiting_")
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


class _FixedTime:
    """Stub for the `time` module -- only .time() is used by
    _db_save_session, pinned so "elapsed since waiting_on_you_since" is
    deterministic instead of depending on wall-clock speed."""
    def __init__(self, now):
        self._now = now
    def time(self):
        return self._now


def make_ns(fixed_now):
    ns = exec_functions(["_db_conn", "_correct_hms_diff", "_db_save_session", "_MAX_PLAUSIBLE_DURATION_S"], {
        "sqlite3": sqlite3, "os": os, "json": json, "datetime": datetime, "time": _FixedTime(fixed_now),
        "DB_FILE": DB_FILE, "_db_lock": threading.Lock(),
        "_now_ts": lambda: "10:00:00",
    })
    return ns["_db_save_session"]


def read_waiting(sid):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT waiting_on_you_s FROM sessions WHERE id=?", (sid,)).fetchone()
    conn.close()
    return row["waiting_on_you_s"] if row else None


c = Checker()

# 1. No sessions dict at all -> 0, not a crash
_db_save_session_ = make_ns(1000.0)
status_none = {"agents": [{"id": "a1", "status": "done"}]}
_db_save_session_(status_none, sid="sess_none")
c.check("no sessions dict -> waiting_on_you_s stored as 0", read_waiting("sess_none") == 0)

# 2. A closed wait period (already committed to waiting_on_you_accum_s by
# _accumulate_waiting_time) is persisted as-is.
status_closed = {
    "agents": [{"id": "a1", "status": "done"}],
    "sessions": {"s1": {"waiting_on_you": False, "waiting_on_you_accum_s": 120}},
}
_db_save_session_(status_closed, sid="sess_closed")
c.check("a closed wait period's accumulated total is persisted", read_waiting("sess_closed") == 120)

# 3. A still-open wait period at save time: the elapsed-so-far (now -
# waiting_on_you_since) must be folded in on top of anything already
# accumulated, not dropped just because the session hasn't flipped back
# to waiting_on_you=False yet.
_db_save_session_open = make_ns(1500.0)
status_open = {
    "agents": [{"id": "a1", "status": "done"}],
    "sessions": {"s1": {"waiting_on_you": True, "waiting_on_you_since": 1400.0, "waiting_on_you_accum_s": 50}},
}
_db_save_session_open(status_open, sid="sess_open")
c.check("a still-open wait period adds elapsed-so-far to the prior total (50+100=150)",
        read_waiting("sess_open") == 150)

# 4. Multi-session snapshot: sums across every session, mirroring how
# tokens/cost already get attributed to the whole snapshot -- not
# cc_version's "pick whichever session has one" simplification, since
# waiting time is meaningfully summable across concurrent sessions.
_db_save_session_multi = make_ns(2000.0)
status_multi = {
    "agents": [{"id": "a1", "status": "done"}],
    "sessions": {
        "s1": {"waiting_on_you": False, "waiting_on_you_accum_s": 60},
        "s2": {"waiting_on_you": True, "waiting_on_you_since": 1950.0, "waiting_on_you_accum_s": 10},
        "s3": {"waiting_on_you": False},  # never waited -- contributes 0
    },
}
_db_save_session_multi(status_multi, sid="sess_multi")
c.check("multi-session snapshot sums waiting time across every session (60 + (10+50) + 0 = 120)",
        read_waiting("sess_multi") == 120)

shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
