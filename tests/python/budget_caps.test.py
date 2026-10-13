"""Hard budget caps: monitor.py's _sanitize/_save/_load_budget_caps (merge
keeps unsent fields), _budget_verdicts (session cap, per-session override,
daily cap + today's override), _budget_hook_reply (the PreToolUse deny JSON
Claude Code gets), _budget_continue, _budget_today_spend against a real
SQLite sessions table, and the real HTTP handler: /hook/pretool answered
without a token only straight from loopback, never with tunnel headers."""
import sys, os, json, shutil, sqlite3, tempfile, threading, re
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
data_dir = tempfile.mkdtemp(prefix="aoc_bc_")
db_path = os.path.join(data_dir, "h.db")


def _db_conn():
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


ns = exec_functions(
    ["_load_json_file", "BUDGET_CAPS_FILE", "_BUDGET_MAX_USD", "_budget_lock", "_budget_state", "_sanitize_budget_caps",
     "_load_budget_caps", "_save_budget_caps", "_budget_verdicts", "_budget_reason", "_budget_hook_reply",
     "_budget_continue", "_budget_today_spend"],
    {"os": os, "json": json, "re": re, "threading": threading, "datetime": datetime,
     "AOC_DATA_DIR": os.path.join(data_dir, "AOC"), "_db_lock": threading.Lock(), "_db_conn": _db_conn})

# ── settings ──
san = ns["_sanitize_budget_caps"]
c.check("defaults: no caps", san(None) == {"session_usd": None, "day_usd": None, "overrides": {}, "day_override": ""})
c.check("garbage / zero / negative / huge -> no cap",
        san({"session_usd": "x"})["session_usd"] is None and san({"session_usd": 0})["session_usd"] is None
        and san({"day_usd": -5})["day_usd"] is None and san({"day_usd": 1e9})["day_usd"] is None)
c.check("numbers rounded to cents", san({"session_usd": "5.257"})["session_usd"] == 5.26)
c.check("bad day_override dropped", san({"day_override": "today"})["day_override"] == "")
c.check("overrides keep valid numbers only", san({"overrides": {"a": 7, "b": "x"}})["overrides"] == {"a": 7.0})
c.check("missing file -> defaults", ns["_load_budget_caps"]()["session_usd"] is None)
ns["_save_budget_caps"]({"session_usd": 5, "day_usd": 40})
ns["_save_budget_caps"]({"overrides": {"s1": 12}})
c.check("saving overrides keeps the caps", ns["_load_budget_caps"]() == {"session_usd": 5.0, "day_usd": 40.0, "overrides": {"s1": 12.0}, "day_override": ""})
ns["_save_budget_caps"]({"session_usd": None})
c.check("clearing one cap keeps the other + overrides",
        ns["_load_budget_caps"]() == {"session_usd": None, "day_usd": 40.0, "overrides": {"s1": 12.0}, "day_override": ""})

# ── verdicts ──
caps = {"session_usd": 5.0, "day_usd": 40.0, "overrides": {"s2": 10.0}, "day_override": ""}
v = ns["_budget_verdicts"](caps, [("s1", 5.2), ("s2", 7.0), ("s3", 1.0)], 12.0, "2026-10-13")
c.check("session over its cap", v["sessions"] == {"s1": {"kind": "session", "spent": 5.2, "cap": 5.0}})
c.check("override lifts s2's cap to 10", "s2" not in v["sessions"])
c.check("day under cap", v["day"] is None)
v = ns["_budget_verdicts"](caps, [("s2", 10.0)], 40.0, "2026-10-13")
c.check("override reached -> blocked again", v["sessions"]["s2"]["cap"] == 10.0)
c.check("daily cap reached", v["day"] == {"kind": "day", "spent": 40.0, "cap": 40.0})
c.check("daily cap lifted for today", ns["_budget_verdicts"]({**caps, "day_override": "2026-10-13"}, [], 99, "2026-10-13")["day"] is None)
c.check("...but not tomorrow", ns["_budget_verdicts"]({**caps, "day_override": "2026-10-13"}, [], 99, "2026-10-14")["day"] is not None)
c.check("no caps -> nothing", ns["_budget_verdicts"](san({}), [("s1", 999)], 999, "2026-10-13") == {"day": None, "sessions": {}})

