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
