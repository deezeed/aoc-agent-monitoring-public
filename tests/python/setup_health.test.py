"""Install health: setup.py's health_checks (behind `setup.py --check` /
--repair and the dashboard's Health -> CLAUDE CODE SETUP card) and
monitor.py's _parse_setup_health / _hooks_health_bad. health_checks runs
setup.py's own merge functions on a copy of settings.json, so a settings
file a fresh install just wrote must pass every check, and each piece
removed must fail exactly its own check. Extracted straight from the
shipped files; nothing touches the real settings.json, hooks or tasks."""
import sys, os, json, copy, subprocess

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
SETUP_PY = os.path.join(ROOT, "setup.py")
c = Checker()

ns = exec_functions(
    ["health_checks", "HOOK_SPECS", "PERMISSION_EVENT", "HELD_EVENTS", "PERMISSION_SCRIPT", "PERMISSION_TIMEOUT_S",
     "BUDGET_HOOK_URL", "BUDGET_HOOK_TIMEOUT_S", "DANGER_HOOK_URL", "DANGER_HOOK_MATCHER", "DANGER_HOOK_TIMEOUT_S",
     "STATUSLINE_SCRIPT", "_merge_hook_entries", "_merge_permission_hook", "_merge_http_hook", "_merge_budget_hook",
     "_merge_danger_hook", "_merge_statusline", "_is_aoc_statusline", "_is_run_hook", "_is_held_hook", "_is_budget_hook",
     "_hook_command", "_permission_command", "_statusline_command", "_find_python_console", "_fmt_specs"],
    {"os": os, "subprocess": subprocess, "HOOKS_DST_DIR": "C:\\Users\\John Smith\\.claude\\hooks"},
    path=SETUP_PY)
PYW = sys.executable  # any real path: _find_python_console looks for python.exe next to it
health = ns["health_checks"]
SCRIPTS = {"aoc_hook.py": "A", "run_hook.pyw": "B"}
TASKS = {r"\AOC\AOC Watchdog": True, r"\AOC\AOC Sentinel": True}


def installed():
    """settings.json exactly as a real install writes it."""
    s = {}
    ns["_merge_hook_entries"](s, ns["_hook_command"](PYW))
    held = ns["_permission_command"](ns["_find_python_console"](PYW))
    for ev in ns["HELD_EVENTS"]:
        ns["_merge_permission_hook"](s, held, ev)
    ns["_merge_budget_hook"](s)
    ns["_merge_danger_hook"](s)
    ns["_merge_statusline"](s, ns["_statusline_command"](ns["_find_python_console"](PYW)))
    return s


def bad_ids(checks):
    return sorted(x["id"] for x in checks if not x["ok"])


full = installed()
res = health(full, PYW, SCRIPTS, dict(SCRIPTS), TASKS)
c.check("fresh install: every check passes", bad_ids(res) == [])
c.check("every check has id/label/ok/detail", all(set(x) == {"id", "label", "ok", "detail"} for x in res))
c.check("health_checks never modifies the settings it is given", res and full == installed())
c.check("ids cover hooks, statusline, scripts, both tasks",
        {x["id"] for x in res} >= {"activity", "held_permissionrequest", "held_stop", "budget", "danger",
                                    "statusline", "scripts", "task_watchdog", "task_sentinel"})

# the 2026-11 incident: danger-guard entry missing, everything else fine
s = installed()
s["hooks"]["PreToolUse"] = [e for e in s["hooks"]["PreToolUse"]
                            if not any(h.get("url") == ns["DANGER_HOOK_URL"] for h in e.get("hooks", []))]
res = health(s, PYW, SCRIPTS, dict(SCRIPTS), TASKS)
c.check("missing danger hook -> only 'danger' fails", bad_ids(res) == ["danger"])
c.check("its detail says missing", "missing" in next(x["detail"] for x in res if x["id"] == "danger"))

