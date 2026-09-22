"""Tests _in_quiet_hours and _notification_suppressed, extracted straight
from monitor.py."""
import sys, os
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

_notify_settings = {"quiet_start": "", "quiet_end": "", "muted_projects": []}
ns = exec_functions(["_in_quiet_hours", "_notification_suppressed"], {
    "_notify_settings": _notify_settings,
    "datetime": datetime,
})
_notification_suppressed = ns["_notification_suppressed"]


class FakeDatetime(datetime):
    _fixed = None
    @classmethod
    def now(cls, tz=None):
        return cls._fixed


def set_now(h, m):
    FakeDatetime._fixed = datetime(2026, 1, 1, h, m)
    ns["datetime"] = FakeDatetime


c = Checker()

_notify_settings["quiet_start"] = ""
_notify_settings["quiet_end"] = ""
_notify_settings["muted_projects"] = []
set_now(3, 0)
c.check("no config -> not suppressed", _notification_suppressed("AnyProject") is False)

_notify_settings["muted_projects"] = ["MutedProj"]
set_now(15, 0)
c.check("muted project -> suppressed regardless of time", _notification_suppressed("MutedProj") is True)
c.check("other project -> not suppressed", _notification_suppressed("OtherProj") is False)

_notify_settings["muted_projects"] = []
_notify_settings["quiet_start"] = "09:00"
_notify_settings["quiet_end"] = "17:00"
set_now(12, 0)
c.check("normal range: inside -> suppressed", _notification_suppressed("P") is True)
set_now(8, 59)
c.check("normal range: just before start -> not suppressed", _notification_suppressed("P") is False)
set_now(17, 0)
c.check("normal range: at end (exclusive) -> not suppressed", _notification_suppressed("P") is False)

_notify_settings["quiet_start"] = "22:00"
_notify_settings["quiet_end"] = "07:00"
set_now(23, 30)
c.check("overnight range: 23:30 -> suppressed", _notification_suppressed("P") is True)
set_now(3, 0)
c.check("overnight range: 03:00 -> suppressed", _notification_suppressed("P") is True)
set_now(12, 0)
c.check("overnight range: daytime -> not suppressed", _notification_suppressed("P") is False)

c.finish()
