"""Tests _is_cost_spike and _project_avg_costs, extracted straight from
monitor.py. Session-level counterpart to the existing token-rate
burn-spike detector: flags a session whose *total* cost so far is an
outlier vs. its project's historical per-session average, catching an
expensive session that never burned tokens fast enough to trip the
rate-based check."""
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ns = exec_functions(["_is_cost_spike"])
_is_cost_spike = ns["_is_cost_spike"]

ns2 = exec_functions(["_project_avg_costs"])
_project_avg_costs = ns2["_project_avg_costs"]

c = Checker()

# ── _is_cost_spike ──
c.check("well above multiplier and floor -> spike",
        _is_cost_spike(session_cost=10.0, project_avg_cost=2.0) is True)
c.check("exactly at the multiplier boundary -> not a spike (strictly greater required)",
        _is_cost_spike(session_cost=6.0, project_avg_cost=2.0) is False)
c.check("just above the multiplier boundary -> spike",
        _is_cost_spike(session_cost=6.01, project_avg_cost=2.0) is True)
c.check("high ratio but below the absolute floor -> not a spike (low-volume noise guard)",
        _is_cost_spike(session_cost=0.5, project_avg_cost=0.01) is False)
c.check("no historical average (0) -> never a spike, nothing to compare against",
        _is_cost_spike(session_cost=100.0, project_avg_cost=0) is False)
c.check("session cost below the average -> not a spike",
        _is_cost_spike(session_cost=1.0, project_avg_cost=2.0) is False)
c.check("custom floor/multiplier are honored",
        _is_cost_spike(session_cost=3.0, project_avg_cost=1.0, min_floor=5.0, multiplier=2.0) is False)
c.check("custom floor/multiplier: passes once both are satisfied",
        _is_cost_spike(session_cost=6.0, project_avg_cost=1.0, min_floor=5.0, multiplier=2.0) is True)

# ── _project_avg_costs ──
rows = [
    {"project": "AOC", "cost": 100.0, "sessions": 20},
    {"project": "PHANTOM AI", "cost": 30.0, "sessions": 10},
    {"project": "NoSessions", "cost": 5.0, "sessions": 0},
    {"project": "", "cost": 9.0, "sessions": 3},
    {"cost": 4.0, "sessions": 2},  # missing "project" key entirely
]
avgs = _project_avg_costs(rows)
c.check("AOC average computed correctly (100/20=5)", avgs.get("AOC") == 5.0)
c.check("PHANTOM AI average computed correctly (30/10=3)", avgs.get("PHANTOM AI") == 3.0)
c.check("zero-session row excluded (would divide by zero)", "NoSessions" not in avgs)
c.check("empty-string project excluded", "" not in avgs)
c.check("row missing 'project' key excluded, doesn't crash", len(avgs) == 2)
c.check("empty input list -> empty dict", _project_avg_costs([]) == {})
c.check("None input -> empty dict, doesn't crash", _project_avg_costs(None) == {})

c.finish()
