"""The hook sends aoc_token.txt only to its own AOC (audit FIND-022): a port
named by AOC_ORCH_PORT -- which any program can set before starting `claude`
and listen on -- gets the status post without the token.

Runs a temp COPY of hooks/aoc_hook.py (deployed the way setup.py does) whose
MONITOR_SCRIPT points at a temp folder holding a FAKE token, against a local
listener. Never reads the real token, never touches 5151 or ~/.claude."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(__file__))
from lib.check import Checker

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FAKE = "FAKE-TOKEN-0123456789abcdef"
c = Checker()

tmp = tempfile.mkdtemp(prefix="aoc_hook_token_")
aoc_dir = os.path.join(tmp, "aoc")
os.makedirs(aoc_dir)
with open(os.path.join(aoc_dir, "aoc_token.txt"), "w") as f:
    f.write(FAKE)
with open(os.path.join(ROOT, "hooks", "aoc_hook.py"), encoding="utf-8") as f:
    SRC = f.read()
c.check("the hook still has its default AOC address (the test swaps it)", SRC.count('"http://127.0.0.1:5151"') == 2)

seen = []


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        seen.append((self.path, self.headers.get("X-AOC-Token")))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


s = socket.socket()
s.bind(("127.0.0.1", 0))
port = s.getsockname()[1]
s.close()
srv = HTTPServer(("127.0.0.1", port), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()


def run_hook(name, default_port, env_extra):
    """Deploy a copy (default AOC address -> default_port) and fire one event."""
    hooks_dir = os.path.join(tmp, name)
    os.makedirs(hooks_dir)
    src = SRC.replace("__MONITOR_SCRIPT__", os.path.join(aoc_dir, "monitor.py").replace("\\", "/"))
    src = src.replace("__PYTHONW__", sys.executable.replace("\\", "/"))
    src = src.replace('"http://127.0.0.1:5151"', f'"http://127.0.0.1:{default_port}"')
    hook_py = os.path.join(hooks_dir, "aoc_hook.py")
    with open(hook_py, "w", encoding="utf-8") as f:
        f.write(src)
    ev = os.path.join(hooks_dir, "event.json")
    with open(ev, "w", encoding="utf-8") as f:
        json.dump({"hook_event_name": "UserPromptSubmit", "session_id": "token-test-" + name,
                   "cwd": tmp, "prompt": "hi"}, f)
    env = {k: v for k, v in os.environ.items() if not k.startswith("AOC_ORCH_")}
    env.update(env_extra)
    del seen[:]
    subprocess.run([sys.executable, hook_py, "--file", ev], env=env, timeout=60)
    return list(seen)


try:
    # a port the environment names (the default AOC is elsewhere: port 9, nothing there)
    got = run_hook("env_port", 9, {"AOC_ORCH_RUN": "r1", "AOC_ORCH_PORT": str(port)})
    c.check("a port named by AOC_ORCH_PORT still gets the status post", any(p == "/update" for p, _ in got))
    c.check("... without the token (FIND-022)", got and all(t is None for _, t in got))

    # the hook's own AOC (default address) -- e.g. a tunnel is on, so the token is needed
    got = run_hook("own_aoc", port, {})
    c.check("its own AOC gets the post with the token", any(p == "/update" and t == FAKE for p, t in got))

    # an Orchestrate worker of that same AOC (its port = the default one)
    got = run_hook("own_worker", port, {"AOC_ORCH_RUN": "r2", "AOC_ORCH_PORT": str(port)})
    c.check("a worker of its own AOC still sends the token", any(p == "/update" and t == FAKE for p, t in got))
finally:
    srv.shutdown()

c.finish()
