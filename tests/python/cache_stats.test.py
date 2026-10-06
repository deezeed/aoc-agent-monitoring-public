"""Tests the prompt-cache report pipeline, extracted straight from monitor.py:
_usage_line_sample (one sample per API response even though Claude Code
writes a line per content block; re-cache detection and the idle flag),
usage indexing through _transcript_index_file (incl. a response split across
two ticks), index schema versioning, and _cache_stats' numbers."""
import sys, os, json, re, sqlite3, tempfile, shutil, time, calendar
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
work = tempfile.mkdtemp(prefix="aoc_cs_")
ns = exec_functions(
    ["_TRANSCRIPT_INDEX_MAX_TEXT", "_TRANSCRIPT_INDEX_BYTES_PER_TICK", "_TRANSCRIPT_INDEX_VERSION",
     "_transcript_index_connect", "_COLD_IDLE_S", "_RECACHE_MIN_TOKENS", "_usage_line_sample",
     "_usage_add_samples", "_transcript_line_messages", "_transcript_index_file", "_transcript_index_tick",
     "_cache_stats", "_MODEL_PRICING", "_CACHE_WRITE_1H_MULT", "_model_pricing", "_calc_cost", "_iso_to_epoch"],
    {"os": os, "json": json, "re": re, "sqlite3": sqlite3, "time": time, "calendar": calendar,
     "datetime": datetime, "TRANSCRIPT_INDEX_DB": "", "_decode_project_name": lambda e: "proj"})
sample = ns["_usage_line_sample"]


def asst(mid, ts, cw=0, cr=0, inp=1, out=100, model="claude-opus-5-5", req=None, cw1h=None):
    return {"type": "assistant", "timestamp": ts, "requestId": req or "r" + mid,
            "message": {"id": mid, "model": model, "content": [{"type": "text", "text": "x"}],
                        "usage": {"input_tokens": inp, "output_tokens": out, "cache_creation_input_tokens": cw,
                                  "cache_read_input_tokens": cr,
                                  "cache_creation": {"ephemeral_1h_input_tokens": cw if cw1h is None else cw1h}}}}


# ── per-line sampling ──
s1, k, t = sample(asst("m1", "2026-10-01T08:00:00Z", cw=30000), None, None)
c.check("first response sampled, never a re-cache", s1 and not s1["cold"] and s1["cw"] == 30000)
s2, k2, t2 = sample(asst("m1", "2026-10-01T08:00:00Z", cw=30000), k, t)
c.check("repeat line of the same response skipped", s2 is None and k2 == k and t2 == t)
s3, k, t = sample(asst("m2", "2026-10-01T08:01:00Z", cw=2000, cr=30000), k, t)
c.check("normal turn: small write, big read -> not cold", s3 and not s3["cold"])
s4, k, t = sample(asst("m3", "2026-10-01T09:30:00Z", cw=33000, cr=0), k, t)
c.check("after 89 min: whole context re-written -> cold + idle", s4["cold"] and s4["cold_idle"])
s5, k, t = sample(asst("m4", "2026-10-01T09:31:00Z", cw=40000, cr=5000), k, t)
c.check("re-cache without a gap (model switch/compaction) -> cold, not idle", s5["cold"] and not s5["cold_idle"])
s6, k, t = sample(asst("m5", "2026-10-01T09:32:00Z", cw=15000, cr=0), k, t)
c.check("under 20k written -> not counted as a re-cache", not s6["cold"])
c.check("synthetic messages ignored", sample(asst("m6", "2026-10-01T09:33:00Z", model="<synthetic>"), k, t)[0] is None)
c.check("non-assistant lines ignored", sample({"type": "user"}, k, t) == (None, k, t))
c.check("day is the local date of the call", s1["day"] == time.strftime("%Y-%m-%d", time.localtime(calendar.timegm((2026, 10, 1, 8, 0, 0)))))

