"""Tests setup.py's settings.json merge/unmerge logic (_merge_hook_entries,
_remove_hook_entries, _hook_command) and stop_aoc_processes, extracted
straight from setup.py. The merge/unmerge functions are pure (dict in, dict
out); stop_aoc_processes runs for real against throwaway python processes in
a scratch directory, never the real AOC install or its scheduled tasks."""
import sys, os, json, copy, subprocess, tempfile, shutil, time

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SETUP_PY = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "setup.py"))
HOOK_SPECS = [("UserPromptSubmit", ""), ("PreToolUse", "Agent"), ("PostToolUse", "Agent"), ("Stop", "")]
HOOKS_DST_DIR = "C:\\Users\\John Smith\\.claude\\hooks"

c = Checker()


def ns_for(aoc_dir="C:\\nowhere"):
    return exec_functions(
        ["_hook_command", "_is_aoc_hook", "_merge_hook_entries", "_remove_hook_entries", "stop_aoc_processes",
         "PERMISSION_SCRIPT", "PERMISSION_EVENT", "PERMISSION_TIMEOUT_S", "_permission_command", "_merge_permission_hook"],
        {"os": os, "subprocess": subprocess, "HOOK_SPECS": HOOK_SPECS, "HOOKS_DST_DIR": HOOKS_DST_DIR,
         "AOC_DIR": aoc_dir, "TASK_NAME": r"\AOC-test-nonexistent\A",
         "SENTINEL_TASK_NAME": r"\AOC-test-nonexistent\B"},
        path=SETUP_PY)


ns = ns_for()
cmd = ns["_hook_command"]("C:\\Program Files\\Py\\pythonw.exe")

# 1. command quoting (paths with spaces)
c.check("hook command quotes both paths",
        cmd == '"C:/Program Files/Py/pythonw.exe" "C:/Users/John Smith/.claude/hooks/run_hook.pyw"')

# 2. fresh settings: all 4 entries added
s = {}
added, updated = ns["_merge_hook_entries"](s, cmd)
c.check("fresh settings -> 4 entries added", len(added) == 4 and updated == [])
c.check("PreToolUse entry has Agent matcher and the command",
        s["hooks"]["PreToolUse"] == [{"matcher": "Agent", "hooks": [{"type": "command", "command": cmd}]}])

# 3. idempotent
before = copy.deepcopy(s)
added, updated = ns["_merge_hook_entries"](s, cmd)
c.check("re-run adds/updates nothing", added == [] and updated == [])
c.check("re-run leaves settings identical", s == before)

# 4. an old unquoted command (e.g. previous install, other python) gets updated in place
old = {"hooks": {"Stop": [{"matcher": "", "hooks": [
    {"type": "command", "command": "C:/Python39/pythonw.exe C:/Users/x/.claude/hooks/run_hook.pyw"}]}]}}
added, updated = ns["_merge_hook_entries"](old, cmd)
c.check("stale AOC command is updated, not duplicated",
        updated == [("Stop", "")] and len(old["hooks"]["Stop"]) == 1
        and old["hooks"]["Stop"][0]["hooks"][0]["command"] == cmd)
c.check("the other 3 events are added", len(added) == 3)

# 5. non-AOC hooks and other keys are untouched by merge
other = {"theme": "dark", "hooks": {"Stop": [{"matcher": "", "hooks": [
    {"type": "command", "command": "notify-send done"}]}]}}
ns["_merge_hook_entries"](other, cmd)
c.check("unrelated setting kept", other["theme"] == "dark")
c.check("unrelated Stop hook kept alongside AOC's",
        any(h["command"] == "notify-send done" for e in other["hooks"]["Stop"] for h in e["hooks"])
        and any(h["command"] == cmd for e in other["hooks"]["Stop"] for h in e["hooks"]))

