"""Tests _session_really_active, extracted straight from monitor.py. This is
the core of the PID-based fix for the recurring "dismissed" bug: no fixed
heartbeat timeout can reliably tell a genuinely-still-open session (long
tool call, thinking pause, brief step-away) from a truly closed one, so
once the heartbeat grace window has passed the function falls back to
checking whether the session's recorded host_pid is still a live
claude.exe process. _is_claude_pid_alive itself is OS-dependent (walks a
live process snapshot via ctypes) and isn't meaningfully unit-testable
without mocking Windows APIs, so it's stubbed here and only
_session_really_active's own branching logic is under test."""
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

# stub replacing the real ctypes-based process walk; the test controls
# exactly which PIDs are "alive" per case
_alive_pids = set()
def _is_claude_pid_alive(pid):
    return pid in _alive_pids

ns = exec_functions(["_session_really_active"], extra_globals={"_is_claude_pid_alive": _is_claude_pid_alive})
_session_really_active = ns["_session_really_active"]

c = Checker()
NOW = 1_000_000.0

# 1. stored session_active is False -> never active, regardless of anything else
_alive_pids = set()
c.check(
    "stored session_active=False short-circuits to False",
    _session_really_active({"session_active": False, "last_seen_epoch": NOW, "host_pid": 111}, NOW) is False,
)

# 2. recent heartbeat -> active without ever needing to check the pid
_alive_pids = set()
c.check(
    "recent heartbeat is active on its own (no host_pid needed)",
    _session_really_active({"session_active": True, "last_seen_epoch": NOW - 10}, NOW) is True,
)

# 3. stale heartbeat (past the 300s default cutoff), but host_pid confirmed alive -> active
_alive_pids = {222}
c.check(
    "stale heartbeat + live host_pid -> still active (the actual bug fix)",
    _session_really_active({"session_active": True, "last_seen_epoch": NOW - 600, "host_pid": 222}, NOW) is True,
)

# 4. stale heartbeat AND host_pid confirmed dead -> genuinely inactive
_alive_pids = set()
c.check(
    "stale heartbeat + dead host_pid -> inactive",
    _session_really_active({"session_active": True, "last_seen_epoch": NOW - 600, "host_pid": 222}, NOW) is False,
)

# 5. stale heartbeat, no host_pid recorded at all (pre-first-heartbeat session) -> inactive
_alive_pids = set()
c.check(
    "stale heartbeat + no host_pid -> inactive (timeout is the only signal available)",
    _session_really_active({"session_active": True, "last_seen_epoch": NOW - 600}, NOW) is False,
)

# 6. custom heartbeat_cutoff_s is honored
_alive_pids = set()
c.check(
    "custom cutoff: a gap just inside a widened cutoff still counts as recent",
    _session_really_active({"session_active": True, "last_seen_epoch": NOW - 100}, NOW, heartbeat_cutoff_s=60) is False,
)
c.check(
    "custom cutoff: a gap inside the widened cutoff counts as recent",
    _session_really_active({"session_active": True, "last_seen_epoch": NOW - 100}, NOW, heartbeat_cutoff_s=200) is True,
)

c.finish()
