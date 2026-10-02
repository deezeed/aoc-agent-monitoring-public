"""Tests _snapshot_processes and _pid_exe_name, extracted straight from
monitor.py, against this machine's real process table (Windows-only, like
the monitor itself; the suite runs on Windows in CI).

_claude_proc_worker used to spawn `tasklist` every 2 seconds to count
claude.exe -- tens of thousands of processes a day, and it regularly blew
its 5s timeout under load, freezing the dashboard's CLI count. It now uses
this in-process snapshot, so the snapshot must actually see processes, name
them like tasklist does, and be fast."""
import sys, os, time, subprocess

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ns = exec_functions(["_snapshot_processes", "_pid_exe_name"], {"os": os})
snap = ns["_snapshot_processes"]
exe_of = ns["_pid_exe_name"]

c = Checker()

t0 = time.perf_counter()
procs = snap()
elapsed = time.perf_counter() - t0
c.check("snapshot returns a list of (pid, exe)", isinstance(procs, list) and procs and all(len(p) == 2 for p in procs))
me = os.path.basename(sys.executable).lower()
c.check("this test's own process is in it, named like tasklist names it",
        (os.getpid(), me) in procs)
c.check("exe names are lowercase", all(exe == exe.lower() for _, exe in procs))
c.check(f"one snapshot is fast (took {elapsed * 1000:.0f} ms, limit 1000)", elapsed < 1.0)

c.check("_pid_exe_name finds this process", exe_of(os.getpid()) == me)
c.check("_pid_exe_name of a falsy pid -> ''", exe_of(0) == "" and exe_of(None) == "")
c.check("_pid_exe_name of a pid that isn't running -> ''",
        exe_of(max(p for p, _ in procs) + 4) == "")

# Same count tasklist reports for one image name (the worker's old source).
img = me
r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {img}", "/FO", "CSV", "/NH"],
                   capture_output=True, text=True, timeout=30)
procs = snap()
snap_count = sum(1 for _, exe in procs if exe == img)
tl_count = r.stdout.lower().count(img)
c.check(f"count matches tasklist for {img} ({snap_count} vs {tl_count}, +-1 for churn)",
        abs(snap_count - tl_count) <= 1)

c.finish()
