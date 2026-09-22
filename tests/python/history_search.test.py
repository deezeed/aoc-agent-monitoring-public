"""Tests _db_search_sessions, extracted straight from monitor.py. Runs
against a real scratch SQLite DB (never the real AOC_DIR/DB_FILE), same
style as backup_restore.test.py. This is the feature that lets History
search reach past the /history endpoint's fixed 100-row cap."""
import sys, os, sqlite3, threading, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_history_search_")
DB_FILE = os.path.join(SCRATCH, "history.db")

ns = exec_functions(["_db_conn", "_db_search_sessions", "_tags_list"], {
    "sqlite3": sqlite3, "DB_FILE": DB_FILE, "_db_lock": threading.Lock(),
})
_db_search_sessions = ns["_db_search_sessions"]

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
            file_count INTEGER DEFAULT 0, snapshot TEXT, cc_version TEXT, tags TEXT DEFAULT ''
        )
    """)
    # 250 rows spread across 3 projects and a range of dates, so we can
    # confirm the search reaches past _db_get_sessions' old 100-row cap.
    rows = []
    for i in range(250):
        proj = ["AOC", "PHANTOM AI", "AOC Monitor"][i % 3]
        date = f"2026-01-{(i % 28) + 1:02d}"
        rows.append((f"sess-{i}", proj, "", date, "10:00:00", "10:30:00", 1800,
                      1, 1, 0, 1000, 0.5, 1, 1, 0, None, None, ""))
    conn.executemany(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    conn.close()

    # 1. no filters at all -> capped only by the `limit` param, not stuck at 100
    all_results = _db_search_sessions(limit=250)
    c.check("no filters, limit=250 -> all 250 rows reachable (beyond the old 100-row cap)", len(all_results) == 250)

    # 2. project name substring match
    aoc_only = _db_search_sessions(query="AOC")
    # "AOC" matches BOTH "AOC" and "AOC Monitor" via LIKE '%AOC%'
    c.check("project substring match finds both AOC and AOC Monitor rows",
            all(("AOC" in r["project"]) for r in aoc_only) and len(aoc_only) > 0)

    phantom_only = _db_search_sessions(query="PHANTOM")
    c.check("project substring match is exclusive to matching rows",
            all(r["project"] == "PHANTOM AI" for r in phantom_only) and len(phantom_only) > 0)

    # 3. session id substring match
    by_id = _db_search_sessions(query="sess-7")
    # matches sess-7, sess-70..79, sess-170..179 etc via LIKE '%sess-7%'
    c.check("session id substring match works", len(by_id) > 0 and all("sess-7" in r["id"] for r in by_id))

    # 4. date range filter
    narrow_range = _db_search_sessions(date_from="2026-01-05", date_to="2026-01-05")
    c.check("date range filter (single day) returns only that day's rows",
            len(narrow_range) > 0 and all(r["date"] == "2026-01-05" for r in narrow_range))

    # 5. combined query + date range
    combined = _db_search_sessions(query="AOC", date_from="2026-01-01", date_to="2026-01-10")
    c.check("combined project+date filter narrows correctly",
            len(combined) > 0 and all("AOC" in r["project"] and "2026-01-01" <= r["date"] <= "2026-01-10" for r in combined))

    # 6. no matches -> empty list, not a crash
    c.check("nonexistent project -> empty list", _db_search_sessions(query="NoSuchProject") == [])

    # 6b. query also matches against tags, not just project/id
    conn = sqlite3.connect(DB_FILE)
    conn.execute("UPDATE sessions SET tags='refactor,bugfix' WHERE id='sess-3'")
    conn.commit()
    conn.close()
    by_tag = _db_search_sessions(query="refactor")
    c.check("query matches against tags too", len(by_tag) == 1 and by_tag[0]["id"] == "sess-3")
    c.check("tags come back split into a list", by_tag[0]["tags"] == ["refactor", "bugfix"])
    c.check("a session with no tags gets an empty list, not None", all_results[0]["tags"] == [])

    # 7. results are ordered newest-first (rowid DESC): sess-249 was
    # inserted last, so it must come back before sess-0 in an unfiltered
    # query, matching _db_get_sessions' own ordering.
    all_results_ordered = _db_search_sessions(limit=250)
    ids_in_order = [r["id"] for r in all_results_ordered]
    c.check("newest-inserted row (sess-249) comes first", ids_in_order[0] == "sess-249")
    c.check("oldest-inserted row (sess-0) comes last", ids_in_order[-1] == "sess-0")

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
