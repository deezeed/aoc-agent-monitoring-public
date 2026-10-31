"""Tests the notification center: _NOTIFY_TYPES table, per-type/channel
toggles (_notify_type_on, _notify_gate, _notify_set), the Settings view
(_notify_center_view), split webhook keys (_sanitize_webhook_events),
the recent log ring (_notify_log_add) and _show_native_toast's gating.
Extracted straight from monitor.py."""
import sys, os, json, re, tempfile, threading, time

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions, read_monitor_source
from lib.check import Checker

tmp = tempfile.mkdtemp()
calls = []
ns = exec_functions([
    "_NOTIFY_TYPES", "_NOTIFY_BY_KEY", "_NOTIFY_CHANNELS", "_notify_type_on", "_notify_gate",
    "_sanitize_notify_channels", "_notify_center_view", "_notify_set", "_sanitize_webhook_events",
    "_NOTIFY_LOG_MAX", "_notify_log_lock", "_notify_log", "_notify_log_add", "_notify_log_clear",
    "_show_native_toast",
], {
    "os": os, "json": json, "time": time, "threading": threading,
    "NOTIFY_LOG_FILE": os.path.join(tmp, "notify_log.json"),
    "_notify_settings": {"quiet_start": "", "quiet_end": "", "muted_projects": []},
    "_webhook_settings": {"url": "", "events": {}},
    "_in_quiet_hours": lambda a, b: False,
    "_push_notify": lambda title, body, tag="": calls.append(("push", title)) or 1,
    "_win_toast": lambda title, body: calls.append(("toast", title)),
    "_log_bg_error": lambda *a: None,
})
on, gate, setf, view = ns["_notify_type_on"], ns["_notify_gate"], ns["_notify_set"], ns["_notify_center_view"]
TYPES = ns["_NOTIFY_TYPES"]

c = Checker()

# ── the table ─────────────────────────────────────────────────────────
keys = [t["key"] for t in TYPES]
c.check("type keys are unique", len(keys) == len(set(keys)))
c.check("cache alert is a type", "cache_expiry" in keys)
c.check("every type has a group, label, channels and a 2-part sample",
        all(t["group"] and t["label"] and t["channels"] and len(t["sample"]) == 2 for t in TYPES))
c.check("webhook channel <-> webhook key", all(("webhook" in t["channels"]) == bool(t.get("webhook")) for t in TYPES))
c.check("channels are known ones", all(set(t["channels"]) <= {"toast", "push", "webhook"} for t in TYPES))
src = read_monitor_source()
used = set(re.findall(r'kind="([a-z_]+)"', src))
c.check("every kind= used in monitor.py is in the table", used <= set(keys))
c.check("webhook keys are all known to _sanitize_webhook_events",
        all(t["webhook"] in ns["_sanitize_webhook_events"]({t["webhook"]: True}) for t in TYPES if t.get("webhook")))

# ── _notify_type_on ───────────────────────────────────────────────────
c.check("toast defaults on", on("done", "toast", {}, {}) is True)
c.check("push defaults on", on("cache_expiry", "push", {}, {}) is True)
c.check("channel the type doesn't have -> off", on("cache_expiry", "webhook", {}, {}) is False)
c.check("permission has no toast", on("permission", "toast", {}, {}) is False)
c.check("unknown kind keeps old behaviour (toast/push on, webhook off)",
        on("nope", "toast", {}, {}) and on("nope", "push", {}, {}) and not on("nope", "webhook", {}, {}))
c.check("toast toggle off is honoured", on("done", "toast", {"channels": {"done": {"toast": False}}}, {}) is False)
c.check("push toggle off leaves toast on", on("done", "toast", {"channels": {"done": {"push": False}}}, {}) is True)
c.check("webhook default: done/error on, others off",
        on("done", "webhook", {}, {}) and on("error", "webhook", {}, {}) and not on("burn_spike", "webhook", {}, {}))
c.check("webhook reads events map", on("burn_spike", "webhook", {}, {"events": {"burn_spike": True}}) is True)
c.check("session_loop inherits legacy stuck", on("session_loop", "webhook", {}, {"events": {"stuck": True}}) is True)
c.check("session_loop own key beats legacy",
        on("session_loop", "webhook", {}, {"events": {"stuck": True, "session_loop": False}}) is False)
c.check("budget_cap inherits budget_alert", on("budget_cap", "webhook", {}, {"events": {"budget_alert": True}}) is True)

# ── _notify_gate ──────────────────────────────────────────────────────
Q = {"quiet_start": "22:00", "quiet_end": "07:00", "muted_projects": ["Muted"]}
c.check("plain send", gate("done", "toast", "", {}, {}, "12:00") == (True, ""))
c.check("off reason", gate("done", "toast", "", {"channels": {"done": {"toast": False}}}, {}, "12:00") == (False, "off"))
c.check("unknown channel", gate("done", "sms", "", {}, {}, "12:00")[0] is False)
c.check("not available", gate("permission", "toast", "", {}, {}, "12:00") == (False, "not available"))
c.check("webhook needs a url", gate("done", "webhook", "", {}, {"events": {}}, "12:00") == (False, "no webhook url"))
c.check("webhook with url", gate("done", "webhook", "", {}, {"url": "https://x", "events": {}}, "12:00") == (True, ""))
c.check("overnight quiet hours hold", gate("done", "toast", "", Q, {}, "23:30") == (False, "quiet hours"))
c.check("early morning still quiet", gate("done", "toast", "", Q, {}, "06:59") == (False, "quiet hours"))
c.check("after quiet hours sends", gate("done", "toast", "", Q, {}, "07:00") == (True, ""))
c.check("muted project", gate("done", "toast", "Muted", Q, {}, "12:00") == (False, "muted project"))
c.check("quiet=False type ignores quiet hours", gate("permission", "push", "", Q, {}, "23:30") == (True, ""))
c.check("quiet=False type ignores muted project", gate("budget_cap", "toast", "Muted", Q, {}, "12:00") == (True, ""))
c.check("quiet=False still honours its toggle",
        gate("update", "toast", "", {"channels": {"update": {"toast": False}}}, {}, "12:00") == (False, "off"))
