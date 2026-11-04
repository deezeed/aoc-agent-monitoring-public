"""Restart AOC: monitor.py's _restart_aoc (POST /restart, Health -> Restart
AOC) hands off to `setup.py --restart` in a detached process, names held
prompts / tunnel / update first unless forced; the route is this-PC-only
and refused in a preview; setup.py's --restart is a known flag and doesn't
`schtasks /End` the job it may run in. Nothing is really restarted."""
import sys, os, subprocess

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions, read_monitor_source, read_source
from lib.check import Checker

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
c = Checker()
spawned = []
ns = exec_functions(["_restart_blockers", "_restart_aoc"],
                    {"os": os, "sys": sys, "AOC_DIR": "C:\\aoc", "_perm_requests": {}, "_tunnel_status": ["off"],
                     "_update_job": {"state": "idle"}, "_hooks_health": {"repairing": False}})
r = ns["_restart_aoc"](spawn=lambda args, cwd: spawned.append((args, cwd)))
c.check("nothing held -> restarts", r == {"ok": True, "blockers": []} and len(spawned) == 1)
args, cwd = spawned[0]
c.check("runs setup.py --restart from the AOC folder", args[-2:] == ["C:\\aoc\\setup.py", "--restart"] and cwd == "C:\\aoc")
c.check("with a console python (python.exe), not pythonw", os.path.basename(args[0]).lower() in ("python.exe", os.path.basename(sys.executable).lower()))

spawned.clear()
ns["_hooks_health"]["repairing"] = True
r = ns["_restart_aoc"](spawn=lambda a, w: spawned.append(a))
c.check("hook repair running -> asks first, nothing spawned", r == {"ok": False, "blockers": ["hook repair running"]} and not spawned)
ns["_hooks_health"]["repairing"] = False
ns["_perm_requests"]["x"] = {"state": "pending"}
r = ns["_restart_aoc"](spawn=lambda a, w: spawned.append(a))
c.check("held prompt -> asks first, nothing spawned", r == {"ok": False, "blockers": ["1 prompt(s) waiting for your OK"]} and not spawned)
r = ns["_restart_aoc"](force=True, spawn=lambda a, w: spawned.append(a))
c.check("force -> restarts anyway, still reports what it dropped", r["ok"] and r["blockers"] and len(spawned) == 1)

src = read_monitor_source()
seg = src[src.index('elif path_no_qs == "/restart":'):]
seg = seg[:seg.index("elif path_no_qs", 10)]
c.check("route: this PC only", "_is_direct_loopback()" in seg)
c.check("route: a preview never restarts the real AOC", "AOC_PREVIEW" in seg)
c.check("installer launch shares the detached spawn", "_spawn_detached([exe] + _INSTALLER_SILENT_ARGS" in src)

# setup.py
setup_src = read_source(os.path.join(ROOT, "setup.py"))
chk = exec_functions(["_BOOL_FLAGS", "_VALUE_FLAGS", "_check_args"], {}, path=os.path.join(ROOT, "setup.py"))["_check_args"]
c.check("--restart is a known flag", chk(["setup.py", "--restart"]) == ("run", ""))
rs = setup_src[setup_src.index("def restart_aoc("):]
rs = rs[:rs.index("\ndef ", 10)]
c.check("--restart doesn't end the scheduled tasks (it may run inside one)", "stop_aoc_processes(end_tasks=False)" in rs)
c.check("--restart calls schtasks itself (no shell path rewriting)", '["schtasks", "/Run", "/TN", TASK_NAME]' in rs)
c.check("falls back to starting watchdog.py directly", "WATCHDOG_SCRIPT" in rs)
st = setup_src[setup_src.index("def stop_aoc_processes("):]
st = st[:st.index("\ndef ", 10)]
c.check("stop_aoc_processes still ends tasks by default (installer path)", "end_tasks: bool = True" in st)
r = subprocess.run([sys.executable, os.path.join(ROOT, "setup.py"), "--help"], capture_output=True, text=True, encoding="utf-8")
c.check("--help documents --restart", "--restart" in r.stdout)
c.finish()
