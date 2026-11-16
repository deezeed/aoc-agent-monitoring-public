"""Every AOC's Orchestrate workers, machine-wide (server/17_orchestration.py):
each AOC lists the claude.exe pids it started in <temp>/aoc-orch-pids/<port>.json
(_orch_pid_register), and the CLI count skips every listed pid -- a preview's
workers, or this AOC's own from before a reload, are no CLI sessions of yours
(_orch_registry_pids, _not_cli_pids in server/08)."""
import sys, os, json, tempfile, time

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
work = tempfile.mkdtemp(prefix="aoc-pidreg-")
errors = []


def replace(src, dst):
    os.replace(src, dst)


O = exec_functions(["_ORCH_PID_TTL_S", "_orch_pid_dir", "_orch_pid_entries", "_orch_pid_register",
                    "_orch_pid_alive_set", "_orch_pid_cache", "_orch_registry_pids", "_not_cli_pids"],
                   {"os": os, "json": json, "time": time, "PORT": 5151, "_replace_with_retry": replace,
                    "_log_bg_error": lambda where, e: errors.append((where, e)),
                    "_snapshot_processes": lambda: None, "_own_claude_pids": {7}})
T = 1_800_000_000.0
ent = O["_orch_pid_entries"]
c.check("entries: pids within the TTL", ent(json.dumps({"11": T - 60, "12": T - 25 * 3600}), T) == {11: T - 60})
c.check("entries: junk is ignored",
        ent("not json", T) == {} and ent("[1,2]", T) == {} and ent(json.dumps({"x": T, "0": T, "5": "y"}), T) == {}
        and ent("", T) == {})

reg = os.path.join(work, "reg")
O["_orch_pid_register"](101, now=T, folder=reg, alive=lambda: None)
O["_orch_pid_register"](102, now=T + 1, folder=reg, alive=lambda: None)
with open(os.path.join(reg, "5151.json"), encoding="utf-8") as f:
    c.check("register: this port's file lists both pids", json.load(f) == {"101": T, "102": T + 1})
O["_orch_pid_register"](103, now=T + 2, folder=reg, alive=lambda: {102, 103})
with open(os.path.join(reg, "5151.json"), encoding="utf-8") as f:
    c.check("register: a pid no longer running claude.exe is dropped", sorted(json.load(f)) == ["102", "103"])
c.check("register: no errors", errors == [])
open(os.path.join(work, "file-in-the-way"), "w").close()
O["_orch_pid_register"](104, now=T, folder=os.path.join(work, "file-in-the-way", "x"), alive=lambda: None)
c.check("register: a folder it can't write -> logged, never raised",
        len(errors) == 1 and errors[0][0] == "orch_pid_register")

with open(os.path.join(reg, "5197.json"), "w", encoding="utf-8") as f:
    json.dump({"201": T, "202": T - 30 * 3600}, f)
with open(os.path.join(reg, "notes.txt"), "w", encoding="utf-8") as f:
    f.write("{\"301\": %f}" % T)
pids = O["_orch_registry_pids"](now=T + 3, folder=reg)
c.check("registry: every port's workers (a preview's too), expired ones left out, only .json files",
        pids == {102, 103, 201})
c.check("registry: a missing folder -> none", O["_orch_registry_pids"](now=T, folder=os.path.join(work, "nope")) == frozenset())
c.check("registry: default folder is <temp>/aoc-orch-pids",
        O["_orch_pid_dir"]() == os.path.join(tempfile.gettempdir(), "aoc-orch-pids"))

g = O["_orch_registry_pids"].__globals__
g["_orch_registry_pids"] = lambda: frozenset({102, 201})
c.check("not-CLI pids: AOC's own claude -p plus every registered worker", O["_not_cli_pids"]() == {7, 102, 201})
g["_orch_registry_pids"] = lambda: (_ for _ in ()).throw(OSError("x"))
c.check("not-CLI pids: a failing registry -> just AOC's own", O["_not_cli_pids"]() == {7})
del g["_orch_registry_pids"]
c.check("not-CLI pids: before server/17 is loaded -> just AOC's own", O["_not_cli_pids"]() == {7})

c.finish()
