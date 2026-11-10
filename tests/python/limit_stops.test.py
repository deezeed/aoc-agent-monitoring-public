"""Stopped by the usage limit (monitor.py): _limit_stop_event reads the
synthetic "You've hit your limit" transcript line (and what clears it),
_limit_stops_apply/_prune/_view keep one record per session (dismissed /
continued stays so until a newer stop), _limit_stop_action resumes the
exact conversation (`claude --resume <id> "pokračuj"`) or queues that for
the reset (opt-in), refuses a session still open in its terminal, and
_queue_tick carries `resume` and won't fork an open session. Nothing is
really launched: a fake launcher / popen records what would run."""
import sys, os, json, re, secrets, tempfile, threading, time, calendar
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions, read_monitor_source
from lib.check import Checker

c = Checker()
data_dir = tempfile.mkdtemp(prefix="aoc_ls_")
proj = os.path.join(data_dir, "proj")
os.makedirs(proj)
launched, toasts, bumps = [], [], []
OPEN = set()

ns = exec_functions(
    ["_iso_to_epoch", "_load_json_file", "_LAUNCH_MODELS", "_LAUNCH_MIN_GAP_S", "_launch_last", "_launch_validate",
     "_launch_argv", "_folder_trusted", "_launch_session",
     "QUEUE_FILE", "_QUEUE_LIMIT_PCT", "_QUEUE_MIN_GAP_S", "_QUEUE_KEEP_DONE_S", "_QUEUE_MAX", "_queue_lock",
     "_queue_last_launch", "_queue_limits_ok", "_queue_waiting_on", "_queue_next_due", "_queue_prune", "_queue_load",
     "_queue_save", "_queue_add", "_queue_cancel", "_queue_view", "_queue_tick",
     "LIMIT_STOPS_FILE", "_LIMIT_CONTINUE_PROMPT", "_LIMIT_STOP_KEEP_S", "_LIMIT_STOP_MAX", "_limit_stops_lock",
     "_LOCAL_CMD_RE", "_limit_stop_event", "_limit_stops_apply", "_limit_stops_prune", "_limit_stops_view",
     "_limit_stops_load", "_limit_stops_save", "_limit_stops_changed", "_limit_stop_record", "_limit_stop_set_state",
     "_limit_stop_action"],
    {"os": os, "json": json, "re": re, "time": time, "threading": threading, "_secrets": secrets,
     "datetime": datetime, "calendar": calendar, "subprocess": None,
     "AOC_DATA_DIR": os.path.join(data_dir, "AOC"),
     "_find_claude_exe": lambda: os.path.join(data_dir, "claude.exe"),
     "_open_session_ids": lambda: set(OPEN),
     "_status_cond": threading.Condition(), "_status_version": [0],
     "_show_native_toast": lambda t, m, **k: toasts.append((t, m)),
     "_log_bg_error": lambda *a: None})

# ── the transcript line Claude Code writes (shape copied from a real one) ──
SID = "c63812a5-b725-4f08-8506-5e08b6124986"
stop_line = {"type": "assistant", "isSidechain": False, "isApiErrorMessage": True, "error": "rate_limit",
             "timestamp": "2026-11-01T18:07:01.241Z", "cwd": proj, "sessionId": SID,
             "quotaLimits": {"status": "rejected", "resetsAt": 1793595600, "rateLimitType": "seven_day"},
             "message": {"model": "<synthetic>", "role": "assistant",
                         "content": [{"type": "text", "text": "You've hit your weekly limit · resets 6am (Europe/Bratislava)"}]}}
ev = ns["_limit_stop_event"]
e, info = ev(stop_line)
c.check("limit line -> stop with kind, reset and when",
        e == "stop" and info == {"kind": "seven_day", "resets_at": 1793595600, "at": float(calendar.timegm((2026, 11, 1, 18, 7, 1, 0, 0, 0)))})
e, info = ev({**stop_line, "quotaLimits": None})
c.check("error rate_limit without quotaLimits -> stop, reset unknown", e == "stop" and info["resets_at"] is None and info["kind"] == "")
c.check("quotaLimits rejected without error field -> stop", ev({**stop_line, "error": None})[0] == "stop")
c.check("bool / negative resetsAt is not a time",
        ev({**stop_line, "quotaLimits": {"status": "rejected", "resetsAt": True}})[1]["resets_at"] is None
        and ev({**stop_line, "quotaLimits": {"status": "rejected", "resetsAt": -5}})[1]["resets_at"] is None)
