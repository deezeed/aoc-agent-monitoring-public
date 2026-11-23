"""Backups beyond history.db (audit FIND-009 / OPP-004), real functions from
server/02 + 17 against temp dirs: _backup_state snapshots orchestration.db
and zips the JSON settings (never a secret), _list_backups lists all three
kinds, the prune keeps the same retention for them, and /restore_backup
puts each kind back -- orchestration only while nothing runs, settings only
for listed names that parse as JSON -- each after a pre-restore copy."""
import sys, os, json, glob, sqlite3, tempfile, threading, time, zipfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
work = tempfile.mkdtemp(prefix="aoc-backup-state-")
AOC_DIR, DATA = os.path.join(work, "aoc"), os.path.join(work, "data")
os.makedirs(AOC_DIR)
os.makedirs(DATA)
ORCH_DB = os.path.join(DATA, "orchestration.db")
HIST_DB = os.path.join(DATA, "history.db")
bdir = os.path.join(AOC_DIR, "backups")
errors, toasts = [], []

NAMES = ["_BACKUP_RETENTION_DAYS", "_BACKUP_PATTERNS", "_BACKUP_STATE_FILES", "_BACKUP_STATE_AOC_FILES",
         "_snapshot_sqlite", "_backup_state", "_backup_history_db", "_list_backups", "_restore_backup",
         "_restore_state", "_get_diag_info", "_orch_restore_backup", "_orch_connect", "_ORCH_ACTIVE"]
ns = exec_functions(NAMES, {
    "os": os, "json": json, "glob": glob, "sqlite3": sqlite3, "time": time, "datetime": datetime,
    "threading": threading, "AOC_DIR": AOC_DIR, "AOC_DATA_DIR": DATA, "ORCH_DB": ORCH_DB, "DB_FILE": HIST_DB,
    "_PROCESS_START": time.time(), "_db_lock": threading.Lock(), "_orch_lock": threading.RLock(),
    "_db_conn": lambda: sqlite3.connect(HIST_DB), "_notify_settings": {"backup_retention_days": 14},
    "_show_native_toast": lambda *a, **k: toasts.append(a), "_log_bg_error": lambda w, e: errors.append((w, e)),
})
g = ns["_backup_state"].__globals__
for k, v in ns.items():
    g[k] = v
day = datetime.now().strftime("%Y-%m-%d")


def orch_rows(path=ORCH_DB):
    conn = sqlite3.connect(path)
    try:
        return [r[0] for r in conn.execute("SELECT goal FROM runs ORDER BY id")]
    finally:
        conn.close()


