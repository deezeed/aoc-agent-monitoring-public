"""Tests _db_by_day_excluding_projects, extracted straight from
monitor.py. Backs the weekly digest's muted_projects exclusion (see
weekly_digest.test.py for the digest-level wiring) -- this is the actual
SQL query doing the filtering. Runs against a real scratch SQLite DB,
same convention as history_search.test.py."""
import sys, os, sqlite3, threading, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_db_by_day_excluding_")
DB_FILE = os.path.join(SCRATCH, "history.db")

ns = exec_functions(["_db_conn", "_db_by_day_excluding_projects"], {
    "sqlite3": sqlite3, "DB_FILE": DB_FILE, "_db_lock": threading.Lock(),
})
_db_by_day_excluding_projects = ns["_db_by_day_excluding_projects"]

c = Checker()

try:
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, project TEXT, date TEXT,
            tokens INTEGER DEFAULT 0, cost REAL DEFAULT 0, errors INTEGER DEFAULT 0
        )
    """)
    conn.executemany(
        "INSERT INTO sessions (id, project, date, tokens, cost, errors) VALUES (?,?,?,?,?,?)",
        [
            ("s1", "AOC", "2026-07-20", 1000, 1.0, 0),
            ("s2", "NoisyProject", "2026-07-20", 5000, 3.0, 2),   # same day, muted project
            ("s3", "PHANTOM AI", "2026-07-21", 2000, 0.5, 0),
            ("s4", "NoisyProject", "2026-07-22", 9999, 9.0, 9),   # a day with ONLY the muted project
        ],
    )
    conn.commit()
    conn.close()

    # 1. no exclusion -> identical to an unfiltered by_day (everything counted)
    unfiltered = {r["date"]: r for r in _db_by_day_excluding_projects([])}
    c.check("no exclusion: 2026-07-20 sums both sessions (1000+5000 tokens)",
            unfiltered["2026-07-20"]["tokens"] == 6000)
    c.check("no exclusion: sessions count includes the muted project too",
            unfiltered["2026-07-20"]["sessions"] == 2)

    # 2. excluding NoisyProject removes its contribution from every day
    filtered = {r["date"]: r for r in _db_by_day_excluding_projects(["NoisyProject"])}
    c.check("excluded project's tokens are gone from 2026-07-20 (only s1's 1000 left)",
            filtered["2026-07-20"]["tokens"] == 1000)
    c.check("excluded project's session no longer counted on 2026-07-20",
            filtered["2026-07-20"]["sessions"] == 1)
    c.check("a day where ONLY the excluded project had sessions disappears entirely",
            "2026-07-22" not in filtered)
    c.check("errors are excluded along with the rest of that project's rows",
            filtered["2026-07-20"]["errors"] == 0)

    # 3. a day with a non-excluded project survives untouched
    filtered2 = _db_by_day_excluding_projects(["AOC"])
    by_date2 = {r["date"]: r for r in filtered2}
    c.check("excluding a different project leaves 2026-07-21's PHANTOM AI session intact",
            by_date2["2026-07-21"]["sessions"] == 1 and by_date2["2026-07-21"]["tokens"] == 2000)

    # 4. blank/empty entries in the exclusion list are ignored, not treated
    # as "exclude sessions with an empty project"
    with_blank = {r["date"]: r for r in _db_by_day_excluding_projects(["", None])}
    c.check("blank/None entries in the exclusion list are no-ops",
            with_blank["2026-07-20"]["tokens"] == 6000)

    # 5. limit param is respected (same shape as by_day's own LIMIT)
    limited = _db_by_day_excluding_projects([], limit=1)
    c.check("limit caps the number of day-rows returned", len(limited) == 1)

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
