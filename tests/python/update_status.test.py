"""Health -> VERSION & UPDATES: the update history that survives the
restarts an update causes (_update_history_add/_read, a failure written by
_update_job_set, a version change by _version_change_entry at the new
version's first start) and the installer log tail (_install_log_tail).
Extracted straight from monitor.py; files live in a temp dir."""
import sys, os, json, tempfile, threading, time

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions, read_monitor_source
from lib.check import Checker

c = Checker()
d = tempfile.mkdtemp(prefix="aoc_updhist_")
errors = []
ns = exec_functions(
    ["_UPDATE_TRANSITIONS", "_update_job_next", "_update_job_set", "UPDATE_HISTORY_FILE", "_UPDATE_HISTORY_KEEP",
     "_update_history_add", "_update_history_read", "_version_change_entry", "_install_log_tail"],
    {"os": os, "json": json, "time": time, "AOC_DATA_DIR": d, "UPDATE_DIR": os.path.join(d, "update"),
     "AOC_VERSION_FILE": os.path.join(d, "VERSION"), "_update_job": {"state": "idle", "pct": 0, "version": "", "error": ""},
     "_update_job_lock": threading.Lock(), "_read_installed_version": lambda p: "1.0.374",
     "_log_bg_error": lambda *a: errors.append(a)})

# history file
c.check("no history yet -> []", ns["_update_history_read"]() == [])
ns["_update_history_add"]({"outcome": "updated", "from": "1.0.360", "to": "1.0.374", "via": "one-click update"}, now=100)
ns["_update_history_add"]({"outcome": "failed", "to": "1.0.380", "error": "checksum mismatch"}, now=200)
with open(ns["UPDATE_HISTORY_FILE"], "a", encoding="utf-8") as f:
    f.write("{nope\n")
h = ns["_update_history_read"]()
c.check("newest first, broken line skipped", [x["t"] for x in h] == [200, 100] and h[0]["outcome"] == "failed")
keep = ns["_UPDATE_HISTORY_KEEP"]
p = os.path.join(d, "trim.jsonl")
for i in range(2 * keep + 1):
    ns["_update_history_add"]({"n": i}, path=p, now=i)
lines = open(p, encoding="utf-8").read().splitlines()
c.check("trimmed to the newest KEEP", len(lines) == keep and json.loads(lines[-1])["n"] == 2 * keep)
c.check("unwritable path is logged, not raised", ns["_update_history_add"]({"x": 1}, path=d) is None)  # a directory: open() fails
errors.clear()

# a failure through the job state machine lands in the history
os.remove(ns["UPDATE_HISTORY_FILE"])
ns["_update_job_set"]("start", version="1.0.380")
ns["_update_job_set"]("fail", error="the installer did not restart AOC")
h = ns["_update_history_read"]()
c.check("failed update recorded with from/to/error",
        len(h) == 1 and h[0]["outcome"] == "failed" and h[0]["from"] == "1.0.374" and h[0]["to"] == "1.0.380"
        and "did not restart" in h[0]["error"])
ns["_update_job_set"]("progress", pct=5)
c.check("other events don't write history", len(ns["_update_history_read"]()) == 1)

# version change at startup
v = ns["_version_change_entry"]
c.check("first install -> nothing", v({}, "1.0.374") is None and v(None, "1.0.374") is None)
c.check("same version -> nothing", v({"seen": "1.0.374"}, "1.0.374") is None)
c.check("dev tree -> nothing", v({"seen": "1.0.360"}, "") is None)
c.check("staged by the updater -> one-click update",
        v({"seen": "1.0.360", "pending": {"version": "1.0.374", "items": []}}, "1.0.374") ==
        {"outcome": "updated", "from": "1.0.360", "to": "1.0.374", "via": "one-click update"})
c.check("no matching pending -> installed by hand",
        v({"seen": "1.0.360", "pending": {"version": "1.0.370"}}, "1.0.374")["via"] == "installed by hand")

# installer log tail
c.check("no log -> {}", ns["_install_log_tail"](path=os.path.join(d, "none.log")) == {})
lp = os.path.join(d, "install.log")
with open(lp, "w", encoding="utf-8") as f:
    f.write("".join(f"line {i}\n" for i in range(100)))
t = ns["_install_log_tail"](path=lp, lines=3)
c.check("tail: last lines + path + mtime", t["text"] == "line 97\nline 98\nline 99\n" and t["path"] == lp and t["mtime"] > 0)

src = read_monitor_source()
c.check("startup records the version change", "change = _version_change_entry(prev, installed)" in src)
c.check("Health asks /update/status", "fetch('/update/status')" in src)
c.check("no logging errors", errors == [])
c.finish()
