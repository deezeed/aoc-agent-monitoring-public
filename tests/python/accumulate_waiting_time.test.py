"""Tests _accumulate_waiting_time, extracted straight from monitor.py.
waiting_on_you/waiting_secs were always live-only (recomputed from
last_seen_epoch on every /status poll) -- nothing persisted how long a
session had spent waiting across its whole lifetime, only "how long has
it been waiting right now". This is the state-machine piece that makes
the WAITING ON YOU history trend possible: called every time either code
path that sets waiting_on_you (the /update heartbeat, and the
transcript-scanner fallback) is about to change it."""
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ns = exec_functions(["_accumulate_waiting_time"])
_accumulate_waiting_time = ns["_accumulate_waiting_time"]

c = Checker()

# 1. False -> True transition: starts the clock, no accumulation yet
sess = {"waiting_on_you": False}
_accumulate_waiting_time(sess, True, 1000.0)
c.check("starting to wait records the start time", sess["waiting_on_you_since"] == 1000.0)
c.check("starting to wait doesn't accumulate anything yet", sess.get("waiting_on_you_accum_s", 0) == 0)

# 2. True -> False transition: commits the elapsed time, clears the clock
sess["waiting_on_you"] = True  # simulate the caller having applied the new value after step 1
_accumulate_waiting_time(sess, False, 1090.0)
c.check("ending a wait accumulates the elapsed seconds (1090-1000=90)", sess["waiting_on_you_accum_s"] == 90)
c.check("ending a wait clears waiting_on_you_since", sess["waiting_on_you_since"] is None)

# 3. Same-value updates (the common case, most heartbeats) are no-ops
sess["waiting_on_you"] = False
before = dict(sess)
_accumulate_waiting_time(sess, False, 2000.0)
c.check("False->False is a no-op", sess == before)

sess2 = {"waiting_on_you": True, "waiting_on_you_since": 500.0, "waiting_on_you_accum_s": 30}
before2 = dict(sess2)
_accumulate_waiting_time(sess2, True, 2000.0)
c.check("True->True is a no-op (doesn't reset the start time or double-count)", sess2 == before2)

# 4. A second wait period accumulates on top of the first, doesn't overwrite
# it. The real caller (see /update's handler) always applies the new value
# to sess["waiting_on_you"] itself right after calling this helper -- this
# function only tracks the *transition*, it never writes waiting_on_you.
sess3 = {"waiting_on_you": False, "waiting_on_you_accum_s": 90}
_accumulate_waiting_time(sess3, True, 5000.0)
sess3["waiting_on_you"] = True
_accumulate_waiting_time(sess3, False, 5030.0)
sess3["waiting_on_you"] = False
c.check("a second wait period adds to the running total (90+30=120)", sess3["waiting_on_you_accum_s"] == 120)

# 5. Ending a wait with no recorded start (defensive: shouldn't happen in
# practice, but a corrupted/partial sess dict must not crash or fabricate
# a bogus elapsed time)
sess4 = {"waiting_on_you": True}  # waiting_on_you_since missing entirely
_accumulate_waiting_time(sess4, False, 9999.0)
c.check("ending a wait with no recorded start doesn't crash or accumulate garbage",
        sess4.get("waiting_on_you_accum_s", 0) == 0)

# 6. new_waiting accepts a truthy/falsy value, not just a literal bool
# (mirrors how update.get("waiting_on_you") arrives straight from parsed
# JSON, which is already a real bool, but stats.get(...) in the
# transcript-scanner path is worth the same defensive coercion).
sess5 = {"waiting_on_you": False}
_accumulate_waiting_time(sess5, 1, 100.0)  # truthy non-bool
c.check("truthy non-bool new_waiting starts a wait period", sess5["waiting_on_you_since"] == 100.0)

c.finish()