# ── hook reply ──
state = {"day": None, "sessions": {"s1": {"kind": "session", "spent": 5.2, "cap": 5.0}}}
r = ns["_budget_hook_reply"](state, {"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Bash"})
dec = r.get("hookSpecificOutput", {})
c.check("over cap -> PreToolUse deny", dec.get("hookEventName") == "PreToolUse" and dec.get("permissionDecision") == "deny")
c.check("reason names the numbers and says stop", "$5.20 of its $5.00" in dec["permissionDecisionReason"]
        and "Do not call any more tools" in dec["permissionDecisionReason"])
c.check("other session -> allowed ({})", ns["_budget_hook_reply"](state, {"hook_event_name": "PreToolUse", "session_id": "s9"}) == {})
c.check("other event -> {}", ns["_budget_hook_reply"](state, {"hook_event_name": "PostToolUse", "session_id": "s1"}) == {})
c.check("garbage body -> {}", ns["_budget_hook_reply"](state, "nope") == {})
day_state = {"day": {"kind": "day", "spent": 41.0, "cap": 40.0}, "sessions": {}}
r = ns["_budget_hook_reply"](day_state, {"hook_event_name": "PreToolUse", "session_id": "brand-new"})
c.check("daily cap denies every session, new ones too", "daily cap" in r["hookSpecificOutput"]["permissionDecisionReason"])

# ── continue ──
caps2, ok = ns["_budget_continue"]({"session_id": "s1"}, caps, state, "2026-10-13")
c.check("continue: one more cap's worth on top of the spend", ok and caps2["overrides"]["s1"] == 10.2)
c.check("continue doesn't mutate the input", "s1" not in caps["overrides"])
caps3, ok = ns["_budget_continue"]({"session_id": "x"}, caps, day_state, "2026-10-13")
c.check("continue under the daily cap lifts it for today", ok and caps3["day_override"] == "2026-10-13")
c.check("continue when nothing is over -> not ok", ns["_budget_continue"]({"session_id": "s5"}, caps, {"day": None, "sessions": {}}, "2026-10-13")[1] is False)

# ── today's spend ──
conn = _db_conn()
conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, date TEXT, cost REAL)")
conn.executemany("INSERT INTO sessions VALUES (?,?,?)",
                 [("a", "2026-10-13", 3.0), ("live1", "2026-10-13", 1.0), ("old", "2026-10-12", 50.0), ("n", "2026-10-13", None)])
conn.commit(); conn.close()
c.check("today = saved rows + live cost replacing the saved one",
        abs(ns["_budget_today_spend"]([("live1", 4.5), ("live2", 2.0)], "2026-10-13") - 9.5) < 1e-9)

# ── the real handler: /hook/pretool without a token, loopback only ──
mon = open(os.path.join(os.path.dirname(__file__), "..", "..", "monitor.py"), encoding="utf-8").read()
c.check("do_POST answers /hook/pretool before the auth check",
        mon.index('== "/hook/pretool" and self._is_direct_loopback()') < mon.index("if not self._check_auth():", mon.index("    def do_POST(self):")))


class FakeHeaders(dict):
    def get(self, k, d=None):
        return super().get(k, d)


src = mon[mon.index("    def _is_direct_loopback(self) -> bool:"):]
src = src[:src.index("\n    def ", 10)]
fn_ns = {}
exec("class _H:\n" + src, fn_ns)
H = fn_ns["_H"]


def direct(ip, headers):
    h = H(); h.client_address = (ip, 1234); h.headers = FakeHeaders(headers)
    return h._is_direct_loopback()


c.check("plain loopback -> direct", direct("127.0.0.1", {}) and direct("::1", {}))
c.check("LAN address -> not direct", not direct("192.168.1.5", {}))
c.check("cloudflared (Cf-Connecting-Ip) -> not direct", not direct("127.0.0.1", {"Cf-Connecting-Ip": "1.2.3.4"}))
c.check("ngrok (X-Forwarded-For) -> not direct", not direct("127.0.0.1", {"X-Forwarded-For": "1.2.3.4"}))

shutil.rmtree(data_dir, ignore_errors=True)
c.finish()
