"""Tests the History row a plain CLI session gets from its transcript:
_transcript_session_times, _db_upsert_transcript_session, _transcript_span
and the startup backfill _db_backfill_transcript_sessions, extracted
straight from monitor.py.

Sessions with no subagents are only ever written by the transcript scanner,
which used to store tokens/cost and nothing else useful: a start time sliced
from the raw UTC timestamp (hours off, sometimes the wrong date), no end
time, duration 0, and no session name -- so History showed "06:50 - —",
"0s" and the cwd folder ("marek") for every one of them.

Wall-clock expectations are derived with the same calendar.timegm +
time.localtime calls the code uses (see iso_utc_to_local_hms.test.py), so
the test holds in any timezone. Runs against a real scratch SQLite DB."""
import sys, os, sqlite3, threading, tempfile, shutil, json, calendar, time, re
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_transcript_history_")
DB_FILE = os.path.join(SCRATCH, "history.db")
conn = sqlite3.connect(DB_FILE)
conn.executescript("""
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY, project TEXT, orchestrator TEXT, date TEXT,
        started_at TEXT, ended_at TEXT, duration_s INTEGER DEFAULT 0,
        agents INTEGER DEFAULT 0, done INTEGER DEFAULT 0, errors INTEGER DEFAULT 0,
        tokens INTEGER DEFAULT 0, cost REAL DEFAULT 0,
        task_done INTEGER DEFAULT 0, task_total INTEGER DEFAULT 0,
        file_count INTEGER DEFAULT 0, snapshot TEXT, cc_version TEXT,
        waiting_on_you_s INTEGER DEFAULT 0, tags TEXT DEFAULT '', title TEXT
    );
""")
conn.commit()
conn.close()

errors = []
ns = exec_functions(
    ["_db_conn", "_now_ts", "_transcript_session_times", "_db_upsert_transcript_session",
     "_TS_RE", "_transcript_span", "_db_backfill_transcript_sessions"],
    {"sqlite3": sqlite3, "os": os, "json": json, "re": re, "datetime": datetime,
     "calendar": calendar, "time": time, "DB_FILE": DB_FILE, "_db_lock": threading.Lock(),
     "_fix_mojibake": lambda s: s, "_log_bg_error": lambda where, e: errors.append((where, e))})
times = ns["_transcript_session_times"]
upsert = ns["_db_upsert_transcript_session"]
span = ns["_transcript_span"]
backfill = ns["_db_backfill_transcript_sessions"]


def local(y, mo, d, h, mi, s, fmt):
    return time.strftime(fmt, time.localtime(calendar.timegm(datetime(y, mo, d, h, mi, s).timetuple())))


def row(sid):
    c = sqlite3.connect(DB_FILE)
    c.row_factory = sqlite3.Row
    r = c.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    c.close()
    return dict(r) if r else None


def run_upsert(*args, **kw):
    c = sqlite3.connect(DB_FILE)
    upsert(c, *args, **kw)
    c.commit()
    c.close()


c = Checker()

# ── _transcript_session_times ──
d, st, en, dur = times("2026-10-02T06:50:10.474Z", "2026-10-02T07:02:42.178Z")
c.check("start converted to local wall-clock, not the raw UTC slice",
        st == local(2026, 10, 2, 6, 50, 10, "%H:%M:%S"))
c.check("end converted to local", en == local(2026, 10, 2, 7, 2, 42, "%H:%M:%S"))
c.check("date is the LOCAL date of the start", d == local(2026, 10, 2, 6, 50, 10, "%Y-%m-%d"))
c.check("duration = last - first", dur == 12 * 60 + 32)
c.check("late-UTC start keeps the local date (not the UTC one)",
        times("2026-10-01T23:30:00Z", "2026-10-01T23:40:00Z")[0] == local(2026, 10, 1, 23, 30, 0, "%Y-%m-%d"))
c.check("bad first timestamp -> empties", times("nope", "2026-10-02T07:00:00Z") == ("", "", "", 0))
c.check("missing last -> start only, no end, 0s", times("2026-10-02T06:50:10Z", "")[2:] == ("", 0))
c.check("last before first -> no end, 0s", times("2026-10-02T07:00:00Z", "2026-10-02T06:00:00Z")[2:] == ("", 0))

# ── _db_upsert_transcript_session ──
run_upsert("s1", "marek", "2026-10-02T06:50:10Z", "2026-10-02T07:00:10Z", 100, 1.5, "2.1.282", "Phantom AI")
r = row("s1")
c.check("insert: end, duration, title, cc_version recorded",
        r["ended_at"] == local(2026, 10, 2, 7, 0, 10, "%H:%M:%S") and r["duration_s"] == 600
        and r["title"] == "Phantom AI" and r["cc_version"] == "2.1.282" and r["tokens"] == 100)

c2 = sqlite3.connect(DB_FILE)
c2.execute("UPDATE sessions SET tags='client', project='renamed' WHERE id='s1'")
c2.commit(); c2.close()
run_upsert("s1", "marek", "2026-10-02T06:50:10Z", "2026-10-02T07:20:10Z", 250, 3.0, "", "")
r = row("s1")
c.check("update: end + duration move forward, tokens/cost refreshed",
        r["duration_s"] == 1800 and r["tokens"] == 250 and r["cost"] == 3.0)
c.check("update: tags and a non-empty project are never touched", r["tags"] == "client" and r["project"] == "renamed")
c.check("update: empty title / cc_version don't blank the stored ones",
        r["title"] == "Phantom AI" and r["cc_version"] == "2.1.282")

