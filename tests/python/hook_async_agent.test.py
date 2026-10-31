"""A background Agent call (run_in_background) answers PostToolUse at once
with {isAsync, status: "async_launched"}: hooks/aoc_hook.py (run for real
against a fake monitor) must keep that agent running with its tasks open,
not mark it done. A normal finished call still posts done + tasks done."""
import sys, os, json, subprocess, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(__file__))
from lib.check import Checker

c = Checker()
ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
posts = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"{}")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        posts.append((self.path, json.loads(self.rfile.read(n) or b"{}")))
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"{}")


srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
work = tempfile.mkdtemp(prefix="aoc_async_")
hook = os.path.join(work, "aoc_hook.py")
with open(os.path.join(ROOT, "hooks", "aoc_hook.py"), encoding="utf-8") as f:
    src = f.read()
with open(hook, "w", encoding="utf-8") as f:
    f.write(src.replace('AOC_URL = "http://127.0.0.1:5151"', f'AOC_URL = "http://127.0.0.1:{srv.server_port}"'))
env = dict(os.environ, USERPROFILE=work, HOME=work, TEMP=work, TMP=work)


def run(resp):
    posts.clear()
    ev = {"session_id": "sess-1", "cwd": work, "transcript_path": "", "hook_event_name": "PostToolUse",
          "tool_name": "Agent", "tool_use_id": "toolu_1",
          "tool_input": {"description": "Fix the bar", "subagent_type": "general-purpose"}, "tool_response": resp}
    subprocess.run([sys.executable, hook], input=json.dumps(ev).encode(), env=env, timeout=60)
    ups = [p[1] for p in posts if p[0] == "/update" and p[1].get("agents")]
    return ups[-1]["agents"][0] if ups else {}


a = run({"isAsync": True, "status": "async_launched", "agentId": "abc", "description": "Fix the bar"})
c.check("async launch -> still running", a.get("status") == "running")
c.check("async launch -> tasks not touched (none marked done)", "tasks" not in a)
c.check("async launch -> no completed_at", "completed_at" not in a and a.get("subagent_type") == "general-purpose")
a = run({"status": "completed", "content": [{"type": "text", "text": "ok"}], "totalTokens": 10})
c.check("finished call -> done with tasks done", a.get("status") == "done" and all(t["done"] for t in a.get("tasks", [])) and a.get("tasks"))
srv.shutdown()
c.finish()