# ── indexing ──
projects = os.path.join(work, "projects")
pdir = os.path.join(projects, "C--x")
os.makedirs(pdir)
fp = os.path.join(pdir, "s1.jsonl")
lines = [asst("a1", "2026-10-01T08:00:00Z", cw=30000), asst("a1", "2026-10-01T08:00:00Z", cw=30000),
         asst("a2", "2026-10-01T08:01:00Z", cw=1000, cr=30000)]
with open(fp, "w", encoding="utf-8") as f:
    for o in lines:
        f.write(json.dumps(o) + "\n")
    f.write(json.dumps(asst("a2", "2026-10-01T08:01:00Z", cw=1000, cr=30000)) + "\n")  # same response, 2nd block
db = os.path.join(work, "idx.db")
conn = ns["_transcript_index_connect"](db)
tick = ns["_transcript_index_tick"]
tick(conn, projects)
with open(fp, "a", encoding="utf-8") as f:  # next tick: yet another block of a2, then a re-cache after 2 h
    f.write(json.dumps(asst("a2", "2026-10-01T08:01:00Z", cw=1000, cr=30000)) + "\n")
    f.write(json.dumps(asst("a3", "2026-10-01T10:05:00Z", cw=32000, cr=0)) + "\n")
tick(conn, projects)
row = conn.execute("SELECT SUM(n), SUM(cw), SUM(cr), SUM(cold_n), SUM(cold_idle_n), SUM(cold_cw) FROM usage").fetchone()
c.check("3 responses counted across 6 lines and 2 ticks", row[0] == 3)
c.check("tokens deduplicated", row[1] == 63000 and row[2] == 30000)
c.check("re-cache after 2 h recorded (prev call carried across ticks)", row[3] == 1 and row[4] == 1 and row[5] == 32000)

st = ns["_cache_stats"](conn, "2000-01-01")
t = st["total"]
c.check("hit rate = reads / all prompt tokens", t["hit_rate"] == round(30000 / (3 + 63000 + 30000), 4))
c.check("avg context per call", t["avg_context"] == (3 + 63000 + 30000) // 3)
c.check("cost split adds up to total cost",
        abs(t["cost_read"] + t["cost_write"] + t["cost_out"] + t["cost_in"] - t["cost"]) < 1e-3)
c.check("cache writes priced as 1-hour (2x input = $8/M on opus-5-5)", abs(t["cost_write"] - 63000 * 8 / 1e6) < 1e-4)
c.check("saved = reads at (input - read) price", abs(t["saved"] - 30000 * (4.0 - 0.2) / 1e6) < 1e-4)
c.check("cold_extra = re-written tokens at (1h write - read) price", abs(t["cold_extra"] - 32000 * (8.0 - 0.2) / 1e6) < 1e-4)
c.check("session row carries its numbers", st["sessions"][0]["session_id"] == "s1" and st["sessions"][0]["cold_n"] == 1)
c.check("by_model + by_day present", st["by_model"][0]["model"] == "claude-opus-5-5" and len(st["by_day"]) == 1)
c.check("since filter", ns["_cache_stats"](conn, "2099-01-01")["total"]["n"] == 0)

# a transcript rewritten from scratch resets its usage too
with open(fp, "w", encoding="utf-8") as f:
    f.write(json.dumps(asst("b1", "2026-10-02T08:00:00Z", cw=500)) + "\n")
tick(conn, projects)
c.check("shrunk file: usage re-counted from 0",
        conn.execute("SELECT SUM(n), SUM(cold_n) FROM usage").fetchone() == (1, 0))

# schema version: an older index is dropped and rebuilt
conn.execute("PRAGMA user_version = 1")
conn.commit()
conn.close()
conn = ns["_transcript_index_connect"](db)
c.check("old index version -> tables dropped, version bumped",
        conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
        and conn.execute("PRAGMA user_version").fetchone()[0] == ns["_TRANSCRIPT_INDEX_VERSION"])
tick(conn, projects)
c.check("...and rebuilt on the next tick", conn.execute("SELECT SUM(n) FROM usage").fetchone()[0] == 1)

conn.close()
shutil.rmtree(work, ignore_errors=True)
c.finish()
