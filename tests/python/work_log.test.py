"""Work log / standup (monitor.py, History -> WORK LOG): _worklog_range
(validation, swap, max length), _worklog_active_s (breaks), _worklog_project
(repo committed to > folder > home group), _worklog_report over a real
sqlite transcript-index schema (by project and by client, period filter on
usage/commits/messages, totals), _worklog_digest (no costs, cut),
_worklog_summary (cache, force, busy, errors -- fake runner, never a real
claude -p), _worklog_print_html (escaping, no costs) and _worklog_csv."""
import sys, os, json, sqlite3, tempfile, shutil, csv, io, threading, time, calendar
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
data = tempfile.mkdtemp(prefix="aoc_wl_")
errors = []
ns = exec_functions(["_load_json_file", "_iso_to_epoch", "_CLIENT_NONE", "_client_for",
                     "WORKLOG_CACHE_FILE", "_WORKLOG_MAX_DAYS", "_WORKLOG_GAP_S", "_WORKLOG_DIGEST_MAX",
                     "_WORKLOG_MODES", "_WORKLOG_HOME_GROUP", "_worklog_lock", "_worklog_busy",
                     "_worklog_range", "_worklog_active_s", "_worklog_project", "_worklog_report",
                     "_worklog_digest", "_WORKLOG_INSTRUCTIONS", "_worklog_key", "_worklog_cached",
                     "_worklog_store", "_worklog_cached_summaries", "_worklog_summary",
                     "_worklog_print_html", "_worklog_csv"],
                    {"os": os, "json": json, "time": time, "calendar": calendar, "threading": threading,
                     "datetime": datetime, "timedelta": timedelta, "AOC_DATA_DIR": data,
                     "_log_bg_error": lambda *a: errors.append(a)})

# ── range ──
R = ns["_worklog_range"]
c.check("valid range kept", R("2026-10-01", "2026-10-05", "2026-10-30") == ("2026-10-01", "2026-10-05"))
c.check("reversed -> swapped", R("2026-10-05", "2026-10-01", "2026-10-30") == ("2026-10-01", "2026-10-05"))
c.check("garbage -> today", R("x", "", "2026-10-30") == ("2026-10-30", "2026-10-30"))
c.check("only from -> single day", R("2026-10-02", "", "2026-10-30") == ("2026-10-02", "2026-10-02"))
c.check("bad date like 02-30 rejected", R("2026-02-30", "2026-02-30", "2026-10-30") == ("2026-10-30", "2026-10-30"))
c.check("too long -> start moves to 92 days", R("2025-01-01", "2026-10-30", "2026-10-30") == ("2026-07-31", "2026-10-30"))

# ── active time ──
A = ns["_worklog_active_s"]
c.check("gaps add up", A([100, 160, 400]) == 300)
c.check("long silence capped at 10 min", A([1, 61, 61 + 3600]) == 60 + 600)
c.check("unsorted + None ignored", A([400, None, 100, 160]) == 300)
c.check("one stamp -> 0", A([5]) == 0 and A([]) == 0)

# ── project ──
P = ns["_worklog_project"]
c.check("repo committed to most wins", P("C:/Users/marek", ["aoc", "omni", "aoc"], "C:\\Users\\marek") == "aoc")
c.check("folder name otherwise", P("C:\\work\\Phantom AI\\", [], "C:/Users/marek") == "Phantom AI")
c.check("home folder -> shared group (case-insensitive)", P("c:/users/MAREK", [], "C:\\Users\\marek") == ns["_WORKLOG_HOME_GROUP"])
c.check("no cwd -> home group", P("", [], "C:/Users/marek") == ns["_WORKLOG_HOME_GROUP"])

# ── report over the real schema ──
conn = sqlite3.connect(":memory:")
conn.executescript("""
CREATE TABLE usage (session_id TEXT, model TEXT, day TEXT, n INT, inp INT, out INT, cw INT, cw1h INT, cr INT, cost REAL,
                    cold_n INT, cold_cw INT, cold_cw1h INT, cold_idle_n INT);
CREATE TABLE sessions_meta (session_id TEXT PRIMARY KEY, project TEXT, cwd TEXT, title TEXT, first_ts TEXT, last_ts TEXT,
                            last_call_epoch REAL, run_cost REAL);
CREATE TABLE commits (session_id TEXT, sha TEXT, kind TEXT, branch TEXT, subject TEXT, cwd TEXT, ts TEXT, epoch REAL,
                      end_epoch REAL, day TEXT, cost_at REAL, files INT, ins INT, dels INT);
CREATE TABLE msgs (text TEXT, session_id TEXT, ts TEXT, role TEXT);
""")
U = lambda sid, day, cost, tok=1000: conn.execute(
    "INSERT INTO usage VALUES (?,?,?,1,?,0,0,0,0,?,0,0,0,0)", (sid, "opus", day, tok, cost))
