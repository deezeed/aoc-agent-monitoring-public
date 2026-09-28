"""Tests _ngrok_authtoken_configured, extracted straight from monitor.py.
It backs a /status field and runs while _status_payload_cache_lock is held,
so it must never wait on ngrok.exe: it returns the cached result at once and
refreshes in the background, one refresh at a time. The real subprocess check
(_ngrok_authtoken_check_now) is swapped for a slow stub."""
import sys, os, time, threading

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()

cache = [False, 0.0]
calls = []
release = threading.Event()

def slow_check_now():
    calls.append(time.time())
    release.wait(5)
    cache[0] = True
    cache[1] = time.time()
    return True

ns = exec_functions(
    ["_ngrok_authtoken_refresh_bg", "_ngrok_authtoken_configured"],
    {"os": os, "time": time, "threading": threading,
     "_ngrok_authtoken_cache": cache,
     "_ngrok_authtoken_refresh_lock": threading.Lock(),
     "_find_ngrok": lambda: "C:/fake/ngrok.exe",
     "_ngrok_authtoken_check_now": slow_check_now})
configured = ns["_ngrok_authtoken_configured"]

# 1. a stale cache returns immediately with the old value, even though the
#    check itself is still running
t0 = time.time()
r = configured()
c.check("stale cache returns without waiting on ngrok", time.time() - t0 < 0.5)
c.check("returns the cached (old) value", r is False)
time.sleep(0.1)
c.check("one background refresh started", len(calls) == 1)

# 2. repeated polls during that refresh don't pile up more checks
for _ in range(20):
    configured()
time.sleep(0.1)
c.check("no duplicate refreshes while one is in flight", len(calls) == 1)

# 3. once the refresh lands, the new value is served and no new check starts
release.set()
time.sleep(0.2)
c.check("refreshed value served", configured() is True)
time.sleep(0.1)
c.check("fresh cache starts no new check", len(calls) == 1)

# 4. stale again -> exactly one more refresh (the lock was released)
cache[1] = 0.0
configured()
time.sleep(0.2)
c.check("lock released after refresh, next stale poll refreshes", len(calls) == 2)

# 5. no ngrok.exe -> False, no check at all
ns["_find_ngrok"] = lambda: ""
cache[1] = 0.0
c.check("no ngrok.exe -> False", configured() is False)
time.sleep(0.1)
c.check("no ngrok.exe -> no check", len(calls) == 2)

c.finish()
