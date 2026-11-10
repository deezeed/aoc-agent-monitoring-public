"""Code reload: watchdog.py restarts monitor.py once monitor.py changed on
disk, the change settled CODE_SETTLE_S and compiles, and monitor.py's
GET /restart_ok names no blockers. Tests the pure pieces straight from the
shipped files: _reload_step, _code_stamp, _compiles (watchdog.py) and
_restart_blockers (monitor.py), plus _restart_ok against a real local
HTTP server."""
import sys, os, json, tempfile, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions, extract_functions
from lib.check import Checker

WATCHDOG_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "watchdog.py"))
c = Checker()

consts = {}
exec(extract_functions(["CODE_SETTLE_S"], path=WATCHDOG_PATH)["CODE_SETTLE_S"], consts)
SETTLE = consts["CODE_SETTLE_S"]
c.check("settle window is 60 s", SETTLE == 60)

fns = exec_functions(["_reload_step", "_code_stamp", "_compiles"],
                     {"os": os, "CODE_SETTLE_S": SETTLE, "MONITOR": WATCHDOG_PATH}, path=WATCHDOG_PATH)
step, stamp, compiles = fns["_reload_step"], fns["_code_stamp"], fns["_compiles"]

A, B, C = (1, 10), (2, 11), (3, 12)
# _reload_step
c.check("unchanged -> none", step(A, None, A, 100) == ("none", None))
c.check("unreadable file -> none", step(A, None, None, 100) == ("none", None))
c.check("first sight of a change -> wait, remembered", step(A, None, B, 100) == ("wait", (B, 100)))
c.check("still settling -> wait", step(A, (B, 100), B, 100 + SETTLE - 1) == ("wait", (B, 100)))
c.check("settled -> ready", step(A, (B, 100), B, 100 + SETTLE) == ("ready", (B, 100)))
c.check("changed again while settling -> clock restarts", step(A, (B, 100), C, 150) == ("wait", (C, 150)))
c.check("reverted before settling -> none", step(A, (B, 100), A, 200) == ("none", None))

# _code_stamp / _compiles on real files
d = tempfile.mkdtemp()
good, bad = os.path.join(d, "good.py"), os.path.join(d, "bad.py")
open(good, "w", encoding="utf-8").write("# -*- coding: utf-8 -*-\nx = 'čšž'\n")
open(bad, "w", encoding="utf-8").write("def broken(:\n")
s1 = stamp(good)
c.check("stamp is (mtime_ns, size)", isinstance(s1, tuple) and s1[1] == os.path.getsize(good))
open(good, "a", encoding="utf-8").write("y = 1\n")
c.check("an edit changes the stamp", stamp(good) != s1)
c.check("missing file -> None", stamp(os.path.join(d, "nope.py")) is None)
c.check("good file compiles", compiles(good) == "")
c.check("syntax error is reported, not raised", compiles(bad).startswith("SyntaxError"))
c.check("the real monitor.py compiles", compiles(os.path.join(os.path.dirname(WATCHDOG_PATH), "monitor.py")) == "")

# dev checkout: reload only once the change is committed
import subprocess
g = exec_functions(["_uncommitted", "_reload_blocker"], {"os": os, "subprocess": subprocess, "MONITOR": WATCHDOG_PATH}, path=WATCHDOG_PATH)
unc, blocker = g["_uncommitted"], g["_reload_blocker"]
c.check("clean + compiles -> go", blocker("", False) == "")
c.check("compile error wins", blocker("SyntaxError: x", True) == "SyntaxError: x")
c.check("uncommitted edits block", "uncommitted" in blocker("", True))
c.check("git unknown (None) doesn't block", blocker("", None) == "")
plain = tempfile.mkdtemp()
open(os.path.join(plain, "monitor.py"), "w").write("x = 1\n")
c.check("not a git checkout (installed release) -> False", unc(os.path.join(plain, "monitor.py")) is False)
repo = tempfile.mkdtemp()
git = lambda *a: subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True)
git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
mp = os.path.join(repo, "monitor.py")
open(mp, "w").write("x = 1\n"); git("add", "monitor.py"); git("commit", "-qm", "init")
c.check("committed -> False", unc(mp) is False)
open(mp, "a").write("y = 2\n")
c.check("edited, not committed -> True", unc(mp) is True)
open(os.path.join(repo, "other.py"), "w").write("z = 3\n")
git("commit", "-qam", "edit")
c.check("other untracked files don't count -> False after commit", unc(mp) is False)
os.makedirs(os.path.join(repo, "web"))
js = os.path.join(repo, "web", "app.js")
open(js, "w").write("let a = 1;\n")
c.check("a new, uncommitted web file blocks too", unc(mp, js) is True)
git("add", "web"); git("commit", "-qm", "web")
c.check("web file committed -> False", unc(mp, js) is False)
open(js, "a").write("let b = 2;\n")
c.check("edited web file -> True", unc(mp, js) is True)
c.check("...but monitor.py alone is still clean", unc(mp) is False)

