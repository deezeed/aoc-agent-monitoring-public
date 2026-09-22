"""Tests the by_day_hook_reliability aggregate in _db_analytics, extracted
straight from monitor.py. Runs against a real scratch SQLite DB (never the
real AOC_DIR/DB_FILE), same style as db_analytics_by_model.test.py --
confirms the day-level view of hook_misses/hook_miss_concurrency (which
otherwise only ever surface an all-time total) correctly attributes each
agent's detected_via to the date its parent session ran on."""
import sys, os, sqlite3, threading, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_db_analytics_hook_")
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
    # _db_analytics is one function covering sessions/agents/file_changes in
    # a single try/except -- an empty-but-present file_changes table is
    # required even though this test doesn't care about file_hotspots, or
    # the whole call falls through to the except branch and by_day comes
    # back empty too.
    conn.execute("CREATE TABLE file_changes (session_id TEXT, agent_id TEXT, path TEXT, type TEXT, lines INTEGER DEFAULT 0)")
    conn.executemany(
        "INSERT INTO sessions (id, date) VALUES (?,?)",
        [("s1", "2026-07-20"), ("s2", "2026-07-21")],
    )
    rows = [
        # session_id, agent_id, detected_via
        ("s1", "a1", "hook"),
        ("s1", "a2", "hook"),
        ("s1", "a3", "transcript"),
        ("s2", "a4", "transcript"),
        ("s2", "a5", "transcript"),
        # NULL detected_via (hook-detected agents from before the column
        # existed) must still count toward `total`, just not `misses`.
        ("s2", "a6", None),
    ]
    conn.executemany(
        "INSERT INTO agents (session_id, agent_id, detected_via) VALUES (?,?,?)", rows
    )
    conn.commit()
    conn.close()

    result = _db_analytics()
    by_day = {r["date"]: r for r in result["by_day_hook_reliability"]}

    c.check("one row per date via the sessions join", set(by_day.keys()) == {"2026-07-20", "2026-07-21"})
    c.check("2026-07-20: 1 miss out of 3 total agents", by_day["2026-07-20"]["misses"] == 1 and by_day["2026-07-20"]["total"] == 3)
    c.check("2026-07-21: 2 misses out of 3 total agents (NULL detected_via counted in total, not misses)",
            by_day["2026-07-21"]["misses"] == 2 and by_day["2026-07-21"]["total"] == 3)
    c.check("rows ordered ascending by date", [r["date"] for r in result["by_day_hook_reliability"]] == ["2026-07-20", "2026-07-21"])

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
