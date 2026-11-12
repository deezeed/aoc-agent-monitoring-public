"""Repair result: monitor.py's _repair_summary (pure) and _repair_now (POST
/hooks_health/repair) remember what one Repair click did -- fixed / partly /
nothing changed / failed / refused off this PC -- so the Health card can say
it plainly. setup.py is never run: _run_setup is a stub."""
import sys, os, time

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
import json, tempfile
ns = exec_functions(["_repair_summary", "_repair_now", "_hooks_health_view", "_hooks_health_bad", "_auto_repair_load"],
                    {"time": time, "os": os, "json": json, "AUTO_REPAIR_FILE": os.path.join(tempfile.mkdtemp(), "auto_repair.json"), "_hooks_health": {"checks": None, "at": 0.0, "error": "", "repairing": False},
                     "_orch_hooks_hint": lambda: ""})
summary = ns["_repair_summary"]

c.check("all fixed", summary(["A", "B"], [], "", 5) == {"at": 5, "state": "fixed", "fixed": ["A", "B"], "left": [], "error": ""})
c.check("partly fixed", summary(["A", "B"], ["B"], "", 5)["state"] == "partial")
r = summary(["A"], ["A"], "", 5)
c.check("nothing changed is its own state", r["state"] == "unchanged" and r["left"] == ["A"] and r["fixed"] == [])
c.check("error wins", summary(["A"], [], "boom", 5)["state"] == "failed")
c.check("None lists are tolerated", summary(None, None, "", 5)["state"] == "fixed")
c.check("a check that newly fails is left, not fixed", summary(["A"], ["A", "C"], "", 5)["left"] == ["A", "C"])

H = ns["_hooks_health"]
bad = lambda *ids: [{"id": i, "label": i, "ok": False, "detail": ""} for i in ids]
good = lambda *ids: [{"id": i, "label": i, "ok": True, "detail": ""} for i in ids]
calls = []

def fake_setup(flag, after):
    def run(f):
        calls.append(f)
        H.update({"checks": after, "error": ""})
    return run

H["checks"] = bad("scripts") + good("danger")
ns["_run_setup"] = fake_setup("--repair", good("scripts", "danger"))
v = ns["_repair_now"](True)
c.check("on this PC: runs setup.py --repair", calls == ["--repair"])
c.check("view carries the result: fixed scripts", v["last_repair"]["state"] == "fixed" and v["last_repair"]["fixed"] == ["scripts"])
c.check("view bad list is empty after", v["bad"] == [])

calls.clear()
H["checks"] = bad("scripts")
ns["_run_setup"] = fake_setup("--repair", bad("scripts"))
v = ns["_repair_now"](True)
c.check("ran but nothing changed -> 'unchanged'", v["last_repair"]["state"] == "unchanged" and v["last_repair"]["left"] == ["scripts"])

calls.clear()
v = ns["_repair_now"](False)
c.check("off this PC: setup.py not run", calls == [])
c.check("off this PC: refusal remembered", v["last_repair"]["state"] == "failed" and "this PC" in v["last_repair"]["error"])
c.check("GET view keeps the last result", ns["_hooks_health_view"]()["last_repair"]["state"] == "failed")

c.finish()
