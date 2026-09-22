"""Tests _db_prune_old, extracted straight from monitor.py. This is a
destructive function -- it deletes rows across sessions/agents/
file_changes based on a date cutoff, runs automatically on every startup
(_init_db(); _db_prune_old(); _prune_old_logs() at module load), and had
zero test coverage before this: a wrong cutoff comparison here means
silent, permanent data loss, not a crash anyone would notice."""
import sys, os, sqlite3, threading, tempfile, shutil
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_db_prune_old_")
DB_FILE = os.path.join(SCRATCH, "history.db")

conn = sqlite3.connect(DB_FILE)
conn.executescript("""
    CREATE TABLE sessions (id TEXT PRIMARY KEY, date TEXT);
    CREATE TABLE agents (session_id TEXT, agent_id TEXT);
    CREATE TABLE file_changes (session_id TEXT, path TEXT);
""")
conn.commit()
conn.close()

ns = exec_functions(["_db_conn", "_HISTORY_RETENTION_DAYS", "_db_prune_old"], {
    "sqlite3": sqlite3, "datetime": datetime, "timedelta": timedelta,
    "DB_FILE": DB_FILE, "_db_lock": threading.Lock(),
    "_log_bg_error": lambda where, exc: None,
})
_db_prune_old = ns["_db_prune_old"]

c = Checker()

try:
    today = datetime.now()
    old_date = (today - timedelta(days=100)).strftime("%Y-%m-%d")      # older than default 90-day window
    recent_date = (today - timedelta(days=10)).strftime("%Y-%m-%d")    # well within it
    boundary_date = (today - timedelta(days=90)).strftime("%Y-%m-%d")  # exactly at the cutoff -- must survive

    conn = sqlite3.connect(DB_FILE)
    conn.executemany("INSERT INTO sessions (id, date) VALUES (?,?)", [
        ("old1", old_date), ("recent1", recent_date), ("boundary1", boundary_date),
    ])
    conn.executemany("INSERT INTO agents (session_id, agent_id) VALUES (?,?)", [
        ("old1", "a1"), ("old1", "a2"), ("recent1", "a3"), ("boundary1", "a4"),
    ])
    conn.executemany("INSERT INTO file_changes (session_id, path) VALUES (?,?)", [
        ("old1", "f1.py"), ("recent1", "f2.py"), ("boundary1", "f3.py"),
    ])
    conn.commit()
    conn.close()

    _db_prune_old()  # default days=_HISTORY_RETENTION_DAYS (90)

    conn = sqlite3.connect(DB_FILE)
    session_ids = {r[0] for r in conn.execute("SELECT id FROM sessions").fetchall()}
    agent_session_ids = {r[0] for r in conn.execute("SELECT session_id FROM agents").fetchall()}
    file_session_ids = {r[0] for r in conn.execute("SELECT session_id FROM file_changes").fetchall()}
    conn.close()

    c.check("session older than the retention window is deleted", "old1" not in session_ids)
    c.check("that session's agents are deleted too", "old1" not in agent_session_ids)
    c.check("that session's file_changes are deleted too", "old1" not in file_session_ids)
    c.check("session within the retention window survives", "recent1" in session_ids)
    c.check("recent session's agents survive", "recent1" in agent_session_ids)
    c.check("session exactly at the cutoff boundary survives (WHERE date < cutoff, not <=)", "boundary1" in session_ids)
    c.check("boundary session's agents survive too", "boundary1" in agent_session_ids)

    # Custom `days` param -- prune everything older than 5 days instead of
    # the default 90, confirming the retention window is actually
    # respected as a parameter, not hardcoded.
    conn = sqlite3.connect(DB_FILE)
    conn.execute("INSERT INTO sessions (id, date) VALUES (?,?)", ("recent2", recent_date))
    conn.commit()
    conn.close()
    _db_prune_old(days=5)
    conn = sqlite3.connect(DB_FILE)
    remaining = {r[0] for r in conn.execute("SELECT id FROM sessions").fetchall()}
    conn.close()
    c.check("custom days= param actually narrows the retention window (10-day-old session now pruned)",
            "recent2" not in remaining)

    # No sessions old enough to prune -- must be a safe no-op, not an error.
    conn = sqlite3.connect(DB_FILE)
    conn.execute("DELETE FROM sessions")
    conn.execute("DELETE FROM agents")
    conn.execute("DELETE FROM file_changes")
    conn.commit()
    conn.close()
    _db_prune_old()
    c.check("pruning an empty table is a safe no-op", True)

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