def write(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# --- nothing to back up yet: no orchestration.db, no settings -> no files, no error
ns["_backup_state"]()
c.check("no orchestration.db and no settings -> nothing written, no error",
        os.listdir(bdir) == [] and errors == [])

# --- live state
conn = ns["_orch_connect"]()
conn.execute("INSERT INTO runs (id, goal, state) VALUES ('r1', 'day-one run', 'merged')")
conn.execute("INSERT INTO agents (id, data, updated) VALUES ('a1', '{\"name\": \"Reviewer\"}', 1)")
conn.commit()
conn.close()
write(os.path.join(DATA, "users.json"), {"people": [{"name": "Maxim", "hash": "ab"}]})
write(os.path.join(DATA, "orch_settings.json"), {"house_rules": "v1"})
write(os.path.join(DATA, "prompts.json"), [{"title": "p1"}])
write(os.path.join(AOC_DIR, "aoc_notify_settings.json"), {"backup_retention_days": 14})
write(os.path.join(DATA, "push_vapid.json"), {"private_key": "SECRET"})
open(os.path.join(AOC_DIR, "aoc_token.txt"), "w").write("SECRET-TOKEN")
write(os.path.join(DATA, "ask_cache.json"), {"cache": 1})

ns["_backup_state"]()
ob = os.path.join(bdir, f"orchestration_{day}.db")
sz = os.path.join(bdir, f"state_{day}.zip")
c.check("orchestration_<day>.db written with the live runs + agent library",
        os.path.isfile(ob) and orch_rows(ob) == ["day-one run"]
        and sqlite3.connect(ob).execute("SELECT COUNT(*) FROM agents").fetchone()[0] == 1)
with zipfile.ZipFile(sz) as z:
    names = sorted(z.namelist())
c.check("state_<day>.zip holds exactly the listed settings that exist",
        names == ["aoc_notify_settings.json", "orch_settings.json", "prompts.json", "users.json"])
c.check("no secret (vapid key, token) and no cache in the zip",
        not {"push_vapid.json", "aoc_token.txt", "ask_cache.json"} & set(names))
c.check("no temp file left, no error, no toast",
        not glob.glob(os.path.join(bdir, "*.tmp")) and errors == [] and toasts == [])

# a second pass the same day overwrites in place (dated names)
write(os.path.join(DATA, "orch_settings.json"), {"house_rules": "v2"})
ns["_backup_state"]()
with zipfile.ZipFile(sz) as z:
    c.check("same day -> same file, newer content", json.loads(z.read("orch_settings.json")) == {"house_rules": "v2"}
            and len(glob.glob(os.path.join(bdir, "state_*.zip"))) == 1)

# --- listing + diag count
sqlite3.connect(HIST_DB).close()
ns["_backup_history_db"]()
lst = ns["_list_backups"]()
kinds = {b["filename"]: b["kind"] for b in lst}
c.check("listing: all three kinds, tagged", kinds == {f"history_{day}.db": "history",
                                                     f"orchestration_{day}.db": "orchestration",
                                                     f"state_{day}.zip": "state"})
c.check("diag counts every backup", ns["_get_diag_info"]()["backups_count"] == 3)

# --- retention prune covers the new kinds (run by _backup_history_db)
old = time.time() - 40 * 86400
for name in ("orchestration_2026-01-01.db", "state_2026-01-01.zip", "orchestration_2026-01-01.db-journal",
             "orchestration_2026-01-02_CORRUPT.db"):
    p = os.path.join(bdir, name)
    open(p, "wb").close()
    os.utime(p, (old, old))
ns["_backup_history_db"]()
left = set(os.listdir(bdir))
c.check("old orchestration/state snapshots + journal pruned",
        not {"orchestration_2026-01-01.db", "state_2026-01-01.zip", "orchestration_2026-01-01.db-journal"} & left)
c.check("corrupt evidence kept, today's snapshots kept",
        {"orchestration_2026-01-02_CORRUPT.db", f"orchestration_{day}.db", f"state_{day}.zip"} <= left)
os.remove(os.path.join(bdir, "orchestration_2026-01-02_CORRUPT.db"))

# --- orchestration restore
conn = ns["_orch_connect"]()
conn.execute("UPDATE runs SET goal='damaged' WHERE id='r1'")
conn.execute("INSERT INTO runs (id, goal, state) VALUES ('r2', 'later run', 'running')")
conn.commit()
conn.close()
r = ns["_restore_backup"](f"orchestration_{day}.db")
c.check("restore refused while a run is running, live DB untouched",
        r.get("ok") is False and "busy" in r.get("error", "") and orch_rows() == ["damaged", "later run"])
conn = ns["_orch_connect"]()
conn.execute("UPDATE runs SET state='review' WHERE id='r2'")
conn.execute("INSERT INTO entities (id, data, job_pid) VALUES ('e1', '{}', 4242)")
conn.commit()
conn.close()
r = ns["_restore_backup"](f"orchestration_{day}.db")
c.check("restore refused while a scout (entity job) runs", r.get("ok") is False and "busy" in r.get("error", ""))
conn = ns["_orch_connect"]()
conn.execute("UPDATE entities SET job_pid=NULL")
conn.commit()
conn.close()
held = ns["_orch_connect"]()  # an open connection, like a request in flight
r = ns["_restore_backup"](f"orchestration_{day}.db")
c.check("idle -> restored", r.get("ok") is True)
c.check("live DB now has the backup's data", orch_rows() == ["day-one run"])
c.check("an already-open connection sees the restored data",
        [x[0] for x in held.execute("SELECT goal FROM runs")] == ["day-one run"])
held.close()
pre = os.path.join(bdir, r.get("pre_restore_backup") or "-")
c.check("pre-restore copy holds what was live", r.get("pre_restore_backup", "").startswith("orchestration_PRE_RESTORE_")
        and orch_rows(pre) == ["damaged", "later run"])
c.check("pre-restore copy is listed", r["pre_restore_backup"] in [b["filename"] for b in ns["_list_backups"]()])

bad = os.path.join(bdir, "orchestration_2026-02-01.db")
open(bad, "wb").write(b"not sqlite at all")
c.check("garbage file refused", ns["_restore_backup"]("orchestration_2026-02-01.db").get("ok") is False
        and orch_rows() == ["day-one run"])
other = os.path.join(bdir, "orchestration_2026-02-02.db")
sqlite3.connect(other).execute("CREATE TABLE t (x)").connection.close()
c.check("a healthy DB that isn't orchestration's refused",
        ns["_restore_backup"]("orchestration_2026-02-02.db").get("ok") is False)
c.check("path traversal / corrupt / missing refused",
        ns["_restore_backup"]("../orchestration_x.db").get("ok") is False
        and ns["_restore_backup"]("orchestration_2026-01-02_CORRUPT.db").get("ok") is False
        and ns["_restore_backup"]("orchestration_2099-01-01.db").get("ok") is False
        and ns["_restore_backup"]("notes_2026.db").get("ok") is False)

# --- settings restore
write(os.path.join(DATA, "orch_settings.json"), {"house_rules": "broken later"})
os.remove(os.path.join(DATA, "prompts.json"))
r = ns["_restore_backup"](f"state_{day}.zip")
c.check("settings restored, restart flagged", r.get("ok") is True and r.get("restart_needed") is True
        and r.get("restored") == ["aoc_notify_settings.json", "orch_settings.json", "prompts.json", "users.json"])
c.check("files have the backup's content (AOC_DIR one too)",
        read(os.path.join(DATA, "orch_settings.json")) == {"house_rules": "v2"}
        and read(os.path.join(DATA, "prompts.json")) == [{"title": "p1"}]
        and read(os.path.join(AOC_DIR, "aoc_notify_settings.json")) == {"backup_retention_days": 14})
with zipfile.ZipFile(os.path.join(bdir, r["pre_restore_backup"])) as z:
    c.check("pre-restore zip has the replaced file", json.loads(z.read("orch_settings.json"))
            == {"house_rules": "broken later"})

evil = os.path.join(bdir, "state_2026-03-01.zip")
with zipfile.ZipFile(evil, "w") as z:
    z.writestr("../evil.json", "{}")
    z.writestr("aoc_token.txt", "attacker")
    z.writestr("users.json", "{\"people\": []}")
r = ns["_restore_backup"]("state_2026-03-01.zip")
c.check("only listed names are written (no traversal, no token)", r.get("restored") == ["users.json"]
        and not os.path.exists(os.path.join(AOC_DIR, "evil.json")) and not os.path.exists(os.path.join(work, "evil.json"))
        and open(os.path.join(AOC_DIR, "aoc_token.txt")).read() == "SECRET-TOKEN")
broken = os.path.join(bdir, "state_2026-03-02.zip")
with zipfile.ZipFile(broken, "w") as z:
    z.writestr("users.json", "{\"people\": []}")
    z.writestr("prompts.json", "{not json")
before = read(os.path.join(DATA, "users.json"))
r = ns["_restore_backup"]("state_2026-03-02.zip")
c.check("a file that isn't JSON -> nothing restored at all", r.get("ok") is False
        and read(os.path.join(DATA, "users.json")) == before)
c.check("no background errors", errors == [])

import shutil
shutil.rmtree(work, ignore_errors=True)
c.finish()
