"""Ask AOC (monitor.py): the question -> period (_ask_period), topics and
search words, the capped fact sheet built from a real transcript index
(_ask_facts: sessions, spend per day/model, commits, word matches,
clients, failures, text search) and _ask end to end with a fake Haiku:
prompt contents, answer cache per question+day, force, busy guard,
session refs kept only when the answer cites them, errors."""
import sys, os, json, re, sqlite3, tempfile, shutil, time, calendar, threading
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
work = tempfile.mkdtemp(prefix="aoc_ask_")
errors = []
ns = exec_functions(
    ["_load_json_file", "_TRANSCRIPT_INDEX_VERSION", "_transcript_index_connect", "_fts_query", "_search_transcripts",
     "_MODEL_PRICING", "_CACHE_WRITE_1H_MULT", "_model_pricing", "_cache_stats", "_commit_stats",
     "CLIENTS_FILE", "_CLIENT_NONE", "_sanitize_clients", "_load_clients", "_client_for", "_client_report",
     "ASK_CACHE_FILE", "_ASK_MAX_FACTS", "_ASK_MAX_Q", "_ASK_KEEP", "_ask_lock", "_ask_busy", "_ASK_INSTRUCTION",
     "_ASK_STOP", "_ASK_TOPICS", "_ASK_WEEKDAYS", "_ASK_MONTHS", "_ask_norm", "_ask_period", "_ask_topics",
     "_ask_keywords", "_ask_cap", "_ask_sessions", "_ask_facts", "_ask_cache_key", "_ask_load", "_ask_store",
     "_ask_history", "_ask"],
    {"os": os, "json": json, "re": re, "sqlite3": sqlite3, "time": time, "threading": threading,
     "datetime": datetime, "calendar": calendar, "AOC_DATA_DIR": work,
     "TRANSCRIPT_INDEX_DB": os.path.join(work, "missing.db"),
     "_log_bg_error": lambda *a: errors.append(a)})

# ── period ──
P = ns["_ask_period"]
today = date(2026, 10, 30)  # a Friday
c.check("default: last 30 days", P("how much?", today) == {"from": "2026-10-01", "to": "2026-10-30", "label": "last 30 days"})
c.check("today / dnes", P("Koľko som minul dnes?", today)["from"] == "2026-10-30" and P("today", today)["to"] == "2026-10-30")
c.check("včera (diacritics)", P("čo som robil včera", today) == {"from": "2026-10-29", "to": "2026-10-29", "label": "yesterday"})
c.check("predvčerom before včera", P("predvčerom", today)["from"] == "2026-10-28")
c.check("ISO date", P("what happened on 2026-10-12", today)["from"] == "2026-10-12")
c.check("d.m. date, future -> last year", P("12.11.", today)["from"] == "2025-11-12" and P("3.10.", today)["from"] == "2026-10-03")
c.check("invalid date ignored", P("31.02. and more", today)["label"] == "last 30 days")
c.check("last 7 days", P("posledných 7 dní", today) == {"from": "2026-10-24", "to": "2026-10-30", "label": "last 7 days"})
c.check("last 2 weeks", P("last 2 weeks", today)["from"] == "2026-10-17")
c.check("this week from Monday", P("tento týždeň", today) == {"from": "2026-10-26", "to": "2026-10-30", "label": "this week"})
c.check("last week Mon-Sun", P("minulý týždeň", today) == {"from": "2026-10-19", "to": "2026-10-25", "label": "last week"})
c.check("this month", P("this month", today)["from"] == "2026-10-01")
c.check("last month", P("minulý mesiac", today) == {"from": "2026-09-01", "to": "2026-09-30", "label": "last month"})
c.check("weekday: v utorok = latest Tuesday", P("čo som robil v utorok", today)["from"] == "2026-10-27")
c.check("weekday today", P("friday", today)["from"] == "2026-10-30")
c.check("month name, past", P("v septembri", today) == {"from": "2026-09-01", "to": "2026-09-30", "label": "September 2026"})
c.check("month name, later -> last year", P("december", today)["from"] == "2025-12-01")
c.check("current month capped at today", P("october", today)["to"] == "2026-10-30")
c.check("all time", P("celkovo", today)["from"] == "2000-01-01")

