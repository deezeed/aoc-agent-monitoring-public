"""Tests _iso_utc_to_local_hms, extracted straight from monitor.py. Had zero
coverage until now despite being the thing that makes transcript-detected
agents' started_at/completed_at compare correctly against hook-reported
ones (both need to land in the same local HH:MM:SS wall-clock form) -- a
regression here would silently break duration/elapsed-time math for every
agent the transcript-scanner fallback ever catches.

Doesn't mock the clock: since the function itself converts via
calendar.timegm + time.localtime (real system calls, whatever this
machine's local timezone happens to be), the test derives its expected
values the same way, matching this repo's existing convention for
wall-clock-dependent functions (see tests/js/month_projection.test.js)."""
import sys, os, calendar, time, re
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ns = exec_functions(["_iso_utc_to_local_hms", "_now_ts"], {
    "datetime": datetime, "calendar": calendar, "time": time,
})
_iso_utc_to_local_hms = ns["_iso_utc_to_local_hms"]

c = Checker()

def expected_hms(y, mo, d, h, mi, s):
    epoch = calendar.timegm(datetime(y, mo, d, h, mi, s).timetuple())
    return time.strftime("%H:%M:%S", time.localtime(epoch))

# 1. well-formed transcript timestamp, milliseconds + trailing Z (the real
# shape Claude Code transcripts actually use)
c.check("converts a well-formed UTC ISO timestamp to local HH:MM:SS",
        _iso_utc_to_local_hms("2026-07-15T18:41:23.530Z") == expected_hms(2026, 7, 15, 18, 41, 23))

# 2. no milliseconds, still has trailing Z
c.check("handles a timestamp with no milliseconds component",
        _iso_utc_to_local_hms("2026-07-15T18:41:23Z") == expected_hms(2026, 7, 15, 18, 41, 23))

# 3. no trailing Z at all -- .rstrip("Z") is a no-op, strptime still succeeds
c.check("handles a timestamp with no trailing Z",
        _iso_utc_to_local_hms("2026-07-15T18:41:23") == expected_hms(2026, 7, 15, 18, 41, 23))

# 4. UTC timestamp near midnight can shift to the previous/next local
# calendar day depending on timezone -- the function only ever returns a
# time-of-day, so this must still produce a well-formed HH:MM:SS regardless
# of which local day it actually lands on.
near_midnight = _iso_utc_to_local_hms("2026-07-15T23:59:59Z")
c.check("near-midnight UTC timestamp still produces well-formed HH:MM:SS",
        bool(re.match(r'^\d{2}:\d{2}:\d{2}$', near_midnight)))

# 5. malformed input falls back to "now" rather than raising -- checked as
# "still a valid HH:MM:SS string", not an exact timing match (avoids a
# second-boundary flake), matching how this function's own docstring frames
# the fallback ("falls back to 'now' on any parse failure").
for bad in ("not a timestamp", "", "2026-13-45T99:99:99Z", None):
    try:
        result = _iso_utc_to_local_hms(bad)
        ok = bool(re.match(r'^\d{2}:\d{2}:\d{2}$', result))
    except Exception:
        ok = False
    c.check(f"malformed input {bad!r} falls back to a valid HH:MM:SS, doesn't raise", ok)

c.finish()
