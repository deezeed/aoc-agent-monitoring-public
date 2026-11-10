"""Tests _infra_health_snapshot, extracted straight from monitor.py.
It backs /status's infra_health and runs while _status_payload_cache_lock is
held, so it must never wait on _build_infra_health's file reads (watchdog.log
in a OneDrive-synced folder blocked for 45 s on 2026-09-28): it returns the
cached payload at once and refreshes in the background, one refresh at a
time. _build_infra_health is swapped for a slow stub.

Waits on the background thread are events (wait_until), never fixed sleeps:
under run_all's parallel load a thread can take well over 100 ms to start,
and the refresh writes the cache BEFORE its finally releases the lock, so
"payload served" doesn't yet mean "next stale poll may refresh"."""
import sys, os, time, threading

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker, wait_until

c = Checker()

cache = [None, 0.0]
calls = []
release = threading.Event()
lock = threading.Lock()

def slow_build():
    calls.append(time.time())
    release.wait(20)
    return {"watchdog": {"pid_alive": True}, "monitor_uptime_s": -1}

ns = exec_functions(
    ["_infra_health_refresh_bg", "_infra_health_snapshot", "_hooks_health_bad"],
    {"time": time, "threading": threading,
     "_hooks_health": {"checks": [{"id": "danger", "label": "Dangerous-command guard", "ok": False}]},
     "_infra_health_cache": cache,
     "_infra_health_refresh_lock": lock,
     "_PROCESS_START": time.time() - 100,
     "_build_infra_health": slow_build})
snapshot = ns["_infra_health_snapshot"]

# 1. empty cache -> None immediately, while the build is still running
#    (a blocking snapshot would take the stub's full 20 s)
t0 = time.time()
r = snapshot()
c.check("empty cache returns without waiting on the build", time.time() - t0 < 2)
c.check("returns None before the first refresh lands", r is None)
c.check("one background refresh started", wait_until(lambda: len(calls) == 1))

# 2. repeated polls during that refresh don't pile up more builds -- the
#    refresh is provably in flight (calls == 1, release not set)
for _ in range(20):
    snapshot()
time.sleep(0.1)
c.check("no duplicate refreshes while one is in flight", len(calls) == 1)

# 3. once the refresh lands (cache written AND lock released), the payload
#    is served with a live uptime
release.set()
c.check("refresh finished", wait_until(lambda: cache[0] is not None and not lock.locked()))
r = snapshot()
c.check("refreshed payload served", r is not None and r["watchdog"]["pid_alive"] is True)
c.check("uptime computed at read time, not build time", 99 < r["monitor_uptime_s"] < 400)
c.check("carries the failing install checks for the status pill", r["hooks_bad"] == ["Dangerous-command guard"])
c.check("cached dict not mutated by the uptime overlay", cache[0]["monitor_uptime_s"] == -1)
c.check("fresh cache starts no new build", not lock.locked() and len(calls) == 1)

# 4. stale again -> old payload returned at once, exactly one more refresh
cache[1] = 0.0
r = snapshot()
c.check("stale cache still serves the old payload", r is not None)
c.check("lock released after refresh, next stale poll refreshes", wait_until(lambda: len(calls) == 2))
c.check("that refresh finished", wait_until(lambda: cache[1] > 0 and not lock.locked()))

# 5. a build that raises keeps the old payload and releases the lock
def boom():
    calls.append(time.time())
    raise OSError("file locked")
ns["_build_infra_health"] = boom
cache[1] = 0.0
snapshot()
c.check("failing refresh ran", wait_until(lambda: len(calls) == 3 and not lock.locked()))
c.check("failed refresh keeps the previous payload", cache[0] is not None)
cache[1] = 0.0
snapshot()
c.check("failed refresh released the lock", wait_until(lambda: len(calls) == 4))

c.finish()
