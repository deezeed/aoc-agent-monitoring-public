"""Tests conversation search, extracted straight from monitor.py: what gets
indexed from a transcript line, incremental indexing (offsets, a partial
last line still being written, a rewritten/shrunk file, the per-tick byte
budget), FTS query building (user input is never FTS syntax), and search
results grouped per session with diacritics-insensitive matching."""
import sys, os, json, re, sqlite3, tempfile, shutil, time, calendar
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
work = tempfile.mkdtemp(prefix="aoc_ts_")
ns = exec_functions(
    ["_TRANSCRIPT_INDEX_MAX_TEXT", "_TRANSCRIPT_INDEX_BYTES_PER_TICK", "_transcript_index_connect",
     "_transcript_line_messages", "_GIT_COMMIT_LINE_RE", "_GIT_SHORTSTAT_RE", "_GIT_COMMIT_CMD_RE", "_git_commit_calls", "_tool_result_ids", "_commit_from_line", "_index_commit_line", "_transcript_index_file", "_transcript_index_tick", "_fts_query",
     "_search_transcripts", "_TRANSCRIPT_INDEX_VERSION", "_COLD_IDLE_S", "_RECACHE_MIN_TOKENS",
     "_usage_line_sample", "_usage_add_samples", "_MODEL_PRICING", "_CACHE_WRITE_1H_MULT",
     "_model_pricing", "_calc_cost", "_iso_to_epoch"],
    {"os": os, "json": json, "re": re, "sqlite3": sqlite3, "TRANSCRIPT_INDEX_DB": "",
     "time": time, "calendar": calendar, "datetime": datetime,
     "_decode_project_name": lambda enc: enc.split("-")[-1]})
msgs_of, fts = ns["_transcript_line_messages"], ns["_fts_query"]

# ── what gets indexed ──
u = lambda content, **kw: dict({"type": "user", "message": {"content": content}}, **kw)
a = lambda blocks, **kw: dict({"type": "assistant", "message": {"content": blocks}}, **kw)
c.check("user prompt string", msgs_of(u("fix the login bug")) == [("user", "fix the login bug")])
c.check("user text blocks", msgs_of(u([{"type": "text", "text": "hello"}, {"type": "image"}])) == [("user", "hello")])
c.check("tool results skipped", msgs_of(u([{"type": "tool_result", "content": "big output"}])) == [])
c.check("meta lines skipped", msgs_of(u("caveat", isMeta=True)) == [])
c.check("harness <task-notification> strings skipped", msgs_of(u("<task-notification>x</task-notification>")) == [])
c.check("sidechain (subagent) skipped", msgs_of(a([{"type": "text", "text": "sub"}], isSidechain=True)) == [])
c.check("assistant text kept, tool_use/thinking dropped",
        msgs_of(a([{"type": "thinking", "thinking": "hm"}, {"type": "text", "text": "Done."},
                   {"type": "tool_use", "name": "Bash"}])) == [("assistant", "Done.")])
c.check("other line types ignored", msgs_of({"type": "attachment"}) == [] and msgs_of("x") == [])
c.check("long text truncated", len(msgs_of(u("x" * 50000))[0][1]) == 20000)

# ── fts query ──
c.check("fts: words ANDed, quoted, last one prefix", fts("login bug") == '"login" "bug"*')
c.check("fts: FTS operators are literal words", fts('NEAR("x") -foo:* AND') == '"NEAR" "x" "foo" "AND"*')
c.check("fts: empty / punctuation only -> ''", fts("") == "" and fts(" ** -- ") == "")
c.check("fts: keeps diacritics and inner apostrophes", fts("pokračuj don't") == '"pokračuj" "don\'t"*')

# ── indexing ──
projects = os.path.join(work, "projects")
pdir = os.path.join(projects, "C--work-shop")
os.makedirs(pdir)
fp = os.path.join(pdir, "sess-1.jsonl")
lines = [
    {"type": "ai-title", "aiTitle": "Checkout redesign"},
    {"type": "user", "cwd": "C:\\work\\shop", "timestamp": "2026-10-01T09:00:00Z",
     "message": {"content": "Prečo padá platobná brána pri checkout?"}},
    {"type": "assistant", "timestamp": "2026-10-01T09:00:05Z",
     "message": {"content": [{"type": "text", "text": "The Stripe webhook secret is missing in production."}]}},
    {"type": "user", "timestamp": "2026-10-01T09:01:00Z",
     "message": {"content": [{"type": "tool_result", "content": "Stripe webhook secret ... (tool output)"}]}},
]
with open(fp, "w", encoding="utf-8") as f:
    for o in lines:
        f.write(json.dumps(o, ensure_ascii=False) + "\n")
    f.write('{"type": "user", "message": {"content": "half a li')  # still being written

