"""Tests _hms_elapsed_hours, _prune_old_agents, and
_clamp_agent_retention_hours, extracted straight from monitor.py. Feature:
auto-archive done/error agents past a configurable retention window, so
TIMELINE/SUMMARY/GRAPH/HEAT/TREE stay populated with recent activity
instead of relying solely on a manual /clear_done sweep (which -- once
finally clicked after 27 agents accumulated this session -- made all five
of those views go instantly blank)."""
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ns = exec_functions(["_clamp_agent_retention_hours"])
_clamp_agent_retention_hours = ns["_clamp_agent_retention_hours"]

ns2 = exec_functions(["_hms_elapsed_hours"])
_hms_elapsed_hours = ns2["_hms_elapsed_hours"]

ns3 = exec_functions(["_prune_old_agents", "_hms_elapsed_hours"])
_prune_old_agents = ns3["_prune_old_agents"]

c = Checker()

# ── _clamp_agent_retention_hours ──
c.check("valid value passes through unchanged", _clamp_agent_retention_hours(6) == 6)
c.check("default 12 passes through unchanged", _clamp_agent_retention_hours(12) == 12)
c.check("zero clamps up to 1", _clamp_agent_retention_hours(0) == 1)
c.check("negative clamps up to 1", _clamp_agent_retention_hours(-5) == 1)
c.check("24 clamps down to 23 (ambiguous with HH:MM:SS-only timestamps)", _clamp_agent_retention_hours(24) == 23)
c.check("huge value clamps down to 23", _clamp_agent_retention_hours(999) == 23)
c.check("exactly 23 passes through unchanged", _clamp_agent_retention_hours(23) == 23)
c.check("exactly 1 passes through unchanged", _clamp_agent_retention_hours(1) == 1)
c.check("None falls back to 12", _clamp_agent_retention_hours(None) == 12)
c.check("non-numeric string falls back to 12", _clamp_agent_retention_hours("abc") == 12)
c.check("numeric string coerces via int()", _clamp_agent_retention_hours("6") == 6)

# ── _hms_elapsed_hours ──
c.check("same time -> 0 hours elapsed", _hms_elapsed_hours("10:00:00", "10:00:00") == 0.0)
c.check("2 hours later, same day", _hms_elapsed_hours("10:00:00", "12:00:00") == 2.0)
c.check("30 minutes -> 0.5 hours", _hms_elapsed_hours("10:00:00", "10:30:00") == 0.5)
c.check("midnight wraparound: 23:00 -> 01:00 is 2h, not -22h",
        _hms_elapsed_hours("23:00:00", "01:00:00") == 2.0)
c.check("almost a full day: 00:00:01 -> 00:00:00 is ~24h (mod 24)",
        abs(_hms_elapsed_hours("00:00:01", "00:00:00") - (86399 / 3600)) < 1e-9)
c.check("malformed timestamp returns None", _hms_elapsed_hours("not-a-time", "10:00:00") is None)
c.check("empty string returns None", _hms_elapsed_hours("", "10:00:00") is None)
c.check("None input returns None", _hms_elapsed_hours(None, "10:00:00") is None)

# ── _prune_old_agents ──
now = "12:00:00"

def mk(id, status, completed_at=None):
    return {"id": id, "status": status, "completed_at": completed_at}

# 1. running/waiting agents are never touched regardless of age
status1 = {"agents": [mk("a1", "running"), mk("a2", "waiting")]}
removed1 = _prune_old_agents(status1, 1, now)
c.check("running agent never pruned", any(a["id"] == "a1" for a in status1["agents"]))
c.check("waiting agent never pruned", any(a["id"] == "a2" for a in status1["agents"]))
c.check("nothing removed when only running/waiting present", removed1 == 0)

# 2. done agent well within the retention window survives
status2 = {"agents": [mk("a3", "done", "11:55:00")]}  # 5 min ago
removed2 = _prune_old_agents(status2, 12, now)
c.check("recent done agent survives (5min old, 12h window)", len(status2["agents"]) == 1)
c.check("nothing removed for recent done agent", removed2 == 0)

# 3. done agent past the retention window gets pruned
status3 = {"agents": [mk("a4", "done", "23:00:00")]}  # 13h ago (23:00 -> 12:00 next day = 13h)
removed3 = _prune_old_agents(status3, 12, now)
c.check("old done agent (13h) is removed with a 12h window", len(status3["agents"]) == 0)
c.check("removed count is 1", removed3 == 1)

# 4. error status treated the same as done
status4 = {"agents": [mk("a5", "error", "23:00:00")]}
removed4 = _prune_old_agents(status4, 12, now)
c.check("old error agent is also removed", len(status4["agents"]) == 0)
c.check("removed count is 1 for error agent", removed4 == 1)

# 5. done agent missing completed_at is left alone (nothing to measure against)
status5 = {"agents": [mk("a6", "done", None)]}
removed5 = _prune_old_agents(status5, 1, now)
c.check("done agent with no completed_at is kept, not guessed at", len(status5["agents"]) == 1)
c.check("nothing removed when completed_at is missing", removed5 == 0)

# 6. mixed batch: only the old done one is removed, everything else survives
status6 = {"agents": [
    mk("running1", "running"),
    mk("fresh_done", "done", "11:50:00"),   # 10 min ago
    mk("old_done", "done", "22:00:00"),     # 14h ago
]}
removed6 = _prune_old_agents(status6, 12, now)
remaining_ids = {a["id"] for a in status6["agents"]}
c.check("mixed batch: exactly 1 removed", removed6 == 1)
c.check("mixed batch: running1 survives", "running1" in remaining_ids)
c.check("mixed batch: fresh_done survives", "fresh_done" in remaining_ids)
c.check("mixed batch: old_done is gone", "old_done" not in remaining_ids)

c.finish()