# ── topics + keywords ──
T, K = ns["_ask_topics"], ns["_ask_keywords"]
c.check("spend topic", "spend" in T("Koľko ma stál OmniSocial?"))
c.check("failure + commit topics", T("which commits failed") == {"failures", "commits"})
c.check("no topic", T("what did I do") == set())
c.check("keywords: names only", K("Koľko ma stál OmniSocial tento mesiac?") == ["omnisocial"])
c.check("keywords skip dates/weekdays/months", K("memebot v utorok 2026-10-12 september") == ["memebot"])
c.check("keywords limit", len(K("alpha beta gamma delta epsilon zeta eta", limit=3)) == 3)

# ── cap ──
cap = ns["_ask_cap"]
c.check("cap keeps blocks that fit", cap(["a", "", "b"], 100) == "a\n\nb")
big = "\n".join("line %d %s" % (i, "x" * 40) for i in range(100))
capped = cap(["head", big, "tail"], 1000)
c.check("cap cuts at a line and drops the rest", len(capped) <= 1000 and capped.endswith("(... more not shown)") and "tail" not in capped)

# ── a real transcript index ──
db = os.path.join(work, "idx.db")
conn = ns["_transcript_index_connect"](db)
S1, S2, S3 = "aaaa1111-0000-0000-0000-000000000001", "bbbb2222-0000-0000-0000-000000000002", "cccc3333-0000-0000-0000-000000000003"
conn.executemany("INSERT INTO sessions_meta (session_id, project, cwd, title) VALUES (?,?,?,?)", [
    (S1, "marek", "C:\\Users\\marek", "OmniSocial scheduler"),
    (S2, "AOC", "C:\\p\\AOC", "AOC inbox"),
    (S3, "marek", "C:\\Users\\marek", "")])
conn.executemany("INSERT INTO usage (session_id, model, day, n, inp, out, cw, cr, cost) VALUES (?,?,?,?,?,?,?,?,?)", [
    (S1, "claude-opus-5-5", "2026-10-29", 10, 100, 200, 300, 400, 4.5),
    (S1, "claude-opus-5-5", "2026-10-30", 5, 10, 20, 30, 40, 1.25),
    (S2, "claude-sonnet-5", "2026-10-30", 8, 10, 20, 30, 40, 2.0),
    (S3, "claude-opus-5-5", "2026-09-10", 3, 1, 1, 1, 1, 9.0)])
conn.executemany("INSERT INTO msgs (text, session_id, ts, role) VALUES (?,?,?,?)", [
    ("pokračuj v OmniSocial, oprav scheduler", S1, "2026-10-29T08:00:00Z", "user"),
    ("Scheduler je opravený a pushnutý", S1, "2026-10-29T09:00:00Z", "assistant"),
    ("build the inbox view", S2, "2026-10-30T08:00:00Z", "user")])
ep = calendar.timegm((2026, 10, 30, 10, 0, 0))
conn.execute("INSERT INTO commits (session_id, sha, kind, subject, cwd, epoch, day, cost_at) VALUES (?,?,?,?,?,?,?,?)",
             (S2, "abcdef1234", "committed", "Inbox view", "C:\\p\\AOC", ep, "2026-10-30", 1.5))
conn.execute("INSERT INTO commits (session_id, sha, kind, subject, cwd, epoch, day, cost_at) VALUES (?,?,?,?,?,?,?,?)",
             (S2, "tu:toolu_x", "pending", "", "C:\\p\\AOC", ep, "2026-10-30", 0))
conn.commit()

F = ns["_ask_facts"]
fails = []


