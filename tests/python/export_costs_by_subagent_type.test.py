"""Tests _export_costs_by_subagent_type_csv (and its _db_conn dependency),
extracted straight from monitor.py. Mirrors export_costs_by_model.test.py
exactly -- same agents-JOIN-sessions shape, just the other identifying
column."""
import sys, os, sqlite3, threading, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_export_costs_by_subagent_type_")
DB_FILE = os.path.join(SCRATCH, "history.db")

conn = sqlite3.connect(DB_FILE)
conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, date TEXT)")
conn.execute("CREATE TABLE agents (session_id TEXT, tokens INTEGER, cost REAL, subagent_type TEXT)")
conn.executemany("INSERT INTO sessions VALUES (?, ?)", [
    ("s1", "2026-07-01"),
    ("s2", "2026-07-15"),
    ("s3", "2026-06-01"),
])
conn.executemany("INSERT INTO agents (session_id, tokens, cost, subagent_type) VALUES (?, ?, ?, ?)", [
    ("s1", 1000, 1.5, "Explore"),
    ("s2", 2000, 3.0, "Explore"),
    ("s2", 500, 2.0, "Plan"),
    ("s3", 100, 0.1, "Explore"),
    # NULL/empty subagent_type (agents saved before it was tracked) -- must
    # be excluded from the report entirely, same as by_subagent_type.
    ("s1", 999, 9.9, None),
    ("s1", 999, 9.9, ""),
])
conn.commit()
conn.close()

ns = exec_functions(["_db_conn", "_export_costs_by_subagent_type_csv"], {
    "sqlite3": sqlite3, "os": os,
    "DB_FILE": DB_FILE, "_db_lock": threading.Lock(),
})
_export_costs_by_subagent_type_csv = ns["_export_costs_by_subagent_type_csv"]

c = Checker()

csv_all = _export_costs_by_subagent_type_csv()
lines = csv_all.strip().splitlines()
c.check("header row present", lines[0] == "subagent_type,agents,tokens,cost_usd")
c.check("has a TOTAL row at the end", lines[-1].startswith("TOTAL,"))
c.check("Explore appears (sorted first, highest cost)", "Explore,3,3100,4.6" in csv_all)
c.check("Plan appears", "Plan,1,500,2.0" in csv_all)
c.check("NULL/empty subagent_type rows excluded from the report and its totals", "TOTAL,4,3600,6.6" in csv_all)

csv_ranged = _export_costs_by_subagent_type_csv(date_from="2026-07-01", date_to="2026-07-31")
c.check("date range excludes the June-dated agent's tokens (Explore total drops)", "Explore,2,3000,4.5" in csv_ranged)

csv_empty_range = _export_costs_by_subagent_type_csv(date_from="2099-01-01", date_to="2099-01-02")
c.check("date range with no matches still returns header + TOTAL only", len(csv_empty_range.strip().splitlines()) == 2)

shutil.rmtree(SCRATCH, ignore_errors=True)
c.finish()
