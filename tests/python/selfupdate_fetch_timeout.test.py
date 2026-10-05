"""Runs _selfupdate_worker, extracted straight from monitor.py, against a fake
git. The checkout lives in OneDrive, which sometimes stalls `git fetch` past
its timeout; every such round used to land in background_errors.log. A few
consecutive timeouts must stay silent and keep the last status, a persistent
one must still be logged, and a successful fetch resets the streak."""
import os, subprocess, sys, threading, time

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker


class _Stop(BaseException):  # not Exception: the worker catches those
    pass


def run_worker(fetch_results, rounds):
    """fetch_results: per round, 'timeout' or 'ok'. Returns (logged, status)."""
    logged, status, it = [], {"stale": None}, iter(fetch_results)

    class R:
        def __init__(self, out=""):
            self.stdout, self.returncode = out, 0

    def fake_run(cmd, timeout=None, **kw):
        if "fetch" in cmd:
            if next(it) == "timeout":
                raise subprocess.TimeoutExpired(cmd, timeout)
            return R()
        if "--count" in cmd:
            return R("0")
        return R("abc")

    ns = exec_functions(["_SELFUPDATE_FETCH_TIMEOUT_S", "_SELFUPDATE_QUIET_FETCH_TIMEOUTS", "_selfupdate_worker"], {
        "subprocess": subprocess, "AOC_DIR": ".", "AOC_VERSION_FILE": "x",
        "_read_installed_version": lambda p: None, "_release_update_worker": lambda v: None,
        "_run": fake_run, "_log_bg_error": lambda where, e: logged.append(type(e).__name__),
        "_self_update_lock": threading.Lock(), "_self_update_status": status,
        "_self_update_notified": [False], "_show_native_toast": lambda *a: None,
    })
    calls = [0]
    real_sleep = time.sleep

    def fake_sleep(s):
        calls[0] += 1
        if calls[0] >= rounds:
            raise _Stop()
    time.sleep = fake_sleep
    try:
        ns["_selfupdate_worker"]()
    except _Stop:
        pass
    finally:
        time.sleep = real_sleep
    return logged, status


c = Checker()
logged, status = run_worker(["timeout", "timeout"], 2)
c.check("two timeouts in a row are not logged", logged == [])
c.check("status untouched while fetch keeps timing out", status["stale"] is None)

logged, _ = run_worker(["timeout"] * 4, 4)
c.check("a third+ consecutive timeout is logged", logged == ["TimeoutExpired", "TimeoutExpired"])

logged, status = run_worker(["timeout", "timeout", "ok", "timeout", "timeout"], 5)
c.check("a good fetch resets the streak", logged == [])
c.check("good fetch still updates status", status["stale"] is False)

ns = exec_functions(["_SELFUPDATE_FETCH_TIMEOUT_S"])
c.check("fetch timeout raised from 15 s", ns["_SELFUPDATE_FETCH_TIMEOUT_S"] >= 30)
c.finish()
