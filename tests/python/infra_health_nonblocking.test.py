"""Tests _infra_health_snapshot, extracted straight from monitor.py.
It backs /status's infra_health and runs while _status_payload_cache_lock is
held, so it must never wait on _build_infra_health's file reads (watchdog.log
in a OneDrive-synced folder blocked for 45 s on 2026-09-28): it returns the
cached payload at once and refreshes in the background, one refresh at a
time. _build_infra_health is swapped for a slow stub."""
import sys, os, time, threading

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()

cache = [None, 0.0]
calls = []
release = threading.Event()

def slow_build():
    calls.append(time.time())
    release.wait(5)
    return {"watchdog": {"pid_alive": True}, "monitor_uptime_s": -1}

ns = exec_functions(
    ["_infra_health_refresh_bg", "_infra_health_snapshot"],
    {"time": time, "threading": threading,
     "_infra_health_cache": cache,
     "_infra_health_refresh_lock": threading.Lock(),
     "_PROCESS_START": time.time() - 100,
     "_build_infra_health": slow_build})
snapshot = ns["_infra_health_snapshot"]

# 1. empty cache -> None immediately, while the build is still running
t0 = time.time()
r = snapshot()
c.check("empty cache returns without waiting on the build", time.time() - t0 < 0.5)
c.check("returns None before the first refresh lands", r is None)
time.sleep(0.1)
c.check("one background refresh started", len(calls) == 1)

# 2. repeated polls during that refresh don't pile up more builds
for _ in range(20):
    snapshot()
time.sleep(0.1)
c.check("no duplicate refreshes while one is in flight", len(calls) == 1)

# 3. once the refresh lands, the payload is served with a live uptime
release.set()
time.sleep(0.2)
r = snapshot()
c.check("refreshed payload served", r is not None and r["watchdog"]["pid_alive"] is True)
c.check("uptime computed at read time, not build time", 99 < r["monitor_uptime_s"] < 110)
c.check("cached dict not mutated by the uptime overlay", cache[0]["monitor_uptime_s"] == -1)
time.sleep(0.1)
c.check("fresh cache starts no new build", len(calls) == 1)

# 4. stale again -> old payload returned at once, exactly one more refresh
cache[1] = 0.0
r = snapshot()
c.check("stale cache still serves the old payload", r is not None)
time.sleep(0.2)
c.check("lock released after refresh, next stale poll refreshes", len(calls) == 2)

# 5. a build that raises keeps the old payload and releases the lock
def boom():
    calls.append(time.time())
    raise OSError("file locked")
ns["_build_infra_health"] = boom
cache[1] = 0.0
snapshot()
time.sleep(0.2)
c.check("failed refresh keeps the previous payload", cache[0] is not None)
cache[1] = 0.0
snapshot()
time.sleep(0.2)
c.check("failed refresh released the lock", len(calls) == 4)

c.finish()
