"""Tests the waiting_on_you_s sum added to _db_analytics' by_day and
by_project aggregates, extracted straight from monitor.py.
waiting_on_you_s is persisted per session by _db_save_session (see
db_save_session_waiting.test.py) but was never summed into either
aggregate before -- by_day is the WAITING ON YOU BY DAY trend chart,
by_project surfaces per-project totals in the COST BY PROJECT tooltip."""
import sys, os, sqlite3, threading, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_db_analytics_waiting_on_you_")
DB_FILE = os.path.join(SCRATCH, "history.db")

ns = exec_functions(["_db_conn", "_db_analytics", "_MAX_PLAUSIBLE_DURATION_S", "_tags_list"], {
    "sqlite3": sqlite3, "DB_FILE": DB_FILE, "_db_lock": threading.Lock(),
})
_db_analytics = ns["_db_analytics"]

c = Checker()

try:
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, project TEXT, orchestrator TEXT, date TEXT,
            started_at TEXT, ended_at TEXT, duration_s INTEGER DEFAULT 0,
            agents INTEGER DEFAULT 0, done INTEGER DEFAULT 0, errors INTEGER DEFAULT 0,
            tokens INTEGER DEFAULT 0, cost REAL DEFAULT 0,
            task_done INTEGER DEFAULT 0, task_total INTEGER DEFAULT 0,
            file_count INTEGER DEFAULT 0, snapshot TEXT, waiting_on_you_s INTEGER DEFAULT 0, tags TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE agents (
            rowid_ INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, agent_id TEXT,
            name TEXT, unit TEXT, status TEXT, started_at TEXT, completed_at TEXT,
            duration_s INTEGER DEFAULT 0, tokens INTEGER DEFAULT 0, cost REAL DEFAULT 0,
            task_done INTEGER DEFAULT 0, task_total INTEGER DEFAULT 0,
            file_count INTEGER DEFAULT 0, error_msg TEXT,
            detected_via TEXT, concurrent_sessions INTEGER, model TEXT, subagent_type TEXT,
            tool_use_count INTEGER
        )
    """)
    conn.execute("CREATE TABLE file_changes (session_id TEXT, agent_id TEXT, path TEXT, type TEXT, lines INTEGER DEFAULT 0)")
    conn.executemany(
        "INSERT INTO sessions (id, project, date, waiting_on_you_s) VALUES (?,?,?,?)",
        [
            ("s1", "AOC", "2026-07-20", 300),
            ("s2", "AOC", "2026-07-20", 120),       # same day, different session -- sums with s1
            ("s3", "PHANTOM AI", "2026-07-21", 0),  # a session that never waited -- contributes 0, not NULL
            ("s4", "AOC", "2026-07-21", 60),        # same project as s1/s2, different day -- sums into AOC's total regardless of date
        ],
    )
    conn.commit()
    conn.close()

    result = _db_analytics()
    by_day = {r["date"]: r for r in result["by_day"]}

    c.check("by_day sums waiting_on_you_s across same-day sessions (300+120=420)",
            by_day["2026-07-20"]["waiting_on_you_s"] == 420)
    c.check("a day with only a zero-waiting session still reports 0, not None",
            by_day["2026-07-21"]["waiting_on_you_s"] == 60)  # s3(0) + s4(60)

    by_project = {r["project"]: r for r in result["by_project"]}
    c.check("by_project sums waiting_on_you_s across a project's sessions regardless of date (300+120+60=480)",
            by_project["AOC"]["waiting_on_you_s"] == 480)
    c.check("a project whose only session never waited reports 0, not None",
            by_project["PHANTOM AI"]["waiting_on_you_s"] == 0)

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
