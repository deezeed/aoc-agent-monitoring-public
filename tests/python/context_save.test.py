"""Save state when the context fills up (monitor.py):
- _ctxsave_sanitize: mode / threshold clamp / prompt default.
- _ctxsave_step: one "crossed" per crossing, re-armed by compaction or by
  falling well under the threshold, quiet right after a compaction, skipped
  in auto-compact territory.
- _ctxsave_view / _ctxsave_tick / _ctxsave_alert, state persisted.
- delivery: _ctxsave_request sends into a turn held for a reply, else arms;
  the next Stop hook (_reply_open) answers with the prompt once, then the
  normal reply logic applies again; cancel; the 7-in-a-row cap holds it."""
import sys, os, json, secrets, tempfile, threading

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
data_dir = os.path.join(tempfile.mkdtemp(prefix="aoc_cs_"), "AOC")
ns = exec_functions(
    ["_load_json_file", "CTXSAVE_FILE", "CTXSAVE_STATE_FILE", "_CTXSAVE_MODES", "_CTXSAVE_DEFAULT_PROMPT",
     "_CTXSAVE_MIN_PCT", "_CTXSAVE_MAX_PCT", "_CTXSAVE_REARM_BELOW", "_CTXSAVE_COMPACT_DROP", "_CTXSAVE_COMPACT_QUIET_S",
     "_CTXSAVE_COMPACTING_PCT", "_CTXSAVE_KEEP_S", "_ctxsave_lock", "_ctxsave_state",
     "_ctxsave_sanitize", "_ctxsave_settings", "_ctxsave_save_settings", "_ctxsave_step", "_ctxsave_view",
     "_ctxsave_persist", "_ctxsave_load", "_ctxsave_tick", "_ctxsave_request", "_ctxsave_cancel", "_ctxsave_take",
     "_ctxsave_alert",
     "_PERM_POLL_STALE_S", "_PERM_BACK_IDLE_S", "_REPLY_MAX_STREAK", "_reply_lock", "_reply_requests", "_reply_streak",
     "_reply_should_hold", "_reply_expire", "_reply_open", "_reply_poll", "_reply_send", "_reply_pending_by_session"],
    {"os": os, "json": json, "threading": threading, "_secrets": secrets, "time": __import__("time"),
     "AOC_DATA_DIR": data_dir,
     "_push_notify": lambda *a, **k: 0, "_notify_gate": lambda *a, **k: (False, "off"),
     "_notify_log_add": lambda *a, **k: None, "_push_session_name": lambda sid: sid,
     "_log_bg_error": lambda *a: None})

# ── settings ──
san = ns["_ctxsave_sanitize"]
d = san({})
c.check("defaults: offer at 90 % with the Slovak prompt",
        d == {"mode": "offer", "threshold": 90, "prompt": ns["_CTXSAVE_DEFAULT_PROMPT"]} and "RESUME HERE" in d["prompt"])
c.check("threshold clamped to 50..95, junk -> 90",
        san({"threshold": 10})["threshold"] == 50 and san({"threshold": 99})["threshold"] == 95
        and san({"threshold": "x"})["threshold"] == 90)
c.check("unknown mode -> offer; auto/off kept",
        san({"mode": "nope"})["mode"] == "offer" and san({"mode": "auto"})["mode"] == "auto" and san({"mode": "off"})["mode"] == "off")
c.check("blank prompt -> default, long one capped", san({"prompt": "  "})["prompt"] == ns["_CTXSAVE_DEFAULT_PROMPT"]
        and len(san({"prompt": "x" * 5000})["prompt"]) == 2000)
saved = ns["_ctxsave_save_settings"]({"mode": "auto"})
ns["_ctxsave_save_settings"]({"threshold": 80})
c.check("saving one field keeps the others", ns["_ctxsave_settings"]() == {**saved, "threshold": 80} and saved["mode"] == "auto")
c.check("non-dict body ignored", ns["_ctxsave_save_settings"]("x")["threshold"] == 80)

# ── step ──
step = ns["_ctxsave_step"]
T = 1_800_000_000
st, ev = step(None, 50, 90, T)
c.check("under the threshold: nothing", ev == "" and not st.get("crossed"))
st, ev = step(st, 91, 90, T + 10)
c.check("crossing -> 'crossed' once", ev == "crossed" and st["crossed"] and st["crossed_at"] == T + 10)
st, ev = step(st, 93, 90, T + 20)
c.check("still over -> no second event", ev == "" and st["crossed"])
st, ev = step(st, 85, 90, T + 30)
st, ev2 = step(st, 91, 90, T + 40)
c.check("a dip less than 10 points doesn't re-arm", ev == "" and ev2 == "")
st, ev = step(st, 79, 90, T + 50)
c.check("falling well under re-arms", ev == "" and not st["crossed"])
st, ev = step(st, 90, 90, T + 60)
c.check("... and the next crossing fires again", ev == "crossed")
st["armed"] = True
st, ev = step(st, 30, 90, T + 70)
c.check("a big drop = compacted: disarmed, re-armed crossing",
        ev == "compacted" and not st["crossed"] and not st["armed"] and st["compacted_at"] == T + 70)