conn = ns["_transcript_index_connect"](os.path.join(work, "idx.db"))
tick = ns["_transcript_index_tick"]
seen, cur, spent = tick(conn, projects)
size = os.path.getsize(fp)
off = conn.execute("SELECT offset FROM files").fetchone()[0]
c.check("partial last line left for later", off < size and spent == off)
c.check("file not current yet while the line is partial", (seen, cur) == (1, 0))
c.check("two messages indexed (tool result not)", conn.execute("SELECT COUNT(*) FROM msgs").fetchone()[0] == 2)
meta = conn.execute("SELECT project, cwd, title, first_ts, last_ts FROM sessions_meta").fetchone()
c.check("session meta: project, cwd, title, first/last ts",
        meta == ("shop", "C:\\work\\shop", "Checkout redesign", "2026-10-01T09:00:00Z", "2026-10-01T09:01:00Z"))

with open(fp, "a", encoding="utf-8") as f:
    f.write('ne about refunds"}, "timestamp": "2026-10-01T09:05:00Z"}\n')
seen, cur, spent = tick(conn, projects)
c.check("completed line indexed on the next tick", (seen, cur) == (1, 1)
        and conn.execute("SELECT COUNT(*) FROM msgs").fetchone()[0] == 3)
c.check("last_ts advanced", conn.execute("SELECT last_ts FROM sessions_meta").fetchone()[0] == "2026-10-01T09:05:00Z")
c.check("idle tick reads nothing", tick(conn, projects)[2] == 0)

search = ns["_search_transcripts"]
r = search(conn, "platobna brana")
c.check("diacritics-insensitive match", len(r) == 1 and r[0]["session_id"] == "sess-1" and r[0]["title"] == "Checkout redesign")
c.check("snippet marks the match with \\x02/\\x03", "\x02platobná\x03" in r[0]["hits"][0]["snippet"])
c.check("prefix match on the last word", len(search(conn, "webho")) == 1)
c.check("all words must match", search(conn, "stripe banana") == [])
c.check("role + ts on hits", search(conn, "stripe")[0]["hits"][0]["role"] == "assistant"
        and search(conn, "stripe")[0]["hits"][0]["ts"] == "2026-10-01T09:00:05Z")
c.check("tool output never matches", search(conn, "tool output") == [])
c.check("FTS syntax in input doesn't raise", search(conn, 'refunds" OR (') != None)

# rewritten (shrunk) transcript -> re-indexed from scratch, no duplicates
with open(fp, "w", encoding="utf-8") as f:
    f.write(json.dumps({"type": "user", "message": {"content": "brand new start"}}) + "\n")
tick(conn, projects)
c.check("shrunk file re-indexed from 0", conn.execute("SELECT COUNT(*) FROM msgs").fetchone()[0] == 1
        and len(search(conn, "brand new")) == 1 and search(conn, "stripe") == [])

# grouping + limits across sessions
for i in range(5):
    with open(os.path.join(pdir, f"multi-{i}.jsonl"), "w", encoding="utf-8") as f:
        for k in range(4):
            f.write(json.dumps({"type": "user", "message": {"content": f"deploy note {k} for run {i}"}}) + "\n")
tick(conn, projects)
r = search(conn, "deploy", limit_sessions=3, hits_per_session=2)
c.check("limited to 3 sessions, 2 hits each, n_hits counts all",
        len(r) == 3 and all(len(s["hits"]) == 2 and s["n_hits"] == 4 for s in r))

# byte budget: a tick stops early, the next one continues
pdir2 = os.path.join(projects, "C--big")
os.makedirs(pdir2)
for i in range(3):
    with open(os.path.join(pdir2, f"big-{i}.jsonl"), "w", encoding="utf-8") as f:
        for k in range(200):
            f.write(json.dumps({"type": "user", "message": {"content": f"bulk text line {k} " + "pad " * 20}}) + "\n")
seen, cur, spent = tick(conn, projects, budget_bytes=30000)
c.check("budget: tick stops after ~budget bytes, not everything current", cur < seen and spent <= 30000 + 200)
for _ in range(20):
    seen, cur, spent = tick(conn, projects, budget_bytes=30000)
    if cur == seen:
        break
c.check("budget: later ticks finish the job", cur == seen
        and conn.execute("SELECT COUNT(*) FROM msgs WHERE msgs MATCH 'bulk'").fetchone()[0] == 600)

conn.close()
shutil.rmtree(work, ignore_errors=True)
c.finish()
