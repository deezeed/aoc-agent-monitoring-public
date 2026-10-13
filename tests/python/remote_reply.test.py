"""Reply from the dashboard (answer a finished turn while you're away):
- monitor.py's pure core (_reply_should_hold, _reply_open / _reply_poll /
  _reply_send / _reply_pending_by_session, the 7-in-a-row streak, settings
  merge that keeps fields the caller didn't send).
- hooks/aoc_permission.py's Stop path for real, against a fake monitor whose
  routes run those same functions: claude -p never held, at the PC / off ->
  no hold, away -> held then reply / "done" / back at the PC / monitor gone;
  and the script run as Claude Code runs it (stdin JSON, stdout block JSON)."""
import sys, os, json, importlib.util, secrets, shutil, subprocess, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
pushes = []
ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
data_dir = tempfile.mkdtemp(prefix="aoc_rr_")

ns = exec_functions(
    ["_load_json_file", "REMOTE_APPROVE_FILE", "_PERM_MODES", "_REPLY_MODES", "_PERM_POLL_STALE_S", "_PERM_BACK_IDLE_S",
     "_sanitize_remote_approve", "_load_remote_approve", "_save_remote_approve",
     "_REPLY_MAX_STREAK", "_reply_lock", "_reply_requests", "_reply_streak", "_reply_should_hold", "_reply_expire",
     "_reply_open", "_reply_poll", "_reply_send", "_reply_pending_by_session"],
    {"os": os, "json": json, "threading": threading, "_secrets": secrets, "AOC_DATA_DIR": os.path.join(data_dir, "AOC"),
     "_push_notify": lambda title, body, tag="aoc", urgent=False, **k: pushes.append((title, body, urgent)) or 1,
     "_push_session_name": lambda sid: f"Session {sid}", "_log_bg_error": lambda *a: None})

# ── settings ──
san = ns["_sanitize_remote_approve"]
c.check("reply defaults to away", san({})["reply"] == "away")
c.check("unknown reply mode -> away", san({"reply": "always"})["reply"] == "away")
c.check("reply off kept", san({"reply": "off"})["reply"] == "off")
ns["_save_remote_approve"]({"reply": "off"})
ns["_save_remote_approve"]({"mode": "always", "idle_min": 9})
c.check("saving mode/idle keeps the stored reply setting",
        ns["_load_remote_approve"]() == {"mode": "always", "idle_min": 9, "reply": "off"})
ns["_save_remote_approve"]({"reply": "away"})
c.check("saving reply alone keeps mode/idle", ns["_load_remote_approve"]() == {"mode": "always", "idle_min": 9, "reply": "away"})

# ── hold decision ──
hold = ns["_reply_should_hold"]
away5 = {"mode": "away", "idle_min": 5, "reply": "away"}
c.check("at the PC -> no hold", not hold(away5, 30, 0))
c.check("idle 5 min -> hold", hold(away5, 300, 0))
c.check("idle unknown -> no hold", not hold(away5, None, 0))
c.check("reply off -> no hold", not hold({**away5, "reply": "off"}, 9999, 0))
c.check("permission mode 'always' doesn't make turn ends hold at the PC", not hold({**away5, "mode": "always"}, 0, 0))
c.check("7 replies in a row -> no hold (Claude Code caps at 8)", not hold(away5, 9999, 7) and hold(away5, 9999, 6))

# ── request lifecycle ──
r = ns["_reply_open"]({"session_id": "s1", "idle_s": 600, "last_assistant_message": "Ship it?"}, away5, 1000)
c.check("open -> hold + id", r["hold"] is True and r["id"])
c.check("held turn pushed to the phone, urgent, question wording",
        pushes[-1][0] == "\U0001f4ac Session s1 asks" and pushes[-1][1] == "Ship it?" and pushes[-1][2] is True)
