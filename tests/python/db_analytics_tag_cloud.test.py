"""Tests the tag_cloud aggregate in _db_analytics, extracted straight
from monitor.py. Tags live in one comma-separated sessions.tags column
(_sanitize_tags), so this aggregates in Python rather than SQL (same
reasoning as by_file_type). Before this, the only way to discover what
tags you'd already used was to remember them or browse individual
sessions one at a time."""
import sys, os, sqlite3, threading, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_db_analytics_tag_cloud_")
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
        "INSERT INTO sessions (id, project, tags) VALUES (?,?,?)",
        [
            ("s1", "AOC", "refactor,bugfix"),
            ("s2", "AOC", "refactor"),          # "refactor" recurs -- must sum, not overwrite
            ("s3", "PHANTOM AI", "experiment"),
            ("s4", "AOC", ""),                   # untagged -- excluded from the cloud entirely
        ],
    )
    conn.commit()
    conn.close()

    result = _db_analytics()
    cloud = {r["tag"]: r["count"] for r in result["tag_cloud"]}

    c.check("recurring tag counted across every session that carries it (2)", cloud["refactor"] == 2)
    c.check("a tag used once is still listed", cloud["bugfix"] == 1)
    c.check("a tag from a different project is included too", cloud["experiment"] == 1)
    c.check("no phantom entry for the untagged session", "" not in cloud)
    c.check("sorted by count descending", result["tag_cloud"][0]["tag"] == "refactor")

    # LIMIT 30 -- insert enough distinct tags to exceed it
    conn = sqlite3.connect(DB_FILE)
    conn.executemany(
        "INSERT INTO sessions (id, project, tags) VALUES (?,?,?)",
        [(f"s{i+10}", "AOC", f"tag{i}") for i in range(40)]
    )
    conn.commit()
    conn.close()
    result2 = _db_analytics()
    c.check("tag_cloud capped at 30 rows", len(result2["tag_cloud"]) == 30)
    c.check("refactor (2 uses) still outranks the 40 single-use tags", result2["tag_cloud"][0]["tag"] == "refactor")

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
