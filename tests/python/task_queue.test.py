"""Task queue (start a session later): monitor.py's _queue_* functions --
limits gate, due order, one launch a minute, add validation, cancel/remove,
prune, and _queue_tick driving a fake _launch_session against a real
queue.json in a scratch AOC data dir."""
import sys, os, json, secrets, shutil, tempfile, threading, time

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
data_dir = tempfile.mkdtemp(prefix="aoc_q_")
proj = os.path.join(data_dir, "proj")
os.makedirs(proj)
toasts = []

ns = exec_functions(
    ["_load_json_file", "_LAUNCH_MODELS", "_launch_validate", "QUEUE_FILE", "_QUEUE_LIMIT_PCT", "_QUEUE_MIN_GAP_S",
     "_QUEUE_KEEP_DONE_S", "_QUEUE_MAX", "_queue_lock", "_queue_last_launch", "_queue_limits_ok", "_queue_waiting_on",
     "_queue_next_due", "_queue_prune", "_queue_load", "_queue_save", "_queue_add", "_queue_cancel", "_queue_view",
     "_queue_tick"],
    {"os": os, "json": json, "time": time, "threading": threading, "_secrets": secrets,
     "AOC_DATA_DIR": os.path.join(data_dir, "AOC"),
     "_show_native_toast": lambda t, m: toasts.append((t, m)),
     "_log_bg_error": lambda *a: None})

ok_view = {"windows": [{"kind": "five_hour", "pct": 40.0, "status": "ok"}, {"kind": "seven_day", "pct": 10.0, "status": "ok"}]}
hit_view = {"windows": [{"kind": "five_hour", "pct": 100.0, "status": "hit"}]}
high_view = {"windows": [{"kind": "seven_day", "pct": 96.0, "status": "warn"}]}
reset_view = {"windows": [{"kind": "five_hour", "pct": None, "status": "reset"}]}

# ── limits gate ──
lim = ns["_queue_limits_ok"]
c.check("no data -> ok", lim(None) and lim({}) and lim({"windows": []}))
c.check("normal usage -> ok", lim(ok_view))
c.check("hit -> not ok", not lim(hit_view))
c.check(">= 95 % -> not ok", not lim(high_view))
c.check("window just reset (no pct) -> ok", lim(reset_view))

# ── waiting / due ──
w = ns["_queue_waiting_on"]
c.check("future time -> waits for time", w({"at": 2000, "limits": True}, ok_view, 1000) == "time")
c.check("time passed + limits hit -> waits for limits", w({"at": 500, "limits": True}, hit_view, 1000) == "limits")
c.check("time passed, limits ignored -> due", w({"at": 500, "limits": False}, hit_view, 1000) == "")
c.check("limits only, ok -> due", w({"at": None, "limits": True}, ok_view, 1000) == "")
T = lambda id, created, at=None, limits=True, state="queued": {"id": id, "created": created, "at": at, "limits": limits, "state": state}
tasks = [T("b", 20), T("a", 10), T("c", 5, at=5000), T("d", 1, state="launched")]
due = ns["_queue_next_due"]
c.check("oldest due first, skips not-yet and finished", due(tasks, ok_view, 1000, 0)["id"] == "a")
c.check("timed task due -> by start time before created", due([T("x", 1, at=900), T("y", 0, at=950)], ok_view, 1000, 0)["id"] == "x")
c.check("nothing due while limits hit", due(tasks, hit_view, 1000, 0) is None)
c.check("one launch a minute", due(tasks, ok_view, 1000, 990) is None and due(tasks, ok_view, 1000, 939)["id"] == "a")

# ── prune ──
pr = ns["_queue_prune"]([T("q", 0), {**T("old", 0, state="launched"), "finished_at": 0},
                         {**T("new", 0, state="failed"), "finished_at": 90000}], 100000)
c.check("prune keeps queued + recent finished", [t["id"] for t in pr] == ["q", "new"])

