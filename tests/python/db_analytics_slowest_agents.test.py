"""Tests the slowest_agents leaderboard in _db_analytics, extracted
straight from monitor.py. Unlike by_project/by_model/by_subagent_type/
file_hotspots (all GROUP BY aggregates), this is a ranked list of
individual agent rows -- duration_s has always been stored per agent but
was only ever averaged into today's session-level avg_duration_s before
this, never surfaced as its own leaderboard."""
import sys, os, sqlite3, threading, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_db_analytics_slowest_agents_")
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
        "INSERT INTO sessions (id, project, date) VALUES (?,?,?)",
        [("s1", "AOC", "2026-07-20"), ("s2", "PHANTOM AI", "2026-07-21")],
    )
    rows = [
        # session_id, agent_id, name, subagent_type, model, duration_s, status, cost
        ("s1", "a1", "Fix bug", "Explore", "claude-sonnet-4-5-20250514", 300, "done", 1.0),
        ("s1", "a2", "Slow one", "Plan", "claude-opus-4-1-20250805", 900, "error", 5.0),
        ("s2", "a3", "Quick task", "Explore", "claude-sonnet-4-5-20250514", 30, "done", 0.2),
        ("s2", "a4", "Zero duration", "Explore", "claude-sonnet-4-5-20250514", 0, "done", 0.1),  # excluded
    ]
    conn.executemany(
        """INSERT INTO agents (session_id, agent_id, name, subagent_type, model, duration_s, status, cost)
           VALUES (?,?,?,?,?,?,?,?)""", rows
    )
    conn.commit()
    conn.close()

    result = _db_analytics()
    leaderboard = result["slowest_agents"]

    c.check("agents with duration_s=0 are excluded", not any(r["agent_id"] == "a4" for r in leaderboard))
    c.check("sorted by duration_s descending", [r["agent_id"] for r in leaderboard] == ["a2", "a1", "a3"])
    c.check("slowest row carries subagent_type/model/project via the sessions join",
            leaderboard[0]["subagent_type"] == "Plan" and leaderboard[0]["model"] == "claude-opus-4-1-20250805"
            and leaderboard[0]["project"] == "AOC")
    c.check("slowest row carries its own status/cost", leaderboard[0]["status"] == "error" and leaderboard[0]["cost"] == 5.0)
    c.check("session_id present for click-through to History", leaderboard[0]["session_id"] == "s1")

    # LIMIT 20 -- insert enough distinct agents to exceed it and confirm the cap
    conn = sqlite3.connect(DB_FILE)
    conn.executemany(
        "INSERT INTO agents (session_id, agent_id, duration_s, status) VALUES (?,?,?,?)",
        [("s2", f"b{i}", 50 + i, "done") for i in range(30)]
    )
    conn.commit()
    conn.close()
    result2 = _db_analytics()
    c.check("slowest_agents capped at 20 rows", len(result2["slowest_agents"]) == 20)
    c.check("the 900s agent still ranks first among the larger set", result2["slowest_agents"][0]["agent_id"] == "a2")

    # A ~86400s "midnight crossover" artifact (see _db_save_session's own
    # comment on how a multi-day-apart started_at/completed_at with a
    # merely similar time-of-day produces this) must not appear on the
    # leaderboard as if it were a real measurement.
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""INSERT INTO agents (session_id, agent_id, duration_s, status)
                     VALUES ('s1','artifact',86349,'done')""")
    conn.commit()
    conn.close()
    result3 = _db_analytics()
    c.check("implausibly long duration_s (past _MAX_PLAUSIBLE_DURATION_S) excluded from the leaderboard",
            not any(r["agent_id"] == "artifact" for r in result3["slowest_agents"]))

    ceiling = ns["_MAX_PLAUSIBLE_DURATION_S"]
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""INSERT INTO agents (session_id, agent_id, duration_s, status)
                     VALUES ('s1','at_ceiling',?,'done')""", (ceiling,))
    conn.commit()
    conn.close()
    result4 = _db_analytics()
    c.check("a duration exactly at the ceiling is still included (boundary inclusive)",
            any(r["agent_id"] == "at_ceiling" for r in result4["slowest_agents"]))

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