s = installed()
for e in s["hooks"]["PreToolUse"]:
    for h in e["hooks"]:
        if h.get("url") == ns["DANGER_HOOK_URL"]:
            h["timeout"] = 5
c.check("changed danger timeout -> 'danger' outdated",
        [x["detail"] for x in health(s, PYW, SCRIPTS, dict(SCRIPTS), TASKS) if not x["ok"]] == [f"PreToolUse {ns['DANGER_HOOK_MATCHER']} hook outdated"])

s = installed(); del s["hooks"]["UserPromptSubmit"]
c.check("missing activity event -> 'activity'", bad_ids(health(s, PYW, SCRIPTS, dict(SCRIPTS), TASKS)) == ["activity"])
s = installed(); del s["hooks"]["Stop"]
c.check("missing Stop event -> activity + reply-from-phone",
        bad_ids(health(s, PYW, SCRIPTS, dict(SCRIPTS), TASKS)) == ["activity", "held_stop"])
s = installed(); s["statusLine"] = {"type": "command", "command": "my-own-bar"}
c.check("someone else's statusLine -> 'statusline'", bad_ids(health(s, PYW, SCRIPTS, dict(SCRIPTS), TASKS)) == ["statusline"])
c.check("empty settings -> every settings check fails, files/tasks still fine",
        bad_ids(health({}, PYW, SCRIPTS, dict(SCRIPTS), TASKS)) ==
        ["activity", "budget", "danger", "held_permissionrequest", "held_stop", "statusline"])
c.check("non-dict settings handled", bad_ids(health(None, PYW, SCRIPTS, dict(SCRIPTS), TASKS))[0] == "activity")

res = health(full, PYW, SCRIPTS, {"aoc_hook.py": "old", "run_hook.pyw": None}, TASKS)
c.check("stale + missing script -> 'scripts'", bad_ids(res) == ["scripts"])
c.check("scripts detail names both", next(x["detail"] for x in res if x["id"] == "scripts") == "aoc_hook.py outdated, run_hook.pyw missing")
res = health(full, PYW, SCRIPTS, dict(SCRIPTS), {r"\AOC\AOC Watchdog": True, r"\AOC\AOC Sentinel": False})
c.check("missing task -> its own check", bad_ids(res) == ["task_sentinel"])
c.check("task label is the short name", next(x["label"] for x in res if x["id"] == "task_sentinel") == "Scheduled task AOC Sentinel")

# monitor.py side
m = exec_functions(["_parse_setup_health", "_hooks_health_bad"], {"json": json})
parse, badf = m["_parse_setup_health"], m["_hooks_health_bad"]
line = "AOC_HEALTH " + json.dumps({"checks": [{"id": "danger", "label": "Dangerous-command guard", "ok": False, "detail": "x"}]})
c.check("parses the AOC_HEALTH line after --repair's [ok] lines",
        parse("[ok] deployed\n[ok] added\n" + line + "\n")[0]["id"] == "danger")
c.check("no AOC_HEALTH line -> None", parse("Traceback ...\nError") is None)
c.check("malformed JSON -> None", parse("AOC_HEALTH {nope") is None)
c.check("checks not a list -> None", parse('AOC_HEALTH {"checks": 3}') is None)
c.check("bad labels listed", badf(parse(line)) == ["Dangerous-command guard"])
c.check("unknown -> nothing bad", badf(None) == [] and badf([{"ok": True, "label": "x"}]) == [])

# the real CLI: --check is read-only and prints the JSON last
r = subprocess.run([sys.executable, SETUP_PY, "--check"], capture_output=True, text=True, encoding="utf-8", timeout=120)
got = parse(r.stdout)
c.check("setup.py --check exits 0 with a checks list", r.returncode == 0 and isinstance(got, list) and len(got) >= 9)
r = subprocess.run([sys.executable, SETUP_PY, "--check", "--bogus"], capture_output=True, text=True, timeout=60)
c.check("unknown flag next to --check is still refused", r.returncode == 2)
c.finish()
