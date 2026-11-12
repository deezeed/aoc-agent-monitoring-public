"""Auto-repair after an update: once per (version + hook sources) the
hooks-health worker runs the Health -> Repair steps by itself, but only
when an update made AOC's own hooks OUTDATED (a missing hook may have been
removed on purpose, a foreign statusLine is the user's own). The try is
spent before it runs, so nothing ever loops; a switch on the Health card
turns it off; a preview never does it (it shares the real ~/.claude).
setup.py is never run: _run_setup is a stub."""
import sys, os, json, tempfile, time

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions, read_monitor_source
from lib.check import Checker

c = Checker()
d = tempfile.mkdtemp()
state_file = os.path.join(d, "auto_repair.json")
H = {"checks": None, "at": 0.0, "error": "", "repairing": False}
ns = exec_functions(["_AUTO_REPAIR_IDS", "_auto_repair_targets", "_hook_sources_key", "_auto_repair_decide",
                     "_auto_repair_load", "_auto_repair_save", "_auto_repair_set", "_auto_repair_maybe",
                     "_repair_summary", "_hooks_health_bad", "_hooks_health_view"],
                    {"os": os, "json": json, "time": time, "_hooks_health": H, "AUTO_REPAIR_FILE": state_file,
                     "AOC_PREVIEW": False, "AOC_VERSION_FILE": os.path.join(d, "VERSION"),
                     "_read_installed_version": lambda p: "1.0.500", "_log_bg_error": lambda *a: None, "_orch_hooks_hint": lambda: ""})
targets, key_of, decide = ns["_auto_repair_targets"], ns["_hook_sources_key"], ns["_auto_repair_decide"]

chk = lambda i, ok=False, detail="": {"id": i, "label": i.upper(), "ok": ok, "detail": detail}
# which failures an automatic repair may touch
c.check("outdated hook scripts -> target", targets([chk("scripts", detail="aoc_permission.py outdated")]) == ["SCRIPTS"])
c.check("outdated held hook -> target", targets([chk("held_permissionrequest", detail="PermissionRequest hook outdated")]) == ["HELD_PERMISSIONREQUEST"])
c.check("outdated statusLine command -> target", targets([chk("statusline", detail="statusLine command outdated")]) == ["STATUSLINE"])
c.check("a missing hook is NOT (maybe removed on purpose)", targets([chk("danger", detail="PreToolUse Bash hook missing")]) == [])
c.check("a foreign statusLine is NOT (the user's own)", targets([chk("statusline", detail="statusLine is not AOC's")]) == [])
c.check("a scheduled task is NOT", targets([chk("task_watchdog", detail="not registered outdated")]) == [])
c.check("passing checks are not", targets([chk("scripts", ok=True, detail="outdated")]) == [])
c.check("junk tolerated", targets(None) == [] and targets(["x", None]) == [])

# the key: version + setup.py + hooks/*
app = os.path.join(d, "app")
os.makedirs(os.path.join(app, "hooks"))
open(os.path.join(app, "setup.py"), "w").write("x = 1\n")
open(os.path.join(app, "hooks", "aoc_hook.py"), "w").write("a = 1\n")
k1 = key_of("1.0.500", app)
c.check("key is stable", key_of("1.0.500", app) == k1 and len(k1) == 16)
c.check("a new version -> new key", key_of("1.0.501", app) != k1)
open(os.path.join(app, "hooks", "aoc_hook.py"), "a").write("b = 2\n")
k2 = key_of("1.0.500", app)
c.check("a changed hook source -> new key (dev checkout pull)", k2 != k1)
open(os.path.join(app, "hooks", "aoc_new.py"), "w").write("")
c.check("a new hook file -> new key", key_of("1.0.500", app) != k2)
c.check("missing hooks dir tolerated", len(key_of("", d)) == 16)

out = [chk("scripts", detail="aoc_permission.py outdated")]
c.check("decide: switched off", decide({"enabled": False}, "k", out) == "off")
c.check("decide: no check result yet", decide({}, "k", None) == "unknown")
c.check("decide: this key already tried", decide({"key": "k"}, "k", out) == "done")
c.check("decide: new key + outdated -> repair", decide({"key": "old"}, "k", out) == "repair")
c.check("decide: new key, nothing outdated", decide({}, "k", [chk("danger", detail="missing")]) == "nothing")