c.check("allowed quota on a normal reply is no stop",
        ev({"type": "assistant", "quotaLimits": {"status": "allowed"}, "message": {"model": "claude-opus-5-5"}}) == ("clear", None))
c.check("a real reply clears", ev({"type": "assistant", "message": {"model": "claude-opus-5-5", "content": []}}) == ("clear", None))
c.check("another synthetic line doesn't clear", ev({"type": "assistant", "message": {"model": "<synthetic>"}}) == (None, None))
c.check("typed prompt (string) clears", ev({"type": "user", "message": {"content": "pokračuj"}}) == ("clear", None))
c.check("typed prompt (text block) clears", ev({"type": "user", "message": {"content": [{"type": "text", "text": "go on"}]}}) == ("clear", None))
c.check("tool result doesn't clear", ev({"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}) == (None, None))
c.check("/usage (local command) doesn't clear",
        ev({"type": "user", "message": {"content": "<command-name>/usage</command-name>"}}) == (None, None)
        and ev({"type": "user", "message": {"content": "<local-command-stdout>5h 100%</local-command-stdout>"}}) == (None, None))
c.check("meta / sidechain lines are ignored",
        ev({"type": "user", "isMeta": True, "message": {"content": "caveat"}}) == (None, None)
        and ev({"type": "assistant", "isSidechain": True, "message": {"model": "claude-opus-5-5"}}) == (None, None))
c.check("system lines / garbage -> nothing", ev({"type": "system"}) == (None, None) and ev("x") == (None, None) and ev(None) == (None, None))

# ── apply / prune ──
ap = ns["_limit_stops_apply"]
NOW = 1793590000.0
s1 = ap({}, SID, "stop", {"kind": "five_hour", "resets_at": NOW + 3600, "at": NOW - 60}, proj, "Shop", NOW)
c.check("first stop -> stopped record", s1[SID] == {"id": SID, "cwd": proj, "title": "Shop", "kind": "five_hour",
                                                   "resets_at": NOW + 3600, "at": NOW - 60, "state": "stopped", "task": None})
dis = {SID: {**s1[SID], "state": "dismissed"}}
c.check("same stop seen again (rescan) keeps dismissed", ap(dis, SID, "stop", {"kind": "five_hour", "resets_at": NOW + 3600, "at": NOW - 60}, proj, "", NOW)[SID]["state"] == "dismissed")
c.check("a NEWER stop re-arms it", ap(dis, SID, "stop", {"kind": "five_hour", "resets_at": NOW + 7200, "at": NOW}, proj, "", NOW)[SID]["state"] == "stopped")
c.check("rescan fills a missing title only", ap({SID: {**s1[SID], "title": ""}}, SID, "stop", {"at": NOW - 60}, proj, "Late title", NOW)[SID]["title"] == "Late title")
c.check("clear removes it, other sessions stay", ap({**s1, "other": {"id": "other"}}, SID, "clear", None, "", "", NOW) == {"other": {"id": "other"}})
c.check("input dict isn't mutated", SID in s1 and ap(s1, SID, "clear", None, "", "", NOW) == {} and SID in s1)
c.check("stop without a time uses now", ap({}, SID, "stop", {"at": None}, proj, "", NOW)[SID]["at"] == NOW)
pr = ns["_limit_stops_prune"]
old = {"a": {"resets_at": NOW - 4 * 86400}, "b": {"resets_at": NOW - 86400}, "c": {"resets_at": None, "at": NOW - 5 * 86400}, "d": "junk"}
c.check("prune: days-old reset / stop and junk go", set(pr(old, NOW)) == {"b"})

# ── view ──
vw = ns["_limit_stops_view"]
hit_view = {"windows": [{"kind": "five_hour", "pct": 100.0, "status": "hit"}]}
stops = {
    "s-future": {"id": "s-future", "cwd": proj, "title": "", "kind": "five_hour", "resets_at": NOW + 600, "at": NOW - 100, "state": "stopped", "task": None},
    "s-past": {"id": "s-past", "cwd": proj, "title": "T", "kind": "seven_day", "resets_at": NOW - 10, "at": NOW - 900, "state": "stopped", "task": None},
    "s-unknown": {"id": "s-unknown", "cwd": proj, "kind": "", "resets_at": None, "at": NOW - 50, "state": "stopped", "task": None},
    "s-queued": {"id": "s-queued", "cwd": proj, "kind": "five_hour", "resets_at": NOW + 600, "at": NOW - 200, "state": "queued", "task": "t1"},
    "s-gone": {"id": "s-gone", "cwd": proj, "resets_at": NOW + 600, "at": NOW - 1, "state": "dismissed"},
    "s-cont": {"id": "s-cont", "cwd": proj, "resets_at": NOW - 5, "at": NOW - 2, "state": "continued"},
}
v = vw(stops, {"s-past"}, [{"id": "t1", "state": "queued"}], hit_view, NOW)
by = {x["id"]: x for x in v}
c.check("dismissed / continued are hidden", set(by) == {"s-future", "s-past", "s-unknown", "s-queued"})
c.check("newest first", [x["id"] for x in v] == ["s-unknown", "s-future", "s-queued", "s-past"])
c.check("ready = reset time passed", by["s-past"]["ready"] and not by["s-future"]["ready"])
c.check("reset unknown -> ready follows the plan-limit view", not by["s-unknown"]["ready"]
        and vw(stops, set(), [], {"windows": []}, NOW)[0]["ready"])
c.check("open flag from the open session ids", by["s-past"]["open"] and not by["s-future"]["open"])
c.check("auto only while its queue task still waits", by["s-queued"]["auto"]
        and not vw(stops, set(), [{"id": "t1", "state": "launched"}], hit_view, NOW)[2]["auto"])
c.check("at most _LIMIT_STOP_MAX rows",
        len(vw({str(i): {"id": str(i), "resets_at": NOW + 60, "at": NOW - i, "state": "stopped"} for i in range(15)}, set(), [], None, NOW)) == ns["_LIMIT_STOP_MAX"])

# ── record (scanner hook) against a real file + /events bump ──
rec, load = ns["_limit_stop_record"], ns["_limit_stops_load"]
rec(SID, "stop", {"kind": "five_hour", "resets_at": time.time() + 3600, "at": time.time() - 5}, proj, "Shop")
c.check("record writes limit_stops.json", load()[SID]["state"] == "stopped" and os.path.exists(ns["LIMIT_STOPS_FILE"]))
c.check("a change wakes /events clients", ns["_status_version"][0] == 1)
rec(SID, "stop", {"kind": "five_hour", "resets_at": load()[SID]["resets_at"], "at": load()[SID]["at"]}, proj, "Shop")
c.check("no change -> no bump", ns["_status_version"][0] == 1)

# ── actions ──
act = ns["_limit_stop_action"]
def fake_launch(req, now=None):
    launched.append(req)
    return {"ok": True, "pid": 4242}
OPEN.add(SID)
r = act({"id": SID, "action": "continue"}, launch=fake_launch)
c.check("open in a terminal -> refused, nothing launched", not r["ok"] and "type pokračuj there" in r["error"] and not launched)
c.check("auto-continue refused too while open", not act({"id": SID, "action": "auto_on"}, launch=fake_launch)["ok"])
OPEN.clear()
r = act({"id": SID, "action": "continue"}, launch=fake_launch)
c.check("continue resumes THAT conversation with pokračuj",
        r["ok"] and launched[-1] == {"cwd": proj, "prompt": "pokračuj", "resume": SID, "keep_prompt": True})
c.check("continued stop leaves the list", load()[SID]["state"] == "continued"
        and vw(load(), set(), [], None, time.time()) == [])
c.check("unknown id / action", not act({"id": "nope", "action": "continue"})["ok"])

rec(SID, "stop", {"kind": "five_hour", "resets_at": int(time.time()) + 3600, "at": time.time() + 1}, proj, "Shop")
r = act({"id": SID, "action": "auto_on"})
q = ns["_queue_load"]()
c.check("auto_on queues a resume task for the reset time (limits still checked)",
        r["ok"] and len(q) == 1 and q[0]["resume"] == SID and q[0]["prompt"] == "pokračuj"
        and q[0]["at"] == load()[SID]["resets_at"] and q[0]["limits"] is True)
c.check("stop now 'queued' with that task", load()[SID]["state"] == "queued" and load()[SID]["task"] == q[0]["id"])
c.check("queue view shows it resumes", ns["_queue_view"](None, time.time())[0]["resume"] == SID)
c.check("bad action", act({"id": SID, "action": "explode"})["error"] == "unknown action")
r = act({"id": SID, "action": "auto_off"})
c.check("auto_off cancels the task, stop back to stopped",
        r["ok"] and ns["_queue_load"]()[0]["state"] == "cancelled" and load()[SID]["state"] == "stopped" and load()[SID]["task"] is None)
act({"id": SID, "action": "auto_on"})
tid = load()[SID]["task"]
r = act({"id": SID, "action": "dismiss"})
c.check("dismiss cancels a queued task too", r["ok"] and load()[SID]["state"] == "dismissed"
        and next(t for t in ns["_queue_load"]() if t["id"] == tid)["state"] == "cancelled")

rec(SID, "stop", {"resets_at": None, "at": time.time() + 2}, proj, "Shop")
act({"id": SID, "action": "auto_on"})
c.check("reset unknown -> queued for 'when limits allow' (no time)",
        next(t for t in ns["_queue_load"]() if t["id"] == load()[SID]["task"])["at"] is None)

# ── the queue worker on a resume task ──
tick = ns["_queue_tick"]
def queue_due(at_offset=-1):
    tasks = ns["_queue_load"]()
    for t in tasks:
        if t["id"] == load()[SID]["task"]:
            t["at"] = time.time() + at_offset
    ns["_queue_save"](tasks)
queue_due()
ns["_queue_last_launch"][0] = 0
launched.clear()
t = tick(launch=fake_launch, view={"windows": []}, open_ids={SID})
c.check("due but session open -> not launched, task failed with the reason",
        not launched and t["state"] == "failed" and "type pokračuj there" in t["error"])
c.check("... and the stop is offered again", load()[SID]["state"] == "stopped")
c.check("... with a toast", toasts and "failed" in toasts[-1][0])

act({"id": SID, "action": "auto_on"})
queue_due()
ns["_queue_last_launch"][0] = 0
t = tick(launch=fake_launch, view={"windows": []}, open_ids=set())
c.check("due and closed -> resume launch with the prompt kept",
        t["state"] == "launched" and launched[-1] == {"cwd": proj, "prompt": "pokračuj", "model": "", "resume": SID, "keep_prompt": True})
c.check("... and the stop is marked continued", load()[SID]["state"] == "continued")

# the real worker passes no open_ids: they come from /status, whose rebuild
# takes _queue_lock (_queue_view) -- so they must be read without holding it
lock_held = []
def open_ids_probe():
    lock_held.append(ns["_queue_lock"].locked())
    return {SID}
ns["_open_session_ids"] = open_ids_probe
tick.__globals__["_open_session_ids"] = open_ids_probe
rec(SID, "stop", {"resets_at": None, "at": time.time() + 3}, proj, "Shop")
act({"id": SID, "action": "auto_on"}, open_ids=set())
queue_due()
ns["_queue_last_launch"][0] = 0
launched.clear()
t = tick(launch=fake_launch, view={"windows": []})
c.check("worker tick reads open sessions outside _queue_lock (no deadlock with /status)",
        lock_held == [False] and not launched and t["state"] == "failed")
lock_held.clear()
tick(launch=fake_launch, view={"windows": []})
c.check("no resume task queued -> open sessions not even read", lock_held == [])

# ── _launch_session keeps the prompt only when asked ──
popen_calls = []
class P:
    def __init__(self, argv, cwd=None, creationflags=0):
        popen_calls.append(argv); self.pid = 7
ns["_launch_last"][0] = 0
ns["_launch_session"]({"cwd": proj, "prompt": "pokračuj", "resume": SID, "keep_prompt": True}, now=time.time(), popen=P)
c.check("keep_prompt: claude --resume <id> pokračuj", popen_calls[-1][1:] == ["--resume", SID, "pokračuj"])
ns["_launch_last"][0] = 0
ns["_launch_session"]({"cwd": proj, "prompt": "pokračuj", "resume": SID}, now=time.time(), popen=P)
c.check("without it a resume still drops the prompt (Last conversation cards)", popen_calls[-1][1:] == ["--resume", SID])
c.check("keep_prompt needs a resume and a prompt",
        "keep_prompt" not in ns["_launch_validate"]({"cwd": proj, "prompt": "x", "keep_prompt": True})[0]
        and "keep_prompt" not in ns["_launch_validate"]({"cwd": proj, "resume": SID, "keep_prompt": True})[0]
        and "keep_prompt" not in ns["_launch_validate"]({"cwd": proj, "prompt": "x", "resume": SID, "keep_prompt": "yes"})[0])

# ── wiring in the real source ──
src = read_monitor_source()
c.check("scanner records each file's last stop/clear", "_limit_stop_event(obj)" in src and "_limit_stop_record(session_id" in src)
c.check("/status carries limit_stops", 'data["limit_stops"] = _limit_stops_view(' in src)
c.check("POST /limit_stop route", 'path_no_qs == "/limit_stop"' in src)

c.finish()