# monitor.py + web/ files together decide when the monitor reloads
srv = os.path.join(repo, "server")
os.makedirs(srv)
p1 = os.path.join(srv, "01_core.py")
open(p1, "w").write("x = 1\n")
web = os.path.dirname(js)
os.makedirs(os.path.join(web, "js"))
for n in ("index.html", "app.css"):
    open(os.path.join(web, n), "w").write("x\n")
js1 = os.path.join(web, "js", "01_core.js")
open(js1, "w").write("let a = 1;\n")
w = exec_functions(["_code_stamp", "_compiles", "_server_parts", "_web_files", "_monitor_stamp", "_monitor_compiles"],
                   {"os": os, "MONITOR": mp, "WEB_DIR": web, "SERVER_DIR": srv}, path=WATCHDOG_PATH)
s0 = w["_monitor_stamp"]()
c.check("monitor stamp covers monitor.py, each server part, index.html, app.css and each web/js part",
        [n for n, _ in s0] == ["monitor.py", "01_core.py", "index.html", "app.css", "01_core.js"]
        and all(st for _, st in s0))
open(js1, "a").write("let c = 3;\n")
s1 = w["_monitor_stamp"]()
c.check("a web edit alone changes the monitor stamp", s1 != s0)
open(p1, "a").write("y = 2\n")
s2 = w["_monitor_stamp"]()
c.check("a server part edit changes it", s2 != s1)
open(os.path.join(srv, "02_more.py"), "w").write("z = 3\n")
c.check("a new server part changes it", w["_monitor_stamp"]() != s2)
s3 = w["_monitor_stamp"]()
open(os.path.join(web, "js", "02_more.js"), "w").write("let d = 4;\n")
c.check("a new web/js part changes it", w["_monitor_stamp"]() != s3)
git("add", "-A"); git("commit", "-qm", "parts")
c.check("web/ dir committed -> clean", unc(mp, srv, web) is False)
open(os.path.join(web, "js", "03_new.js"), "w").write("let e = 5;\n")
c.check("a new, uncommitted web/js part -> dirty", unc(mp, srv, web) is True)
c.check("all parts compile -> ''", w["_monitor_compiles"]() == "")
open(os.path.join(srv, "03_bad.py"), "w").write("def broken(:\n")
c.check("a broken part blocks the reload, named", w["_monitor_compiles"]().startswith("03_bad.py: SyntaxError"))
c.check("server/ with an uncommitted part -> dirty", unc(mp, srv, js) is True)
src_w = open(WATCHDOG_PATH, encoding="utf-8").read()
c.check("the loop reloads on the monitor stamp, server parts + web files included",
        "_reload_step(running_stamp, pending, _monitor_stamp()" in src_w
        and "_reload_blocker(_monitor_compiles(), _uncommitted(MONITOR, SERVER_DIR, WEB_DIR))" in src_w)

# monitor.py _restart_blockers
blockers = exec_functions(["_restart_blockers"], {})["_restart_blockers"]
c.check("nothing held, no tunnel -> safe", blockers({}, "off") == [])
c.check("answered prompts don't block", blockers({"a": {"state": "allow"}}, None) == [])
c.check("held prompt blocks", blockers({"a": {"state": "pending"}, "b": {"state": "deny"}}, "off") == ["1 prompt(s) waiting for your OK"])
c.check("open tunnel blocks", blockers({}, "ready") == ["remote tunnel is open"])
c.check("starting tunnel blocks", blockers(None, "starting") == ["remote tunnel is open"])
c.check("update on its way blocks", blockers({}, "off", "installing") == ["update installing"])
c.check("idle / failed update doesn't block", blockers({}, "off", "idle") == [] and blockers({}, "off", "error") == [])
c.check("hook repair running blocks (a reload would kill setup.py mid-repair)", blockers({}, "off", "idle", True) == ["hook repair running"])

# _restart_ok against a real server
REPLY = [b""]
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(REPLY[0])
    def log_message(self, *a):
        pass
srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
ok = exec_functions(["_restart_ok"], {"urllib": urllib, "json": json, "STATUS_TIMEOUT": 2,
                                      "RESTART_OK_URL": f"http://127.0.0.1:{srv.server_address[1]}/restart_ok"},
                    path=WATCHDOG_PATH)["_restart_ok"]
REPLY[0] = json.dumps({"pid": 4242, "blockers": []}).encode()
c.check("reads pid + empty blockers", ok() == (4242, []))
REPLY[0] = json.dumps({"pid": 4242, "blockers": ["remote tunnel is open"]}).encode()
c.check("reads blockers", ok() == (4242, ["remote tunnel is open"]))
REPLY[0] = b"not json"
c.check("garbage -> unreachable (None, None)", ok() == (None, None))
srv.shutdown()

# an old monitor without /restart_ok must never be force-restarted
src = open(WATCHDOG_PATH, encoding="utf-8").read()
c.check("no answer from /restart_ok blocks the reload", '"monitor did not answer /restart_ok" if blockers is None' in src)
c.finish()
