"""Tests the retry_count/retry_patterns aggregate in _db_analytics,
extracted straight from monitor.py. Detects an agent named the same as
an earlier, errored one, run again in that SAME session -- exact-name
match only (same reasoning common_errors' exact-text grouping already
uses: Claude Code doesn't rename a retried Task call). Grouped and
walked in Python since this needs an ordered same-group comparison SQL
doesn't do cheaply."""
import sys, os, sqlite3, threading, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_db_analytics_retry_patterns_")
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
    conn.executemany("INSERT INTO sessions (id) VALUES (?)", [("s1",), ("s2",), ("s3",)])
    rows = [
        # session_id, agent_id, name, status -- rowid order is insertion order,
        # which is what the ORDER BY ... rowid_ in the real query relies on
        # for "which attempt came first" within a (session, name) group.
        ("s1", "a1", "Fix bug X", "error"),
        ("s1", "a2", "Fix bug X", "done"),      # retry after error, same session, same name -> 1 retry
        ("s1", "a3", "Fix bug X", "error"),     # errored AGAIN -> the a2->a3 transition is not a retry (a2 wasn't error)
        ("s1", "a4", "Fix bug X", "done"),      # retried again after a3's error -> 2nd retry
        ("s2", "a5", "Fix bug X", "error"),     # different session, same name, only 1 attempt -> no retry (no 2nd attempt)
        ("s3", "a6", "Explore repo", "done"),   # never errored -> no retry
        ("s1", "a7", "", "error"),               # blank name -- excluded entirely, can't be grouped
    ]
    conn.executemany(
        "INSERT INTO agents (session_id, agent_id, name, status) VALUES (?,?,?,?)", rows
    )
    conn.commit()
    conn.close()

    result = _db_analytics()
    patterns = {r["name"]: r for r in result["retry_patterns"]}

    c.check("'Fix bug X' has 2 retries within session s1 (a1->a2, a3->a4)", patterns["Fix bug X"]["retries"] == 2)
    c.check("retries attributed to 1 distinct session (both retries happened in s1)",
            patterns["Fix bug X"]["sessions"] == 1)
    c.check("a name with only one attempt in a session (s2) contributes no retry",
            result["retry_count"] == 2)  # only the 2 from s1, s2's single error doesn't count
    c.check("a name that never errored is absent from retry_patterns", "Explore repo" not in patterns)
    c.check("blank-name agents are excluded, not grouped together as ''", "" not in patterns)
    c.check("retry_count matches the sum across all patterns", result["retry_count"] == sum(r["retries"] for r in patterns.values()))

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