# the full step, setup.py stubbed
calls, hist, toasts = [], [], []
def runner(after, err=""):
    def run(flag):
        calls.append(flag)
        H.update({"checks": after, "error": err})
    return run
maybe = lambda run: ns["_auto_repair_maybe"](run=run, aoc_dir=app, history_add=hist.append,
                                             notify=lambda *a, **k: toasts.append((a, k)), now=100)
H["checks"] = out + [chk("danger", ok=True)]
r = maybe(runner([chk("scripts", ok=True), chk("danger", ok=True)]))
st = json.load(open(state_file))
c.check("outdated after an update -> runs --repair once", r == "repair" and calls == ["--repair"])
c.check("result remembered as source auto", st["last"]["state"] == "fixed" and st["last"]["source"] == "auto")
c.check("in-memory last_repair for the Health card", H["last_repair"]["source"] == "auto")
c.check("update history gets a 'repaired' entry", hist and hist[0]["outcome"] == "repaired" and hist[0]["fixed"] == ["SCRIPTS"])
c.check("fixed -> no toast", toasts == [])
H["checks"] = out
r = maybe(runner(out))
c.check("same key again -> never a second try (no loop)", r == "done" and calls == ["--repair"])

# a failed attempt: toast, history says so, still only once
os.remove(state_file); hist.clear()
H["checks"] = out
r = maybe(runner(out, err="TimeoutExpired"))
c.check("failed repair -> toast with the notify kind", toasts and toasts[0][1].get("kind") == "auto_repair")
c.check("failed repair -> history 'repair_failed'", hist[0]["outcome"] == "repair_failed" and hist[0]["error"] == "TimeoutExpired")
c.check("...and the key is spent", maybe(runner([])) == "done" and calls.count("--repair") == 2)

# the try is spent before it runs: a crash mid-repair doesn't repeat it
os.remove(state_file)
def crash(flag):
    raise RuntimeError("killed")
H["checks"] = out
try:
    maybe(crash)
except RuntimeError:
    pass
c.check("crash mid-repair -> key already saved, next round is 'done'", maybe(runner([])) == "done")

# nothing outdated: key recorded, no run
os.remove(state_file); calls.clear()
H["checks"] = [chk("danger", detail="hook missing")]
c.check("only a missing hook -> nothing, no run", maybe(runner([])) == "nothing" and calls == [])
c.check("...key recorded so a later run is 'done'", maybe(runner([])) == "done")

# switch + preview
ns["_auto_repair_set"](False)
c.check("switch off persists", json.load(open(state_file))["enabled"] is False)
c.check("view says off", ns["_hooks_health_view"]()["auto_repair"] is False)
os.remove(state_file)
c.check("default is on", ns["_hooks_health_view"]()["auto_repair"] is True)
ns["_auto_repair_set"](False)
H["checks"] = out
c.check("off -> no run", maybe(runner([])) == "off" and calls == [])
ns["_auto_repair_set"](True)
ns["AOC_PREVIEW"] = True
os.environ.pop("AOC_AUTO_REPAIR", None)
c.check("a preview never auto-repairs (shares the real ~/.claude)", maybe(runner([])) == "preview" and calls == [])
os.environ["AOC_AUTO_REPAIR"] = "1"
c.check("...unless AOC_AUTO_REPAIR=1 (sandboxed check)", maybe(runner([chk("scripts", ok=True)])) == "repair")
os.environ.pop("AOC_AUTO_REPAIR", None)

# wiring
src = read_monitor_source()
w = src[src.index("def _hooks_health_worker"):]
w = w[:w.index("threading.Thread(target=_hooks_health_worker")]
c.check("worker: check, then maybe repair, errors logged", w.index('_run_setup("--check")') < w.index("_auto_repair_maybe()") and "_log_bg_error" in w)
seg = src[src.index('elif path_no_qs == "/hooks_health/auto_repair":'):]
seg = seg[:seg.index("elif path_no_qs", 10)]
c.check("switch route: this PC only", "_is_direct_loopback()" in seg and "_auto_repair_set(" in seg)
c.check("auto_repair is a notification type", '"key": "auto_repair"' in src)
c.finish()
