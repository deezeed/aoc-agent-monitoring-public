"""Tests the plan rate-limit pipeline in monitor.py, extracted straight from
the source: pace from samples, the /status view (statusline snapshot vs.
transcript limit hit, reset windows, projection), alert selection and its
dedupe keys, the persisted notified set, and _rate_limits_snapshot reading a
real file and collecting per-window pace samples."""
import sys, os, json, time, threading, tempfile, calendar
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
tmp = tempfile.mkdtemp(prefix="aoc_rl_")
errors = []

ns = exec_functions(
    ["_RATE_LIMIT_KINDS", "_RATE_LIMIT_WARN_PCT", "_rate_limit_pace", "_rate_limit_view",
     "_fmt_reset_time", "_rate_limit_alerts", "_record_rate_limit_hit", "_rate_limits_snapshot",
     "_load_rate_limit_notified", "_save_rate_limit_notified", "_load_json_file", "_iso_to_epoch"],
    {"os": os, "json": json, "time": time, "datetime": datetime, "calendar": calendar,
     "threading": threading, "_rate_limit_lock": threading.Lock(),
     "_rate_limit_state": {"mtime": None, "snap": None, "samples": {}, "hits": {}},
     "AOC_DATA_DIR": tmp,
     "RATE_LIMITS_FILE": os.path.join(tmp, "rate_limits.json"),
     "RATE_LIMIT_NOTIFIED_FILE": os.path.join(tmp, "rate_limit_notified.json"),
     "_log_bg_error": lambda where, e: errors.append((where, e))})

pace, view, alerts = ns["_rate_limit_pace"], ns["_rate_limit_view"], ns["_rate_limit_alerts"]
NOW = 1_800_000_000.0

# ── pace ──
c.check("pace: no samples -> None", pace([], NOW) is None)
c.check("pace: < 5 min span -> None", pace([(NOW - 200, 10), (NOW, 12)], NOW) is None)
c.check("pace: 10 points over 30 min -> 20 %/h",
        abs(pace([(NOW - 1800, 40), (NOW - 900, 45), (NOW, 50)], NOW) - 20) < 1e-9)
c.check("pace: samples older than the span are ignored",
        pace([(NOW - 7200, 0), (NOW - 600, 50), (NOW, 52)], NOW) is not None
        and abs(pace([(NOW - 7200, 0), (NOW - 600, 50), (NOW, 52)], NOW) - 12) < 1e-9)
c.check("pace: flat usage -> 0, not None", pace([(NOW - 900, 30), (NOW, 30)], NOW) == 0.0)

# ── view ──
c.check("view: nothing -> None", view(None, {}, {}, NOW) is None)
snap = {"updated_at": NOW - 30,
        "five_hour": {"used_percentage": 42.0, "resets_at": NOW + 7200},
        "seven_day": {"used_percentage": 18.4, "resets_at": NOW + 3 * 86400}}
v = view(snap, {}, {}, NOW)
fh = v["windows"][0]
c.check("view: both windows, 5h first", [w["kind"] for w in v["windows"]] == ["five_hour", "seven_day"])
c.check("view: 5h ok, no pace yet", fh["status"] == "ok" and fh["pace_pct_per_h"] is None and fh["eta_full_at"] is None)
c.check("view: resets_in_s and age", fh["resets_in_s"] == 7200 and v["age_s"] == 30)
c.check("view: weekly label", v["windows"][1]["label"] == "weekly")

# pace 20 %/h at 60 % with 2.5 h left -> hits 100 % in 2 h (before reset) -> warn
s2 = {"updated_at": NOW, "five_hour": {"used_percentage": 60.0, "resets_at": NOW + 9000}}
samples = {("five_hour", NOW + 9000): [(NOW - 1800, 50.0), (NOW, 60.0)]}
w = view(s2, {}, samples, NOW)["windows"][0]
c.check("view: projected pct at reset", w["projected_pct"] == 110.0)
c.check("view: eta_full_at 2 h out", w["eta_full_at"] == int(NOW + 7200))
c.check("view: on pace to hit -> warn under 80 %", w["status"] == "warn")

# slow pace, never reaches 100 before reset -> ok
samples_slow = {("five_hour", NOW + 9000): [(NOW - 1800, 59.0), (NOW, 60.0)]}
w = view(s2, {}, samples_slow, NOW)["windows"][0]
c.check("view: slow pace -> ok, no eta", w["status"] == "ok" and w["eta_full_at"] is None and w["projected_pct"] == 65.0)

c.check("view: 85 % -> warn",
        view({"five_hour": {"used_percentage": 85, "resets_at": NOW + 60}}, {}, {}, NOW)["windows"][0]["status"] == "warn")
c.check("view: 100 % -> hit",
        view({"five_hour": {"used_percentage": 100, "resets_at": NOW + 60}}, {}, {}, NOW)["windows"][0]["status"] == "hit")
r = view({"five_hour": {"used_percentage": 97, "resets_at": NOW - 5}}, {}, {}, NOW)["windows"][0]
c.check("view: window past its reset -> status reset, pct unknown", r["status"] == "reset" and r["pct"] is None)

# transcript limit hit newer than the statusline snapshot wins
hits = {"five_hour": {"resets_at": NOW + 1200, "seen_at": NOW - 10}}
w = view({"updated_at": NOW - 600, "five_hour": {"used_percentage": 91, "resets_at": NOW + 1200}}, hits, {}, NOW)["windows"][0]
c.check("view: newer limit message -> hit at 100 %", w["status"] == "hit" and w["pct"] == 100.0 and w["source"] == "limit_message")
w = view({"updated_at": NOW, "five_hour": {"used_percentage": 3, "resets_at": NOW + 18000}},
         {"five_hour": {"resets_at": NOW + 1200, "seen_at": NOW - 600}}, {}, NOW)["windows"][0]
