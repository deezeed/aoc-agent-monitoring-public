"""Tests _apply_agent_update, extracted straight from monitor.py. This is
the shared upsert path for both the hook-driven (/update handler) and
transcript-scanner-driven agent-creation routes -- its own docstring
calls it out as "one place the agent schema can drift, not two." Despite
that, it had zero test coverage before this file. `_log` is stubbed with
a fake recorder (no-op methods that just append to a list) rather than
the real LogWriter, matching how other tests stub side-effecting globals."""
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker


class _FakeLog:
    def __init__(self):
        self.calls = []
    def agent_start(self, agent):
        self.calls.append(("agent_start", agent.get("id")))
    def agent_done(self, agent):
        self.calls.append(("agent_done", agent.get("id")))
    def task_complete(self, agent, task):
        self.calls.append(("task_complete", agent.get("id"), task.get("done")))


def make_ns(log_cls=_FakeLog):
    fake_log = log_cls()
    bg_errors = []
    # _pending_parent_links is a module-level dict _apply_agent_update mutates
    # in place (.pop()/[]=) but never rebinds -- seeding a fresh one via
    # extra_globals (same pattern as _log below) is enough, no need to
    # extract its own source (it's an annotated assignment, which
    # extract_functions' ast.Assign check doesn't match anyway).
    # _log_bg_error is stubbed the same way -- _apply_agent_update's
    # agent_start failure path calls it by name, so it must exist in this
    # namespace even though no test here makes it fire until case 12.
    ns = exec_functions(["_apply_agent_update", "_calc_cost", "_model_pricing", "_MODEL_PRICING", "_CLAUDE_CONTEXT_WINDOW"],
                        {"_log": fake_log, "_pending_parent_links": {},
                         "_log_bg_error": lambda where, exc: bg_errors.append((where, str(exc)))})
    return ns, fake_log, bg_errors


c = Checker()

# 1. missing id -> no-op, nothing added, no crash
ns, log, _bg = make_ns()
status = {"agents": []}
ns["_apply_agent_update"](status, {}, "sess1", "AOC", "10:00:00")
c.check("missing id is a no-op, agents list stays empty", status["agents"] == [])

# 2. create a brand-new agent (normal case, has started_at)
ns, log, _bg = make_ns()
status = {"agents": [], "sessions": {}}
ns["_apply_agent_update"](status, {"id": "a1", "name": "Explore", "status": "running", "started_at": "10:00:00"},
                          "sess1", "AOC", "10:00:00")
c.check("new agent created", len(status["agents"]) == 1)
a1 = status["agents"][0]
c.check("new agent id/name carried through", a1["id"] == "a1" and a1["name"] == "Explore")
c.check("new agent session_id/session_project defaulted from args", a1["session_id"] == "sess1" and a1["session_project"] == "AOC")
c.check("new agent started_at uses the provided value, not 'now'", a1["started_at"] == "10:00:00")
c.check("new agent has no 'registration missed' log prefix (normal creation)", a1["log"] == [])
c.check("new agent gets token_limit so the card's context-window bar can render", a1["token_limit"] == ns["_CLAUDE_CONTEXT_WINDOW"] == 200_000)
c.check("new agent creation logs agent_start", ("agent_start", "a1") in log.calls)
c.check("session_active flipped True as a side effect of the first agent", status["session_active"] is True)

# 3. concurrent_sessions counts only active, non-dismissed sessions
ns, log, _bg = make_ns()
status = {"agents": [], "sessions": {
    "s1": {"session_active": True, "dismissed": False},
    "s2": {"session_active": True, "dismissed": True},   # dismissed -- excluded
    "s3": {"session_active": False, "dismissed": False},  # inactive -- excluded
}}
ns["_apply_agent_update"](status, {"id": "a2", "status": "running", "started_at": "10:00:00"}, "sess1", "AOC", "10:00:00")
c.check("concurrent_sessions counts only active+non-dismissed sessions", status["agents"][0]["concurrent_sessions"] == 1)

# 4. late arrival: done/error with no started_at gets flagged in the log
ns, log, _bg = make_ns()
status = {"agents": [], "sessions": {}}
ns["_apply_agent_update"](status, {"id": "a3", "status": "done", "completed_at": "11:00:00"}, "sess1", "AOC", "11:00:05")
a3 = status["agents"][0]
c.check("late-arrival agent's started_at falls back to completed_at", a3["started_at"] == "11:00:00")
c.check("late-arrival agent's log is prefixed with the registration-missed marker",
        a3["log"] and "registration missed" in a3["log"][0])

# 5. updating an existing agent: status transition running->done triggers agent_done
ns, log, _bg = make_ns()
status = {"agents": [{"id": "a4", "status": "running", "name": "Fix bug", "tasks": [{"done": False}]}], "sessions": {}}
ns["_apply_agent_update"](status, {"id": "a4", "status": "done"}, "sess1", "AOC", "12:00:00")
c.check("status transition running->done recorded", status["agents"][0]["status"] == "done")
c.check("agent_done fired on the transition", ("agent_done", "a4") in log.calls)
c.check("completed_at set on first done transition", status["agents"][0]["completed_at"] == "12:00:00")

