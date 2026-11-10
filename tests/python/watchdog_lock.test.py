"""Tests _acquire_lock()/_release_lock()'s new observability: corrupt PID
files and write/remove failures were always tolerated (the watchdog must
never refuse to start over a bad lock file), but were previously swallowed
with a bare `except: pass` -- no trace in watchdog.log if the lock file
itself was ever the problem. Extracted straight from watchdog.py."""
import sys, os, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

WATCHDOG_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "watchdog.py"))

c = Checker()

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_watchdog_lock_")

logs = []


def fake_log(msg):
    logs.append(msg)


def fake_is_python_process(pid):
    return True  # never reached in the corrupt-file case; present only so the name resolves


try:
    # --- corrupt PID file: int(f.read().strip()) raises ValueError ---
    corrupt_pid_file = os.path.join(SCRATCH, "watchdog.pid")
    with open(corrupt_pid_file, "w") as f:
        f.write("not-a-pid")

    ns = exec_functions(["_acquire_lock"], {
        "os": os,
        "PID_FILE": corrupt_pid_file,
        "_log": fake_log,
        "_is_python_process": fake_is_python_process,
        "sys": sys,
    }, path=WATCHDOG_PATH)
    _acquire_lock = ns["_acquire_lock"]

    result = _acquire_lock()
    c.check("still acquires the lock despite a corrupt PID file (never blocks startup)", result is True)
    c.check("logs that the PID file was unreadable", any("Could not read/parse PID file" in m for m in logs))
    with open(corrupt_pid_file) as f:
        c.check("overwrites the corrupt file with this process's own PID", f.read().strip() == str(os.getpid()))

    logs.clear()

    # --- PID file path in a nonexistent directory: write fails ---
    unwritable_pid_file = os.path.join(SCRATCH, "no_such_dir", "watchdog.pid")
    ns2 = exec_functions(["_acquire_lock"], {
        "os": os,
        "PID_FILE": unwritable_pid_file,
        "_log": fake_log,
        "_is_python_process": fake_is_python_process,
        "sys": sys,
    }, path=WATCHDOG_PATH)
    _acquire_lock2 = ns2["_acquire_lock"]

    result2 = _acquire_lock2()
    c.check("still returns True (lock 'acquired') even when the PID file can't be written", result2 is True)
    c.check("logs that the PID file write failed", any("Failed to write PID file" in m for m in logs))

    logs.clear()

    # --- a live watchdog holds the lock: logged, unless sentinel's routine revive attempt ---
    live_pid_file = os.path.join(SCRATCH, "live.pid")
    with open(live_pid_file, "w") as f:
        f.write("4242")
    ns4 = exec_functions(["_acquire_lock"], {
        "os": os, "PID_FILE": live_pid_file, "_log": fake_log,
        "_is_python_process": lambda pid: True, "sys": sys,
    }, path=WATCHDOG_PATH)
    c.check("another live watchdog -> lock refused", ns4["_acquire_lock"]() is False)
    c.check("a second watchdog started by hand is logged", any("Another watchdog appears to be running (PID 4242)" in m for m in logs))
    logs.clear()
    c.check("sentinel's launch (quiet) -> still refused", ns4["_acquire_lock"](quiet=True) is False)
    c.check("sentinel's launch finding it alive -> nothing in watchdog.log", logs == [])
    with open(live_pid_file) as f:
        c.check("the live watchdog's PID file is left alone", f.read().strip() == "4242")
    with open(os.path.join(os.path.dirname(WATCHDOG_PATH), "sentinel.py"), encoding="utf-8") as f:
        c.check("sentinel.py passes --from-sentinel", '"--from-sentinel"' in f.read())
    with open(WATCHDOG_PATH, encoding="utf-8") as f:
        c.check("watchdog.py main() reads --from-sentinel", '_acquire_lock(quiet="--from-sentinel" in sys.argv)' in f.read())
    logs.clear()

    # --- _release_lock: file already gone -- os.remove raises FileNotFoundError ---
    ns3 = exec_functions(["_release_lock"], {
        "os": os,
        "PID_FILE": os.path.join(SCRATCH, "already_gone.pid"),
        "_log": fake_log,
    }, path=WATCHDOG_PATH)
    ns3["_release_lock"]()
    c.check("logs when the PID file can't be removed on shutdown", any("Failed to remove PID file" in m for m in logs))

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
