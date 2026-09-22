"""Tests the task_done/task_total sums added to _db_analytics' by_day and
by_project aggregates, extracted straight from monitor.py. task_done/
task_total were already persisted per session (for the SESSIONS list
rows' own task-completion chips) but never summed into by_day (for a
TASK COMPLETION RATE BY DAY trend) or by_project (for a per-project
completion-rate figure) before this."""
import sys, os, sqlite3, threading, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_db_analytics_task_completion_")
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
        "INSERT INTO sessions (id, project, date, task_done, task_total) VALUES (?,?,?,?,?)",
        [
            ("s1", "AOC", "2026-07-20", 8, 10),
            ("s2", "AOC", "2026-07-20", 2, 5),           # same day, different session -- sums with s1
            ("s3", "PHANTOM AI", "2026-07-21", 1, 4),
            ("s4", "", "2026-07-21", 3, 3),               # no project -- excluded from by_project, still in by_day
        ],
    )
    conn.commit()
    conn.close()

    result = _db_analytics()

    by_day = {r["date"]: r for r in result["by_day"]}
    c.check("by_day sums task_done across same-day sessions (8+2=10)", by_day["2026-07-20"]["task_done"] == 10)
    c.check("by_day sums task_total across same-day sessions (10+5=15)", by_day["2026-07-20"]["task_total"] == 15)
    c.check("by_day includes the no-project session's tasks too (1+3=4 done)", by_day["2026-07-21"]["task_done"] == 4)
    c.check("by_day includes the no-project session's tasks too (4+3=7 total)", by_day["2026-07-21"]["task_total"] == 7)

    by_project = {r["project"]: r for r in result["by_project"]}
    c.check("by_project sums task_done across a project's sessions (8+2=10)", by_project["AOC"]["task_done"] == 10)
    c.check("by_project sums task_total across a project's sessions (10+5=15)", by_project["AOC"]["task_total"] == 15)
    c.check("by_project single-session project sums correctly too", by_project["PHANTOM AI"]["task_done"] == 1
            and by_project["PHANTOM AI"]["task_total"] == 4)
    c.check("by_project excludes the no-project session entirely (matches existing project!='' filter)",
            "" not in by_project)

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
