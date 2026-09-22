"""Tests _render_prometheus_metrics (and its _prom_escape helper),
extracted straight from monitor.py. This is the /metrics endpoint's
rendering logic -- a pure function over the same data /status already
returns, so it's testable with a synthetic payload instead of a live
server."""
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ns = exec_functions(["_prom_escape", "_render_prometheus_metrics"])
_prom_escape = ns["_prom_escape"]
_render_prometheus_metrics = ns["_render_prometheus_metrics"]

c = Checker()

# ── _prom_escape ──
c.check("backslash escaped", _prom_escape("a\\b") == "a\\\\b")
c.check("double-quote escaped", _prom_escape('say "hi"') == 'say \\"hi\\"')
c.check("newline escaped", _prom_escape("line1\nline2") == "line1\\nline2")
c.check("plain ascii unchanged", _prom_escape("AOC") == "AOC")

# ── _render_prometheus_metrics ──
data = {
    "sessions_count": 2,
    "agents": [
        {"id": "a1", "status": "running"},
        {"id": "a2", "status": "done"},
        {"id": "a3", "status": "done"},
        {"id": "a4", "status": "error"},
    ],
    "sessions_list": [
        {"id": "sess-1", "project": "AOC", "estimated_cost": 0.0421},
        {"id": "sess-2", "project": "", "estimated_cost": 0.0},
    ],
}
analytics = {"hook_misses": 7}

out = _render_prometheus_metrics(data, analytics)

c.check("sessions_active gauge present with correct value", "aoc_sessions_active 2" in out)
c.check("agents_total has HELP/TYPE lines", "# HELP aoc_agents_total" in out and "# TYPE aoc_agents_total gauge" in out)
c.check("agents_total running=1", 'aoc_agents_total{status="running"} 1' in out)
c.check("agents_total done=2 (summed across two agents)", 'aoc_agents_total{status="done"} 2' in out)
c.check("agents_total error=1", 'aoc_agents_total{status="error"} 1' in out)
c.check("per-session cost line for sess-1 with its project label", 'aoc_session_cost_dollars{session_id="sess-1",project="AOC"} 0.0421' in out)
c.check("empty project falls back to the literal label 'none'", 'aoc_session_cost_dollars{session_id="sess-2",project="none"} 0.0' in out)
c.check("hook_misses_total counter present with correct value", "aoc_hook_misses_total 7" in out)
c.check("output ends with a trailing newline (valid exposition format)", out.endswith("\n"))

# a project name containing a double-quote must not break the label syntax
data2 = {"sessions_count": 0, "agents": [], "sessions_list": [
    {"id": "s1", "project": 'weird "quoted" name', "estimated_cost": 1.0},
]}
out2 = _render_prometheus_metrics(data2, {"hook_misses": 0})
c.check("project names with quotes are escaped in the label", 'project="weird \\"quoted\\" name"' in out2)

c.finish()
