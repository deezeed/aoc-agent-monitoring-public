"""Plan-limit forecast: the pace is a least-squares slope over the running
maximum (noise and single bursts don't swing it), a resets_at that wobbles
between readings stays one window, samples survive a monitor restart
(RATE_LIMIT_SAMPLES_FILE), the view flags "runs out within 30 min", and the
limit_soon alert fires once per window and stands in for its warn. Extracted
straight from monitor.py."""
import sys, os, json, time, threading, tempfile, calendar
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions, read_monitor_source
from lib.check import Checker

c = Checker()
tmp = tempfile.mkdtemp(prefix="aoc_fc_")
errors = []


def fresh_ns():
    return exec_functions(
        ["_RATE_LIMIT_KINDS", "_RATE_LIMIT_WARN_PCT", "_RATE_LIMIT_SOON_S", "_RATE_LIMIT_KEY_TOL_S",
         "_RATE_LIMIT_SAVE_EVERY_S", "_rate_limit_pace", "_rate_limit_sample_key", "_rate_limit_samples_load",
         "_rate_limit_samples_dump", "_rate_limit_view", "_fmt_reset_time", "_fmt_in", "_rate_limit_alerts",
         "_rate_limit_soon_alerts", "_rate_limits_snapshot", "_load_json_file"],
        {"os": os, "json": json, "time": time, "datetime": datetime, "calendar": calendar,
         "threading": threading, "_rate_limit_lock": threading.Lock(),
         "_rate_limit_state": {"mtime": None, "snap": None, "samples": {}, "hits": {}},
         "AOC_DATA_DIR": tmp,
         "RATE_LIMITS_FILE": os.path.join(tmp, "rate_limits.json"),
         "RATE_LIMIT_SAMPLES_FILE": os.path.join(tmp, "rate_limit_samples.json"),
         "_log_bg_error": lambda where, e: errors.append((where, e))})


ns = fresh_ns()
pace, key, view = ns["_rate_limit_pace"], ns["_rate_limit_sample_key"], ns["_rate_limit_view"]
soon_alerts, alerts, fmt_in = ns["_rate_limit_soon_alerts"], ns["_rate_limit_alerts"], ns["_fmt_in"]
NOW = 1_800_000_000.0

# ── pace ──
c.check("pace: a lower reading (rounding noise) doesn't drag the pace down",
        abs(pace([(NOW - 1200, 50), (NOW - 600, 48), (NOW, 60)], NOW) - pace([(NOW - 1200, 50), (NOW - 600, 50), (NOW, 60)], NOW)) < 1e-9)
c.check("pace: never negative", pace([(NOW - 900, 60), (NOW, 40)], NOW) == 0.0)
steady = [(NOW - 1800 + i * 180, 40 + i) for i in range(11)]  # 1 pt / 3 min = 20 %/h
c.check("pace: steady line -> its slope", abs(pace(steady, NOW) - 20) < 1e-6)
burst = steady[:-1] + [(NOW, 60)]                             # last reading jumps +11 instead of +1
p_burst = pace(burst, NOW)
c.check("pace: one late burst moves the regression less than endpoints would",
        20 < p_burst < (60 - 40) / 1800 * 3600)
c.check("pace: unsorted samples are fine", abs(pace(list(reversed(steady)), NOW) - 20) < 1e-6)
c.check("pace: identical timestamps only -> None", pace([(NOW, 10), (NOW, 12)], NOW) is None)

# ── sample key ──
keys = {("five_hour", NOW + 9000), ("seven_day", NOW + 9000)}
c.check("key: reading 30 s off joins the window", key(keys, "five_hour", NOW + 9030) == ("five_hour", NOW + 9000))
c.check("key: other kind never joins", key({("seven_day", NOW + 9000)}, "five_hour", NOW + 9000) == ("five_hour", NOW + 9000))
c.check("key: a new window (hours later) is its own key", key(keys, "five_hour", NOW + 9000 + 18000) == ("five_hour", NOW + 27000))
c.check("key: closest of two candidates", key({("five_hour", NOW), ("five_hour", NOW + 100)}, "five_hour", NOW + 90) == ("five_hour", NOW + 100))

# ── view: forecast fields ──
snap = {"updated_at": NOW, "five_hour": {"used_percentage": 80.0, "resets_at": NOW + 7200}}
fast = {("five_hour", NOW + 7230): [(NOW - 1800, 60.0), (NOW, 80.0)]}  # 40 %/h, key 30 s off the snapshot
w = view(snap, {}, fast, NOW)["windows"][0]
c.check("view: jittered key still finds the samples", w["pace_pct_per_h"] == 40.0)
c.check("view: eta in 30 min, flagged soon", w["eta_full_in_s"] == 1800 and w["soon"] is True)
slow = {("five_hour", NOW + 7200): [(NOW - 1800, 70.0), (NOW, 80.0)]}  # 20 %/h -> 1 h to full, before reset
w = view(snap, {}, slow, NOW)["windows"][0]
c.check("view: eta 1 h -> not soon", w["eta_full_in_s"] == 3600 and w["soon"] is False)
w = view(snap, {}, {}, NOW)["windows"][0]
c.check("view: no samples -> no eta, not soon", w["eta_full_in_s"] is None and w["soon"] is False)
late = {"updated_at": NOW, "five_hour": {"used_percentage": 80.0, "resets_at": NOW + 600}}
w = view(late, {}, {("five_hour", NOW + 600): [(NOW - 1800, 60.0), (NOW, 80.0)]}, NOW)["windows"][0]
c.check("view: resets before it would run out -> not soon", w["eta_full_at"] is None and w["soon"] is False)