pend = ns["_reply_pending_by_session"](1002)
c.check("pending listed with the message + age", pend.get("s1") == {"id": r["id"], "message": "Ship it?", "age_s": 2})
c.check("poll while pending", ns["_reply_poll"](r["id"], 700, away5, 1003) == {"state": "pending"})
c.check("empty reply rejected", ns["_reply_send"](r["id"], "reply", "   ") == (False, "empty reply"))
c.check("unknown action rejected", ns["_reply_send"](r["id"], "maybe", "x")[0] is False)
c.check("reply accepted", ns["_reply_send"](r["id"], "reply", " yes, ship it ") == (True, ""))
c.check("second answer rejected", ns["_reply_send"](r["id"], "done") == (False, "no longer waiting"))
c.check("answered -> not listed", "s1" not in ns["_reply_pending_by_session"](1004))
c.check("poll hands out the reply once (trimmed)", ns["_reply_poll"](r["id"], 700, away5, 1004) == {"state": "reply", "reply": "yes, ship it"})
c.check("then gone -> release", ns["_reply_poll"](r["id"], 700, away5, 1005) == {"state": "release"})
c.check("streak counted", ns["_reply_streak"].get("s1") == 1)

r = ns["_reply_open"]({"session_id": "s2", "idle_s": 600}, away5, 2000)
ns["_reply_send"](r["id"], "done")
c.check("'done for now' -> release", ns["_reply_poll"](r["id"], 700, away5, 2001)["state"] == "release")
c.check("...and no streak for a release", "s2" not in ns["_reply_streak"])

r = ns["_reply_open"]({"session_id": "s3", "idle_s": 600}, away5, 3000)
c.check("back at the PC -> release", ns["_reply_poll"](r["id"], 1, away5, 3001)["state"] == "release")
r = ns["_reply_open"]({"session_id": "s3", "idle_s": 600}, away5, 3100)
c.check("replies switched off mid-hold -> release", ns["_reply_poll"](r["id"], 700, {**away5, "reply": "off"}, 3101)["state"] == "release")

r1 = ns["_reply_open"]({"session_id": "s4", "idle_s": 600, "last_assistant_message": "old"}, away5, 4000)
r2 = ns["_reply_open"]({"session_id": "s4", "idle_s": 600, "last_assistant_message": "new"}, away5, 4001)
c.check("newer turn end replaces the older one", ns["_reply_pending_by_session"](4002)["s4"]["id"] == r2["id"]
        and ns["_reply_send"](r1["id"], "reply", "x")[0] is False)
c.check("hook stopped polling -> expired", "s4" not in ns["_reply_pending_by_session"](4001 + 16))
c.check("no session id -> no hold", ns["_reply_open"]({"idle_s": 600}, away5, 5000) == {"hold": False})

# streak: stop_hook_active true keeps counting, false (you typed) resets
ns["_reply_streak"]["s5"] = 7
c.check("streak at 7 + continuing turn -> no hold",
        ns["_reply_open"]({"session_id": "s5", "idle_s": 600, "stop_hook_active": True}, away5, 6000) == {"hold": False})
c.check("a turn you started resets the streak -> hold",
        ns["_reply_open"]({"session_id": "s5", "idle_s": 600, "stop_hook_active": False}, away5, 6001)["hold"] is True)
ns["_reply_open"]({"session_id": "s6", "idle_s": 600, "last_assistant_message": "a" * 3000 + "b" * 6000}, away5, 6003)
c.check("message keeps its last 6000 chars", ns["_reply_pending_by_session"](6004)["s6"]["message"] == "b" * 6000)

# ── the real hook script (Stop path) against a fake monitor ──
cfg = {"v": dict(away5)}
posts = []
clock_now = [10000.0]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        idle = float(qs["idle"][0]) if "idle" in qs else None
        self._send(ns["_reply_poll"](qs["id"][0], idle, cfg["v"], clock_now[0]))

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        posts.append((self.path, data))
        if self.path == "/reply/request":
            self._send(ns["_reply_open"](data, cfg["v"], clock_now[0]))
        else:
            self._send({"ok": True})


srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]

