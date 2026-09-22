"""Tests the by_model and by_day_model aggregates in _db_analytics,
extracted straight from monitor.py. Runs against a real scratch SQLite DB
(never the real AOC_DIR/DB_FILE), same style as history_search.test.py --
confirms the Sonnet-vs-Opus-vs-Haiku cost breakdown the model column
exists to support actually aggregates and sorts correctly, and that its
day-level counterpart (which needs a JOIN against sessions for the date,
since agents carry no date of their own) does too. Also confirms
by_model's done/errors counts (mirroring by_project's, which COST BY
PROJECT rows already surface as an error badge -- COST BY MODEL rows
never did)."""
import sys, os, sqlite3, threading, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_db_analytics_")
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
    # the whole call falls through to the except branch and by_model comes
    # back empty too.
    conn.execute("CREATE TABLE file_changes (session_id TEXT, agent_id TEXT, path TEXT, type TEXT, lines INTEGER DEFAULT 0)")
    # by_day_model needs each agent's parent session for its date (agents
    # only carry time-of-day, not a date of their own).
    conn.executemany(
        "INSERT INTO sessions (id, date) VALUES (?,?)",
        [("s1", "2026-07-20"), ("s2", "2026-07-21"), ("s3", "2026-07-21")],
    )
    rows = [
        # session_id, agent_id, tokens, cost, model, status, tool_use_count, duration_s
        ("s1", "a1", 10000, 1.0, "claude-sonnet-4-5-20250514", "done", 4, 100),
        ("s1", "a2", 20000, 2.0, "claude-sonnet-4-5-20250514", "error", 6, 300),
        ("s2", "a3", 5000, 3.0, "claude-opus-4-1-20250805", "done", 10, 200),
        # old row saved before the `model` column existed -- must be
        # excluded from by_model, not surfaced as a blank bucket.
        ("s2", "a4", 1000, 0.1, None, "done", 2, 50),
        # empty-string model (defensive: same exclusion as NULL)
        ("s2", "a5", 500, 0.05, "", "done", 1, 50),
    ]
    conn.executemany(
        """INSERT INTO agents (session_id, agent_id, tokens, cost, model, status, tool_use_count, duration_s)
           VALUES (?,?,?,?,?,?,?,?)""", rows
    )
    conn.commit()
    conn.close()

    result = _db_analytics()
    by_model = {r["model"]: r for r in result["by_model"]}

    c.check("by_model excludes NULL/empty model rows", set(by_model.keys()) == {
        "claude-sonnet-4-5-20250514", "claude-opus-4-1-20250805"
    })
    c.check("by_model sums cost per model correctly",
            by_model["claude-sonnet-4-5-20250514"]["cost"] == 3.0
            and by_model["claude-opus-4-1-20250805"]["cost"] == 3.0)
    c.check("by_model sums tokens per model correctly",
            by_model["claude-sonnet-4-5-20250514"]["tokens"] == 30000)
    c.check("by_model counts agents per model correctly",
            by_model["claude-sonnet-4-5-20250514"]["agents"] == 2
            and by_model["claude-opus-4-1-20250805"]["agents"] == 1)
    c.check("by_model sums tool_use_count per model correctly (tokens-per-call efficiency source)",
            by_model["claude-sonnet-4-5-20250514"]["tool_use_count"] == 10
            and by_model["claude-opus-4-1-20250805"]["tool_use_count"] == 10)
    c.check("by_model counts done per model correctly (mirrors by_project's done/errors)",
            by_model["claude-sonnet-4-5-20250514"]["done"] == 1
            and by_model["claude-opus-4-1-20250805"]["done"] == 1)
    c.check("by_model counts errors per model correctly",
            by_model["claude-sonnet-4-5-20250514"]["errors"] == 1
            and by_model["claude-opus-4-1-20250805"]["errors"] == 0)
    c.check("by_model averages duration per model (mirrors slowest_agents' MODEL PERFORMANCE use)",
            by_model["claude-sonnet-4-5-20250514"]["avg_duration_s"] == 200  # (100+300)/2
            and by_model["claude-opus-4-1-20250805"]["avg_duration_s"] == 200)

    # implausible duration (past _MAX_PLAUSIBLE_DURATION_S, e.g. an
    # unconverted midnight-crossover artifact) must not skew the average --
    # same exclusion slowest_agents' own WHERE clause already applies.
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""INSERT INTO agents (session_id, agent_id, tokens, cost, model, duration_s)
                     VALUES ('s1','a7',100,0.01,'claude-opus-4-1-20250805',999999)""")
    conn.commit()
    conn.close()
    result_capped = _db_analytics()
    by_model_capped = {r["model"]: r for r in result_capped["by_model"]}
    c.check("by_model's avg_duration_s excludes implausibly-large durations",
            by_model_capped["claude-opus-4-1-20250805"]["avg_duration_s"] == 200)

    by_day_model = {(r["date"], r["model"]): r for r in result["by_day_model"]}
    c.check("by_day_model has one row per (date, model) via the sessions join",
            set(by_day_model.keys()) == {("2026-07-20", "claude-sonnet-4-5-20250514"),
                                          ("2026-07-21", "claude-opus-4-1-20250805")})
    c.check("by_day_model cost/tokens match the session-attributed agents",
            by_day_model[("2026-07-20", "claude-sonnet-4-5-20250514")]["cost"] == 3.0
            and by_day_model[("2026-07-20", "claude-sonnet-4-5-20250514")]["tokens"] == 30000)
    c.check("by_day_model excludes NULL/empty-model agents same as by_model",
            by_day_model[("2026-07-21", "claude-opus-4-1-20250805")]["cost"] == 3.0)

    # tie-break: sonnet and opus are equal cost above (3.0 each) -- add one
    # more sonnet agent so the ORDER BY SUM(cost) DESC has an unambiguous
    # winner to check. Also lands on the same date (2026-07-21) as the
    # existing opus row but a different model -- by_day_model must keep
    # them as two separate (date, model) buckets, not merge them.
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""INSERT INTO agents (session_id, agent_id, tokens, cost, model)
                     VALUES ('s3','a6',1000,5.0,'claude-sonnet-4-5-20250514')""")
    conn.commit()
    conn.close()
    result2 = _db_analytics()
    c.check("by_model sorted by cost descending", result2["by_model"][0]["model"] == "claude-sonnet-4-5-20250514")
    by_day_model2 = {(r["date"], r["model"]): r for r in result2["by_day_model"]}
    c.check("by_day_model: same date, different model stays a separate bucket",
            by_day_model2[("2026-07-21", "claude-sonnet-4-5-20250514")]["cost"] == 5.0
            and by_day_model2[("2026-07-21", "claude-opus-4-1-20250805")]["cost"] == 3.0)

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
