"""Tests _sanitize_project_budgets and _project_month_to_date_costs,
extracted straight from monitor.py. These back the per-project monthly
budget_alert check in _webhook_notify_worker: _sanitize_project_budgets is
the one place a user-supplied {project: $} map from the Settings textarea
gets validated before _webhook_notify_worker trusts it as a numeric
threshold to compare against; _project_month_to_date_costs is the
server-side aggregate (mirrors _db_analytics()'s by_project shape, just
date-scoped to the current calendar month) that check compares spend
against. Runs against a real scratch SQLite DB, same style as
db_analytics_by_model.test.py."""
import sys, os, sqlite3, threading, tempfile, shutil
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()

# ── _sanitize_project_budgets ──
ns = exec_functions(["_sanitize_project_budgets"])
_sanitize_project_budgets = ns["_sanitize_project_budgets"]

c.check("not-a-dict input returns empty dict", _sanitize_project_budgets("nope") == {})
c.check("not-a-dict input (list) returns empty dict", _sanitize_project_budgets([1, 2]) == {})

full = _sanitize_project_budgets({"AOC": 5, "PHANTOM AI": "10.5"})
c.check("numeric and numeric-string values both coerce to float", full == {"AOC": 5.0, "PHANTOM AI": 10.5})

c.check("zero budget is dropped (not a real limit)", "z" not in _sanitize_project_budgets({"z": 0}))
c.check("negative budget is dropped", "neg" not in _sanitize_project_budgets({"neg": -5}))
c.check("non-numeric value is dropped, doesn't crash", _sanitize_project_budgets({"bad": "not-a-number"}) == {})
c.check("blank/whitespace-only project name is dropped", _sanitize_project_budgets({"   ": 5}) == {})

long_name = "x" * 500
c.check("project name is truncated to 100 chars", len(next(iter(_sanitize_project_budgets({long_name: 5})))) == 100)

c.check("budget value is capped at 1,000,000", _sanitize_project_budgets({"huge": 5_000_000})["huge"] == 1_000_000)
c.check("budget value is rounded to 2 decimal places", _sanitize_project_budgets({"p": 1.23456})["p"] == 1.23)

many = {f"p{i}": 1 for i in range(80)}
c.check("map is capped at 50 entries", len(_sanitize_project_budgets(many)) == 50)

# ── _project_month_to_date_costs ──
SCRATCH = tempfile.mkdtemp(prefix="aoc_test_project_budgets_")
DB_FILE = os.path.join(SCRATCH, "history.db")

ns2 = exec_functions(["_db_conn", "_project_month_to_date_costs"], {
    "sqlite3": sqlite3, "DB_FILE": DB_FILE, "_db_lock": threading.Lock(), "datetime": datetime,
})
_project_month_to_date_costs = ns2["_project_month_to_date_costs"]

try:
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, project TEXT, date TEXT, cost REAL DEFAULT 0
        )
    """)
    this_month = datetime.now().strftime("%Y-%m")
    # a date in a different (safely past) month, so it's excluded regardless
    # of which month the test actually runs in
    other_month_date = "2020-01-15"
    conn.executemany(
        "INSERT INTO sessions (id, project, date, cost) VALUES (?,?,?,?)",
        [
            ("s1", "AOC", f"{this_month}-05", 3.0),
            ("s2", "AOC", f"{this_month}-12", 2.0),
            ("s3", "PHANTOM AI", f"{this_month}-01", 7.5),
            ("s4", "AOC", other_month_date, 100.0),  # different month -- excluded
            ("s5", "", f"{this_month}-01", 9.0),  # no project -- excluded
        ],
    )
    conn.commit()
    conn.close()

    result = _project_month_to_date_costs()
    c.check("sums cost per project within the current month only", result.get("AOC") == 5.0)
    c.check("a project with a single this-month row sums correctly", result.get("PHANTOM AI") == 7.5)
    c.check("a different month's cost is excluded from the total", result.get("AOC") != 105.0)
    c.check("rows with no project are excluded entirely", "" not in result)
finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
