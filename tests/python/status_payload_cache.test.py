"""Tests _status_cache_is_fresh, extracted straight from monitor.py.
Regression coverage for the fix to a confirmed real problem: every open
/events WebSocket client (plus watchdog's own poll) independently re-read
and re-parsed the full status file (multi-MB on a long session with many
accumulated agents) on every _status_version bump, and a burst of those
landing together was tripping watchdog's 2-strikes health check. This
function is the pure go/no-go decision _build_status_payload uses to
share one rebuilt payload across those concurrent callers instead."""
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ns = exec_functions(["_status_cache_is_fresh"], {"_STATUS_PAYLOAD_CACHE_TTL": 1.0})
_status_cache_is_fresh = ns["_status_cache_is_fresh"]

c = Checker()

# 1. no cache built yet (version is None, the module's initial state)
c.check("never-built cache (version=None) is never fresh",
        _status_cache_is_fresh(None, 0.0, 5, now=100.0) is False)

# 2. same version, well within TTL -> fresh
c.check("same version, just built -> fresh",
        _status_cache_is_fresh(5, 100.0, 5, now=100.2) is True)

# 3. same version, right at the TTL boundary (exclusive) -> not fresh
c.check("same version, exactly at TTL boundary -> not fresh (end exclusive)",
        _status_cache_is_fresh(5, 100.0, 5, now=101.0, ttl=1.0) is False)

# 4. same version, just under the TTL boundary -> fresh
c.check("same version, just under TTL boundary -> fresh",
        _status_cache_is_fresh(5, 100.0, 5, now=100.999, ttl=1.0) is True)

# 5. version changed (a real status-affecting event happened) -> never fresh,
#    regardless of how recently it was built -- a real change must never be
#    served stale just because the TTL window hasn't elapsed yet
c.check("version bumped since build, even 1ms later -> not fresh",
        _status_cache_is_fresh(5, 100.0, 6, now=100.001) is False)

# 6. version went "backwards" (shouldn't happen in practice, but the check
#    is a plain != so it's symmetric) -> not fresh
c.check("cached version newer than current (shouldn't happen, still handled) -> not fresh",
        _status_cache_is_fresh(6, 100.0, 5, now=100.001) is False)

# 7. custom TTL is honored, not just the module default
c.check("custom short TTL (0.1s) expires sooner",
        _status_cache_is_fresh(5, 100.0, 5, now=100.2, ttl=0.1) is False)
c.check("custom long TTL (10s) still fresh far past the default 1s",
        _status_cache_is_fresh(5, 100.0, 5, now=105.0, ttl=10.0) is True)

c.finish()
