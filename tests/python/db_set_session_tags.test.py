"""Tests _db_set_session_tags, extracted straight from monitor.py. Unlike
session_note (live status-dict state, only snapshotted into history.db at
save time), tags are edited on an ALREADY-saved History row -- this
writes the DB directly, backing the /history_tags POST endpoint. Runs
against a real scratch SQLite DB, same convention as
db_save_session_duration_cap.test.py."""
import sys, os, sqlite3, threading, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_db_set_session_tags_")
DB_FILE = os.path.join(SCRATCH, "history.db")

conn = sqlite3.connect(DB_FILE)
conn.execute("""
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY, project TEXT, tags TEXT DEFAULT ''
    )
""")
conn.executemany("INSERT INTO sessions (id, project) VALUES (?,?)", [("s1", "AOC"), ("s2", "AOC")])
conn.commit()
conn.close()

ns = exec_functions(["_db_conn", "_sanitize_tags", "_db_set_session_tags"], {
    "sqlite3": sqlite3, "DB_FILE": DB_FILE, "_db_lock": threading.Lock(),
})
_db_set_session_tags = ns["_db_set_session_tags"]

c = Checker()


def read_tags(sid):
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute("SELECT tags FROM sessions WHERE id=?", (sid,)).fetchone()
    conn.close()
    return row[0] if row else None


# 1. setting tags on an existing session persists them, sanitized
ok = _db_set_session_tags("s1", ["Refactor", "bugfix", "  "])
c.check("returns True when the session exists", ok is True)
c.check("tags persisted as a comma-joined, sanitized string", read_tags("s1") == "Refactor,bugfix")

# 2. overwriting replaces the full set, doesn't merge with the old one
_db_set_session_tags("s1", ["experiment"])
c.check("a second call replaces the tag set rather than merging", read_tags("s1") == "experiment")

# 3. clearing (empty list) removes all tags
_db_set_session_tags("s1", [])
c.check("an empty list clears all tags", read_tags("s1") == "")

# 4. setting tags on an unknown session id is a no-op, returns False
ok2 = _db_set_session_tags("no-such-session", ["x"])
c.check("returns False when the session doesn't exist", ok2 is False)

# 5. a session never touched keeps its default empty string
c.check("an untouched session still has the DEFAULT '' value", read_tags("s2") == "")

shutil.rmtree(SCRATCH, ignore_errors=True)
c.finish()
