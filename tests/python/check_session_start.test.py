"""Tests _check_session_start, extracted straight from monitor.py. Small,
but real state-transition logic with zero prior coverage: it's the one
place SESSION_START gets logged, gated on an in-memory previous-state
flag so a re-poll of an already-active session doesn't refire it on
every single call."""
import sys, os, threading

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker


class _FakeLog:
    def __init__(self):
        self.session_start_calls = 0

    def session_start(self, status):
        self.session_start_calls += 1


def make_ns():
    fake_log = _FakeLog()
    ns = exec_functions(["_check_session_start"], {
        "threading": threading,
        "_log": fake_log,
        "_prev_session_active": False,
        "_prev_session_active_lock": threading.Lock(),
    })
    return ns, fake_log


c = Checker()

# 1. False -> True transition fires session_start exactly once
ns, log = make_ns()
ns["_check_session_start"]({"session_active": True})
c.check("newly-active session fires session_start", log.session_start_calls == 1)

# 2. Staying True on repeated calls does not refire
ns["_check_session_start"]({"session_active": True})
ns["_check_session_start"]({"session_active": True})
c.check("repeated True calls do not refire session_start", log.session_start_calls == 1)

# 3. Starting from an already-active state (module-level flag already
# True, e.g. process restarted mid-session) does NOT fire on the first
# call -- only a genuine False->True edge should.
ns2 = exec_functions(["_check_session_start"], {
    "threading": threading,
    "_log": _FakeLog(),
    "_prev_session_active": True,
    "_prev_session_active_lock": threading.Lock(),
})
log2 = ns2["_log"]
ns2["_check_session_start"]({"session_active": True})
c.check("starting from an already-True state does not fire (no edge)", log2.session_start_calls == 0)

# 4. True -> False -> True fires again on the second rising edge
ns3, log3 = make_ns()
ns3["_check_session_start"]({"session_active": True})
ns3["_check_session_start"]({"session_active": False})
ns3["_check_session_start"]({"session_active": True})
c.check("a second rising edge after going inactive fires session_start again", log3.session_start_calls == 2)

# 5. Missing/falsy session_active key never fires
ns4, log4 = make_ns()
ns4["_check_session_start"]({})
c.check("missing session_active key (falsy) never fires", log4.session_start_calls == 0)

c.finish()