# 6. removal: only AOC hooks go, empty entries/events/hooks key cleaned up
removed = ns["_remove_hook_entries"](other)
c.check("removal counts the 4 AOC hooks", removed == 4)
c.check("unrelated hook survives removal",
        other["hooks"] == {"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "notify-send done"}]}]})
c.check("unrelated setting survives removal", other["theme"] == "dark")

only_aoc = {}
ns["_merge_hook_entries"](only_aoc, cmd)
ns["_remove_hook_entries"](only_aoc)
c.check("removing from AOC-only settings drops the hooks key entirely", only_aoc == {})

mixed_entry = {"hooks": {"Stop": [{"matcher": "", "hooks": [
    {"type": "command", "command": cmd}, {"type": "command", "command": "echo hi"}]}]}}
ns["_remove_hook_entries"](mixed_entry)
c.check("AOC hook removed from a shared entry, sibling kept",
        mixed_entry["hooks"]["Stop"][0]["hooks"] == [{"type": "command", "command": "echo hi"}])
c.check("removal on settings without hooks is a no-op", ns["_remove_hook_entries"]({"a": 1}) == 0)
c.check("backslash-style old AOC command is recognized too",
        ns["_is_aoc_hook"]({"command": "C:\\Py\\pythonw.exe C:\\u\\.claude\\hooks\\run_hook.pyw"}))

# 7. stop_aoc_processes kills python processes running from AOC_DIR only
SCRATCH = tempfile.mkdtemp(prefix="aoc_test_stop_")
procs = []
try:
    aoc_dir = os.path.join(SCRATCH, "AOC")
    other_dir = os.path.join(SCRATCH, "AOC-other")  # prefix of the name must not match
    for d in (aoc_dir, other_dir):
        os.makedirs(d)
        with open(os.path.join(d, "sleeper.py"), "w") as f:
            f.write("import time\ntime.sleep(60)\n")
    # forward slashes, like the hook's ensure_monitor launch
    inside = subprocess.Popen([sys.executable, os.path.join(aoc_dir, "sleeper.py").replace("\\", "/")])
    outside = subprocess.Popen([sys.executable, os.path.join(other_dir, "sleeper.py")])
    procs = [inside, outside]
    time.sleep(1.0)
    ns_for(aoc_dir)["stop_aoc_processes"]()
    deadline = time.time() + 10
    while inside.poll() is None and time.time() < deadline:
        time.sleep(0.2)
    c.check("process running a script from AOC_DIR was stopped", inside.poll() is not None)
    c.check("process in a sibling dir sharing the name prefix survived", outside.poll() is None)
finally:
    for p in procs:
        if p.poll() is None:
            p.kill()
            p.wait()
    shutil.rmtree(SCRATCH, ignore_errors=True)

# ── PermissionRequest hook (remote approve): own command + timeout ──
pns = ns_for()
pcmd = pns["_permission_command"]("C:\\Program Files\\Py\\python.exe")
c.check("permission command quoted, forward slashes, console python",
        pcmd == '"C:/Program Files/Py/python.exe" "C:/Users/John Smith/.claude/hooks/aoc_permission.py"')
s = {"hooks": {"PermissionRequest": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "mine.sh"}]}]}}
c.check("permission hook added", pns["_merge_permission_hook"](s, pcmd) == "added")
entries = s["hooks"]["PermissionRequest"]
c.check("user's own PermissionRequest hook kept", entries[0]["hooks"][0]["command"] == "mine.sh")
c.check("AOC entry has the long timeout", entries[1]["hooks"][0] == {"type": "command", "command": pcmd, "timeout": 1800})
c.check("re-run -> unchanged", pns["_merge_permission_hook"](s, pcmd) == "unchanged" and len(entries) == 2)
c.check("new python -> updated in place",
        pns["_merge_permission_hook"](s, pcmd.replace("Py", "Py2")) == "updated" and len(entries) == 2)
c.check("recognized as an AOC hook", pns["_is_aoc_hook"]({"command": pcmd}))
removed = pns["_remove_hook_entries"](s)
c.check("uninstall removes only AOC's permission hook",
        removed == 1 and s["hooks"]["PermissionRequest"] == [{"matcher": "Bash", "hooks": [{"type": "command", "command": "mine.sh"}]}])

c.finish()