work = tempfile.mkdtemp(prefix="aoc_rrh_")
script = os.path.join(work, "aoc_permission.py")
with open(os.path.join(ROOT, "hooks", "aoc_permission.py"), encoding="utf-8") as f:
    src = f.read()
src = src.replace("http://127.0.0.1:5151", f"http://127.0.0.1:{port}").replace("__MONITOR_SCRIPT__", work.replace("\\", "/") + "/monitor.py")
with open(script, "w", encoding="utf-8") as f:
    f.write(src)
spec = importlib.util.spec_from_file_location("aoc_permission", script)
hookmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hookmod)

HOOK = {"session_id": "sess-r", "cwd": "C:/proj", "hook_event_name": "Stop", "stop_hook_active": False,
        "last_assistant_message": "Tests pass. Shall I also update the README?"}
CLI = {"CLAUDE_CODE_ENTRYPOINT": "cli"}


def run_with(idles, on_sleep=None, env=CLI, hook=HOOK):
    seq = list(idles)
    ticks = [0.0]

    def idle():
        return seq.pop(0) if len(seq) > 1 else seq[0]

    def sleep(_s):
        ticks[0] += 1
        clock_now[0] += 1
        if on_sleep:
            on_sleep(ticks[0])

    return hookmod.run_reply(hook, sleep=sleep, clock=lambda: ticks[0], idle=idle, env=env)


def pending_id():
    p = ns["_reply_pending_by_session"](clock_now[0]).get("sess-r")
    return p["id"] if p else None


posts.clear()
c.check("claude -p (sdk-cli) -> never asks the monitor", run_with([9999], env={"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}) is None and posts == [])
c.check("at the PC -> turn ends (no output)", run_with([3]) is None)

posts.clear()
out = run_with([600], lambda t: t == 2 and ns["_reply_send"](pending_id(), "reply", "Yes, README too. Počkaj na CI."))
c.check("away + reply -> block JSON carrying the reply",
        out is not None and out["decision"] == "block" and out["reason"].endswith("Yes, README too. Počkaj na CI.")
        and "AOC dashboard" in out["reason"])
req = [d for p, d in posts if p == "/reply/request"]
c.check("request carried message, idle, stop_hook_active",
        req and req[0]["last_assistant_message"] == HOOK["last_assistant_message"] and req[0]["idle_s"] == 600
        and req[0]["stop_hook_active"] is False)
ups = [d for p, d in posts if p == "/update"]
c.check("session marked no longer waiting after the reply", len(ups) == 1 and ups[0]["waiting_on_you"] is False)

c.check("'done for now' -> turn ends", run_with([600], lambda t: t == 1 and ns["_reply_send"](pending_id(), "done")) is None)
c.check("back at the PC mid-hold -> turn ends", run_with([600, 900, 0.5]) is None)
cfg["v"] = {**away5, "reply": "off"}
c.check("replies off -> turn ends", run_with([9999]) is None)
cfg["v"] = dict(away5)

real_max = hookmod.MAX_HOLD_S
hookmod.MAX_HOLD_S = 5
c.check("nobody answers -> turn ends", run_with([600]) is None)
hookmod.MAX_HOLD_S = real_max
c.check("reply_output: release / empty -> None", hookmod.reply_output("release") is None and hookmod.reply_output("reply", "  ") is None)


def stop_server(t):
    if t == 1:
        srv.shutdown(); srv.server_close()


c.check("monitor gone mid-hold -> turn ends", run_with([600], stop_server) is None)

# as Claude Code runs it: Stop event on stdin, monitor not there -> exit 0, no output, fast
env = dict(os.environ, CLAUDE_CODE_ENTRYPOINT="cli")
p = subprocess.run([sys.executable, script], input=json.dumps({**HOOK, "cwd": "C:/Počítač"}, ensure_ascii=False).encode("utf-8"),
                   capture_output=True, timeout=30, env=env)
c.check("script: Stop, no monitor -> exit 0, no output", p.returncode == 0 and p.stdout == b"")

shutil.rmtree(work, ignore_errors=True)
shutil.rmtree(data_dir, ignore_errors=True)
c.finish()