# 6. same status twice in a row -> no duplicate agent_start/agent_done call
ns, log, _bg = make_ns()
status = {"agents": [{"id": "a5", "status": "running", "tasks": []}], "sessions": {}}
ns["_apply_agent_update"](status, {"id": "a5", "status": "running"}, "sess1", "AOC", "12:00:00")
c.check("no-op status (same as before) does not re-fire agent_start", log.calls == [])

# 7. task completion: done flips False->True fires task_complete and stamps completed_at
ns, log, _bg = make_ns()
status = {"agents": [{"id": "a6", "status": "running", "tasks": [{"done": False}, {"done": True, "completed_at": "09:00:00"}]}], "sessions": {}}
ns["_apply_agent_update"](status, {"id": "a6", "tasks": [{"done": True}, {"done": True}]}, "sess1", "AOC", "13:00:00")
tasks = status["agents"][0]["tasks"]
c.check("newly-completed task gets completed_at stamped with 'now'", tasks[0]["completed_at"] == "13:00:00")
c.check("already-completed task keeps its original completed_at, not overwritten", tasks[1]["completed_at"] == "09:00:00")
c.check("task_complete fired exactly once (only for the newly-done task)",
        log.calls.count(("task_complete", "a6", True)) == 1)

# 8. log accumulation is capped at the last 300 entries
ns, log, _bg = make_ns()
status = {"agents": [{"id": "a7", "status": "running", "log": [f"old-{i}" for i in range(250)]}], "sessions": {}}
ns["_apply_agent_update"](status, {"id": "a7", "log": [f"new-{i}" for i in range(100)]}, "sess1", "AOC", "14:00:00")
merged_log = status["agents"][0]["log"]
c.check("log is capped at 300 entries total", len(merged_log) == 300)
c.check("the most recent entries survive the cap (last one is new-99)", merged_log[-1] == "new-99")
c.check("oldest entries are the ones dropped, not the newest", "old-0" not in merged_log)

# 9. per-model cost calculation path (real _calc_cost, not stubbed)
ns, log, _bg = make_ns()
status = {"agents": [], "sessions": {}}
ns["_apply_agent_update"](status, {
    "id": "a8", "status": "running", "started_at": "10:00:00",
    "model": "claude-sonnet-4-20250514", "input_tokens": 1_000_000, "output_tokens": 0,
}, "sess1", "AOC", "10:00:00")
c.check("estimated_cost computed via _calc_cost when model+input_tokens present",
        status["agents"][0]["estimated_cost"] is not None and status["agents"][0]["estimated_cost"] > 0)

# 10. child_ids (TREE view's parent_id source, from aoc_hook.py's
# _extract_child_agent_ids) -- child already exists -> parent_id set
# directly on it. Parent is itself an EXISTING agent here (going through
# the a.update(au) merge path), so this also verifies child_ids gets
# popped before that merge rather than leaking onto the parent's own record.
ns, log, _bg = make_ns()
status = {"agents": [
    {"id": "child1", "status": "running", "tasks": []},
    {"id": "parent1", "status": "running", "tasks": []},
], "sessions": {}}
ns["_apply_agent_update"](status, {"id": "parent1", "status": "running",
                                    "child_ids": ["child1"]}, "sess1", "AOC", "10:00:00")
c.check("existing child gets parent_id set directly", status["agents"][0]["parent_id"] == "parent1")
c.check("child_ids itself is popped, not merged onto the parent's own record",
        "child_ids" not in status["agents"][1])

# 11. child_ids referencing a child that doesn't exist YET -> pending link,
# applied automatically once that child agent is actually created (real
# ordering: the child's own PreToolUse/PostToolUse almost always land
# first, but this is the safety net for when they don't)
ns, log, _bg = make_ns()
status = {"agents": [], "sessions": {}}
ns["_apply_agent_update"](status, {"id": "parent2", "status": "running", "started_at": "10:00:00",
                                    "child_ids": ["child2"]}, "sess1", "AOC", "10:00:00")
c.check("parent alone doesn't create a phantom child entry", len(status["agents"]) == 1)
ns["_apply_agent_update"](status, {"id": "child2", "status": "running", "started_at": "10:00:05"}, "sess1", "AOC", "10:00:05")
child2 = next(a for a in status["agents"] if a["id"] == "child2")
c.check("child created after its parent's child_ids message still gets parent_id via the pending-links map",
        child2["parent_id"] == "parent2")

# 12. _log.agent_start raising doesn't propagate or abort agent creation --
# the outer except now reports it via _log_bg_error instead of a bare pass
class _RaisingLog(_FakeLog):
    def agent_start(self, agent):
        raise RuntimeError("disk full")

ns, log, bg_errors = make_ns(_RaisingLog)
status = {"agents": [], "sessions": {}}
ns["_apply_agent_update"](status, {"id": "a9", "status": "running", "started_at": "10:00:00"},
                          "sess1", "AOC", "10:00:00")
c.check("agent still created despite agent_start raising", len(status["agents"]) == 1 and status["agents"][0]["id"] == "a9")
c.check("agent_start failure reported via _log_bg_error, not silently swallowed",
        len(bg_errors) == 1 and bg_errors[0][0] == "_apply_agent_update:agent_start" and "disk full" in bg_errors[0][1])

c.finish()