# ── limit_soon alert ──
vsoon = view(snap, {}, fast, NOW)
a = soon_alerts(vsoon, set(), NOW)
c.check("soon: one alert", len(a) == 1)
keys_, title, body = a[0]
c.check("soon: marks its own key and the window's warn", keys_ == [("five_hour", int(NOW + 7200), "soon"), ("five_hour", int(NOW + 7200), "warn")])
c.check("soon: title says how long", title == "⏳ 5-hour limit in ~30 min at this pace")
c.check("soon: body has pct, reset and the advice", body.startswith("Now 80 % · resets ") and "pause new agents or switch to a cheaper model" in body)
notified = set(keys_)
c.check("soon: once per window", soon_alerts(vsoon, notified, NOW) == [])
c.check("soon: the ordinary warn is then skipped", alerts(vsoon, notified, NOW) == [])
c.check("soon: not soon -> nothing", soon_alerts(view(snap, {}, slow, NOW), set(), NOW) == [])
c.check("soon: hit window -> nothing (the hit alert covers it)",
        soon_alerts({"windows": [{"kind": "five_hour", "resets_at": 1, "status": "hit", "soon": True, "eta_full_at": NOW, "pct": 100}]}, set(), NOW) == [])
c.check("soon: None view -> nothing", soon_alerts(None, set(), NOW) == [])

c.check("fmt_in: minutes", fmt_in(25 * 60) == "~25 min" and fmt_in(10) == "~1 min")
c.check("fmt_in: rounds up", fmt_in(24 * 60 + 1) == "~25 min")
c.check("fmt_in: hours", fmt_in(70 * 60) == "~1 h 10 min" and fmt_in(120 * 60) == "~2 h")

# ── samples persistence ──
load, dump = ns["_rate_limit_samples_load"], ns["_rate_limit_samples_dump"]
smp = {("five_hour", NOW + 100): [(NOW - 60, 10.0), (NOW, 11.0)], ("seven_day", NOW - 5): [(NOW - 60, 1.0)]}
back = load(json.loads(json.dumps(dump(smp))), NOW)
c.check("persist: round trip, expired window dropped", back == {("five_hour", NOW + 100): [(NOW - 60, 10.0), (NOW, 11.0)]})
c.check("persist: garbage -> empty", load("x", NOW) == {} and load([{"kind": "five_hour"}, 5, None], NOW) == {})

rf, sf = ns["RATE_LIMITS_FILE"], ns["RATE_LIMIT_SAMPLES_FILE"]
reset = int(time.time()) + 9000
t = time.time()
with open(rf, "w") as f:
    json.dump({"updated_at": t - 1200, "five_hour": {"used_percentage": 40.0, "resets_at": reset}}, f)
os.utime(rf, (t - 1200, t - 1200))
ns["_rate_limits_snapshot"]()
c.check("persist: first reading saved to disk", os.path.exists(sf) and len(json.load(open(sf))[0]["samples"]) == 1)
with open(rf, "w") as f:
    json.dump({"updated_at": t - 1, "five_hour": {"used_percentage": 50.0, "resets_at": reset + 20}}, f)  # resets_at wobbles
os.utime(rf, (t - 1, t - 1))
ns["_rate_limits_snapshot"]()
ns["_rate_limit_state"]["saved_at"] = 0  # force the throttled save on the next change
with open(rf, "w") as f:
    json.dump({"updated_at": t, "five_hour": {"used_percentage": 50.5, "resets_at": reset}}, f)
os.utime(rf, (t, t))
ns["_rate_limits_snapshot"]()
saved = json.load(open(sf))
c.check("persist: wobbling resets_at kept one window on disk", len(saved) == 1 and len(saved[0]["samples"]) == 3)

ns2 = fresh_ns()  # a restarted monitor
w = ns2["_rate_limits_snapshot"]()["windows"][0]
c.check("persist: restarted monitor has the pace right away", w["pace_pct_per_h"] is not None and w["pace_pct_per_h"] > 25)

# ── wiring ──
src = read_monitor_source()
c.check("limit_soon is a notification type (toast + push)",
        '"key": "limit_soon"' in src and '"Plan limit runs out within 30 min at this pace", "channels": ("toast", "push")' in src)
c.check("worker sends it with kind=limit_soon before the ordinary alerts",
        src.index('rl_due = _rate_limit_alerts(rl_view') < src.index('kind="limit_soon"') < src.index('for key, title, body, payload in rl_due'))
c.check("the covered warn still sends its webhook, only its toast is skipped",
        src.index('_notify_webhook("rate_limit", payload') < src.index('if key not in rl_covered:') < src.index('kind="rate_limit")'))
c.check("no background errors", errors == [])

c.finish()
