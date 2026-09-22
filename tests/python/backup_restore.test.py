"""Tests _list_backups and _restore_backup, extracted straight from
monitor.py. Runs entirely against a scratch temp directory -- never the
real AOC_DIR/DB_FILE."""
import sys, os, sqlite3, shutil, tempfile, threading
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_backup_restore_")
AOC_DIR = SCRATCH
DB_FILE = os.path.join(SCRATCH, "history.db")

ns = exec_functions(["_list_backups", "_restore_backup"], {
    "os": os, "sqlite3": sqlite3, "glob": __import__("glob"),
    "AOC_DIR": AOC_DIR, "DB_FILE": DB_FILE,
    "_db_lock": threading.Lock(), "datetime": datetime,
})
_list_backups = ns["_list_backups"]
_restore_backup = ns["_restore_backup"]

c = Checker()
os.makedirs(os.path.join(AOC_DIR, "backups"), exist_ok=True)


def make_db(path, rows):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE sessions (id TEXT, note TEXT)")
    conn.executemany("INSERT INTO sessions VALUES (?, ?)", rows)
    conn.commit()
    conn.close()


r = _restore_backup("../../etc/passwd")
c.check("path traversal rejected", r["ok"] is False and "invalid filename" in r["error"])

r = _restore_backup("not_a_history_file.db")
c.check("filename not starting with history_ rejected", r["ok"] is False)

r = _restore_backup("history_2026-01-01_CORRUPT.db")
c.check("_CORRUPT.db filename refused outright", r["ok"] is False and "integrity check" in r["error"])

r = _restore_backup("history_2099-01-01.db")
c.check("nonexistent backup file rejected", r["ok"] is False and "not found" in r["error"])

make_db(DB_FILE, [("current-session", "LIVE data before restore")])
backup_path = os.path.join(AOC_DIR, "backups", "history_2026-07-01.db")
make_db(backup_path, [("old-session", "BACKUP data")])

r = _restore_backup("history_2026-07-01.db")
c.check("restore reports ok", r.get("ok") is True)

conn = sqlite3.connect(DB_FILE)
rows = conn.execute("SELECT id, note FROM sessions").fetchall()
conn.close()
c.check("live DB now contains the backup's data", rows == [("old-session", "BACKUP data")])

pre_restore_path = os.path.join(AOC_DIR, "backups", r.get("pre_restore_backup", ""))
c.check("pre-restore safety copy file created", os.path.isfile(pre_restore_path))
pre_conn = sqlite3.connect(pre_restore_path)
pre_rows = pre_conn.execute("SELECT id, note FROM sessions").fetchall()
pre_conn.close()
c.check("pre-restore safety copy has the ORIGINAL data", pre_rows == [("current-session", "LIVE data before restore")])

listing = _list_backups()
names = [b["filename"] for b in listing]
c.check("listing includes the restored-from backup", "history_2026-07-01.db" in names)
c.check("listing includes the new pre-restore safety copy", r.get("pre_restore_backup") in names)

bad_backup_path = os.path.join(AOC_DIR, "backups", "history_2026-06-01.db")
with open(bad_backup_path, "wb") as f:
    f.write(b"not a real sqlite file at all")
r = _restore_backup("history_2026-06-01.db")
c.check("restoring a corrupt sqlite file fails integrity check cleanly", r.get("ok") is False)

shutil.rmtree(SCRATCH, ignore_errors=True)
c.finish()
