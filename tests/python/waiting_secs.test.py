"""Tests _compute_waiting_secs, extracted straight from monitor.py. This
is the "waiting too long" nudge's core computation: how long a session
has been sitting in waiting_on_you state, derived from last_seen_epoch
with no new tracking field (the Stop heartbeat that flips waiting_on_you
to True is the same event that last refreshed last_seen_epoch)."""
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ns = exec_functions(["_compute_waiting_secs"])
_compute_waiting_secs = ns["_compute_waiting_secs"]

c = Checker()
NOW = 1_000_000.0

# 1. not waiting -> always 0, regardless of last_seen_epoch
c.check(
    "waiting_on_you=False -> 0 even with a huge last_seen gap",
    _compute_waiting_secs({"waiting_on_you": False, "last_seen_epoch": NOW - 99999}, NOW) == 0,
)
c.check(
    "waiting_on_you missing entirely -> 0",
    _compute_waiting_secs({"last_seen_epoch": NOW - 500}, NOW) == 0,
)

# 2. waiting, recent last_seen -> small waiting_secs
c.check(
    "waiting just started (last_seen 30s ago) -> ~30",
    _compute_waiting_secs({"waiting_on_you": True, "last_seen_epoch": NOW - 30}, NOW) == 30,
)

# 3. waiting for a long time -> large waiting_secs
c.check(
    "waiting for 2h -> 7200",
    _compute_waiting_secs({"waiting_on_you": True, "last_seen_epoch": NOW - 7200}, NOW) == 7200,
)

# 4. waiting with no last_seen_epoch recorded at all -> falls back to now (0), doesn't crash
c.check(
    "waiting=True but no last_seen_epoch -> 0 (defensive fallback, not a crash)",
    _compute_waiting_secs({"waiting_on_you": True}, NOW) == 0,
)

c.finish()
