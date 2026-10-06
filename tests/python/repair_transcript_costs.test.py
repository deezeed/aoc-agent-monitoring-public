"""Tests _db_repair_transcript_costs, extracted straight from monitor.py:
History rows the old transcript scanner wrote (~2.4x overcounted) take the
deduplicated tokens/cost from the transcript index's usage table -- only
rows without subagents, only when the transcript is still indexed, and
running it again changes nothing. Also checks _apply_agent_update prices a
subagent's 1-hour cache writes at 2x input."""
import sys, os, sqlite3

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
repair = exec_functions(["_db_repair_transcript_costs"])["_db_repair_transcript_costs"]

hist = sqlite3.connect(":memory:")
hist.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, agents INTEGER, tokens INTEGER, cost REAL)")
hist.executemany("INSERT INTO sessions VALUES (?, ?, ?, ?)", [
    ("inflated", 0, 1151492, 203.59),   # old scanner row, transcript indexed
    ("agents", 3, 900000, 150.0),       # has subagents -> never touched
    ("gone", 0, 500000, 80.0),          # transcript deleted -> never touched
    ("correct", 0, 94513, 10.11),       # already right (new scanner)
    ("nullagents", None, 2000, 5.0),    # agents NULL counts as none
])
idx = sqlite3.connect(":memory:")
idx.execute("CREATE TABLE usage (session_id TEXT, model TEXT, day TEXT, inp INTEGER, out INTEGER, cost REAL)")
idx.executemany("INSERT INTO usage VALUES (?, ?, ?, ?, ?, ?)", [
    ("inflated", "claude-opus-5-5", "2026-09-17", 4000, 300000, 60.25),
    ("inflated", "claude-opus-5-5", "2026-09-18", 412, 92000, 30.25),   # two days, summed
    ("agents", "claude-sonnet-5", "2026-09-20", 10, 1000, 1.0),
    ("correct", "claude-opus-5-5", "2026-10-06", 13, 94500, 10.1100001),
    ("nullagents", "claude-sonnet-5", "2026-09-01", 10, 990, 2.0),
])
n = repair(hist, idx)
rows = dict((r[0], r[1:]) for r in hist.execute("SELECT id, tokens, cost FROM sessions"))
c.check("two rows changed", n == 2)
c.check("inflated row takes the summed index numbers", rows["inflated"] == (396412, 90.5))
c.check("row with subagents untouched", rows["agents"] == (900000, 150.0))
c.check("row without transcript untouched", rows["gone"] == (500000, 80.0))
c.check("already-correct row untouched", rows["correct"] == (94513, 10.11))
c.check("NULL agents treated as none", rows["nullagents"] == (1000, 2.0))
c.check("second run changes nothing", repair(hist, idx) == 0)

# subagent cost with 1-hour cache writes (aoc_hook.py now sends the share)
ns = exec_functions(["_apply_agent_update", "_calc_cost", "_model_pricing", "_MODEL_PRICING",
                     "_CACHE_WRITE_1H_MULT", "_CLAUDE_CONTEXT_WINDOW"],
                    {"_log": type("L", (), {"agent_start": staticmethod(lambda a: None)})(),
                     "_pending_parent_links": {},
                     "_log_bg_error": lambda *a: None})
status = {"agents": []}
ns["_apply_agent_update"](status, {"id": "ag_1", "name": "x", "status": "done", "model": "claude-opus-5-5",
                                   "input_tokens": 0, "output_tokens": 0, "cache_write_tokens": 1_000_000,
                                   "cache_write_1h_tokens": 1_000_000, "cache_read_tokens": 0},
                          "s1", "proj", "10:00:00")
c.check("subagent 1h cache writes priced at 2x input ($8/M on opus-5-5)",
        abs(status["agents"][0]["estimated_cost"] - 8.0) < 1e-9)

c.finish()