run_upsert("s1", "", "2026-10-02T06:50:10Z", "", None, None)
r = row("s1")
c.check("no last_ts: stored end kept, duration not shrunk",
        r["ended_at"] == local(2026, 10, 2, 7, 20, 10, "%H:%M:%S") and r["duration_s"] == 1800)
c.check("tokens/cost None keep the stored values", r["tokens"] == 250 and r["cost"] == 3.0)

# ── _transcript_span + backfill ──
proj = os.path.join(SCRATCH, "projects", "C--Users-marek")
os.makedirs(proj)
lines = [
    {"type": "summary"},  # no timestamp
    {"type": "user", "timestamp": "2026-09-30T06:37:53.100Z"},
    {"type": "ai-title", "aiTitle": "First title"},
    {"type": "assistant", "timestamp": "2026-09-30T08:07:53.900Z"},
    {"type": "ai-title", "aiTitle": "Final title"},
]
with open(os.path.join(proj, "old-1.jsonl"), "w", encoding="utf-8") as f:
    f.write("\n".join(json.dumps(x) for x in lines) + "\nnot json at all\n")
with open(os.path.join(proj, "old-2.jsonl"), "w", encoding="utf-8") as f:
    f.write(json.dumps({"type": "user", "timestamp": "2026-09-29T10:00:00Z"}) + "\n")

first, last, title = span(os.path.join(proj, "old-1.jsonl"))
c.check("span: first and last timestamps", first.startswith("2026-09-30T06:37:53") and last.startswith("2026-09-30T08:07:53"))
c.check("span: the LAST ai-title wins", title == "Final title")

# Rows as the old scanner left them: raw-UTC start, no end, 0s, no title.
c3 = sqlite3.connect(DB_FILE)
c3.executemany("INSERT INTO sessions (id, project, date, started_at, tokens, cost) VALUES (?,?,?,?,?,?)", [
    ("old-1", "marek", "2026-09-30", "06:37:53", 944962, 123.04),
    ("old-2", "Git", "2026-09-29", "10:00:00", 10, 0.1),
    ("no-transcript", "x", "2026-09-01", "01:00:00", 5, 0.01),
])
c3.commit(); c3.close()

# A transcript rewritten after the real start (compaction/resume): its first
# line is 07:24Z but the old scanner had already recorded 06:37:20Z (as raw
# UTC, ended_at NULL). The earlier, real start must survive.
with open(os.path.join(proj, "rewritten.jsonl"), "w", encoding="utf-8") as f:
    f.write(json.dumps({"type": "attachment", "timestamp": "2026-09-30T07:24:09.507Z"}) + "\n")
    f.write(json.dumps({"type": "assistant", "timestamp": "2026-09-30T18:18:38.000Z"}) + "\n")
c4 = sqlite3.connect(DB_FILE)
c4.execute("INSERT INTO sessions (id, project, date, started_at, tokens, cost) VALUES ('rewritten','Git','2026-09-30','06:37:20',1,0.1)")
c4.commit(); c4.close()

fixed = backfill(os.path.join(SCRATCH, "projects"))
r = row("rewritten")
c.check("rewritten transcript: earlier stored start (read as legacy UTC) wins",
        r["started_at"] == local(2026, 9, 30, 6, 37, 20, "%H:%M:%S"))
c.check("rewritten transcript: duration counts from that earlier start",
        r["duration_s"] == (18 * 3600 + 18 * 60 + 38) - (6 * 3600 + 37 * 60 + 20))

# A row the NEW code wrote (local start, ended_at set) is not re-read as UTC,
# and a later transcript start doesn't move it either.
run_upsert("rewritten", "", "2026-09-30T09:00:00Z", "2026-09-30T19:00:00Z", None, None)
r = row("rewritten")
c.check("new-format row: start stays put, not shifted again as if UTC",
        r["started_at"] == local(2026, 9, 30, 6, 37, 20, "%H:%M:%S")
        and r["ended_at"] == local(2026, 9, 30, 19, 0, 0, "%H:%M:%S"))

# After a restart the scanner can briefly report an OLDER last_ts than the one
# already stored -- the end (and duration) must not move back.
run_upsert("rewritten", "", "2026-09-30T06:37:20Z", "2026-09-30T10:00:00Z", None, None)
r = row("rewritten")
c.check("stale older last_ts: end and duration stay at the later value",
        r["ended_at"] == local(2026, 9, 30, 19, 0, 0, "%H:%M:%S")
        and r["duration_s"] == (19 * 3600) - (6 * 3600 + 37 * 60 + 20))
r = row("old-1")
c.check("backfill repaired the three rows that have transcripts", fixed == 3)
c.check("backfill: local start, end, 90 min duration",
        r["started_at"] == local(2026, 9, 30, 6, 37, 53, "%H:%M:%S")
        and r["ended_at"] == local(2026, 9, 30, 8, 7, 53, "%H:%M:%S") and r["duration_s"] == 5400)
c.check("backfill: title, and tokens/cost/project untouched",
        r["title"] == "Final title" and r["tokens"] == 944962 and r["cost"] == 123.04 and r["project"] == "marek")
c.check("backfill: a transcript without ai-title marks title '' (not re-read next start)",
        row("old-2")["title"] == "")
c.check("backfill: a row without a transcript is left alone", row("no-transcript")["ended_at"] is None)
c.check("backfill second run reads only what is still unrepaired", backfill(os.path.join(SCRATCH, "projects")) == 0)
c.check("backfill: missing projects dir -> 0, no crash", backfill(os.path.join(SCRATCH, "nope")) == 0)
c.check("no background errors logged", not errors)

shutil.rmtree(SCRATCH, ignore_errors=True)
c.finish()