st, ev = step(st, 50, 90, T + 80)
st, ev = step(st, 92, 90, T + 100)
c.check("right after a compaction: no offer", ev == "" and not st.get("crossed"))
st, ev = step(st, 92, 90, T + 70 + 901)
c.check("after the quiet time it fires", ev == "crossed")
st, ev = step({"pct": 80}, 98, 90, T)
c.check("first reading already in auto-compact territory: counted, not offered", ev == "" and st["crossed"])
st, ev = step(st, 99, 90, T + 5)
c.check("... and not offered later in the same crossing", ev == "")
st, ev = step(None, 92, 90, T)
c.check("first reading ever above the threshold offers", ev == "crossed")

# ── view ──
view = ns["_ctxsave_view"]
cfg = {"mode": "offer", "threshold": 90, "prompt": "p"}
c.check("no crossing / off -> None", view({"crossed": False}, cfg) is None and view(None, cfg) is None
        and view({"crossed": True}, {**cfg, "mode": "off"}) is None)
c.check("offer / armed / sent states",
        view({"crossed": True}, cfg)["state"] == "offer"
        and view({"crossed": True, "armed": True}, cfg)["state"] == "armed"
        and view({"crossed": True, "sent_at": T}, cfg) == {"state": "sent", "mode": "offer", "threshold": 90, "sent_at": T})

# ── alert text ──
alert = ns["_ctxsave_alert"]
s1 = {"id": "s1", "display_name": "AOC work", "context": {"pct": 91.4}}
t, b = alert(s1, cfg)
c.check("offer alert: name + pct + one click", "AOC work" in t and "91 %" in t and "one click" in b)
c.check("auto alert says it asks by itself", "when this turn ends" in alert(s1, {**cfg, "mode": "auto"})[1])

# ── tick ──
tick, state = ns["_ctxsave_tick"], ns["_ctxsave_state"]
sess = lambda sid, pct, active=True: {"id": sid, "session_active": active, "context": {"pct": pct}}
out = tick([sess("a", 50), sess("b", 95), sess("c", 99, active=False), {"id": "d", "session_active": True}], cfg, T)
c.check("tick: only the open session past the threshold fires", [(s["id"], e) for s, e in out] == [("b", "crossed")])
c.check("closed / no-context sessions not tracked", "c" not in state and "d" not in state)
c.check("tick again: no repeat", tick([sess("b", 96)], cfg, T + 5) == [])
c.check("state persisted to disk", json.load(open(ns["CTXSAVE_STATE_FILE"], encoding="utf-8"))["b"]["crossed"] is True)
state["old"] = {"pct": 10, "t": T - 4 * 86400}
tick([], cfg, T)
c.check("entries untouched for 3 days dropped", "old" not in state)
state.clear()
ns["_ctxsave_load"]()
c.check("state reloads after a restart (no second offer)", state["b"]["crossed"] and tick([sess("b", 96)], cfg, T + 9) == [])

# ── delivery ──
ns["_ctxsave_save_settings"]({"prompt": "Save it now."})
ra = {"mode": "away", "idle_min": 5, "reply": "away"}
r = ns["_ctxsave_request"]("b", T)
c.check("no held turn -> armed", r == {"ok": True, "state": "armed"} and state["b"]["armed"])
c.check("view shows armed", view(state["b"], cfg)["state"] == "armed")
c.check("cancel disarms; cancel again -> False", ns["_ctxsave_cancel"]("b") and not state["b"]["armed"] and not ns["_ctxsave_cancel"]("b"))
ns["_ctxsave_request"]("b", T)
o = ns["_reply_open"]({"session_id": "b", "idle_s": 0, "stop_hook_active": False, "last_assistant_message": "done"}, ra, T + 1)
c.check("next turn end (even at the PC) is held for it", o["hold"] and o["id"])
p = ns["_reply_poll"](o["id"], 0, ra, T + 2)
c.check("first poll hands over the prompt", p == {"state": "reply", "reply": "Save it now.", "kind": "ctx_save"})
c.check("marked sent, disarmed", state["b"]["sent_at"] == T + 1 and not state["b"]["armed"]
        and view(state["b"], cfg)["state"] == "sent")
o2 = ns["_reply_open"]({"session_id": "b", "idle_s": 0, "stop_hook_active": True, "last_assistant_message": "saved"}, ra, T + 30)
c.check("the turn after it ends normally (at the PC -> no hold)", o2 == {"hold": False})

o3 = ns["_reply_open"]({"session_id": "e", "idle_s": 999, "stop_hook_active": False, "last_assistant_message": "?"}, ra, T)
c.check("away: a turn held for a reply", o3["hold"])
r = ns["_ctxsave_request"]("e", T + 3)
c.check("held turn -> sent straight away", r == {"ok": True, "state": "sent"} and state["e"]["sent_at"] == T + 3)
c.check("the held turn gets the prompt", ns["_reply_poll"](o3["id"], 999, ra, T + 4) == {"state": "reply", "reply": "Save it now.", "kind": "ctx_save"})

ns["_reply_streak"]["f"] = ns["_REPLY_MAX_STREAK"]
ns["_ctxsave_request"]("f", T)
o4 = ns["_reply_open"]({"session_id": "f", "idle_s": 0, "stop_hook_active": True}, ra, T + 1)
c.check("at the 7-in-a-row cap: not sent, stays armed", o4 == {"hold": False} and state["f"]["armed"])
c.check("no session -> error", ns["_ctxsave_request"]("", T)["ok"] is False)

c.finish()
