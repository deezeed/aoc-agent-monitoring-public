"""System monitor for the Command Center (monitor.py): _cpu_pct (busy share
between two (idle, total) samples, clamped, None when unusable) and _sysinfo
on this machine (percentages in range, first call takes its own second
sample, later calls reuse the previous one)."""
import sys, os, time, threading, socket

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
ns = exec_functions(["_sysinfo_prev", "_sysinfo_lock", "_cpu_times", "_cpu_pct", "_mem_pct", "_sysinfo"],
                    {"os": os, "time": time, "threading": threading, "_socket": socket})
P = ns["_cpu_pct"]
c.check("busy share", P((100, 1000), (150, 1100)) == 50.0 and P((0, 0), (0, 100)) == 100.0 and P((0, 0), (100, 100)) == 0.0)
c.check("clamped", P((0, 0), (150, 100)) == 0.0)
c.check("unusable samples -> None", P(None, (1, 2)) is None and P((1, 5), (1, 5)) is None and P((1, 5), None) is None)
t0 = time.monotonic()
r = ns["_sysinfo"]()
first = time.monotonic() - t0
pct = lambda v: v is None or 0 <= v <= 100
c.check("shape + ranges", set(r) >= {"cpu", "ram", "ram_total", "disk", "disk_total", "disk_path", "host"}
        and pct(r["cpu"]) and pct(r["ram"]) and pct(r["disk"]))
c.check("real values on this OS", r["ram"] is not None and r["disk"] is not None and r["cpu"] is not None and r["ram_total"] > 0)
t0 = time.monotonic()
ns["_sysinfo"]()
c.check("second call reuses the previous sample (no sleep)", time.monotonic() - t0 < 0.2 <= first + 0.05)
c.check("host name", r["host"] == socket.gethostname())
c.finish()
