"""Tests the file_hotspots aggregate in _db_analytics, extracted straight
from monitor.py. Mirrors db_analytics_by_model.test.py's structure. This
is the first time file_changes (path/type/lines, one row per file any
agent ever touched, already used per-session by the FILES tab) gets
aggregated across all of history -- ranks files by how often they've
been touched, not by cost, so it's a distinct dimension from
by_project/by_model/by_subagent_type."""
import sys, os, sqlite3, threading, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_db_analytics_file_hotspots_")
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
        CREATE TABLE file_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT, agent_id TEXT, path TEXT, type TEXT, lines INTEGER DEFAULT 0
        )
    """)
    # _db_analytics is one function covering sessions/agents/file_changes in
    # a single try/except -- an empty-but-present agents table is required
    # even though this test only cares about file_hotspots, or the whole
    # call falls through to the except branch and file_hotspots comes back
    # empty too (see db_analytics_by_model.test.py's own subagent_type
    # column fix for the same class of gap, other direction).
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
    conn.executemany(
        "INSERT INTO sessions (id, date) VALUES (?,?)",
        [("s1", "2026-07-20"), ("s2", "2026-07-21")],
    )
    rows = [
        # session_id, agent_id, path, type, lines
        ("s1", "a1", "monitor.py", "changed", 40),
        ("s1", "a2", "monitor.py", "changed", 10),  # same file, same session, different agent
        ("s2", "a3", "monitor.py", "changed", 5),   # same file, different session
        ("s2", "a4", "README.md", "changed", 20),
        ("s2", "a4", "", "changed", 3),  # blank path -- must be excluded
    ]
    conn.executemany(
        "INSERT INTO file_changes (session_id, agent_id, path, type, lines) VALUES (?,?,?,?,?)",
        rows
    )
    conn.commit()
    conn.close()

    result = _db_analytics()
    hotspots = {r["path"]: r for r in result["file_hotspots"]}

    c.check("blank path excluded", "" not in hotspots)
    c.check("monitor.py counted 3 times (every touch, not deduped)", hotspots["monitor.py"]["changes"] == 3)
    c.check("monitor.py distinct sessions is 2 (s1 touched twice, s2 once)", hotspots["monitor.py"]["sessions"] == 2)
    c.check("monitor.py total_lines sums every touch", hotspots["monitor.py"]["total_lines"] == 55)
    c.check("README.md counted once", hotspots["README.md"]["changes"] == 1)
    c.check("sorted by changes descending", result["file_hotspots"][0]["path"] == "monitor.py")

    by_day_hotspots = {(r["date"], r["path"]): r for r in result["by_day_file_hotspots"]}
    c.check("by_day_file_hotspots has a (date, path) row for monitor.py on 2026-07-20",
            by_day_hotspots.get(("2026-07-20", "monitor.py"), {}).get("changes") == 2)
    c.check("by_day_file_hotspots has a separate row for the same file on a different day",
            by_day_hotspots.get(("2026-07-21", "monitor.py"), {}).get("changes") == 1)
    c.check("by_day_file_hotspots includes README.md too", ("2026-07-21", "README.md") in by_day_hotspots)

    # LIMIT 20 -- insert enough distinct paths to exceed it and confirm the cap
    conn = sqlite3.connect(DB_FILE)
    conn.executemany(
        "INSERT INTO file_changes (session_id, agent_id, path, type, lines) VALUES (?,?,?,?,?)",
        [("s2", f"a{i}", f"file_{i}.py", "new", 1) for i in range(30)]
    )
    conn.commit()
    conn.close()
    result2 = _db_analytics()
    c.check("file_hotspots capped at 20 rows", len(result2["file_hotspots"]) == 20)
    c.check("monitor.py (3 changes) still outranks the 30 single-touch files", result2["file_hotspots"][0]["path"] == "monitor.py")

    top20_paths = {r["path"] for r in result2["file_hotspots"]}
    by_day_paths = {r["path"] for r in result2["by_day_file_hotspots"]}
    c.check("by_day_file_hotspots stays scoped to the top-20 file_hotspots paths, not every file ever touched",
            by_day_paths.issubset(top20_paths) and len(by_day_paths) <= 20)

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
