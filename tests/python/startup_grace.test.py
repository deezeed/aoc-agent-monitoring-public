"""Tests _first_backup_delay, extracted straight from monitor.py. The first
~3 min after monitor.py starts, the machine is already saturated (boot,
watchdog restart) and /status took 1-15 s, so the full history.db backup
waits out _STARTUP_GRACE_S -- and after a restart within the hour it doesn't
re-copy the whole DB at all until today's snapshot is due again. Also checks
that the webhook worker's first _db_analytics() refresh is deferred the same
way (source-level, since that worker is one big loop)."""
import sys, os, re

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions, read_monitor_source
from lib.check import Checker

c = Checker()

ns = exec_functions(["_STARTUP_GRACE_S", "_BACKUP_INTERVAL_S", "_first_backup_delay"])
delay = ns["_first_backup_delay"]
grace = ns["_STARTUP_GRACE_S"]
interval = ns["_BACKUP_INTERVAL_S"]
now = 1_800_000_000.0

c.check("grace is a few minutes", 60 <= grace <= 600)
c.check("no snapshot today -> wait only the startup grace", delay(now, None) == grace)
c.check("stale snapshot (2 h old) -> startup grace", delay(now, now - 7200) == grace)
c.check("snapshot 58 min old (due in 2 min) -> still waits the full grace", delay(now, now - 3480) == grace)
c.check("snapshot 50 min old -> due in 10 min", abs(delay(now, now - 3000) - 600) < 1e-6)
c.check("fresh snapshot (10 min old) -> rest of the interval",
        abs(delay(now, now - 600) - (interval - 600)) < 1e-6)
c.check("snapshot written just now -> a full interval", abs(delay(now, now) - interval) < 1e-6)
c.check("mtime in the future (clock skew) -> capped at one interval", delay(now, now + 5000) == interval)

src = read_monitor_source()
c.check("backup worker sleeps before its first backup",
        re.search(r"def _backup_worker\(\):.*?time\.sleep\(_first_backup_delay\(.*?\)\)\s*\n\s*while True:\s*\n\s*_backup_history_db\(\)",
                  src, re.S) is not None)
c.check("first analytics refresh deferred by the startup grace",
        "_project_avg_cost_cache_at = time.time() - 300 + _STARTUP_GRACE_S" in src)

c.finish()
