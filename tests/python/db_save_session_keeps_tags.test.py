"""Tests that _db_save_session (extracted straight from monitor.py) keeps a
row's tags and title when it re-saves the same session. It used INSERT OR
REPLACE, which deletes the row before inserting -- so the auto-save worker,
re-saving a live session under the same id every cycle, silently wiped any
tags the user had already put on it in History. Real scratch SQLite DB,
same harness as db_save_session_waiting.test.py."""
import sys, os, sqlite3, threading, tempfile, shutil, json, time
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_save_keeps_tags_")
DB_FILE = os.path.join(SCRATCH, "history.db")
conn = sqlite3.connect(DB_FILE)
conn.executescript("""
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY, project TEXT, orchestrator TEXT, date TEXT,
        started_at TEXT, ended_at TEXT, duration_s INTEGER DEFAULT 0,
        agents INTEGER DEFAULT 0, done INTEGER DEFAULT 0, errors INTEGER DEFAULT 0,
        tokens INTEGER DEFAULT 0, cost REAL DEFAULT 0,
        task_done INTEGER DEFAULT 0, task_total INTEGER DEFAULT 0,
        file_count INTEGER DEFAULT 0, snapshot TEXT, cc_version TEXT,
        waiting_on_you_s INTEGER DEFAULT 0, tags TEXT DEFAULT '', title TEXT
    );
    CREATE TABLE agents (
        rowid_ INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, agent_id TEXT,
        name TEXT, unit TEXT, status TEXT, started_at TEXT, completed_at TEXT,
        duration_s INTEGER DEFAULT 0, tokens INTEGER DEFAULT 0, cost REAL DEFAULT 0,
        task_done INTEGER DEFAULT 0, task_total INTEGER DEFAULT 0,
        file_count INTEGER DEFAULT 0, error_msg TEXT,
        detected_via TEXT, concurrent_sessions INTEGER, model TEXT, subagent_type TEXT,
        tool_use_count INTEGER, parent_id TEXT,
        UNIQUE(session_id, agent_id)
    );
    CREATE TABLE file_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT, agent_id TEXT, path TEXT, type TEXT, lines INTEGER DEFAULT 0
    );
""")
conn.commit()
conn.close()

ns = exec_functions(["_db_conn", "_correct_hms_diff", "_db_save_session", "_MAX_PLAUSIBLE_DURATION_S"], {
    "sqlite3": sqlite3, "os": os, "json": json, "datetime": datetime, "time": time,
    "DB_FILE": DB_FILE, "_db_lock": threading.Lock(), "_now_ts": lambda: "10:00:00",
    "_log_bg_error": lambda where, e: (_ for _ in ()).throw(e),
})
save = ns["_db_save_session"]


def row(sid):
    c = sqlite3.connect(DB_FILE)
    c.row_factory = sqlite3.Row
    r = c.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    c.close()
    return dict(r)


c = Checker()
status = {"project": "omnisocial", "started_at": "09:00:00",
          "agents": [{"id": "a1", "status": "running", "tokens_used": 100}]}
save(status, sid="20261002_090000")

c2 = sqlite3.connect(DB_FILE)
c2.execute("UPDATE sessions SET tags='client,urgent', title='Inbox SLA' WHERE id='20261002_090000'")
c2.commit(); c2.close()

status["agents"][0]["status"] = "done"
save(status, sid="20261002_090000")  # the next auto-save cycle
r = row("20261002_090000")
c.check("re-save keeps the user's tags", r["tags"] == "client,urgent")
c.check("re-save keeps the title", r["title"] == "Inbox SLA")
c.check("re-save still refreshes the snapshot fields", r["done"] == 1 and r["ended_at"] == "10:00:00")
c.check("still exactly one row for the session",
        sqlite3.connect(DB_FILE).execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1)

shutil.rmtree(SCRATCH, ignore_errors=True)
c.finish()
