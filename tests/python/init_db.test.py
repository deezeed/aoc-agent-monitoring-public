"""Tests _init_db, extracted straight from monitor.py. This is the actual
schema-migration function -- ironic that it had zero direct test coverage,
since every OTHER _db_analytics/_db_save_session test in this suite
bypasses it entirely by hand-building a scratch schema with every column
already present, which is exactly the class of gap that broke several of
those tests (twice, same day) when a new column got added to a real query
but not to their hand-built fixtures. This test runs the actual migration
function against a genuinely fresh (and then a genuinely pre-migrated) DB
file, so the ALTER TABLE guards themselves are what's under test."""
import sys, os, sqlite3, threading, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_init_db_")
DB_FILE = os.path.join(SCRATCH, "history.db")

ns = exec_functions(["_db_conn", "_init_db"], {
    "sqlite3": sqlite3, "DB_FILE": DB_FILE, "_db_lock": threading.Lock(),
})
_init_db = ns["_init_db"]

c = Checker()

def columns(table):
    conn = sqlite3.connect(DB_FILE)
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    conn.close()
    return cols

try:
    # 1. Against a file that doesn't exist yet at all -- the from-scratch case.
    c.check("DB file doesn't exist before _init_db runs", not os.path.exists(DB_FILE))
    _init_db()
    c.check("DB file exists after _init_db", os.path.exists(DB_FILE))

    sessions_cols = columns("sessions")
    agents_cols = columns("agents")
    file_changes_cols = columns("file_changes")

    c.check("sessions table has its base columns", {
        "id", "project", "orchestrator", "date", "started_at", "ended_at",
        "duration_s", "agents", "done", "errors", "tokens", "cost",
        "task_done", "task_total", "file_count", "snapshot",
    }.issubset(sessions_cols))
    c.check("sessions table got the cc_version migration column", "cc_version" in sessions_cols)

    c.check("agents table has its base columns", {
        "rowid_", "session_id", "agent_id", "name", "unit", "status",
        "started_at", "completed_at", "duration_s", "tokens", "cost",
        "task_done", "task_total", "file_count", "error_msg",
    }.issubset(agents_cols))
    c.check("agents table got all 6 migration columns", {
        "detected_via", "concurrent_sessions", "model", "subagent_type",
        "tool_use_count", "parent_id",
    }.issubset(agents_cols))

    c.check("file_changes table has its columns", {
        "id", "session_id", "agent_id", "path", "type", "lines",
    }.issubset(file_changes_cols))

    # 2. agents(session_id, agent_id) UNIQUE constraint actually exists --
    # this is what _db_save_session's ON CONFLICT(session_id, agent_id)
    # upsert depends on; without it, that INSERT would just raise instead
    # of updating in place.
    conn = sqlite3.connect(DB_FILE)
    conn.execute("INSERT INTO agents (session_id, agent_id) VALUES ('s1','a1')")
    conn.commit()
    raised = False
    try:
        conn.execute("INSERT INTO agents (session_id, agent_id) VALUES ('s1','a1')")
        conn.commit()
    except sqlite3.IntegrityError:
        raised = True
    conn.close()
    c.check("agents(session_id, agent_id) UNIQUE constraint is actually enforced", raised)

    # 3. Idempotency: re-running _init_db against an ALREADY-migrated DB
    # (simulating every subsequent monitor.py startup) must not raise --
    # this is exactly what the "swallow the duplicate-column
    # OperationalError" comment in the real function claims, now verified
    # rather than just asserted in a comment.
    raised_on_rerun = False
    try:
        _init_db()
    except Exception:
        raised_on_rerun = True
    c.check("re-running _init_db against an already-migrated DB does not raise", not raised_on_rerun)
    c.check("columns are unchanged after a second run", columns("sessions") == sessions_cols)

    conn = sqlite3.connect(DB_FILE)
    still_there = conn.execute("SELECT COUNT(*) FROM agents WHERE session_id='s1'").fetchone()[0]
    conn.close()
    c.check("re-running _init_db is non-destructive to existing data", still_there == 1)

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
