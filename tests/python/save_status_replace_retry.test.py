"""Tests _replace_with_retry / _save_status, extracted straight from
monitor.py, against a real file on this machine (Windows, like the monitor).

On Windows, os.replace onto a file that another handle has open fails with
WinError 5. The status file is opened by every /status rebuild, so a hook
/update or scanner write that landed during a read raised and the update was
lost -- logged live as "[WinError 5] Access is denied: ...monitor-status.json.tmp".
The write must now wait out a short-lived reader instead."""
import sys, os, json, time, threading, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_replace_retry_")
STATUS_FILE = os.path.join(SCRATCH, "monitor-status.json")
ns = exec_functions(["_replace_with_retry", "_save_status"], {
    "os": os, "json": json, "time": time, "STATUS_FILE": STATUS_FILE,
    "_status_cond": threading.Condition(), "_status_version": [0],
})
save = ns["_save_status"]
replace = ns["_replace_with_retry"]
c = Checker()

try:
    save({"v": 1})
    c.check("plain save works", json.load(open(STATUS_FILE, encoding="utf-8")) == {"v": 1})

    # Sanity: on this platform a bare os.replace really does fail while the
    # target is open -- otherwise the checks below prove nothing.
    with open(os.path.join(SCRATCH, "x.tmp"), "w") as f:
        f.write("x")
    held = open(STATUS_FILE, "r", encoding="utf-8")
    try:
        os.replace(os.path.join(SCRATCH, "x.tmp"), STATUS_FILE)
        bare_fails = False
    except PermissionError:
        bare_fails = True
    finally:
        held.close()
    c.check("precondition: bare os.replace fails while a reader holds the file", bare_fails)

    # A reader holds the file for 200ms while the save happens.
    held = open(STATUS_FILE, "r", encoding="utf-8")
    threading.Timer(0.2, held.close).start()
    t0 = time.perf_counter()
    save({"v": 2})
    waited = time.perf_counter() - t0
    c.check("save waits out a short-lived reader instead of raising",
            json.load(open(STATUS_FILE, encoding="utf-8")) == {"v": 2})
    c.check(f"and only as long as needed ({waited:.2f}s)", 0.15 <= waited < 1.0)
    c.check("status version bumped for each successful save", ns["_status_version"][0] == 2)

    # A lock that never clears still surfaces as an error (not a silent hang).
    with open(os.path.join(SCRATCH, "y.tmp"), "w") as f:
        f.write("y")
    held = open(STATUS_FILE, "r", encoding="utf-8")
    try:
        replace(os.path.join(SCRATCH, "y.tmp"), STATUS_FILE, attempts=3, delay_s=0.01)
        raised = False
    except PermissionError:
        raised = True
    finally:
        held.close()
    c.check("a lock that outlasts the retries still raises PermissionError", raised)
finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
