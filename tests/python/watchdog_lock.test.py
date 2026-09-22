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