U("aoc1", "2026-10-20", 5.0); U("aoc1", "2026-10-21", 1.0)
U("omni1", "2026-10-21", 3.0)
U("home1", "2026-10-21", 9.0)
U("old", "2026-10-01", 50.0)
for row in [("aoc1", "AOC", "C:/Users/marek", "Work log <tab>"),
            ("omni1", "omnisocial-web", "D:/code/omnisocial-web", "Pokračuj v omnisocial"),
            ("home1", "marek", "C:/Users/marek", ""),
            ("old", "x", "D:/x", "Old one")]:
    conn.execute("INSERT INTO sessions_meta (session_id, project, cwd, title) VALUES (?,?,?,?)", row)
# local-noon epochs so the local-day filter is timezone-proof
noon = lambda day, h=12, m=0: time.mktime(time.strptime(f"{day} {h:02d}:{m:02d}", "%Y-%m-%d %H:%M"))
iso = lambda ep: time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ep))
C = lambda sid, sha, subj, cwd, ep: conn.execute(
    "INSERT INTO commits (session_id, sha, kind, subject, cwd, epoch, day, ins, dels) VALUES (?,?,?,?,?,?,?,1,1)",
    (sid, sha, "commit", subj, cwd, ep, time.strftime("%Y-%m-%d", time.localtime(ep))))
C("aoc1", "abc1234def", "Work log tab", "C:/repo/AOC", noon("2026-10-21", 12, 20))
C("aoc1", "tu:toolu_1", "pending", "C:/repo/AOC", noon("2026-10-21", 12, 21))
C("aoc1", "fff0000aaa", "Earlier", "C:/repo/AOC", noon("2026-10-15"))
M = lambda sid, ep, role, text: conn.execute("INSERT INTO msgs VALUES (?,?,?,?)", (text, sid, iso(ep), role))
M("aoc1", noon("2026-10-21", 12, 0), "user", "Pridaj   work log\n tab")
M("aoc1", noon("2026-10-21", 12, 5), "assistant", "Hotovo, tab je tam.")
M("aoc1", noon("2026-10-19"), "user", "outside the period")
M("omni1", noon("2026-10-21", 9, 0), "user", "continue omni")
M("omni1", noon("2026-10-21", 9, 3), "assistant", "Done.")
M("home1", noon("2026-10-21", 15, 0), "user", "random question")

rep = ns["_worklog_report"](conn, "2026-10-21", "2026-10-21", "project", {"clients": [], "overrides": {}}, "C:/Users/marek")
names = [g["name"] for g in rep["groups"]]
c.check("groups: most spend first, home group last", names == ["omnisocial-web", "AOC", ns["_WORKLOG_HOME_GROUP"]])
aoc = rep["groups"][1]
c.check("only the period's cost", aoc["cost"] == 1.0 and rep["total"]["cost"] == 13.0)
c.check("pending + out-of-period commits dropped", [x["sha"] for x in aoc["commits"]] == ["abc1234def"])
s = aoc["sessions"][0]
c.check("asks whitespace-collapsed, outside-period msgs dropped", s["asks"] == ["Pridaj work log tab"])
c.check("last reply", s["last_reply"] == "Hotovo, tab je tam.")
c.check("active = msgs + commit (5 + 15 min, capped 10)", s["active_s"] == 300 + 600)
c.check("totals", rep["total"]["sessions"] == 3 and rep["total"]["commits"] == 1 and rep["total"]["tokens"] == 3000)
home = rep["groups"][-1]["sessions"][0]
c.check("untitled session -> first ask as title", home["title"] == "random question")
c.check("old session not in period", "old" not in [x["id"] for g in rep["groups"] for x in g["sessions"]])

