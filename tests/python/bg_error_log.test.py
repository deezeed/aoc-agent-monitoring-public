"""Tests _log_bg_error, extracted straight from monitor.py. Runs against a
scratch logs directory (never the real LOGS_DIR/BG_ERROR_LOG), same style
as alert_audit_log.test.py's scratch-dir approach. This is the append-only
record background/maintenance workers' outermost except:pass now writes to
instead of failing completely silently -- previously nothing recorded this
at all."""
import sys, os, json, tempfile, shutil, time
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_bg_error_")
LOGS_DIR = SCRATCH
BG_ERROR_LOG = os.path.join(SCRATCH, "background_errors.log")

c = Checker()

try:
    def read_entries():
        with open(BG_ERROR_LOG, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def make_ns(start_time):
        # time.time is stubbed with a mutable clock so the throttle window
        # can be advanced deterministically instead of sleeping for real.
        clock = {"now": start_time}
        fake_time = type("FakeTime", (), {"time": staticmethod(lambda: clock["now"])})
        ns = exec_functions(["_append_audit_entry", "_log_bg_error"], {
            "os": os, "json": json, "datetime": datetime, "time": fake_time,
            "LOGS_DIR": LOGS_DIR, "BG_ERROR_LOG": BG_ERROR_LOG,
            "_bg_error_last": {}, "_BG_ERROR_MIN_INTERVAL_S": 300,
        })
        return ns, clock

    # 1. a single exception writes exactly one JSON line with where/error/ts
    ns, clock = make_ns(1000.0)
    ns["_log_bg_error"]("_autosave_worker", RuntimeError("disk full"))
    entries = read_entries()
    c.check("one exception -> one JSON line", len(entries) == 1)
    c.check("where recorded", entries[0]["where"] == "_autosave_worker")
    c.check("error message recorded", entries[0]["error"] == "disk full")
    c.check("ts is a non-empty string", isinstance(entries[0]["ts"], str) and len(entries[0]["ts"]) > 0)

    # 2. same where + same message within the throttle window -> nothing new written
    ns["_log_bg_error"]("_autosave_worker", RuntimeError("disk full"))
    entries = read_entries()
    c.check("repeat of same message within window is throttled (no new line)", len(entries) == 1)

    # 3. different message for the same 'where' within the window -> written immediately
    ns["_log_bg_error"]("_autosave_worker", RuntimeError("permission denied"))
    entries = read_entries()
    c.check("different message for same 'where' bypasses the throttle", len(entries) == 2)
    c.check("second entry carries the new message", entries[1]["error"] == "permission denied")

    # 4. same message again, but after the throttle window has elapsed -> written again
    clock["now"] += 301
    ns["_log_bg_error"]("_autosave_worker", RuntimeError("permission denied"))
    entries = read_entries()
    c.check("same message after window elapses is logged again", len(entries) == 3)

    # 5. a different 'where' is tracked independently of any other call site
    ns["_log_bg_error"]("_backup_history_db", RuntimeError("permission denied"))
    entries = read_entries()
    c.check("a different call site is never throttled by another site's history", len(entries) == 4)
    c.check("fourth entry's where is the new call site", entries[3]["where"] == "_backup_history_db")

    # 6. _log_bg_error itself never raises, even if the underlying write fails
    # (e.g. LOGS_DIR unwritable) -- it's called from inside an except block,
    # so it must never be the thing that turns a handled failure into a crash.
    ns_broken = exec_functions(["_append_audit_entry", "_log_bg_error"], {
        "os": os, "json": json, "datetime": datetime, "time": time,
        "LOGS_DIR": "Z:\\this\\path\\does\\not\\exist\\at\\all",
        "BG_ERROR_LOG": "Z:\\this\\path\\does\\not\\exist\\at\\all\\bg.log",
        "_bg_error_last": {}, "_BG_ERROR_MIN_INTERVAL_S": 300,
    })
    raised = False
    try:
        ns_broken["_log_bg_error"]("_some_worker", RuntimeError("boom"))
    except Exception:
        raised = True
    c.check("a write failure inside _log_bg_error itself is swallowed, never propagates", raised is False)

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