c.check("view: statusline newer than the hit (new window) wins", w["pct"] == 3 and w["source"] == "statusline")
c.check("view: expired hit ignored", view(None, {"five_hour": {"resets_at": NOW - 1, "seen_at": NOW - 100}}, {}, NOW) is None)
w = view(None, {"seven_day": {"resets_at": NOW + 86400, "seen_at": NOW}}, {}, NOW)
c.check("view: hit alone (no statusline) is shown", w["windows"][0]["kind"] == "seven_day" and w["age_s"] is None)

# ── alerts ──
v85 = view({"five_hour": {"used_percentage": 85.2, "resets_at": NOW + 3600}}, {}, {}, NOW)
a = alerts(v85, set(), NOW)
c.check("alerts: one warning at 85 %", len(a) == 1 and a[0][0] == ("five_hour", int(NOW + 3600), "warn"))
c.check("alerts: warning title names the window and pct", "5-hour limit at 85%" in a[0][1])
c.check("alerts: webhook payload", a[0][3]["event"] == "rate_limit" and a[0][3]["level"] == "warning" and a[0][3]["window"] == "five_hour")
c.check("alerts: already notified -> nothing", alerts(v85, {a[0][0]}, NOW) == [])
vhit = view({"five_hour": {"used_percentage": 100, "resets_at": NOW + 3600}}, {}, {}, NOW)
ah = alerts(vhit, {a[0][0]}, NOW)
c.check("alerts: hit fires even after the warning", len(ah) == 1 and ah[0][3]["level"] == "hit" and "reached" in ah[0][1])
vpace = view(s2, {}, samples, NOW)
ap = alerts(vpace, set(), NOW)
c.check("alerts: pace warning says when", len(ap) == 1 and ap[0][1].startswith("⚠ On pace to hit the 5-hour limit at"))
c.check("alerts: ok window -> nothing", alerts(view(snap, {}, {}, NOW), set(), NOW) == [])
c.check("alerts: None view -> nothing", alerts(None, set(), NOW) == [])

fmt = ns["_fmt_reset_time"]
c.check("fmt_reset_time: same day -> HH:MM", len(fmt(NOW + 3600, NOW)) == 5)
c.check("fmt_reset_time: days away -> weekday", len(fmt(NOW + 3 * 86400, NOW)) == 9)

# ── notified persistence ──
now = time.time()
ns["_save_rate_limit_notified"]({("five_hour", now + 100, "warn"), ("seven_day", now - 100, "hit")})
loaded = ns["_load_rate_limit_notified"](now)
c.check("notified: saved keys load back, expired ones dropped", loaded == {("five_hour", now + 100, "warn")})
with open(ns["RATE_LIMIT_NOTIFIED_FILE"], "w") as f:
    f.write("garbage")
c.check("notified: corrupt file -> empty set", ns["_load_rate_limit_notified"](now) == set())

# ── iso / hits ──
c.check("iso_to_epoch", ns["_iso_to_epoch"]("2026-10-06T10:00:00.123Z") == calendar.timegm((2026, 10, 6, 10, 0, 0)))
c.check("iso_to_epoch: junk -> None", ns["_iso_to_epoch"](None) is None)
rec = ns["_record_rate_limit_hit"]
rec("five_hour", now + 500, now - 50)
rec("five_hour", now + 400, now - 100)  # older sighting doesn't replace a newer one
rec("opus_weekly", now + 500, now)      # unknown kind ignored
rec("seven_day", "soon", now)            # bad resets_at ignored
hits_state = ns["_rate_limit_state"]["hits"]
c.check("record hit: newest sighting kept, unknown/bad ignored",
        hits_state == {"five_hour": {"resets_at": now + 500, "seen_at": now - 50}})
ns["_rate_limit_state"]["hits"].clear()

# ── snapshot from a real file ──
snapshot = ns["_rate_limits_snapshot"]
c.check("snapshot: no file -> None", snapshot() is None)
rf = ns["RATE_LIMITS_FILE"]
reset = int(time.time()) + 9000

def write(pct, updated_at, mtime):
    with open(rf, "w") as f:
        json.dump({"updated_at": updated_at, "five_hour": {"used_percentage": pct, "resets_at": reset}}, f)
    os.utime(rf, (mtime, mtime))

t = time.time()
write(40.0, t - 1200, t - 1200)
s1 = snapshot()
c.check("snapshot: reads the file", s1["windows"][0]["pct"] == 40.0)
write(50.0, t - 1, t - 1)
s2v = snapshot()
c.check("snapshot: second reading -> pace from samples", s2v["windows"][0]["pace_pct_per_h"] is not None
        and abs(s2v["windows"][0]["pace_pct_per_h"] - 30.0) < 0.2)
c.check("snapshot: unchanged mtime doesn't add a sample",
        snapshot() and len(ns["_rate_limit_state"]["samples"][("five_hour", reset)]) == 2)
with open(rf, "w") as f:
    f.write("{not json")
os.utime(rf, (t + 5, t + 5))
c.check("snapshot: corrupt file keeps the last good reading", snapshot()["windows"][0]["pct"] == 50.0)
c.check("no background errors", errors == [])

c.finish()
