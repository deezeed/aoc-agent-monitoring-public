"""Tests the /status stall diagnostics (_probe_status_once, _open_stall_log),
extracted straight from monitor.py. Runs against a scratch log file, never
the real LOGS_DIR. The probe is a stub callable, so no HTTP server is
involved -- what matters is that a slow probe gets its stacks dumped *while*
it is still pending (including when another thread is hogging the GIL), and
a fast one writes nothing."""
import sys, os, time, threading, tempfile, shutil, re
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_stall_")
STALL_LOG = os.path.join(SCRATCH, "logs", "status_stalls.log")

c = Checker()

try:
    def make_ns(max_bytes=1_000_000):
        return exec_functions(
            ["_open_stall_log", "_probe_status_once"],
            {"os": os, "time": time, "threading": threading, "datetime": datetime,
             "STALL_LOG": STALL_LOG, "_STALL_LOG_MAX_BYTES": max_bytes})

    def read_log():
        if not os.path.exists(STALL_LOG):
            return ""
        with open(STALL_LOG, encoding="utf-8") as f:
            return f.read()

    ns = make_ns()

    # 1. a fast probe writes nothing and returns None (and creates the logs dir)
    with ns["_open_stall_log"]() as f:
        r = ns["_probe_status_once"](lambda: None, 0.5, f)
    c.check("fast probe returns None", r is None)
    c.check("fast probe writes nothing", read_log() == "")

    # 2. a slow probe: stacks dumped mid-stall, including the probe's own frame
    def slow_status_probe():
        time.sleep(0.6)
    with ns["_open_stall_log"]() as f:
        r = ns["_probe_status_once"](slow_status_probe, 0.2, f)
    log = read_log()
    c.check("slow probe returns elapsed seconds", isinstance(r, float) and r >= 0.5)
    c.check("faulthandler dump written", "Timeout (0:00:00" in log and "Thread 0x" in log)
    c.check("dump shows the probe still inside its call", "slow_status_probe" in log)
    c.check("summary line written after the dump", "/status probe took 0.6s" in log or "/status probe took 0.7s" in log)
    import struct
    tid = f"0x{threading.get_ident():0{struct.calcsize('L') * 2}x}"
    c.check("summary names threads in faulthandler's id format", f"{tid}=MainThread" in log)
    c.check("that id actually appears in the dump", f"Thread {tid}" in log)

    # 3. the dump fires even while another thread holds the GIL: the probe
    #    waits on a thread running a long C-level call that never releases it
    open(STALL_LOG, "w").close()
    def gil_hog():
        re.match(r"(a+)+$", "a" * 23 + "b")  # catastrophic backtracking, holds the GIL
    def probe_behind_hog():
        t = threading.Thread(target=gil_hog, name="gil-hog")
        t.start()
        t.join()
    with ns["_open_stall_log"]() as f:
        r = ns["_probe_status_once"](probe_behind_hog, 0.2, f)
    log = read_log()
    c.check("GIL hog long enough to cross the threshold", r is not None)
    c.check("dump captured the hogging thread mid-call", "in gil_hog" in log)

    # 4. a probe that raises is swallowed, and a fast failure writes nothing
    open(STALL_LOG, "w").close()
    def boom():
        raise ConnectionRefusedError("down")
    with ns["_open_stall_log"]() as f:
        r = ns["_probe_status_once"](boom, 0.5, f)
    c.check("raising probe returns None", r is None)
    c.check("raising probe writes nothing", read_log() == "")

    # 5. the dump is disarmed after a fast probe (nothing fires later)
    with ns["_open_stall_log"]() as f:
        ns["_probe_status_once"](lambda: None, 0.2, f)
        time.sleep(0.4)
    c.check("no late dump after a fast probe", read_log() == "")

    # 6. rotation past the size cap
    with open(STALL_LOG, "w") as f:
        f.write("x" * 50)
    small = make_ns(max_bytes=10)
    with small["_open_stall_log"]() as f:
        f.write("second")
    c.check("log rotated to .1 once over the cap", os.path.exists(STALL_LOG + ".1"))
    c.check("new log starts fresh after rotation", read_log() == "second")

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
