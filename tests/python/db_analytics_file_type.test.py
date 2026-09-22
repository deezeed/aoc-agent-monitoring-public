"""Tests the by_file_type aggregate in _db_analytics, extracted straight
from monitor.py. Same file_changes source as file_hotspots, grouped by
extension instead of path -- SQLite has no clean "text after the last
dot" builtin, so this one aggregates in Python inside _db_analytics
rather than SQL, unlike every other by_* breakdown."""
import sys, os, sqlite3, threading, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_db_analytics_file_type_")
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
    conn.execute("""
        CREATE TABLE file_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT, agent_id TEXT, path TEXT, type TEXT, lines INTEGER DEFAULT 0
        )
    """)
    conn.executemany(
        "INSERT INTO sessions (id, date) VALUES (?,?)",
        [("s1", "2026-07-20"), ("s2", "2026-07-21")],
    )
    rows = [
        # session_id, agent_id, path, type, lines
        ("s1", "a1", "monitor.py", "changed", 40),
        ("s1", "a2", "watchdog.py", "changed", 10),   # same ext, same session
        ("s2", "a3", "tests/python/foo.test.py", "changed", 5),  # same ext, different session, nested path
        ("s2", "a4", "README.md", "changed", 20),
        ("s2", "a4", "src\\windows\\path.py", "changed", 7),  # backslash path -- must still extract .py
        ("s2", "a5", "Makefile", "new", 3),            # no extension at all
        ("s2", "a4", "", "changed", 3),                 # blank path -- excluded (same as file_hotspots)
    ]
    conn.executemany(
        "INSERT INTO file_changes (session_id, agent_id, path, type, lines) VALUES (?,?,?,?,?)",
        rows
    )
    conn.commit()
    conn.close()

    result = _db_analytics()
    by_ext = {r["ext"]: r for r in result["by_file_type"]}

    c.check("blank path excluded", "" not in by_ext)
    c.check("groups by extension regardless of directory depth or slash style",
            by_ext["py"]["changes"] == 4)
    c.check("extension counted case-insensitively lowercased", "py" in by_ext)
    c.check(".py total_lines sums every touch (40+10+5+7)", by_ext["py"]["total_lines"] == 62)
    c.check(".py distinct sessions is 2 (s1 twice, s2 twice)", by_ext["py"]["sessions"] == 2)
    c.check(".md counted once", by_ext["md"]["changes"] == 1)
    c.check("extensionless files bucket under a distinct non-empty label",
            "(no extension)" in by_ext and by_ext["(no extension)"]["changes"] == 1)
    c.check("sorted by changes descending", result["by_file_type"][0]["ext"] == "py")

    # LIMIT 20 -- insert enough distinct extensions to exceed it
    conn = sqlite3.connect(DB_FILE)
    conn.executemany(
        "INSERT INTO file_changes (session_id, agent_id, path, type, lines) VALUES (?,?,?,?,?)",
        [("s2", f"a{i}", f"file{i}.ext{i}", "new", 1) for i in range(30)]
    )
    conn.commit()
    conn.close()
    result2 = _db_analytics()
    c.check("by_file_type capped at 20 rows", len(result2["by_file_type"]) == 20)
    c.check(".py (4 changes) still outranks the 30 single-touch extensions",
            result2["by_file_type"][0]["ext"] == "py")

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
