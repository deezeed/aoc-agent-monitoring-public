"""Orchestrate kills only its own processes (server/17_orchestration.py):
_orch_pid_started reads a real process's start time, _orch_ours tells a
recycled pid (a process that started after ours) from ours, _orch_stop
never calls kill for a dead or foreign pid, and _orch_llm_budget caps the
planner / supervisor at what the run has left."""
import os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
O = exec_functions(["_ORCH_PID_SLACK_S", "_ORCH_PLANNER_BUDGET", "_orch_pid_started", "_orch_ours", "_orch_stop",
                    "_orch_llm_budget"],
                   {"os": os, "_orch_alive": lambda pid: False, "_orch_kill": lambda pid: None})

# a real process: its start time, read from Windows
before = time.time()
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
try:
    started = O["_orch_pid_started"](child.pid)
    c.check("start time of a real process", started is not None and before - 5 <= started <= time.time() + 1)
    c.check("this process started before the child", O["_orch_pid_started"](os.getpid()) <= started)
    c.check("ours: noted when we started it", O["_orch_ours"](child.pid, before))
    c.check("ours: noted a bit late (within the slack)", O["_orch_ours"](child.pid, before - O["_ORCH_PID_SLACK_S"] + 5))
    c.check("NOT ours: the pid was noted long before this process started (recycled pid)",
            not O["_orch_ours"](child.pid, before - 3600))
    c.check("no time noted (old rows) -> trusted", O["_orch_ours"](child.pid, None))

    killed = []
    stop = lambda pid, since: O["_orch_stop"](pid, since, alive=lambda p: True, kill=killed.append)
    c.check("stop: ours and alive -> killed", stop(child.pid, before) and killed == [child.pid])
    killed.clear()
    c.check("stop: a recycled pid -> left alone", not stop(child.pid, before - 3600) and killed == [])
    c.check("stop: not running -> left alone",
            not O["_orch_stop"](child.pid, before, alive=lambda p: False, kill=killed.append) and killed == [])
    c.check("stop: no pid -> nothing", not stop(None, before) and not stop(0, before) and killed == [])
finally:
    child.kill()
    child.wait()

c.check("start time of pid 0 / a pid that can't exist -> None",
        O["_orch_pid_started"](0) is None and O["_orch_pid_started"](4_000_000_001) is None)

# the planner's / supervisor's cap
b = O["_orch_llm_budget"]
c.check("budget: plenty left -> the usual cap", b({"budget_usd": 10}, 2) == O["_ORCH_PLANNER_BUDGET"])
c.check("budget: little left -> what's left", b({"budget_usd": 10}, 9.6) == 0.4)
c.check("budget: nothing left -> $0.05 to answer", b({"budget_usd": 10}, 12) == 0.05 and b({}, None) == 0.05)
c.finish()