# ── add ──
add = ns["_queue_add"]
c.check("needs a prompt", add({"cwd": proj, "prompt": " "}, now=1000)["ok"] is False)
c.check("needs an existing folder", add({"cwd": os.path.join(data_dir, "nope"), "prompt": "x"}, now=1000)["ok"] is False)
c.check("bad time", add({"cwd": proj, "prompt": "x", "at": "soon"}, now=1000)["error"] == "bad start time")
c.check("time in the past", add({"cwd": proj, "prompt": "x", "at": 100}, now=1000)["ok"] is False)
c.check("more than 14 days ahead", add({"cwd": proj, "prompt": "x", "at": 1000 + 15 * 86400}, now=1000)["ok"] is False)
c.check("no time and no limits -> nothing to wait for", "nothing to wait for" in add({"cwd": proj, "prompt": "x", "limits": False}, now=1000)["error"])
r1 = add({"cwd": proj, "prompt": "Refactor the parser", "model": "sonnet"}, now=1000)
c.check("limits task added", r1["ok"] and r1["task"]["at"] is None and r1["task"]["limits"] is True and r1["task"]["model"] == "sonnet")
r2 = add({"cwd": proj, "prompt": "Nightly cleanup", "at": 1000 + 3600, "limits": False}, now=1000)
c.check("timed task added", r2["ok"] and r2["task"]["at"] == 4600 and r2["task"]["limits"] is False)
c.check("saved to queue.json", len(ns["_queue_load"]()) == 2)

v = ns["_queue_view"](hit_view, 1000)
c.check("view: both queued, what they wait for",
        [(t["id"], t["waiting_on"]) for t in v] == [(r1["task"]["id"], "limits"), (r2["task"]["id"], "time")])

# ── tick ──
launched = []


def fake_launch(data, now=None):
    launched.append(data)
    return {"ok": True, "pid": 4242, "cwd": data["cwd"]}


c.check("tick: limits hit -> nothing started", ns["_queue_tick"](now=1000, launch=fake_launch, view=hit_view) is None and not launched)
t = ns["_queue_tick"](now=1100, launch=fake_launch, view=ok_view)
c.check("tick: limits back -> the limits task starts", t and t["id"] == r1["task"]["id"] and t["state"] == "launched" and t["pid"] == 4242)
c.check("launch got folder, prompt, model", launched[-1] == {"cwd": proj, "prompt": "Refactor the parser", "model": "sonnet"})
c.check("toast says it started", toasts and toasts[-1][0].startswith("▶ Queued task started"))
c.check("state persisted", [x["state"] for x in ns["_queue_load"]() if x["id"] == r1["task"]["id"]] == ["launched"])
c.check("timed task waits for its time even with limits ignored", ns["_queue_tick"](now=4000, launch=fake_launch, view=hit_view) is None)
t = ns["_queue_tick"](now=4700, launch=fake_launch, view=hit_view)
c.check("timed task with limits off starts at its time even at 100 %", t and t["id"] == r2["task"]["id"])

r3 = add({"cwd": proj, "prompt": "x"}, now=5000)
ns["_queue_tick"](now=5000, launch=lambda d, now=None: {"ok": False, "error": "one launch every few seconds, please"}, view=ok_view)
c.check("manual launch just ran -> stays queued, retried soon",
        [x["state"] for x in ns["_queue_load"]() if x["id"] == r3["task"]["id"]] == ["queued"]
        and ns["_queue_last_launch"][0] == 5000 - 60 + 10)
t = ns["_queue_tick"](now=5011, launch=lambda d, now=None: {"ok": False, "error": "Claude Code (claude) not found on this PC"}, view=ok_view)
c.check("launch error -> failed with the reason", t["state"] == "failed" and "not found" in t["error"])
c.check("toast says it failed", toasts[-1][0].startswith("⚠ Queued task failed"))

# ── cancel / remove ──
r4 = add({"cwd": proj, "prompt": "later"}, now=6000)
c.check("cancel a queued task", ns["_queue_cancel"](r4["task"]["id"], now=6001)
        and [x["state"] for x in ns["_queue_load"]() if x["id"] == r4["task"]["id"]] == ["cancelled"])
c.check("cancelled never starts", ns["_queue_tick"](now=7000, launch=fake_launch, view=ok_view) is None)
c.check("x on a finished task removes it", ns["_queue_cancel"](r4["task"]["id"], now=6002)
        and all(x["id"] != r4["task"]["id"] for x in ns["_queue_load"]()))
c.check("unknown id -> False", ns["_queue_cancel"]("nope") is False)

ns["_queue_save"]([{"id": f"t{i}", "state": "queued", "created": 1, "cwd": proj, "prompt": "x"} for i in range(50)])
c.check("queue full at 50", "full" in add({"cwd": proj, "prompt": "x"}, now=8000)["error"])
with open(ns["QUEUE_FILE"], "w") as f:
    f.write("{broken")
c.check("broken queue.json -> empty, no crash", ns["_queue_load"]() == [] and ns["_queue_view"](ok_view, 1) == [])

shutil.rmtree(data_dir, ignore_errors=True)
c.finish()
