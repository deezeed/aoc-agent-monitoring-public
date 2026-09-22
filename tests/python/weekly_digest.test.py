"""Tests _should_send_digest, _digest_marker_for, and
_build_digest_summary, extracted straight from monitor.py. The digest
uses a calendar-based schedule (configurable cadence: daily, weekly on
Monday, or off -- all within a 09:00-10:00 window) rather than a rolling
"N days since boot" timer, specifically so server restarts
(watchdog-triggered) don't cause a double-send or a skipped period --
these tests cover that scheduling logic in isolation, plus the summary
aggregation over _db_analytics' by_day."""
import sys, os
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ns = exec_functions(["_should_send_digest", "_digest_marker_for"])
_should_send_digest = ns["_should_send_digest"]
_digest_marker_for = ns["_digest_marker_for"]

c = Checker()

# ── _should_send_digest ──

monday_930 = datetime(2026, 7, 20, 9, 30)  # 2026-07-20 is a Monday
c.check("Monday within the 9-10am window, not yet sent this week -> True",
        _should_send_digest(monday_930, "2026-29") is True)
c.check("Monday within the window, ALREADY sent this week -> False",
        _should_send_digest(monday_930, monday_930.strftime("%G-%V")) is False)

tuesday_930 = datetime(2026, 7, 21, 9, 30)
c.check("Tuesday, same time-of-day -> False (not Monday)", _should_send_digest(tuesday_930, "") is False)

monday_859 = datetime(2026, 7, 20, 8, 59)
c.check("Monday just before the window opens -> False", _should_send_digest(monday_859, "") is False)

monday_1000 = datetime(2026, 7, 20, 10, 0)
c.check("Monday exactly at the window's end -> False (end exclusive)", _should_send_digest(monday_1000, "") is False)

monday_900 = datetime(2026, 7, 20, 9, 0)
c.check("Monday exactly at the window's start -> True (start inclusive)", _should_send_digest(monday_900, "") is True)

# a different Monday (next week) with the PREVIOUS week's marker -> True again
next_monday = datetime(2026, 7, 27, 9, 15)
c.check("a later Monday with last week's marker stored -> True (new week)",
        _should_send_digest(next_monday, monday_930.strftime("%G-%V")) is True)

# default cadence (2-arg call, matching every call site above) is "weekly" --
# these existing assertions passing at all IS the proof the default wasn't
# broken by adding the third parameter.
c.check("2-arg call (no cadence) still behaves as weekly by default",
        _should_send_digest(monday_930, "2026-29") is True)

# ── cadence: "off" ──
c.check("off: never sends, even Monday 9:30 with no marker at all", _should_send_digest(monday_930, "", "off") is False)
c.check("off: never sends regardless of any other day/time", _should_send_digest(tuesday_930, "", "off") is False)

# ── cadence: "daily" ──
c.check("daily: Tuesday within the window, not sent today -> True", _should_send_digest(tuesday_930, "2026-07-20", "daily") is True)
c.check("daily: Tuesday within the window, ALREADY sent today -> False",
        _should_send_digest(tuesday_930, tuesday_930.strftime("%Y-%m-%d"), "daily") is False)
c.check("daily: works on Monday too (not restricted to non-Mondays)", _should_send_digest(monday_930, "2026-07-19", "daily") is True)
c.check("daily: still respects the 9-10am window", _should_send_digest(monday_859, "", "daily") is False)
c.check("daily: a new calendar day resets even if last sent yesterday", _should_send_digest(datetime(2026, 7, 21, 9, 30), "2026-07-20", "daily") is True)

# ── unrecognized cadence falls back to weekly, not a crash ──
c.check("unrecognized cadence string falls back to weekly behavior (Monday-gated)",
        _should_send_digest(tuesday_930, "", "bogus-value") is False)
c.check("unrecognized cadence on a Monday still sends like weekly would",
        _should_send_digest(monday_930, "2026-29", "bogus-value") is True)

# ── _digest_marker_for ──
c.check("daily marker is a calendar date", _digest_marker_for(monday_930, "daily") == "2026-07-20")
c.check("weekly marker is an ISO week string", _digest_marker_for(monday_930, "weekly") == monday_930.strftime("%G-%V"))
c.check("unrecognized cadence's marker falls back to the ISO week form (matching the weekly fallback above)",
        _digest_marker_for(monday_930, "bogus-value") == monday_930.strftime("%G-%V"))

