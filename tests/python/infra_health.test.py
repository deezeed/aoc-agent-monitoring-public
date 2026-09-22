"""Tests _tail_log_lines, _parse_log_line_ts, _tail_json_log, and
_bucket_audit_events, extracted straight from monitor.py. The first two
are the infra health panel's data layer for watchdog.py/sentinel.py's
plaintext logs (separate OS processes with no shared Python state); the
latter two are the same idea for the JSON-lines kill_audit.log/
alert_audit.log audit trails, which were write-only until this panel
started reading them back."""
import sys, os, json, tempfile, shutil, time, re, threading
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ns = exec_functions(
    ["_tail_log_lines", "_parse_log_line_ts", "_tail_json_log", "_bucket_audit_events"],
    extra_globals={
        "re": re, "datetime": datetime, "json": json, "os": os, "threading": threading,
        # _tail_json_log delegates to _tail_log_lines and caches its parsed
        # result keyed by (path, max_lines) -> (mtime, size, entries); these
        # module-level globals it reads/writes need seeding the same way
        # DB_FILE/_db_lock are seeded for the DB-backed tests elsewhere.
        "_tail_json_log_cache": {}, "_tail_json_log_cache_lock": threading.Lock(),
    },
)
_tail_log_lines = ns["_tail_log_lines"]
_parse_log_line_ts = ns["_parse_log_line_ts"]
_tail_json_log = ns["_tail_json_log"]
_bucket_audit_events = ns["_bucket_audit_events"]

c = Checker()
tmpdir = tempfile.mkdtemp(prefix="aoc_infra_test_")

try:
    # ── _tail_log_lines ──
    log_path = os.path.join(tmpdir, "test.log")
    with open(log_path, "w", encoding="utf-8") as f:
        for i in range(30):
            f.write(f"[2026-07-19 10:{i:02d}:00] line {i}\n")

    tail5 = _tail_log_lines(log_path, 5)
    c.check("returns exactly n lines when file has more", len(tail5) == 5)
    c.check("returns the LAST n lines, in order", tail5[-1] == "[2026-07-19 10:29:00] line 29")
    c.check("first of the tail is the correct offset", tail5[0] == "[2026-07-19 10:25:00] line 25")

    small_path = os.path.join(tmpdir, "small.log")
    with open(small_path, "w", encoding="utf-8") as f:
        f.write("[2026-07-19 10:00:00] only line\n")
    c.check("file with fewer than n lines returns all of them", len(_tail_log_lines(small_path, 20)) == 1)

    c.check("nonexistent file returns empty list, doesn't crash", _tail_log_lines(os.path.join(tmpdir, "nope.log"), 10) == [])

    blank_path = os.path.join(tmpdir, "blank.log")
    with open(blank_path, "w", encoding="utf-8") as f:
        f.write("\n\n[2026-07-19 10:00:00] real line\n\n")
    c.check("blank lines are skipped", _tail_log_lines(blank_path, 10) == ["[2026-07-19 10:00:00] real line"])

    # ── _parse_log_line_ts ──
    ts = _parse_log_line_ts("[2026-07-19 10:15:30] Monitor restarted (PID 1234)")
    expected = datetime(2026, 7, 19, 10, 15, 30).timestamp()
    c.check("parses a well-formed log line's timestamp correctly", ts == expected)
    c.check("malformed line (no brackets) returns None", _parse_log_line_ts("not a log line") is None)
    c.check("empty string returns None", _parse_log_line_ts("") is None)
    c.check("line with a valid-looking but bogus date still doesn't crash", _parse_log_line_ts("[9999-99-99 99:99:99] bad") is None)

    # ── _tail_json_log ──
    json_path = os.path.join(tmpdir, "audit.log")
    with open(json_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "2026-07-19 10:00:00", "event": "done"}) + "\n")
        f.write("not valid json\n")
        f.write(json.dumps({"ts": "2026-07-19 10:05:00", "event": "error"}) + "\n")
    parsed = _tail_json_log(json_path)
    c.check("valid JSON lines parsed, malformed line skipped", len(parsed) == 2)
    c.check("parsed entries keep their fields", parsed[0]["event"] == "done" and parsed[1]["event"] == "error")
    c.check("nonexistent JSON log returns empty list, doesn't crash", _tail_json_log(os.path.join(tmpdir, "nope.log")) == [])

    many_path = os.path.join(tmpdir, "many.log")
    with open(many_path, "w", encoding="utf-8") as f:
        for i in range(10):
            f.write(json.dumps({"ts": "2026-07-19 10:00:00", "n": i}) + "\n")
    c.check("max_lines caps how many trailing entries are parsed", len(_tail_json_log(many_path, max_lines=3)) == 3)
    c.check("max_lines keeps the LAST n entries", _tail_json_log(many_path, max_lines=3)[-1]["n"] == 9)

    # ── _tail_json_log cache (mtime/size keyed, added to avoid re-reading
    # kill_audit.log/alert_audit.log in full on every ~1s /status tick) ──
    cache_path = os.path.join(tmpdir, "cache.log")
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "2026-07-19 10:00:00", "n": 1}) + "\n")
    first = _tail_json_log(cache_path)
    c.check("cache: first read gets the one entry written so far", len(first) == 1)
    with open(cache_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "2026-07-19 10:01:00", "n": 2}) + "\n")
    second = _tail_json_log(cache_path)
    c.check("cache: appending a new line invalidates the cache (size changed), new entry is picked up", len(second) == 2)
    third = _tail_json_log(cache_path)
    c.check("cache: unchanged file on a repeat call still returns the same data", third == second)

    # ── _bucket_audit_events ──
    now = datetime(2026, 7, 23, 12, 0, 0).timestamp()
    entries = [
        {"ts": "2026-07-23 11:00:00", "event": "done"},     # 1h ago -- in window
        {"ts": "2026-07-22 12:00:00", "event": "done"},     # 1d ago -- in window
        {"ts": "2026-07-20 12:00:00", "event": "error"},    # 3d ago -- in window
        {"ts": "2026-07-01 12:00:00", "event": "done"},     # 22d ago -- outside 7d window
        {"event": "burn_spike"},                            # missing ts entirely -- skipped, not a crash
    ]
    counts = _bucket_audit_events(entries, now, "event", days=7)
    c.check("in-window entries counted correctly", counts.get("done") == 2 and counts.get("error") == 1)
    c.check("out-of-window entry excluded", "burn_spike" not in counts)
    c.check("entry missing the ts field is skipped, not counted", sum(counts.values()) == 3)
    c.check("empty entries list -> empty dict", _bucket_audit_events([], now, "event") == {})
    # days=1 cutoff lands exactly on entry 2's timestamp (24h ago) -- the
    # boundary is inclusive (`ts < cutoff` only excludes strictly older),
    # so both same-day entries still count and only the 3-day-old error
    # entry falls outside this narrower window.
    narrow = _bucket_audit_events(entries, now, "event", days=1)
    c.check("narrower days window excludes older in-range entries", narrow.get("done") == 2 and "error" not in narrow)

    unkeyed = _bucket_audit_events([{"ts": "2026-07-23 11:00:00"}], now, "event", days=7)
    c.check("entry present but missing the group-by key falls into (unknown)", unkeyed == {"(unknown)": 1})

finally:
    shutil.rmtree(tmpdir, ignore_errors=True)

c.finish()
