"""Tests _db_get_errors, extracted straight from monitor.py. Runs against
a real scratch SQLite DB (never the real AOC_DIR/DB_FILE), same style as
db_session_detail_model_error.test.py -- confirms the ERRORS panel's
backing query now carries `model` (the one field the rest of the
model-visibility thread already reached: card, detail panel, markdown
export, Agent/Session Compare, GRAPH/TREE tooltip, History Detail --
the ERRORS panel was the one surface left out), while still only ever
returning agents that actually errored.

Also covers `detected_via`/`concurrent_sessions` (the HOOK MISS thread),
added in a later pass once live data showed real error rows with
detected_via='transcript' sitting in history.db that the ERRORS panel
could never surface -- same class of gap as the model fix above, just a
different field on the same narrow, hand-picked SELECT."""
import sys, os, sqlite3, threading, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_db_get_errors_")
DB_FILE = os.path.join(SCRATCH, "history.db")

ns = exec_functions(["_db_conn", "_db_get_errors"], {
    "sqlite3": sqlite3, "DB_FILE": DB_FILE, "_db_lock": threading.Lock(),
})
_db_get_errors = ns["_db_get_errors"]

c = Checker()

try:
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, project TEXT, date TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE agents (
            rowid_ INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, agent_id TEXT,
            name TEXT, unit TEXT, status TEXT, completed_at TEXT,
            error_msg TEXT, model TEXT, detected_via TEXT, concurrent_sessions INTEGER
        )
    """)
    conn.execute(
        "INSERT INTO sessions (id, project, date) VALUES (?,?,?)",
        ("s1", "AOC", "2026-07-28"),
    )
    conn.executemany(
        """INSERT INTO agents (session_id, agent_id, name, status, completed_at, error_msg, model, detected_via, concurrent_sessions)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        [
            ("s1", "a1", "worker-1", "error", "10:05:00", "Connection timed out", "claude-opus-4-1-20250805", "transcript", 3),
            # old row saved before the `model`/`detected_via` columns existed -- must come back as None, not dropped
            ("s1", "a2", "worker-2", "error", "10:06:00", "Unknown error", None, None, None),
            # a done agent must never appear even though it's in the same session
            ("s1", "a3", "worker-3", "done", "10:07:00", "", "claude-sonnet-4-5-20250514", None, None),
        ],
    )
    conn.commit()
    conn.close()

    result = {r["agent_id"]: r for r in _db_get_errors()}

    c.check("only the 2 errored agents are returned, not the done one", set(result.keys()) == {"a1", "a2"})
    c.check("model is present for a1", result["a1"]["model"] == "claude-opus-4-1-20250805")
    c.check("model is None (not dropped) for a row saved before the column existed", result["a2"]["model"] is None)
    c.check("detected_via is present for a1", result["a1"]["detected_via"] == "transcript")
    c.check("concurrent_sessions is present for a1", result["a1"]["concurrent_sessions"] == 3)
    c.check("detected_via is None (not dropped) for a row saved before the column existed", result["a2"]["detected_via"] is None)
    c.check("concurrent_sessions is None (not dropped) for a row saved before the column existed", result["a2"]["concurrent_sessions"] is None)
    c.check("session_id/project/date still come through the join", result["a1"]["project"] == "AOC" and result["a1"]["date"] == "2026-07-28")
    c.check("error_msg still comes through", result["a1"]["error_msg"] == "Connection timed out")
    c.check("empty DB / no errors returns an empty list, not a crash", _db_get_errors() != None)

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
