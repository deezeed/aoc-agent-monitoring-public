"""Tests _ngrok_authtoken_configured, extracted straight from monitor.py.
It backs a /status field and runs while _status_payload_cache_lock is held,
so it must never wait on ngrok.exe: it returns the cached result at once and
refreshes in the background, one refresh at a time. The real subprocess check
(_ngrok_authtoken_check_now) is swapped for a slow stub.

Waits on the background thread are events (wait_until), never fixed sleeps:
under run_all's parallel load a thread can take well over 100 ms to start,
and the check writes the cache BEFORE _ngrok_authtoken_refresh_bg's finally
releases the lock, so "new value served" doesn't yet mean "next stale poll
may refresh"."""
import sys, os, time, threading

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker, wait_until

c = Checker()

cache = [False, 0.0]
calls = []
release = threading.Event()
lock = threading.Lock()

def slow_check_now():
    calls.append(time.time())
    release.wait(20)
    cache[0] = True
    cache[1] = time.time()
    return True

ns = exec_functions(
    ["_ngrok_authtoken_refresh_bg", "_ngrok_authtoken_configured"],
    {"os": os, "time": time, "threading": threading,
     "_ngrok_authtoken_cache": cache,
     "_ngrok_authtoken_refresh_lock": lock,
     "_find_ngrok": lambda: "C:/fake/ngrok.exe",
     "_ngrok_authtoken_check_now": slow_check_now})
configured = ns["_ngrok_authtoken_configured"]

# 1. a stale cache returns immediately with the old value, even though the
#    check itself is still running (a blocking call would take the full 20 s)
t0 = time.time()
r = configured()
c.check("stale cache returns without waiting on ngrok", time.time() - t0 < 2)
c.check("returns the cached (old) value", r is False)
c.check("one background refresh started", wait_until(lambda: len(calls) == 1))

# 2. repeated polls during that refresh don't pile up more checks -- the
#    check is provably in flight (calls == 1, release not set)
for _ in range(20):
    configured()
time.sleep(0.1)
c.check("no duplicate refreshes while one is in flight", len(calls) == 1)

# 3. once the refresh lands (cache written AND lock released), the new value
#    is served and no new check starts
release.set()
c.check("refresh finished", wait_until(lambda: cache[0] is True and not lock.locked()))
c.check("refreshed value served", configured() is True)
c.check("fresh cache starts no new check", not lock.locked() and len(calls) == 1)

# 4. stale again -> exactly one more refresh (the lock was released)
cache[1] = 0.0
configured()
c.check("lock released after refresh, next stale poll refreshes", wait_until(lambda: len(calls) == 2))
c.check("that refresh finished", wait_until(lambda: cache[1] > 0 and not lock.locked()))

# 5. no ngrok.exe -> False, no check at all
ns["_find_ngrok"] = lambda: ""
cache[1] = 0.0
c.check("no ngrok.exe -> False", configured() is False)
c.check("no ngrok.exe -> no check (lock never taken)", not lock.locked() and len(calls) == 2)

c.finish()
