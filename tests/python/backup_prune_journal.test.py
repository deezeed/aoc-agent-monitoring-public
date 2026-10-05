"""Runs _backup_history_db, extracted straight from monitor.py, against a
temp dir. Its prune loop only globbed history_*.db, so a history_*.db-journal
left behind by an interrupted backup was never deleted (backups/ still had
July/August journals in October). Old journals must go, recent ones and
corrupt-backup evidence must stay, and normal .db pruning must still work."""
import glob, os, sqlite3, sys, tempfile, threading, time
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

tmp = tempfile.mkdtemp()
src_db = os.path.join(tmp, "src.db")
conn = sqlite3.connect(src_db)
conn.execute("CREATE TABLE t (x)")
conn.commit()
conn.close()

errors = []
ns = exec_functions(["_backup_history_db"], extra_globals={
    "os": os, "glob": glob, "time": time, "sqlite3": sqlite3, "datetime": datetime,
    "AOC_DIR": tmp, "_db_lock": threading.Lock(),
    "_db_conn": lambda: sqlite3.connect(src_db),
    "_BACKUP_RETENTION_DAYS": 14, "_notify_settings": {"backup_retention_days": 14},
    "_show_native_toast": lambda *a: None,
    "_log_bg_error": lambda where, e: errors.append((where, e)),
})

bdir = os.path.join(tmp, "backups")
os.makedirs(bdir)
old = time.time() - 40 * 86400
recent = time.time() - 2 * 86400


def touch(name, mtime):
    p = os.path.join(bdir, name)
    open(p, "wb").close()
    os.utime(p, (mtime, mtime))
    return p


old_db = touch("history_2026-07-01.db", old)
old_journal = touch("history_2026-07-22.db-journal", old)
recent_journal = touch("history_2026-10-03.db-journal", recent)
recent_db = touch("history_2026-10-03.db", recent)
corrupt = touch("history_2026-07-02_CORRUPT.db", old)
corrupt_journal = touch("history_2026-07-02_CORRUPT.db-journal", old)

ns["_backup_history_db"]()

c = Checker()
c.check("no background error", not errors)
c.check("today's backup written", os.path.exists(os.path.join(bdir, f"history_{datetime.now():%Y-%m-%d}.db")))
c.check("old .db pruned", not os.path.exists(old_db))
c.check("old .db-journal pruned", not os.path.exists(old_journal))
c.check("recent .db-journal kept", os.path.exists(recent_journal))
c.check("recent .db kept", os.path.exists(recent_db))
c.check("old _CORRUPT.db kept as evidence", os.path.exists(corrupt))
c.check("old _CORRUPT.db-journal kept as evidence", os.path.exists(corrupt_journal))
c.finish()