cfg = {"clients": [{"name": "Omni", "match": ["omnisocial"], "markup": 0}], "overrides": {"home1": "Omni"}}
rep_c = ns["_worklog_report"](conn, "2026-10-20", "2026-10-21", "client", cfg, "C:/Users/marek")
by = {g["name"]: g for g in rep_c["groups"]}
c.check("by client: keyword + override", sorted(x["id"] for x in by["Omni"]["sessions"]) == ["home1", "omni1"])
c.check("by client: unassigned last", rep_c["groups"][-1]["name"] == "" and rep_c["groups"][-1]["sessions"][0]["id"] == "aoc1")
c.check("two-day range sums both days", by[""]["cost"] == 6.0 and rep_c["group"] == "client")
empty = ns["_worklog_report"](conn, "2025-01-01", "2025-01-01", "project", cfg, "")
c.check("empty period", empty["groups"] == [] and empty["total"]["sessions"] == 0)

# ── digest ──
D = ns["_worklog_digest"]
dg = D(rep)
c.check("digest has commits, asks, replies", "COMMIT AOC: Work log tab" in dg and "YOU: Pridaj work log tab" in dg
        and "CLAUDE (last): Hotovo" in dg and dg.startswith("PERIOD: 2026-10-21\n"))
c.check("digest carries no costs", "$" not in dg and "13.0" not in dg)
cut = D(rep, max_chars=80)
c.check("long digest cut in the middle", "[... cut ...]" in cut and cut.startswith("PERIOD") and len(cut) < 120)

# ── summary (fake runner) ──
calls = []
def run(prompt, text):
    calls.append((prompt, text)); return "- Done: work log", 0.0123
S = ns["_worklog_summary"]
r1 = S(rep, "standup", run=run, now=1000)
c.check("summary written", r1["ok"] and r1["fresh"] and r1["text"] == "- Done: work log" and r1["cost"] == 0.0123)
c.check("runner got the standup prompt + digest", calls[0][0] == ns["_WORKLOG_INSTRUCTIONS"]["standup"] and calls[0][1] == dg)
r2 = S(rep, "standup", run=run)
c.check("second time from cache, no new call", r2["ok"] and not r2["fresh"] and len(calls) == 1)
r3 = S(rep, "standup", force=True, run=run, now=2000)
c.check("force rewrites", r3["fresh"] and len(calls) == 2 and r3["generated_at"] == 2000)
c.check("cache file persisted", os.path.exists(ns["WORKLOG_CACHE_FILE"]))
cs = ns["_worklog_cached_summaries"](rep)
c.check("cached summaries per mode", cs["standup"]["text"] == "- Done: work log" and cs["client"] is None)
c.check("client mode uses its own prompt", S(rep, "client", run=run)["ok"] and calls[-1][0] == ns["_WORKLOG_INSTRUCTIONS"]["client"])
c.check("unknown mode refused", S(rep, "poem", run=run)["ok"] is False)
c.check("empty period refused, no call", S(empty, "standup", run=run)["ok"] is False and len(calls) == 3)
def boom(p, t): raise RuntimeError("claude not found")
rb = S(rep, "standup", force=True, run=boom)
c.check("runner error -> ok false + logged", rb["ok"] is False and "claude not found" in rb["error"] and errors)
key = ns["_worklog_key"]("standup", dg)
ns["_worklog_busy"].add(key)
c.check("same digest already running -> busy", S(rep, "standup", force=True, run=run).get("busy") is True)
ns["_worklog_busy"].discard(key)
c.check("busy cleared after error", key not in ns["_worklog_busy"])

# ── print + csv ──
h = ns["_worklog_print_html"](rep, {"text": "<b>Done</b>"})
c.check("print: titles + summary escaped", "Work log &lt;tab&gt;" in h and "&lt;b&gt;Done&lt;/b&gt;" in h and "<b>Done</b>" not in h)
c.check("print: no costs", "$" not in h.split("<body>")[1])
c.check("print: commit sha short", "<code>abc1234</code>" in h)
c.check("print: empty period message", "No work recorded" in ns["_worklog_print_html"](empty))
rows = list(csv.reader(io.StringIO(ns["_worklog_csv"](rep))))
c.check("csv header", rows[0][:3] == ["from", "to", "project"] and rows[0][-1] == "cost_usd")
c.check("csv session line + total per group", rows[3][2] == "AOC" and rows[3][3] == "Work log <tab>" and rows[3][-1] == "1.00"
        and rows[4][3] == "TOTAL" and rows[4][8] == "1")
c.check("csv covers every session", sum(1 for r in rows[1:] if r[3] != "TOTAL") == 3)

shutil.rmtree(data, ignore_errors=True)
c.finish()