def fake_fail(days):
    fails.append(days)
    return {"failed": 7, "calls": 100, "rate": 7.0, "secs": 600, "cost": 1.234, "denied": 2,
            "top": [{"tool": "Bash", "target": "pytest", "reason": "exit code 1", "count": 5, "sessions": 1, "cost": 1.0,
                     "last_sid": S1, "last_label": "OmniSocial scheduler"}]}


facts, ids, period = F(conn, "Koľko ma stál OmniSocial tento týždeň?", today, fail_fn=fake_fail, client_cfg=None)
c.check("period in sheet", "PERIOD: this week (2026-10-26 to 2026-10-30)" in facts)
c.check("total over period only (S3 is September)", "Total Claude Code spend: $7.75 in 2 sessions; 1 commits." in facts)
c.check("word match block", "- 'omnisocial': $5.75 in 1 sessions ([#aaaa1111])" in facts)
c.check("spend per day", "2026-10-29 Thu $4.50" in facts and "2026-10-30 Fri $3.25" in facts)
c.check("sessions most expensive first", facts.index("[#aaaa1111] 2026-10-29..2026-10-30 $5.75") < facts.index("[#bbbb2222] 2026-10-30 $2.00"))
c.check("first message shown", 'first msg: "pokračuj v OmniSocial, oprav scheduler"' in facts)
c.check("by model with spend topic", "SPEND BY MODEL:" in facts and "claude-opus-5-5: $5.75, 15 API calls" in facts)
c.check("text search hit", "TEXT SEARCH IN CONVERSATIONS" in facts and "[#aaaa1111]" in facts)
c.check("failures not scanned unless asked", fails == [])
c.check("commits block only when asked or no topic", "COMMITS" not in facts)
c.check("id map", ids[S1[:8]] == {"id": S1, "title": "OmniSocial scheduler"} and S2[:8] in ids)

facts, ids, _p = F(conn, "what did I commit today? anything failed", today, fail_fn=fake_fail, client_cfg=None)
c.check("commits block, pending tu: rows skipped", "COMMITS" in facts and "abcdef1" in facts and '"Inbox view" $1.50 [#bbbb2222]' in facts
        and "tu:toolu" not in facts)
c.check("failures block from fail_fn", "FAILURES:" in facts and "7 failed tool calls of 100 (7.0%)" in facts and "~10 min and $1.23 lost" in facts)
c.check("failure pattern cites session", "- Bash pytest (exit code 1): 5x in 1 sessions, $1.00; last [#aaaa1111]" in facts)
c.check("fail days = from period start to today", fails and fails[-1] == 1)

cfg = ns["_sanitize_clients"]({"clients": [{"name": "Acme", "match": ["omnisocial"], "markup": 20}]})
facts, _i, _p = F(conn, "koľko faktúrovať klientom tento mesiac", today, fail_fn=None, client_cfg=cfg)
c.check("clients block", "CLIENTS in 2026-10:" in facts and "- Acme: $5.75 (billed $6.90), 1 sessions, 0 commits" in facts)
c.check("unassigned clients line", "- (no client): $2.00" in facts)

facts, _i, _p = F(conn, "how was my prompt cache last 7 days", today, fail_fn=None, client_cfg=None)
c.check("cache block", "PROMPT CACHE since 2026-10-24: hit rate" in facts)

facts, ids, _p = F(conn, "zzzunknownword in september", today, fail_fn=None, client_cfg=None)
c.check("empty-ish period still has a total", "Total Claude Code spend: $9.00 in 1 sessions" in facts and "TEXT SEARCH" not in facts)

facts, _i, _p = F(conn, "scheduler zzzmissing", today, fail_fn=None, client_cfg=None)
c.check("AND search empty -> word by word", "TEXT SEARCH" in facts and "[#aaaa1111]" in facts.split("TEXT SEARCH")[1])
conn.close()

# ── _ask end to end with a fake Haiku ──
ask = ns["_ask"]
calls = []


