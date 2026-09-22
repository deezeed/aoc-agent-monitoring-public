"""Tests _export_costs_csv (and its _db_conn dependency), extracted
straight from monitor.py. Runs against a scratch SQLite DB with a minimal
sessions table -- never the real history.db."""
import sys, os, sqlite3, threading, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_export_costs_")
DB_FILE = os.path.join(SCRATCH, "history.db")

conn = sqlite3.connect(DB_FILE)
conn.execute("CREATE TABLE sessions (project TEXT, date TEXT, tokens INTEGER, cost REAL)")
conn.executemany("INSERT INTO sessions VALUES (?, ?, ?, ?)", [
    ("AOC", "2026-07-01", 1000, 1.5),
    ("AOC", "2026-07-15", 2000, 3.0),
    ("PHANTOM AI", "2026-07-10", 500, 0.5),
    ("", "2026-06-01", 100, 0.1),  # empty project -> should map to '(none)'
])
conn.commit()
conn.close()

ns = exec_functions(["_db_conn", "_export_costs_csv"], {
    "sqlite3": sqlite3, "os": os,
    "DB_FILE": DB_FILE, "_db_lock": threading.Lock(),
})
_export_costs_csv = ns["_export_costs_csv"]

c = Checker()

csv_all = _export_costs_csv()
lines = csv_all.strip().splitlines()
c.check("header row present", lines[0] == "project,sessions,tokens,cost_usd")
c.check("has a TOTAL row at the end", lines[-1].startswith("TOTAL,"))
c.check("AOC project appears (sorted first, highest cost)", "AOC,2,3000,4.5" in csv_all)
c.check("empty project mapped to (none)", "(none),1,100,0.1" in csv_all)
c.check("PHANTOM AI project appears", "PHANTOM AI,1,500,0.5" in csv_all)
c.check("TOTAL row sums all rows correctly", "TOTAL,4,3600,5.1" in csv_all)

csv_ranged = _export_costs_csv(date_from="2026-07-01", date_to="2026-07-31")
c.check("date range excludes out-of-range rows (June session gone)", "(none)" not in csv_ranged)
c.check("date range still includes in-range rows", "AOC" in csv_ranged)

csv_empty_range = _export_costs_csv(date_from="2099-01-01", date_to="2099-01-02")
c.check("date range with no matches still returns header + TOTAL only", len(csv_empty_range.strip().splitlines()) == 2)

shutil.rmtree(SCRATCH, ignore_errors=True)
c.finish()
