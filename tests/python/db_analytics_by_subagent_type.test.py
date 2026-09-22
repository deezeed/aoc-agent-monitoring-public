"""Tests the by_subagent_type and by_day_subagent_type aggregates in
_db_analytics, extracted straight from monitor.py. Mirrors
db_analytics_by_model.test.py exactly -- same shape, same reasoning, just
grouped by subagent_type (Explore/Plan/general-purpose/...) instead of
model. subagent_type was already shown per-agent (card badge, Compare,
Markdown export) but never aggregated across history before this."""
import sys, os, sqlite3, threading, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_db_analytics_subagent_type_")
DB_FILE = os.path.join(SCRATCH, "history.db")

ns = exec_functions(["_db_conn", "_db_analytics", "_MAX_PLAUSIBLE_DURATION_S", "_tags_list"], {
    "sqlite3": sqlite3, "DB_FILE": DB_FILE, "_db_lock": threading.Lock(),
})
_db_analytics = ns["_db_analytics"]

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
            file_count INTEGER DEFAULT 0, snapshot TEXT, waiting_on_you_s INTEGER DEFAULT 0, tags TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE agents (
            rowid_ INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, agent_id TEXT,
            name TEXT, unit TEXT, status TEXT, started_at TEXT, completed_at TEXT,
            duration_s INTEGER DEFAULT 0, tokens INTEGER DEFAULT 0, cost REAL DEFAULT 0,
            task_done INTEGER DEFAULT 0, task_total INTEGER DEFAULT 0,
            file_count INTEGER DEFAULT 0, error_msg TEXT,
            detected_via TEXT, concurrent_sessions INTEGER, model TEXT, subagent_type TEXT,
            tool_use_count INTEGER
        )
    """)
    # _db_analytics is one function covering sessions/agents/file_changes in
    # a single try/except -- an empty-but-present file_changes table is
    # required even though this test doesn't care about file_hotspots, or
    # the whole call falls through to the except branch and by_subagent_type
    # comes back empty too.
    conn.execute("CREATE TABLE file_changes (session_id TEXT, agent_id TEXT, path TEXT, type TEXT, lines INTEGER DEFAULT 0)")
    conn.executemany(
        "INSERT INTO sessions (id, date) VALUES (?,?)",
        [("s1", "2026-07-20"), ("s2", "2026-07-21"), ("s3", "2026-07-21")],
    )
    rows = [
        # session_id, agent_id, tokens, cost, subagent_type, status, tool_use_count
        ("s1", "a1", 10000, 1.0, "Explore", "done", 4),
        ("s1", "a2", 20000, 2.0, "Explore", "error", 6),
        ("s2", "a3", 5000, 3.0, "Plan", "done", 10),
        # old row saved before subagent_type was tracked -- must be
        # excluded, not surfaced as a blank bucket.
        ("s2", "a4", 1000, 0.1, None, "done", 2),
        # empty-string subagent_type (defensive: same exclusion as NULL)
        ("s2", "a5", 500, 0.05, "", "done", 1),
    ]
    conn.executemany(
        """INSERT INTO agents (session_id, agent_id, tokens, cost, subagent_type, status, tool_use_count)
           VALUES (?,?,?,?,?,?,?)""", rows
    )
    conn.commit()
    conn.close()

    result = _db_analytics()
    by_type = {r["subagent_type"]: r for r in result["by_subagent_type"]}

    c.check("by_subagent_type excludes NULL/empty subagent_type rows", set(by_type.keys()) == {
        "Explore", "Plan"
    })
    c.check("by_subagent_type sums cost per type correctly",
            by_type["Explore"]["cost"] == 3.0 and by_type["Plan"]["cost"] == 3.0)
    c.check("by_subagent_type sums tokens per type correctly",
            by_type["Explore"]["tokens"] == 30000)
    c.check("by_subagent_type counts agents per type correctly",
            by_type["Explore"]["agents"] == 2 and by_type["Plan"]["agents"] == 1)
    c.check("by_subagent_type sums tool_use_count per type correctly (tokens-per-call efficiency source)",
            by_type["Explore"]["tool_use_count"] == 10 and by_type["Plan"]["tool_use_count"] == 10)
    c.check("by_subagent_type counts done per type correctly (mirrors by_model's)",
            by_type["Explore"]["done"] == 1 and by_type["Plan"]["done"] == 1)
    c.check("by_subagent_type counts errors per type correctly",
            by_type["Explore"]["errors"] == 1 and by_type["Plan"]["errors"] == 0)

    by_day_type = {(r["date"], r["subagent_type"]): r for r in result["by_day_subagent_type"]}
    c.check("by_day_subagent_type has one row per (date, type) via the sessions join",
            set(by_day_type.keys()) == {("2026-07-20", "Explore"), ("2026-07-21", "Plan")})
    c.check("by_day_subagent_type cost/tokens match the session-attributed agents",
            by_day_type[("2026-07-20", "Explore")]["cost"] == 3.0
            and by_day_type[("2026-07-20", "Explore")]["tokens"] == 30000)
    c.check("by_day_subagent_type excludes NULL/empty-type agents same as by_subagent_type",
            by_day_type[("2026-07-21", "Plan")]["cost"] == 3.0)

    # tie-break + separate-bucket check, mirroring db_analytics_by_model.test.py
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""INSERT INTO agents (session_id, agent_id, tokens, cost, subagent_type)
                     VALUES ('s3','a6',1000,5.0,'Explore')""")
    conn.commit()
    conn.close()
    result2 = _db_analytics()
    c.check("by_subagent_type sorted by cost descending", result2["by_subagent_type"][0]["subagent_type"] == "Explore")
    by_day_type2 = {(r["date"], r["subagent_type"]): r for r in result2["by_day_subagent_type"]}
    c.check("by_day_subagent_type: same date, different type stays a separate bucket",
            by_day_type2[("2026-07-21", "Explore")]["cost"] == 5.0
            and by_day_type2[("2026-07-21", "Plan")]["cost"] == 3.0)

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
