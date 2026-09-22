"""Tests the common_errors aggregate in _db_analytics, extracted straight
from monitor.py. error_msg has always been persisted per errored agent
and shown in the ERRORS panel, but only ever as a flat chronological
list -- never grouped to answer "what keeps breaking". Exact-text
grouping (not fuzzy matching, which SQL can't do cheaply): a genuinely
recurring failure (the same exception, the same timeout) produces
identical text every time it fires, so this still catches the case that
matters."""
import sys, os, sqlite3, threading, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_db_analytics_common_errors_")
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
        "INSERT INTO sessions (id, project) VALUES (?,?)",
        [("s1", "AOC"), ("s2", "AOC"), ("s3", "PHANTOM AI"), ("s4", "AOC")],
    )
    rows = [
        # rowid order matters here (insertion order == rowid_ order) for the
        # "most recent occurrence" correlated subquery -- inserted oldest first.
        ("s1", "a1", "error", "Timeout waiting for response"),
        ("s2", "a2", "error", "Timeout waiting for response"),   # recurs, different session -- last one should win
        ("s3", "a3", "error", "Timeout waiting for response"),   # recurs a third time, in a different project
        ("s3", "a4", "error", "FileNotFoundError: config.yaml"), # a different, one-off error
        ("s4", "a5", "done", "should never appear -- status != error"),
        ("s4", "a6", "error", ""),  # blank error_msg -- excluded
    ]
    conn.executemany(
        "INSERT INTO agents (session_id, agent_id, status, error_msg) VALUES (?,?,?,?)",
        rows
    )
    conn.commit()
    conn.close()

    result = _db_analytics()
    errors = {r["error_msg"]: r for r in result["common_errors"]}

    c.check("only error-status, non-blank error_msg rows are counted",
            "should never appear -- status != error" not in errors and "" not in errors)
    c.check("recurring error grouped by exact text, counted 3 times", errors["Timeout waiting for response"]["occurrences"] == 3)
    c.check("a one-off error is still listed, counted once", errors["FileNotFoundError: config.yaml"]["occurrences"] == 1)
    c.check("sorted by occurrence count descending", result["common_errors"][0]["error_msg"] == "Timeout waiting for response")
    c.check("last_session_id points at the MOST RECENT occurrence (s3, inserted last)",
            errors["Timeout waiting for response"]["last_session_id"] == "s3")
    c.check("project reflects that same most-recent occurrence's session (PHANTOM AI, not AOC)",
            errors["Timeout waiting for response"]["project"] == "PHANTOM AI")

    # LIMIT 15 -- insert enough distinct one-off errors to exceed it
    conn = sqlite3.connect(DB_FILE)
    conn.executemany(
        "INSERT INTO agents (session_id, agent_id, status, error_msg) VALUES (?,?,?,?)",
        [("s4", f"b{i}", "error", f"unique error {i}") for i in range(20)]
    )
    conn.commit()
    conn.close()
    result2 = _db_analytics()
    c.check("common_errors capped at 15 rows", len(result2["common_errors"]) == 15)
    c.check("the 3x-recurring error still outranks the 20 one-off errors", result2["common_errors"][0]["error_msg"] == "Timeout waiting for response")

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
