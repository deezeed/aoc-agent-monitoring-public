"""Launch a Claude Code session from the dashboard (monitor.py):
_launch_validate (folder must exist and be absolute, prompt size, model),
_launch_argv (a list, never a shell string: quotes/&/; reach Claude as
typed), _launch_dirs (existing folders, newest first, once each) and
_launch_session (new console, cwd, rate limit, no claude installed)."""
import sys, os, json, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
tmp = tempfile.mkdtemp(prefix="aoc_ln_")
exe = [os.path.join(tmp, "claude.exe")]
ns = exec_functions(["_LAUNCH_MODELS", "_LAUNCH_MIN_GAP_S", "_launch_last", "_launch_validate", "_launch_argv",
                     "_launch_dirs", "_folder_trusted", "_load_json_file", "_launch_session"],
                    {"os": os, "time": __import__("time"), "subprocess": None, "_find_claude_exe": lambda: exe[0]})
v = ns["_launch_validate"]
args, err = v({"cwd": f'  "{tmp}"  ', "prompt": "  fix the tests  ", "model": "Sonnet"})
c.check("valid: quotes/space trimmed, model lowercased", err is None and args == {"cwd": os.path.normpath(tmp), "prompt": "fix the tests", "model": "sonnet"})
c.check("no folder", v({"cwd": ""})[1] and v({})[1])
c.check("relative folder refused", v({"cwd": "projects"})[1] is not None)
c.check("missing folder refused", v({"cwd": os.path.join(tmp, "nope")})[1] is not None)
c.check("a file is not a folder", v({"cwd": __file__})[1] is not None)
c.check("unknown model", v({"cwd": tmp, "model": "gpt"})[1] == "unknown model")
c.check("huge prompt", "too long" in v({"cwd": tmp, "prompt": "x" * 20001})[1])
c.check("empty prompt ok", v({"cwd": tmp})[0]["prompt"] == "")
c.check("garbage body", v("x")[1] == "bad request")

a = ns["_launch_argv"]
tricky = 'say "hi" & del *; echo %PATH% | more'
c.check("argv: prompt is ONE argument, untouched", a("C:/c.exe", {"prompt": tricky, "model": ""}) == ["C:/c.exe", tricky])
c.check("argv: model flag", a("c", {"prompt": "x", "model": "haiku"}) == ["c", "--model", "haiku", "x"])
c.check("argv: no prompt -> plain interactive", a("c", {"prompt": "", "model": ""}) == ["c"])

d = ns["_launch_dirs"]
exists = {"C:\\p\\AOC", "C:\\p\\Omni", "C:\\Users\\m"}
rows = [("C:\\p\\AOC", 300), ("c:\\p\\aoc", 100), ("C:\\p\\Omni", 200), ("C:\\gone", 999), (None, 5), ("C:\\Users\\m", None)]
out = d(rows, isdir=lambda f: f in exists)
c.check("dirs: existing only, newest first", [x["path"] for x in out] == ["C:\\p\\AOC", "C:\\p\\Omni", "C:\\Users\\m"])
c.check("dirs: names", [x["name"] for x in out] == ["AOC", "Omni", "m"])
c.check("dirs: limit", len(d([(f"C:\\x{i}", i) for i in range(50)], isdir=lambda f: True, limit=5)) == 5)

calls = []


class P:
    pid = 4242


def popen(argv, cwd=None, creationflags=0):
    calls.append((argv, cwd, creationflags))
    return P()


ls = ns["_launch_session"]
r = ls({"cwd": tmp, "prompt": "hello", "model": "opus"}, now=1000, popen=popen)
c.check("launched: pid, folder, trust flag", r["ok"] and r["pid"] == 4242 and r["cwd"] == os.path.normpath(tmp) and r["trusted"] in (True, False))
c.check("launched in that folder, new console, no shell", calls[0] == ([exe[0], "--model", "opus", "hello"], os.path.normpath(tmp),
                                                                      0x10 if os.name == "nt" else 0))
r = ls({"cwd": tmp}, now=1002, popen=popen)
c.check("second launch within 5 s refused", not r["ok"] and len(calls) == 1)
r = ls({"cwd": tmp}, now=1006, popen=popen)
c.check("after 5 s ok", r["ok"] and len(calls) == 2)
r = ls({"cwd": "nope"}, now=2000, popen=popen)
c.check("validation error passed through", not r["ok"] and "folder" in r["error"] and len(calls) == 2)
exe[0] = None
r = ls({"cwd": tmp}, now=3000, popen=popen)
c.check("no claude installed -> clear error, nothing started", not r["ok"] and "not found" in r["error"] and len(calls) == 2)

ft = ns["_folder_trusted"]
cfg = {"projects": {"C:/Users/m/OneDrive/Projekty/AOC": {"hasTrustDialogAccepted": True},
                    "C:/Users/m": {"hasTrustDialogAccepted": False}, "D:/work": {"hasTrustDialogAccepted": True}, "x": "junk"}}
c.check("trusted folder (backslashes, case)", ft(r"c:\users\M\OneDrive\Projekty\aoc", cfg))
c.check("subfolder of a trusted folder", ft(r"D:\work\api\src", cfg))
c.check("not accepted -> untrusted", not ft(r"C:\Users\m", cfg) and not ft(r"C:\Users\m\new", cfg))
c.check("unknown / empty config", not ft(r"E:\x", cfg) and not ft(r"C:\a", {}))

shutil.rmtree(tmp, ignore_errors=True)
c.finish()