# ── _build_digest_summary ──
# 14 rows: the first 7 are "this week" (index 0-6), the next 7 are
# "last week" (index 7-13) -- lets prev_week_cost/cost_pct_change be
# tested meaningfully, not just the last-7-days sum.
ns2 = exec_functions(["_build_digest_summary"], extra_globals={
    "_notify_settings": {"muted_projects": []},
    "_db_analytics": lambda: {
        "by_day": [
            {"date": "2026-07-19", "sessions": 3, "tokens": 10000, "cost": 1.5, "errors": 1},
            {"date": "2026-07-18", "sessions": 2, "tokens": 5000, "cost": 0.75, "errors": 0},
            {"date": "2026-07-17", "sessions": 1, "tokens": 2000, "cost": 0.25, "errors": 0},
            {"date": "2026-07-16", "sessions": 4, "tokens": 8000, "cost": 1.0, "errors": 2},
            {"date": "2026-07-15", "sessions": 1, "tokens": 1000, "cost": 0.1, "errors": 0},
            {"date": "2026-07-14", "sessions": 2, "tokens": 3000, "cost": 0.4, "errors": 0},
            {"date": "2026-07-13", "sessions": 1, "tokens": 1000, "cost": 0.05, "errors": 0},
            # previous week (index 7-13): sums to 2.0 total cost
            {"date": "2026-07-12", "sessions": 2, "tokens": 4000, "cost": 0.5, "errors": 0},
            {"date": "2026-07-11", "sessions": 1, "tokens": 2000, "cost": 0.3, "errors": 0},
            {"date": "2026-07-10", "sessions": 1, "tokens": 1000, "cost": 0.2, "errors": 0},
            {"date": "2026-07-09", "sessions": 1, "tokens": 1000, "cost": 0.2, "errors": 0},
            {"date": "2026-07-08", "sessions": 1, "tokens": 1000, "cost": 0.3, "errors": 0},
            {"date": "2026-07-07", "sessions": 1, "tokens": 1000, "cost": 0.3, "errors": 0},
            {"date": "2026-07-06", "sessions": 1, "tokens": 1000, "cost": 0.2, "errors": 0},
            # 15th entry -- must NOT be included in either window
            {"date": "2026-07-05", "sessions": 99, "tokens": 999999, "cost": 999.0, "errors": 99},
        ],
    },
})
summary = ns2["_build_digest_summary"]()
c.check("sums sessions across exactly the last 7 days", summary["sessions"] == 3+2+1+4+1+2+1)
c.check("sums tokens across exactly the last 7 days", summary["tokens"] == 10000+5000+2000+8000+1000+3000+1000)
c.check("sums cost across exactly the last 7 days, rounded to 2dp", summary["cost"] == round(1.5+0.75+0.25+1.0+0.1+0.4+0.05, 2))
c.check("sums errors across exactly the last 7 days", summary["errors"] == 1+0+0+2+0+0+0)
c.check("the 15th (older) day is excluded from every total", summary["sessions"] != 99 and summary["tokens"] < 900000)
c.check("prev_week_cost sums exactly the preceding 7 days", summary["prev_week_cost"] == round(0.5+0.3+0.2+0.2+0.3+0.3+0.2, 2))
_this_cost = summary["cost"]
_prev_cost = summary["prev_week_cost"]
c.check("cost_pct_change matches (this-prev)/prev*100, rounded to 1dp",
        summary["cost_pct_change"] == round((_this_cost - _prev_cost) / _prev_cost * 100, 1))

# a week with no prior week at all -> cost_pct_change is None, not a crash
ns_noprev = exec_functions(["_build_digest_summary"], extra_globals={
    "_notify_settings": {"muted_projects": []},
    "_db_analytics": lambda: {"by_day": [{"date": "2026-07-19", "sessions": 1, "tokens": 100, "cost": 1.0, "errors": 0}]},
})
noprev_summary = ns_noprev["_build_digest_summary"]()
c.check("no prior week data -> prev_week_cost is 0", noprev_summary["prev_week_cost"] == 0.0)
c.check("no prior week data -> cost_pct_change is None (nothing to compare against)", noprev_summary["cost_pct_change"] is None)

# empty by_day (no data yet) doesn't crash, returns zeros for every field
ns3 = exec_functions(["_build_digest_summary"], extra_globals={
    "_notify_settings": {"muted_projects": []},
    "_db_analytics": lambda: {"by_day": []},
})
empty_summary = ns3["_build_digest_summary"]()
c.check("empty analytics -> all-zero summary, no crash", empty_summary == {
    "sessions": 0, "tokens": 0, "cost": 0.0, "errors": 0, "prev_week_cost": 0.0, "cost_pct_change": None,
})

# muted_projects: the digest must exclude them, using
# _db_by_day_excluding_projects instead of the unfiltered _db_analytics()
# path -- a muted project already has its individual toast/webhook events
# suppressed, so its numbers silently inflating the digest (itself a
# notification) would defeat the point of muting it.
_muted_call_args = []
ns_muted = exec_functions(["_build_digest_summary"], extra_globals={
    "_notify_settings": {"muted_projects": ["NoisyProject", ""]},  # blank entry must be filtered, not passed through
    "_db_analytics": lambda: (_ for _ in ()).throw(AssertionError("unfiltered _db_analytics() must not be called when a project is muted")),
    "_db_by_day_excluding_projects": lambda excluded, **kw: (_muted_call_args.append(excluded) or [
        {"date": "2026-07-19", "sessions": 2, "tokens": 5000, "cost": 1.0, "errors": 0},
    ]),
})
muted_summary = ns_muted["_build_digest_summary"]()
c.check("muted_projects routes through _db_by_day_excluding_projects, not the unfiltered path",
        muted_summary["sessions"] == 2 and muted_summary["cost"] == 1.0)
c.check("blank entries in muted_projects are dropped before being passed down",
        _muted_call_args == [["NoisyProject"]])

c.finish()
