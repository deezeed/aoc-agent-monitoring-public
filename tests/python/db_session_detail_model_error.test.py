"""Tests _db_get_session_detail, extracted straight from monitor.py. Runs
against a real scratch SQLite DB (never the real AOC_DIR/DB_FILE), same
style as db_analytics_by_model.test.py -- confirms the per-agent `model`,
`error_msg`, `subagent_type`, `tool_use_count`, `detected_via`,
`concurrent_sessions`, and `parent_id` columns (already tracked for the
live view, Compare, and Analytics) come back through the History Detail
read path too, instead of being silently dropped by a SELECT that
predates those columns."""
import sys, os, sqlite3, threading, tempfile, shutil, json

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_session_detail_")
DB_FILE = os.path.join(SCRATCH, "history.db")

ns = exec_functions(["_db_conn", "_db_get_session_detail", "_tags_list"], {
    "sqlite3": sqlite3, "DB_FILE": DB_FILE, "_db_lock": threading.Lock(),
    "json": json,
})
_db_get_session_detail = ns["_db_get_session_detail"]

c = Checker()

try:
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, project TEXT, snapshot TEXT, cc_version TEXT, tags TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE agents (
            rowid_ INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, agent_id TEXT,
            name TEXT, unit TEXT, status TEXT, started_at TEXT, completed_at TEXT,
            duration_s INTEGER DEFAULT 0, tokens INTEGER DEFAULT 0, cost REAL DEFAULT 0,
            task_done INTEGER DEFAULT 0, task_total INTEGER DEFAULT 0,
            file_count INTEGER DEFAULT 0, error_msg TEXT, model TEXT,
            subagent_type TEXT, tool_use_count INTEGER,
            detected_via TEXT, concurrent_sessions INTEGER, parent_id TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE file_changes (
            session_id TEXT, agent_id TEXT, path TEXT, type TEXT, lines INTEGER
        )
    """)
    conn.execute(
        "INSERT INTO sessions (id, project, snapshot) VALUES (?,?,?)",
        ("s1", "AOC", json.dumps({"project": "AOC", "started_at": "10:00"})),
    )
    conn.executemany(
        """INSERT INTO agents (session_id, agent_id, name, status, model, error_msg, subagent_type, tool_use_count,
                               detected_via, concurrent_sessions, parent_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [
            ("s1", "a1", "worker-1", "done", "claude-sonnet-4-5-20250514", "", "Explore", 7, "transcript", 3, None),
            ("s1", "a2", "worker-2", "error", "claude-opus-4-1-20250805", "Connection timed out after 30s", None, None, None, None, "a1"),
            # old row saved before the `model`/`subagent_type`/`tool_use_count`/`detected_via`/`parent_id` columns existed
            ("s1", "a3", "worker-3", "done", None, "", None, None, None, None, None),
        ],
    )
    conn.commit()
    conn.close()

    result = _db_get_session_detail("s1")
    agents = {a["agent_id"]: a for a in result["agents"]}

    c.check("returns all 3 agents", len(result["agents"]) == 3)
    c.check("model is present for a1", agents["a1"]["model"] == "claude-sonnet-4-5-20250514")
    c.check("model is present for a2", agents["a2"]["model"] == "claude-opus-4-1-20250805")
    c.check("agent saved before the model column existed comes back as None, not dropped",
            "a3" in agents and agents["a3"]["model"] is None)
    c.check("error_msg is present for the errored agent", agents["a2"]["error_msg"] == "Connection timed out after 30s")
    c.check("error_msg is empty (not missing) for the done agent", agents["a1"]["error_msg"] == "")
    c.check("subagent_type is present for a1", agents["a1"]["subagent_type"] == "Explore")
    c.check("tool_use_count is present for a1", agents["a1"]["tool_use_count"] == 7)
    c.check("subagent_type/tool_use_count are None (not dropped) when never captured",
            agents["a2"]["subagent_type"] is None and agents["a2"]["tool_use_count"] is None)
    c.check("detected_via is present for a1 (hook-miss agent)", agents["a1"]["detected_via"] == "transcript")
    c.check("concurrent_sessions is present for a1", agents["a1"]["concurrent_sessions"] == 3)
    c.check("detected_via/concurrent_sessions are None (not dropped) when never captured",
            agents["a2"]["detected_via"] is None and agents["a2"]["concurrent_sessions"] is None)
    c.check("parent_id is present for a2 (a1 spawned it)", agents["a2"]["parent_id"] == "a1")
    c.check("parent_id is None (not dropped) for an agent with no parent", agents["a1"]["parent_id"] is None)
    c.check("unknown session id returns empty agents list, not a crash", _db_get_session_detail("nope")["agents"] == [])

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
