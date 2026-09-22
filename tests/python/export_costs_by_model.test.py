"""Tests _export_costs_by_model_csv (and its _db_conn dependency),
extracted straight from monitor.py. Mirrors export_costs.test.py's
scratch-DB convention -- the model-based CSV needs both agents and
sessions tables (a join, since agents carries no date of its own, same
shape as by_day_model), never a single-table query like the
project-based report."""
import sys, os, sqlite3, threading, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_export_costs_by_model_")
DB_FILE = os.path.join(SCRATCH, "history.db")

conn = sqlite3.connect(DB_FILE)
conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, date TEXT)")
conn.execute("CREATE TABLE agents (session_id TEXT, tokens INTEGER, cost REAL, model TEXT)")
conn.executemany("INSERT INTO sessions VALUES (?, ?)", [
    ("s1", "2026-07-01"),
    ("s2", "2026-07-15"),
    ("s3", "2026-06-01"),
])
conn.executemany("INSERT INTO agents (session_id, tokens, cost, model) VALUES (?, ?, ?, ?)", [
    ("s1", 1000, 1.5, "claude-sonnet-4-5-20250514"),
    ("s2", 2000, 3.0, "claude-sonnet-4-5-20250514"),
    ("s2", 500, 2.0, "claude-opus-4-1-20250805"),
    ("s3", 100, 0.1, "claude-sonnet-4-5-20250514"),
    # NULL/empty model (agents saved before the column existed) -- must be
    # excluded from the report entirely, same as by_model/by_day_model.
    ("s1", 999, 9.9, None),
    ("s1", 999, 9.9, ""),
])
conn.commit()
conn.close()

ns = exec_functions(["_db_conn", "_export_costs_by_model_csv"], {
    "sqlite3": sqlite3, "os": os,
    "DB_FILE": DB_FILE, "_db_lock": threading.Lock(),
})
_export_costs_by_model_csv = ns["_export_costs_by_model_csv"]

c = Checker()

csv_all = _export_costs_by_model_csv()
lines = csv_all.strip().splitlines()
c.check("header row present", lines[0] == "model,agents,tokens,cost_usd")
c.check("has a TOTAL row at the end", lines[-1].startswith("TOTAL,"))
c.check("sonnet appears (sorted first, highest cost)", "claude-sonnet-4-5-20250514,3,3100,4.6" in csv_all)
c.check("opus appears", "claude-opus-4-1-20250805,1,500,2.0" in csv_all)
c.check("NULL/empty model rows excluded from the report and its totals", "TOTAL,4,3600,6.6" in csv_all)

csv_ranged = _export_costs_by_model_csv(date_from="2026-07-01", date_to="2026-07-31")
c.check("date range excludes the June-dated agent's tokens (sonnet total drops)", "claude-sonnet-4-5-20250514,2,3000,4.5" in csv_ranged)

csv_empty_range = _export_costs_by_model_csv(date_from="2099-01-01", date_to="2099-01-02")
c.check("date range with no matches still returns header + TOTAL only", len(csv_empty_range.strip().splitlines()) == 2)

shutil.rmtree(SCRATCH, ignore_errors=True)
c.finish()
