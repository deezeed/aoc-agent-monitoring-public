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

    # "Fast" probes get a FAST_S budget, not a tight one: on a starved CPU
    # (the whole suite running, other load) even a no-op probe has measured
    # ~0.4s, and the code then rightly logs it as a starvation stall --
    # which made these checks fail whenever the machine was busy.
    FAST_S = 1.0

    # 1. a fast probe writes nothing and returns None (and creates the logs dir)
    with ns["_open_stall_log"]() as f:
        r = ns["_probe_status_once"](lambda: None, FAST_S, f)
    c.check("fast probe returns None", r is None)
    c.check("fast probe writes nothing", read_log() == "")

    # 2. a slow probe: stacks dumped mid-stall, including the probe's own frame
    # 1.5s against a 0.2s threshold: the dump comes from faulthandler's own
    # watchdog thread, which on a starved CPU can wait a good while to be
    # scheduled -- a 0.4s margin (the old 0.6s probe) wasn't always enough.
    def slow_status_probe():
        time.sleep(1.5)
    with ns["_open_stall_log"]() as f:
        r = ns["_probe_status_once"](slow_status_probe, 0.2, f)
    log = read_log()
    c.check("slow probe returns elapsed seconds", isinstance(r, float) and r >= 1.4)
    c.check("faulthandler dump written", "Timeout (0:00:00" in log and "Thread 0x" in log)
    c.check("dump shows the probe still inside its call", "slow_status_probe" in log)
    # matched against the elapsed time the call itself returned, not a fixed
    # 0.6/0.7s: a loaded machine oversleeps the 0.6s probe
    c.check("summary line written after the dump", f"/status probe took {r:.1f}s" in log)
    import struct
    tid = f"0x{threading.get_ident():0{struct.calcsize('L') * 2}x}"
    c.check("summary names threads in faulthandler's id format", f"{tid}=MainThread" in log)
    c.check("that id actually appears in the dump", f"Thread {tid}" in log)

    # 3. the dump fires even while another thread holds the GIL: the probe
    #    waits on a thread running a long C-level call that never releases it
    open(STALL_LOG, "w").close()
    # Catastrophic backtracking holds the GIL, and each extra "a" doubles the
    # time -- but the base speed differs a lot between machines and Python
    # versions (a fixed 24 took ~2s locally and <0.5s on the CI runner), so
    # calibrate: grow n until one match takes >=0.4s, then use n+2 (~4x).
    # Measured in this thread's CPU time, not wall time: on a loaded machine
    # wall time is inflated by preemption, which picked too small an n.
    # Wall time is never less than CPU time, so the hog lasts >=1.6s anyway.
    n = 16
    while True:
        t0 = time.thread_time()
        re.match(r"(a+)+$", "a" * n + "b")
        if time.thread_time() - t0 >= 0.4:
            break
        n += 1
    HOG_N = n + 2
    def gil_hog():
        re.match(r"(a+)+$", "a" * HOG_N + "b")  # >=1.6s on any machine, holds the GIL
    def probe_behind_hog():
        t = threading.Thread(target=gil_hog, name="gil-hog")
        t.start()
        t.join()
    with ns["_open_stall_log"]() as f:
        # 0.5s, not 0.2s: on a loaded machine the hog thread may not even be
        # running yet at 0.2s (still in thread start-up), and the dump would
        # miss it. The calibrated hog runs >=1.6s, so the dump still lands
        # well inside it.
        r = ns["_probe_status_once"](probe_behind_hog, 0.5, f)
    log = read_log()
    c.check("GIL hog long enough to cross the threshold", r is not None)
    c.check("dump captured the hogging thread mid-call", "in gil_hog" in log)

    # 4. a probe that raises is swallowed, and a fast failure writes nothing
    open(STALL_LOG, "w").close()
    def boom():
        raise ConnectionRefusedError("down")
    with ns["_open_stall_log"]() as f:
        r = ns["_probe_status_once"](boom, FAST_S, f)
    c.check("raising probe returns None", r is None)
    c.check("raising probe writes nothing", read_log() == "")

    # 5. the dump is disarmed after a fast probe (nothing fires later)
    with ns["_open_stall_log"]() as f:
        r = ns["_probe_status_once"](lambda: None, FAST_S, f)
        time.sleep(FAST_S * 1.5)  # well past the point the dump would have fired
    c.check("fast probe stayed under its budget", r is None)
    c.check("no late dump after a fast probe", read_log() == "")

    # 5b. elapsed crosses the threshold but the dump timer never fired (what
    #     a suspend looks like): the summary must say no stacks were dumped
    open(STALL_LOG, "w").close()
    class JumpyClock:
        _reads = iter([0.0, 10.0])
        def monotonic(self):
            return next(self._reads)
        def __getattr__(self, name):
            return getattr(time, name)
    jumpy = exec_functions(
        ["_probe_status_once"],
        {"os": os, "time": JumpyClock(), "threading": threading, "datetime": datetime})
    with ns["_open_stall_log"]() as f:
        r = jumpy["_probe_status_once"](lambda: None, 5.0, f)
    log = read_log()
    c.check("no-dump stall still reported", r == 10.0 and "/status probe took 10.0s" in log)
    c.check("no-dump stall says no stacks fired", "NO stack dump fired" in log and "stacks above" not in log)

    # 5c. the real-dump summary still points at the stacks
    open(STALL_LOG, "w").close()
    with ns["_open_stall_log"]() as f:
        ns["_probe_status_once"](slow_status_probe, 0.2, f)
    log = read_log()
    c.check("real dump summary says 'stacks above'", "stacks above dumped" in log and "NO stack dump" not in log)

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
