"""Tests that _show_native_toast() now logs when it fails, in both
watchdog.py and sentinel.py. Previously a bare `except: pass` -- if the
one channel these scripts have for surfacing a monitor.py outage to the
user (a Windows toast) itself broke (e.g. powershell missing, WinRT
unavailable), nothing would ever record that the toast never fired."""
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

AOC_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
WATCHDOG_PATH = os.path.join(AOC_DIR, "watchdog.py")
SENTINEL_PATH = os.path.join(AOC_DIR, "sentinel.py")

c = Checker()


class FakeSubprocess:
    CREATE_NO_WINDOW = 0x08000000

    @staticmethod
    def Popen(*a, **kw):
        raise OSError("powershell.exe not found")


for label, path in (("watchdog.py", WATCHDOG_PATH), ("sentinel.py", SENTINEL_PATH)):
    logs = []

    def fake_log(msg, _logs=logs):
        _logs.append(msg)

    ns = exec_functions(["_show_native_toast"], {
        "os": os,
        "sys": sys,
        "subprocess": FakeSubprocess(),
        "_log": fake_log,
        "_AOC_AUMID": "AOC.AgentOperationsCenter",
        "_AOC_SHORTCUT_NAME": "AOC Agent Operations Center.lnk",
    }, path=path)

    ns["_show_native_toast"]("Title", "Message")
    c.check(f"{label}: logs when the toast fails to show",
            any("Failed to show toast" in m and "powershell.exe not found" in m for m in logs))

c.finish()
