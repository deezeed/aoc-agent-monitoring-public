"""Runs hooks/aoc_statusline.py for real, the way Claude Code does (JSON on
stdin, read stdout), with LOCALAPPDATA pointed at a scratch dir: it saves
rate_limits to AOC\\rate_limits.json, prints a compact line, skips
rewriting unchanged numbers, chains a user's own statusLine command when
aoc_statusline_chain.json exists, and never fails on bad input. Also covers
setup.py's pure _merge_statusline / _remove_statusline."""
import sys, os, json, shutil, subprocess, tempfile, time

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(ROOT, "hooks", "aoc_statusline.py")
work = tempfile.mkdtemp(prefix="aoc_sl_")
hooks_dir = os.path.join(work, "hooks")
os.makedirs(hooks_dir)
script = os.path.join(hooks_dir, "aoc_statusline.py")
shutil.copy(SCRIPT, script)
appdata = os.path.join(work, "appdata")
rl_file = os.path.join(appdata, "AOC", "rate_limits.json")
env = dict(os.environ, LOCALAPPDATA=appdata)

reset = int(time.time()) + 3600
payload = {
    "session_id": "s-1",
    "model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"},
    "workspace": {"current_dir": "C:\\work\\my-project"},
    "rate_limits": {"five_hour": {"used_percentage": 42.3, "resets_at": reset},
                    "seven_day": {"used_percentage": 18.0, "resets_at": reset + 86400}},
}


def run(data, raw=None):
    inp = raw if raw is not None else json.dumps(data).encode("utf-8")
    r = subprocess.run([sys.executable, script], input=inp, capture_output=True, env=env, timeout=30)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


code, out, err = run(payload)
c.check("exit 0, nothing on stderr", code == 0 and not err.strip())
hhmm = time.strftime("%H:%M", time.localtime(reset))
c.check("line: model · folder · 5h · weekly", out == f"Opus 5.5 · my-project · 5h 42% ↻{hhmm} · wk 18%")
saved = json.load(open(rl_file))
c.check("file: both windows saved", saved["five_hour"] == {"used_percentage": 42.3, "resets_at": reset}
        and saved["seven_day"]["used_percentage"] == 18.0)
c.check("file: session id + updated_at", saved["session_id"] == "s-1" and abs(saved["updated_at"] - time.time()) < 30)

mtime = os.path.getmtime(rl_file)
time.sleep(0.05)
run(payload)
c.check("same numbers within a minute -> file not rewritten", os.path.getmtime(rl_file) == mtime)
payload["rate_limits"]["five_hour"]["used_percentage"] = 44.0
run(payload)
c.check("changed numbers -> rewritten", json.load(open(rl_file))["five_hour"]["used_percentage"] == 44.0)

no_rl = dict(payload)
del no_rl["rate_limits"]
os.remove(rl_file)
code, out, _ = run(no_rl)
c.check("no rate_limits (API key) -> no file, line without limits",
        code == 0 and not os.path.exists(rl_file) and out == "Opus 5.5 · my-project")
code, out, err = run(None, raw=b"\xff not json")
c.check("garbage stdin -> exit 0, empty line, no traceback", code == 0 and out == "" and "Traceback" not in err)
code, out, err = run(None, raw=json.dumps([1, 2]).encode())
c.check("non-object JSON -> exit 0, no traceback", code == 0 and "Traceback" not in err)

# chained user statusLine: their output, unchanged; limits still recorded
chained_py = os.path.join(work, "mine.py")
with open(chained_py, "w") as f:
    f.write("import sys, json\nd = json.load(sys.stdin)\nsys.stdout.write('MINE ' + d['session_id'])\n")
with open(os.path.join(hooks_dir, "aoc_statusline_chain.json"), "w") as f:
    json.dump({"statusLine": {"type": "command", "command": f'"{sys.executable}" "{chained_py}"'}}, f)
code, out, _ = run(payload)
c.check("chain: user's command gets the same stdin, output printed as is", out == "MINE s-1")
c.check("chain: limits still recorded", os.path.exists(rl_file))

# ── setup.py pure helpers ──
ns = exec_functions(["_is_aoc_statusline", "_merge_statusline", "_remove_statusline"],
                    {"STATUSLINE_SCRIPT": "aoc_statusline.py"}, path=os.path.join(ROOT, "setup.py"))
merge, remove = ns["_merge_statusline"], ns["_remove_statusline"]
CMD = '"C:/Py/python.exe" "C:/Users/John Smith/.claude/hooks/aoc_statusline.py"'

s = {"theme": "dark"}
c.check("merge: none -> added", merge(s, CMD) == ("added", None)
        and s["statusLine"] == {"type": "command", "command": CMD, "padding": 0} and s["theme"] == "dark")
c.check("merge: again -> unchanged", merge(s, CMD) == ("unchanged", None))
c.check("merge: other python -> updated", merge(s, CMD.replace("Py", "Py2")) == ("updated", None)
        and "Py2" in s["statusLine"]["command"])
c.check("remove: no chain -> key dropped", remove(s, None) is True and "statusLine" not in s)

mine = {"type": "command", "command": "bash ~/.claude/my-line.sh", "padding": 2, "refreshInterval": 5}
s = {"statusLine": dict(mine)}
action, chain = merge(s, CMD)
c.check("merge: user's own -> wrapped, original returned for the chain file", action == "wrapped" and chain == mine)
c.check("merge: wrapped keeps padding/refreshInterval", s["statusLine"]["padding"] == 2 and s["statusLine"]["refreshInterval"] == 5
        and s["statusLine"]["command"] == CMD)
c.check("remove: restores the user's statusLine", remove(s, chain) is True and s["statusLine"] == mine)
c.check("remove: not ours -> untouched", remove(s, chain) is False and s["statusLine"] == mine)
c.check("merge: statusLine without command is replaced, not wrapped",
        merge({"statusLine": {"type": "command"}}, CMD)[0] == "added")

shutil.rmtree(work, ignore_errors=True)
c.finish()
