"""Tests _kill()'s new observability: previously, if a monitor.py process
survived terminate() + kill() + taskkill /F entirely, _kill() returned
silently -- watchdog.log would show "Restarting monitor..." and then
nothing, with no indication the restart never actually happened. Extracted
straight from watchdog.py."""
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

WATCHDOG_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "watchdog.py"))

c = Checker()

logs = []


def fake_log(msg):
    logs.append(msg)


class FakeTime:
    def sleep(self, s):
        pass  # no real waiting in tests


class FakeCompletedProcess:
    returncode = 0


class FakeSubprocess:
    CREATE_NO_WINDOW = 0x08000000

    @staticmethod
    def run(*a, **kw):
        return FakeCompletedProcess()


class FakeProc:
    """poll() returns None (still alive) until `dies_after` calls have been
    made, then reports dead. dies_after=None means it never dies, no matter
    what _kill() tries -- the case that used to vanish silently."""
    def __init__(self, pid=4242, dies_after=None):
        self.pid = pid
        self._polls = 0
        self.dies_after = dies_after

    def poll(self):
        self._polls += 1
        if self.dies_after is not None and self._polls >= self.dies_after:
            return 0
        return None

    def terminate(self):
        pass

    def kill(self):
        pass


ns = exec_functions(["_kill"], {
    "subprocess": FakeSubprocess(),
    "time": FakeTime(),
    "sys": sys,
    "_log": fake_log,
}, path=WATCHDOG_PATH)
_kill = ns["_kill"]

# Case 1: process outlives terminate, kill, AND taskkill -- must now log it.
proc = FakeProc(dies_after=None)
_kill(proc)
c.check("logs when the process survives terminate/kill/taskkill entirely",
        any("Could not kill" in m and "4242" in m for m in logs))

logs.clear()

# Case 2: process dies partway through the terminate-wait loop -- normal
# case, must NOT produce a failure log.
proc2 = FakeProc(pid=99, dies_after=3)
_kill(proc2)
c.check("does not log a failure when the process dies during the terminate loop",
        not any("Could not kill" in m for m in logs))

logs.clear()

# Case 3: proc already dead on entry -- _kill returns immediately, no log at all.
proc3 = FakeProc(pid=7, dies_after=1)
_kill(proc3)
c.check("no log emitted when the process was already dead on entry",
        len(logs) == 0)

c.finish()
