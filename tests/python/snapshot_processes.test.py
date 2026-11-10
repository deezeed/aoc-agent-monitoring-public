"""Tests _snapshot_processes and _pid_exe_name, extracted straight from
monitor.py, against this machine's real process table (Windows-only, like
the monitor itself; the suite runs on Windows in CI).

_claude_proc_worker used to spawn `tasklist` every 2 seconds to count
claude.exe -- tens of thousands of processes a day, and it regularly blew
its 5s timeout under load, freezing the dashboard's CLI count. It now uses
this in-process snapshot, so the snapshot must actually see processes, name
them like tasklist does (the exe file name, lowercased), and be fast.

The reference here used to be tasklist itself -- which is exactly why it
flaked: under run_all's parallel load tasklist took over 30 s (and > 120 s
with a few suites running at once), and the python.exe counts it and the
snapshot saw a moment apart differed by more than +-1 as test processes came
and went. Now the reference is a second, independent in-process source
(psapi EnumProcesses + QueryFullProcessImageNameW, not Toolhelp32), compared
by PID set so churn can't break it."""
import sys, os, time, ctypes
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ns = exec_functions(["_snapshot_processes", "_pid_exe_name"], {"os": os})
snap = ns["_snapshot_processes"]
exe_of = ns["_pid_exe_name"]

c = Checker()


def psapi_exe_names():
    """-> (every pid EnumProcesses lists, {pid: lowercase exe file name} for
    the ones this user may query -- an elevated/other user's process is
    listed but can't be named)."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    arr = (wintypes.DWORD * 8192)()
    got = wintypes.DWORD()
    if not psapi.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(got)):
        return set(), {}
    listed = set(arr[:got.value // ctypes.sizeof(wintypes.DWORD)])
    out = {}
    for pid in listed:
        if not pid:
            continue
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                out[pid] = os.path.basename(buf.value).lower()
        finally:
            k32.CloseHandle(h)
    return listed, out


# Best of 3: the limit is about the snapshot's own cost (the tasklist it
# replaced took seconds), not about how long run_all's parallel load keeps
# this process off the CPU during one particular call.
elapsed = float("inf")
for _ in range(3):
    t0 = time.perf_counter()
    procs = snap()
    elapsed = min(elapsed, time.perf_counter() - t0)
c.check("snapshot returns a list of (pid, exe)", isinstance(procs, list) and procs and all(len(p) == 2 for p in procs))
me = os.path.basename(sys.executable).lower()
c.check("this test's own process is in it, named like tasklist names it",
        (os.getpid(), me) in procs)
c.check("exe names are lowercase", all(exe == exe.lower() for _, exe in procs))
c.check(f"one snapshot is fast (best of 3 took {elapsed * 1000:.0f} ms, limit 1000)", elapsed < 1.0)

c.check("_pid_exe_name finds this process", exe_of(os.getpid()) == me)
c.check("_pid_exe_name of a falsy pid -> ''", exe_of(0) == "" and exe_of(None) == "")
c.check("_pid_exe_name of a pid that isn't running -> ''",
        exe_of(max(p for p, _ in procs) + 4) == "")

# Same python.exe processes as the independent source, by PID. Every one
# alive in both snapshots (taken around the reference read) must be in the
# reference under the same name; every one the reference saw must be in a
# snapshot, unless it was short-lived (started after the first snapshot and
# gone again by now -- a PID still running that no snapshot saw is a real miss).
img = me
before = {pid for pid, exe in snap() if exe == img}
listed, ref = psapi_exe_names()
after = {pid for pid, exe in snap() if exe == img}
ref_img = {pid for pid, exe in ref.items() if exe == img}
stable = before & after
c.check(f"reference source works (sees this process as {img})", ref.get(os.getpid()) == img)
c.check(f"every {img} alive throughout is listed by the reference (missing {sorted(stable - listed)})",
        stable <= listed)
wrong = {pid: ref[pid] for pid in stable if pid in ref and ref[pid] != img}
c.check(f"...and named {img} there whenever it can be opened (mismatch {wrong})", not wrong)
extra = ref_img - before - after
alive_now = {pid for pid, exe in snap() if exe == img}
c.check(f"the reference saw no live {img} the snapshots missed (extra {sorted(extra & alive_now)})",
        not (extra & alive_now))

c.finish()
