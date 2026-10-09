"""monitor.py's context meter backend: _context_view (saved statusline file ->
/status 'context' dict, levels ok/warn/high, junk rejected) and
_context_snapshot (per-session files, re-read on mtime change, unsafe ids
ignored, files idle > 3 days pruned)."""
import sys, os, json, shutil, tempfile, threading, time

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
work = tempfile.mkdtemp(prefix="aoc_ctx_")


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


ns = exec_functions(["_context_view", "_context_snapshot"], {
    "os": os, "time": time, "CONTEXT_DIR": work, "_CONTEXT_PRUNE_AFTER_S": 3 * 86400,
    "_context_lock": threading.Lock(), "_context_state": {"files": {}, "pruned_at": 0.0},
    "_load_json_file": load_json,
})
view, snap = ns["_context_view"], ns["_context_snapshot"]

v = view({"used_percentage": 41.0, "window": 1000000, "input_tokens": 412500,
          "cache_expires_at": 123, "cache_ttl": "1h", "recache_tokens": 400000, "updated_at": 5})
c.check("view: fields", v == {"pct": 41.0, "window": 1000000, "tokens": 412500, "level": "ok",
                              "cache_expires_at": 123, "cache_ttl": "1h", "recache_tokens": 400000, "updated_at": 5})
c.check("view: 70 % -> warn", view({"used_percentage": 70, "window": 200000})["level"] == "warn")
c.check("view: 85 % -> high", view({"used_percentage": 85, "window": 200000})["level"] == "high")
c.check("view: clamps > 100", view({"used_percentage": 140, "window": 200000})["pct"] == 100.0)
c.check("view: junk -> None", view(None) is None and view({"used_percentage": "x", "window": 1}) is None
        and view({"used_percentage": 5, "window": 0}) is None)
c.check("view: bad cache fields -> None", view({"used_percentage": 5, "window": 9, "cache_ttl": "9y",
                                                "cache_expires_at": "soon"})["cache_ttl"] is None)


def write(sid, pct, age=0):
    p = os.path.join(work, sid + ".json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"used_percentage": pct, "window": 200000, "input_tokens": int(pct * 2000)}, f)
    if age:
        t = time.time() - age
        os.utime(p, (t, t))
    return p


write("a-1", 10)
write("b-2", 90)
s = snap(["a-1", "b-2", "missing"])
c.check("snapshot: only sessions with files", set(s) == {"a-1", "b-2"} and s["b-2"]["level"] == "high")
p = write("a-1", 50)
t = time.time() + 5
os.utime(p, (t, t))
c.check("snapshot: re-read after mtime change", snap(["a-1"])["a-1"]["pct"] == 50.0)
c.check("snapshot: unsafe id ignored", snap(["..\\b-2", "../b-2", ""]) == {})
old = write("old-9", 20, age=4 * 86400)
ns["_context_state"]["pruned_at"] = 0.0
snap([])
c.check("snapshot: files idle > 3 days pruned", not os.path.exists(old) and os.path.exists(os.path.join(work, "a-1.json")))
with open(os.path.join(work, "bad-1.json"), "w") as f:
    f.write("{nope")
c.check("snapshot: corrupt file -> skipped", "bad-1" not in snap(["bad-1"]))

shutil.rmtree(work, ignore_errors=True)
c.finish()
