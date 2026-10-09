"""'Needs your OK' vs 'waiting on you', and switching to a session's terminal:
- hooks/aoc_hook.py run for real (copied to a scratch dir, pointed at a fake
  monitor): Notification permission_prompt / elicitation_dialog -> /update
  with waiting_kind + message; other notification types post nothing; Stop
  says waiting_kind 'turn'.
- monitor.py's _transcript_woy_applies (a transcript line older than the
  hook's word must not clear 'needs permission'), _norm_window_title,
  _ancestor_pids and _pick_session_window."""
import sys, os, json, shutil, subprocess, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))

# ── fake monitor ──
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
work = tempfile.mkdtemp(prefix="aoc_wk_")
hook = os.path.join(work, "aoc_hook.py")
with open(os.path.join(ROOT, "hooks", "aoc_hook.py"), encoding="utf-8") as f:
    src = f.read()
c.check("hook has the AOC_URL line to redirect", 'AOC_URL = "http://127.0.0.1:5151"' in src)
with open(hook, "w", encoding="utf-8") as f:
    f.write(src.replace('AOC_URL = "http://127.0.0.1:5151"', f'AOC_URL = "http://127.0.0.1:{srv.server_port}"'))
env = dict(os.environ, USERPROFILE=work, HOME=work, TEMP=work, TMP=work)


def run(event):
    posts.clear()
    base = {"session_id": "sess-1", "cwd": work, "transcript_path": ""}
    subprocess.run([sys.executable, hook], input=json.dumps(dict(base, **event)).encode(), env=env, timeout=60)
    return [p for p in posts if p[0] == "/update"]


u = run({"hook_event_name": "Notification", "notification_type": "permission_prompt",
         "message": "Claude needs your permission to use Bash"})
c.check("permission prompt -> one /update", len(u) == 1)
p = u[0][1] if u else {}
c.check("...waiting, kind permission, message", p.get("waiting_on_you") is True and p.get("waiting_kind") == "permission"
        and p.get("waiting_message") == "Claude needs your permission to use Bash" and p.get("session_active") is True)
u = run({"hook_event_name": "Notification", "notification_type": "elicitation_dialog", "message": "Pick one"})
c.check("question dialog -> kind question", len(u) == 1 and u[0][1].get("waiting_kind") == "question")
c.check("idle_prompt -> nothing posted", run({"hook_event_name": "Notification", "notification_type": "idle_prompt",
                                              "message": "Claude is waiting for your input"}) == [])
u = run({"hook_event_name": "Stop"})
c.check("Stop -> waiting, kind turn", len(u) == 1 and u[0][1].get("waiting_on_you") is True and u[0][1].get("waiting_kind") == "turn")
u = run({"hook_event_name": "UserPromptSubmit"})
c.check("UserPromptSubmit -> not waiting, no kind", len(u) == 1 and u[0][1].get("waiting_on_you") is False
        and "waiting_kind" not in u[0][1])
srv.shutdown()

# ── monitor.py pure pieces ──
ns = exec_functions(["_transcript_woy_applies", "_norm_window_title", "_pick_session_window", "_ancestor_pids"])
applies = ns["_transcript_woy_applies"]
c.check("transcript line older than the hook -> ignored", not applies({"waiting_hook_epoch": 100}, {"woy_epoch": 90}))
c.check("newer line (tool_result after the OK) -> applies", applies({"waiting_hook_epoch": 100}, {"woy_epoch": 105}))
c.check("no hook yet -> applies", applies({}, {"woy_epoch": 1}))

norm = ns["_norm_window_title"]
c.check("spinner glyph stripped", norm("◑ AOC pokračování") == "aoc pokračování" and norm("✳ Phantom AI") == "phantom ai")

procs = [(10, "claude.exe", 9), (9, "bash.exe", 8), (8, "windowsterminal.exe", 1), (1, "explorer.exe", 0)]
c.check("ancestors nearest first", ns["_ancestor_pids"](10, procs) == [9, 8, 1])
c.check("loop-safe", ns["_ancestor_pids"](5, [(5, "a", 6), (6, "b", 5)]) == [6, 5])
pick = ns["_pick_session_window"]
wins = [(111, 8, "Windows Terminal"), (222, 50, "◑ AOC pokračování"), (333, 51, "◐ Phantom AI"), (444, 52, "Phantom AI notes")]
c.check("ancestor window wins", pick(wins, [9, 8, 1], "AOC pokračování") == (111, "ancestor"))
c.check("else exact title", pick(wins, [9], "AOC pokračování") == (222, "title"))
c.check("exact beats substring", pick(wins, [], "Phantom AI") == (333, "title"))
c.check("unique substring ok", pick(wins, [], "pokračování") == (222, "title"))
c.check("ambiguous substring -> none", pick(wins + [(555, 53, "x pokračování y")], [], "pokračování") == (None, None))
c.check("no / too short title -> none", pick(wins, [], "") == (None, None) and pick(wins, [], "AO") == (None, None))

shutil.rmtree(work, ignore_errors=True)
c.finish()
