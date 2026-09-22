"""Tests _clamp_backup_retention_days, extracted straight from monitor.py.
Guards _backup_history_db's pruning loop: a malformed/absent value must
never be able to delete everything (0 or negative) or effectively disable
pruning entirely (a typo'd huge number)."""
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ns = exec_functions(["_clamp_backup_retention_days"])
_clamp_backup_retention_days = ns["_clamp_backup_retention_days"]

c = Checker()

c.check("valid value passes through unchanged", _clamp_backup_retention_days(30) == 30)
c.check("default 14 passes through unchanged", _clamp_backup_retention_days(14) == 14)
c.check("zero clamps up to 1 (never delete everything)", _clamp_backup_retention_days(0) == 1)
c.check("negative clamps up to 1", _clamp_backup_retention_days(-5) == 1)
c.check("huge value clamps down to 365", _clamp_backup_retention_days(99999) == 365)
c.check("exactly 365 passes through unchanged", _clamp_backup_retention_days(365) == 365)
c.check("exactly 1 passes through unchanged", _clamp_backup_retention_days(1) == 1)
c.check("None falls back to 14", _clamp_backup_retention_days(None) == 14)
c.check("non-numeric string falls back to 14", _clamp_backup_retention_days("abc") == 14)
c.check("numeric string coerces via int()", _clamp_backup_retention_days("30") == 30)
c.check("float value coerces via int() (truncates)", _clamp_backup_retention_days(30.9) == 30)

c.finish()
