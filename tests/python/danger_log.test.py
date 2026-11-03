"""Dangerous-command guard, round 7: the log of held commands
(_danger_log_entry/_append/_read, written by _danger_hook after each hold)
and the self-test (_danger_test behind POST /danger_guard/test, which only
judges a command). Extracted straight from monitor.py; the log lives in a
throwaway directory."""
import sys, os, json, re, tempfile, threading, time

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions, read_monitor_source
from lib.check import Checker

c = Checker()
data_dir = tempfile.mkdtemp(prefix="aoc_dglog_")


class _Cond:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def notify_all(self): pass


import secrets as _secrets
errors = []
ns = exec_functions(
    ["_PERM_POLL_STALE_S", "_perm_expire", "_PERM_WORD_OK", "_PERM_SCRIPT_WORDS", "_perm_head", "_perm_simple_commands",
     "DANGER_GUARD_FILE", "_DANGER_MODES", "_DANGER_HOLD_MAX_MIN", "_DANGER_MAX_PATTERNS", "_DANGER_RULES",
     "_DANGER_RULE_IDS", "_DANGER_TEXT_HEADS", "_DANGER_RM_HEADS", "_DANGER_SQL", "_danger_cfg_cache",
     "_load_json_file", "_sanitize_danger_guard", "_load_danger_guard",
     "_danger_unquote", "_danger_broad_path", "_danger_rm_hit", "_danger_git_hits", "_danger_db_hit",
     "_danger_disk_hit", "_danger_check", "_danger_reply", "_danger_open", "_danger_wait", "_danger_decision", "_danger_hook",
     "DANGER_LOG_FILE", "_DANGER_LOG_KEEP", "_DANGER_OUTCOMES", "_danger_log_lock",
     "_danger_log_entry", "_danger_log_append", "_danger_log_read", "_danger_test"],
    {"os": os, "json": json, "re": re, "time": time, "threading": threading, "_secrets": _secrets,
     "AOC_DATA_DIR": os.path.join(data_dir, "AOC"), "_perm_lock": threading.Lock(), "_perm_requests": {},
     "_status_cond": _Cond(), "_status_version": [0], "_push_session_name": lambda sid: "S",
     "_push_notify": lambda *a, **k: 1, "_show_native_toast": lambda *a, **k: None,
     "_log_bg_error": lambda *a: errors.append(a)})
CFG = ns["_sanitize_danger_guard"]({})
LOG = ns["DANGER_LOG_FILE"]

# 1. _danger_log_entry
hook = {"session_id": "abc", "cwd": "C:\\proj\\shop", "tool_name": "Bash",
        "tool_input": {"command": "git push --force origin main"}}
hits = ns["_danger_check"](hook["tool_input"]["command"], "Bash", CFG)
e = ns["_danger_log_entry"](hook, hits, "deny", 1000.04, 12.345)
c.check("entry: command, rule labels, outcome wording",
        e["cmd"] == "git push --force origin main" and e["rules"] == ["Force push"] and e["outcome"] == "denied")
c.check("entry: rounded times, session, cwd", e["t"] == 1000.0 and e["held_s"] == 12.3 and e["sid"] == "abc" and e["cwd"] == "C:\\proj\\shop")
c.check("every hook outcome has wording", all(ns["_danger_log_entry"](hook, hits, s, 0, 0)["outcome"] != s
                                               for s in ("allow", "deny", "release", "timeout", "gone")))
c.check("long command cut to 300", len(ns["_danger_log_entry"]({"tool_input": {"command": "x" * 999}}, [], "allow", 0, 0)["cmd"]) == 300)
c.check("garbage tool_input tolerated", ns["_danger_log_entry"]({"tool_input": "nope"}, [], "gone", 0, -5)["held_s"] == 0)

# 2. append/read: newest first, broken lines skipped, missing file = []
c.check("no log yet -> []", ns["_danger_log_read"]() == [])
for i in range(3):
    ns["_danger_log_append"]({**e, "t": i, "cmd": f"cmd {i} čšž"})
with open(LOG, "a", encoding="utf-8") as f:
    f.write("{broken\n")
got = ns["_danger_log_read"]()
c.check("read: newest first, broken line skipped", [x["cmd"] for x in got] == ["cmd 2 čšž", "cmd 1 čšž", "cmd 0 čšž"])
c.check("read: limit", len(ns["_danger_log_read"](limit=2)) <= 2)

# 3. trimmed to _DANGER_LOG_KEEP once it passes twice that
keep = ns["_DANGER_LOG_KEEP"]
p = os.path.join(data_dir, "trim.jsonl")
for i in range(2 * keep + 1):
    ns["_danger_log_append"]({"n": i}, path=p)
lines = open(p, encoding="utf-8").read().splitlines()
c.check("trimmed to the newest KEEP lines", len(lines) == keep and json.loads(lines[-1])["n"] == 2 * keep)

# 4. _danger_hook logs each hold with its outcome; a harmless command logs nothing
os.remove(LOG)
reply = ns["_danger_hook"]({**hook, "hook_event_name": "PreToolUse"}, cfg=CFG, wait=lambda rid, s, gone_fn=None: ("allow", ""), now=500.0)
c.check("hook still answers allow", reply["hookSpecificOutput"]["permissionDecision"] == "allow")
got = ns["_danger_log_read"]()
c.check("held command logged as allowed", len(got) == 1 and got[0]["outcome"] == "allowed" and got[0]["t"] == 500.0)
ns["_danger_hook"]({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git status"}}, cfg=CFG,
                   wait=lambda *a, **k: ("allow", ""))
c.check("harmless command not logged", len(ns["_danger_log_read"]()) == 1)
c.check("no logging errors", errors == [])

# 5. self-test
t = ns["_danger_test"]("git push --force origin main", "Bash", CFG)
c.check("test: force push would be held", t["would_hold"] and [h["id"] for h in t["hits"]] == ["force_push"])
t = ns["_danger_test"]("git status && npm test", "Bash", CFG)
c.check("test: harmless -> no hits", t["hits"] == [] and not t["would_hold"])
t = ns["_danger_test"]("Remove-Item -Recurse -Force C:\\", "PowerShell", CFG)
c.check("test: PowerShell rule", [h["id"] for h in t["hits"]] == ["rm_broad"])
t = ns["_danger_test"]("git push --force", "Bash", {**CFG, "mode": "off"})
c.check("test while guard off: still reports the match, would not hold", t["hits"] and not t["would_hold"] and t["mode"] == "off")
t = ns["_danger_test"]("git push --force", "Bash", {**CFG, "off_rules": ["force_push"]})
c.check("test: a rule switched off doesn't match", t["hits"] == [])
c.check("test: unknown shell falls back to Bash", ns["_danger_test"]("rm -rf /", "zsh", CFG)["hits"][0]["id"] == "rm_broad")
c.check("test: empty command", ns["_danger_test"]("", "Bash", CFG)["hits"] == [])

# 6. wiring: the test endpoint never holds/logs, GET adds the log
src = read_monitor_source()
seg = src[src.index('elif path_no_qs == "/danger_guard/test":'):]
seg = seg[:seg.index("elif path_no_qs", 10)]
c.check("/danger_guard/test only calls _danger_test", "_danger_test(" in seg and "_danger_hook" not in seg and "_danger_open" not in seg)
c.check("GET /danger_guard includes the log", 'view["log"] = _danger_log_read()' in src)
c.finish()
