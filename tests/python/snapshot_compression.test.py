"""Tests compressed session snapshots (_pack_snapshot, _unpack_snapshot,
_db_compress_snapshots, and _db_get_session_detail reading both formats),
extracted straight from monitor.py, against a real scratch SQLite DB.

Snapshots were 123 of history.db's 125 MB (agents' log arrays), which made
every hourly backup + integrity check + OneDrive upload of it heavy enough
to stall the monitor (profiled live 2026-10-03: the backup was 57% of all
active samples). They compress ~10x; old rows and old backups stay
readable."""
import sys, os, json, sqlite3, threading, tempfile, shutil, time, zlib

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_snapshot_zip_")
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
        file_count INTEGER DEFAULT 0, error_msg TEXT, detected_via TEXT,
        concurrent_sessions INTEGER, model TEXT, subagent_type TEXT,
        tool_use_count INTEGER, parent_id TEXT, UNIQUE(session_id, agent_id)
    );
    CREATE TABLE file_changes (id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT, agent_id TEXT, path TEXT, type TEXT, lines INTEGER DEFAULT 0);
""")
conn.commit(); conn.close()

errors = []
ns = exec_functions(
    ["_db_conn", "_pack_snapshot", "_unpack_snapshot", "_db_compress_snapshots",
     "_tags_list", "_db_get_session_detail"],
    {"sqlite3": sqlite3, "os": os, "json": json, "time": time, "DB_FILE": DB_FILE,
     "_db_lock": threading.Lock(), "_log_bg_error": lambda where, e: errors.append((where, e))})
pack, unpack = ns["_pack_snapshot"], ns["_unpack_snapshot"]
compress_all, detail = ns["_db_compress_snapshots"], ns["_db_get_session_detail"]
c = Checker()


def status(n):  # shaped like a real snapshot: agents with long log arrays
    return {"project": "omnisocial", "agents": [
        {"id": f"a{i}", "name": f"Agent {i} – ľščťž", "log": [f"[12:00:{j % 60:02d}] Read src/file_{j}.ts" for j in range(400)]}
        for i in range(n)]}


try:
    s = status(3)
    blob = pack(s)
    c.check("pack gives bytes, much smaller than the JSON",
            isinstance(blob, (bytes, memoryview)) and len(bytes(blob)) * 5 < len(json.dumps(s, ensure_ascii=False).encode()))
    c.check("unpack(pack(x)) == x, non-ASCII intact", unpack(blob) == s)
    c.check("legacy plain-JSON text still unpacks", unpack(json.dumps(s, ensure_ascii=False)) == s)
    c.check("empty / None -> None", unpack(None) is None and unpack("") is None)

    # A DB full of legacy text snapshots, plus one malformed and one empty.
    db = sqlite3.connect(DB_FILE)
    for i in range(60):
        db.execute("INSERT INTO sessions (id, snapshot) VALUES (?, ?)",
                   (f"s{i}", json.dumps(status(4), ensure_ascii=False)))
    db.execute("INSERT INTO sessions (id, snapshot) VALUES ('broken', '{not json')")
    db.execute("INSERT INTO sessions (id, snapshot) VALUES ('none', NULL)")
    db.execute("INSERT INTO sessions (id, snapshot) VALUES ('new', ?)", (pack(status(1)),))
    db.commit(); db.close()
    size_before = os.path.getsize(DB_FILE)

    n = compress_all(batch=25)
    c.check("every legacy text snapshot converted (60 + the malformed one)", n == 61)
    db = sqlite3.connect(DB_FILE)
    kinds = dict(db.execute("SELECT typeof(snapshot), COUNT(*) FROM sessions GROUP BY 1").fetchall())
    db.close()
    c.check("no text snapshots left; NULL untouched", kinds.get("text", 0) == 0 and kinds.get("null") == 1)
    size_after = os.path.getsize(DB_FILE)
    c.check(f"VACUUM shrank the file ({size_before // 1024} KB -> {size_after // 1024} KB)", size_after * 4 < size_before)
    c.check("a second pass is a no-op", compress_all() == 0)

    d = detail("s7")
    c.check("detail view reads a migrated snapshot", d["snapshot"] == status(4))
    c.check("detail view reads a natively packed one", detail("new")["snapshot"] == status(1))
    c.check("detail view: NULL snapshot -> None", detail("none")["snapshot"] is None)
    c.check("malformed legacy row kept byte-for-byte (compressed, not dropped)",
            zlib.decompress(bytes(sqlite3.connect(DB_FILE).execute(
                "SELECT snapshot FROM sessions WHERE id='broken'").fetchone()[0])) == b"{not json")
    c.check("no background errors", not errors)
finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