c.check("cache alert respects quiet hours", gate("cache_expiry", "push", "", Q, {}, "23:00")[0] is False)

# ── _sanitize_notify_channels ─────────────────────────────────────────
san = ns["_sanitize_notify_channels"]
c.check("drops unknown types and channels",
        san({"done": {"toast": 0, "webhook": True, "x": 1}, "bogus": {"toast": True}}) == {"done": {"toast": False}})
c.check("drops a channel the type lacks", san({"permission": {"toast": True}}) == {})
c.check("non-dict -> empty", san(["x"]) == {} and san(None) == {})

# ── _notify_set ───────────────────────────────────────────────────────
n2, w2 = setf("cache_expiry", "push", False, {"quiet_start": "x"}, {"url": "u"})
c.check("set toast/push lands in notify cfg", n2["channels"] == {"cache_expiry": {"push": False}} and n2["quiet_start"] == "x")
c.check("set toast/push leaves webhook cfg alone", w2 == {"url": "u"})
n3, w3 = setf("session_loop", "webhook", True, {}, {"url": "u", "events": {"done": True}})
c.check("set webhook lands in events, split key kept", w3["events"]["session_loop"] is True and w3["events"]["done"] is True)
c.check("set webhook doesn't flip the legacy key", w3["events"]["stuck"] is False)
orig = {"channels": {"done": {"toast": True}}}
setf("done", "toast", False, orig, {})
c.check("set does not mutate its input", orig == {"channels": {"done": {"toast": True}}})
for bad in (("nope", "toast"), ("permission", "toast"), ("done", "sms")):
    try:
        setf(bad[0], bad[1], True, {}, {})
        c.check(f"set {bad} raises", False)
    except ValueError:
        c.check(f"set {bad} raises", True)

# ── _sanitize_webhook_events split keys ───────────────────────────────
sw = ns["_sanitize_webhook_events"]
c.check("split keys absent unless set", "session_loop" not in sw({}) and "budget_cap" not in sw({}))
c.check("split keys kept once set", sw({"session_loop": 1, "budget_cap": 0}) == {**sw({}), "session_loop": True, "budget_cap": False})

# ── _notify_center_view ───────────────────────────────────────────────
v = view({"quiet_start": "22:00", "quiet_end": "07:00", "muted_projects": ["A"], "channels": {"done": {"push": False}}},
         {"url": "https://x", "events": {"burn_spike": True}}, 2, "23:00")
row = {r["key"]: r for r in v["types"]}
c.check("view lists every type in order", [r["key"] for r in v["types"]] == keys)
c.check("view channel states", row["done"]["channels"] == {"toast": True, "push": False, "webhook": True})
c.check("view: missing channel is None", row["cache_expiry"]["channels"]["webhook"] is None)
c.check("view: webhook from events", row["burn_spike"]["channels"]["webhook"] is True)
c.check("view: quiet flag", row["permission"]["quiet"] is False and row["done"]["quiet"] is True)
c.check("view: quiet now", v["quiet_now"] is True and view(Q, {}, 0, "12:00")["quiet_now"] is False)
c.check("view: meta", v["muted_projects"] == ["A"] and v["webhook_url"] is True and v["push_devices"] == 2)

# ── log ring ──────────────────────────────────────────────────────────
add, log = ns["_notify_log_add"], ns["_notify_log"]
for i in range(105):
    add("done", "toast", f"t{i}", "b", now=1000 + i)
c.check("log keeps the last 100", len(log) == 100 and log[0]["title"] == "t5" and log[-1]["title"] == "t104")
with open(os.path.join(tmp, "notify_log.json"), encoding="utf-8") as f:
    disk = json.load(f)
c.check("log written to disk", len(disk) == 100 and disk[-1]["t"] == 1104)
e = add("x" * 99, "push", "T" * 500, "B" * 500, now=1, test=True)
c.check("log entry trimmed + test flag", len(e["kind"]) == 40 and len(e["title"]) == 120 and len(e["body"]) == 200 and e["test"])
c.check("None kind logs as other", add(None, "toast", "x")["kind"] == "other")
ns["_notify_log_clear"]()
c.check("clear empties the log and file", log == [] and not os.path.exists(os.path.join(tmp, "notify_log.json")))

# ── _show_native_toast gating ─────────────────────────────────────────
toast = ns["_show_native_toast"]
calls.clear(); toast("A", "b")
c.check("no kind: toast + push as before", sorted(x[0] for x in calls) == ["push", "toast"])
ns["_notify_settings"].clear()
ns["_notify_settings"].update({"channels": {"cache_expiry": {"push": False}}})
calls.clear(); toast("C", "b", kind="cache_expiry")
c.check("kind with push off: toast only", [x[0] for x in calls] == ["toast"])
ns["_notify_settings"]["channels"] = {"cache_expiry": {"push": False, "toast": False}}
calls.clear(); toast("C", "b", kind="cache_expiry")
c.check("kind with both off: nothing", calls == [])
c.check("sent toasts are logged", any(x["kind"] == "cache_expiry" and x["channel"] == "toast" for x in log))

c.finish()