def haiku(instr, stdin):
    calls.append((instr, stdin))
    return "Spolu $7.75; najviac [#aaaa1111] ($5.75).\n", 0.0123


conn_fn = lambda: ns["_transcript_index_connect"](db)
NOW = calendar.timegm((2026, 10, 30, 12, 0, 0))
r = ask("  Koľko ma stál   OmniSocial tento týždeň? ", run=haiku, now=NOW, conn_fn=conn_fn, fail_fn=fake_fail)
c.check("answer ok", r["ok"] and r["answer"] == "Spolu $7.75; najviac [#aaaa1111] ($5.75)." and r["cached"] is False)
c.check("question normalized", r["q"] == "Koľko ma stál OmniSocial tento týždeň?")
c.check("instruction + question + facts sent", calls[0][0] == ns["_ASK_INSTRUCTION"] and calls[0][1].startswith("QUESTION: Koľko ma stál")
        and "FACTS:\nPERIOD: this week" in calls[0][1])
c.check("instruction: language + only facts", "same language as the question" in calls[0][0] and "ONLY the FACTS" in calls[0][0])
c.check("only cited sessions kept", list(r["sessions"]) == ["aaaa1111"] and r["sessions"]["aaaa1111"]["id"] == S1)
c.check("cost rounded", r["cost"] == 0.0123 and r["period"]["label"] == "this week")
r2 = ask("koľko ma stál omnisocial tento týždeň?", run=haiku, now=NOW + 60, conn_fn=conn_fn, fail_fn=fake_fail)
c.check("same question same day -> cache, no new call", r2["cached"] is True and len(calls) == 1 and r2["answer"] == r["answer"])
r3 = ask("koľko ma stál omnisocial tento týždeň?", force=True, run=haiku, now=NOW + 120, conn_fn=conn_fn, fail_fn=fake_fail)
c.check("force asks again", r3["cached"] is False and len(calls) == 2)
r4 = ask("koľko ma stál omnisocial tento týždeň?", run=haiku, now=NOW + 86400, conn_fn=conn_fn, fail_fn=fake_fail)
c.check("next day -> new answer", r4["cached"] is False and len(calls) == 3)
c.check("history newest first", ns["_ask_history"]()["items"][0]["at"] == NOW + 86400)
c.check("cache file on disk", os.path.exists(ns["ASK_CACHE_FILE"]))

c.check("too short", ask("a", run=haiku, conn_fn=conn_fn)["ok"] is False and len(calls) == 3)
c.check("no index yet", "isn't built yet" in ask("how much today", run=haiku, now=NOW)["error"])
c.check("long question capped", ask("q" * 900, run=haiku, now=NOW, conn_fn=conn_fn, fail_fn=fake_fail)["q"] == "q" * 500)

ns["_ask_busy"][0] = True
rb = ask("something else entirely", run=haiku, now=NOW, conn_fn=conn_fn)
c.check("busy guard", rb["ok"] is False and rb.get("busy") is True)
ns["_ask_busy"][0] = False


def broken(instr, stdin):
    raise RuntimeError("Claude Code took too long to answer")


rf = ask("why did it fail", run=broken, now=NOW, conn_fn=conn_fn, fail_fn=fake_fail)
c.check("haiku failure -> error, busy released, logged", rf["ok"] is False and "too long" in rf["error"]
        and ns["_ask_busy"][0] is False and errors)
c.check("failure not cached", ask("why did it fail", run=haiku, now=NOW, conn_fn=conn_fn, fail_fn=fake_fail)["cached"] is False)

d = ns["_ask_load"]()
for i in range(70):
    ns["_ask_store"]("k%d" % i, {"key": "k%d" % i, "q": "x", "at": i})
c.check("cache keeps newest _ASK_KEEP", len(ns["_ask_load"]()) == ns["_ASK_KEEP"] and "k69" in ns["_ask_load"]() and "k0" not in ns["_ask_load"]())

shutil.rmtree(work, ignore_errors=True)
c.finish()
