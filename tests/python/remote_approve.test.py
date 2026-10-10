"""Remote approve (answer permission prompts from the dashboard):
- monitor.py's pure core (_perm_should_hold, _perm_summary, _perm_open /
  _perm_poll / _perm_decide / _perm_pending_by_session, settings sanitizer).
- hooks/aoc_permission.py for real, against a fake monitor whose routes run
  those same monitor.py functions: off / at the PC -> no hold; away ->
  held, then allow / deny / "answer in terminal" / back at the PC; monitor
  gone mid-hold; and the script run as Claude Code runs it (stdin JSON,
  stdout decision)."""
import sys, os, json, importlib.util, secrets, shutil, subprocess, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
data_dir = tempfile.mkdtemp(prefix="aoc_ra_")

ns = exec_functions(
    ["_load_json_file", "REMOTE_APPROVE_FILE", "_PERM_MODES", "_PERM_POLL_STALE_S", "_PERM_BACK_IDLE_S",
     "_perm_lock", "_perm_requests", "_sanitize_remote_approve", "_load_remote_approve", "_save_remote_approve",
     "_perm_should_hold", "_perm_summary", "_perm_expire", "_perm_open", "_perm_poll", "_perm_decide",
     "_perm_pending_by_session"],
    {"os": os, "json": json, "threading": threading, "_secrets": secrets, "AOC_DATA_DIR": os.path.join(data_dir, "AOC")})

# ── settings ──
san = ns["_sanitize_remote_approve"]
c.check("defaults: away after 5 min", san(None) == {"mode": "away", "idle_min": 5})
c.check("unknown mode -> away", san({"mode": "yolo"})["mode"] == "away")
c.check("idle_min clamped", san({"idle_min": 0})["idle_min"] == 1 and san({"idle_min": 999})["idle_min"] == 120)
c.check("idle_min garbage -> 5", san({"idle_min": "x"})["idle_min"] == 5)
c.check("missing file -> defaults", ns["_load_remote_approve"]() == {"mode": "away", "idle_min": 5})
ns["_save_remote_approve"]({"mode": "always", "idle_min": 3, "evil": 1})
c.check("save -> load round trip, only known keys", ns["_load_remote_approve"]() == {"mode": "always", "idle_min": 3})

# ── hold decision ──
hold = ns["_perm_should_hold"]
away5 = {"mode": "away", "idle_min": 5}
c.check("away: at the PC -> terminal", not hold(away5, 20))
c.check("away: idle 5 min -> hold", hold(away5, 300))
c.check("away: idle unknown -> terminal", not hold(away5, None))
c.check("always -> hold", hold({"mode": "always", "idle_min": 5}, 0))
c.check("off -> terminal", not hold({"mode": "off", "idle_min": 5}, 9999))

# ── summary ──
summ = ns["_perm_summary"]
s, d = summ("Bash", {"command": "npm test", "description": "Run tests"})
c.check("Bash summary = command", s == "Bash: npm test" and d == "Run tests")
s, d = summ("Edit", {"file_path": "C:/x/a.py", "old_string": "a", "new_string": "b"})
c.check("Edit summary = path, detail = diff", s == "Edit: C:/x/a.py" and d == "- a\n+ b")
s, d = summ("mcp__srv__do", {"q": "hi"})
c.check("unknown tool -> key=value summary", s == 'mcp__srv__do: q="hi"' and '"q": "hi"' in d)
c.check("summary capped", len(summ("Bash", {"command": "x" * 1000})[0]) == 300)
c.check("garbage input survives", summ(None, "nope")[0] == "?:")

# ── request lifecycle ──
always = {"mode": "always", "idle_min": 5}
r = ns["_perm_open"]({"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": "ls"}, "idle_s": 0}, always, 1000)
c.check("open -> hold + id + summary", r["hold"] and r["id"] and r["summary"] == "Bash: ls")
c.check("off -> no hold", ns["_perm_open"]({"session_id": "s1"}, {"mode": "off", "idle_min": 5}, 1000) == {"hold": False})
pend = ns["_perm_pending_by_session"](1001)
c.check("pending listed under its session", [q["id"] for q in pend.get("s1", [])] == [r["id"]] and pend["s1"][0]["age_s"] == 1)
c.check("poll while pending", ns["_perm_poll"](r["id"], None, always, 1002) == {"state": "pending"})
c.check("bad decision rejected", not ns["_perm_decide"](r["id"], "maybe"))
c.check("allow accepted", ns["_perm_decide"](r["id"], "allow"))
c.check("second decision rejected", not ns["_perm_decide"](r["id"], "deny"))
c.check("decided -> not listed as pending", "s1" not in ns["_perm_pending_by_session"](1003))
c.check("poll hands out allow once", ns["_perm_poll"](r["id"], None, always, 1003) == {"state": "allow", "message": ""})
c.check("then it's gone -> release", ns["_perm_poll"](r["id"], None, always, 1004) == {"state": "release"})

r = ns["_perm_open"]({"session_id": "s2", "tool_name": "Bash", "tool_input": {}}, always, 2000)
ns["_perm_decide"](r["id"], "deny", "not now")
c.check("deny carries the message", ns["_perm_poll"](r["id"], None, always, 2001) == {"state": "deny", "message": "not now"})

r = ns["_perm_open"]({"session_id": "s3", "tool_name": "Bash", "tool_input": {}}, always, 3000)
ns["_perm_decide"](r["id"], "terminal")
c.check("'answer in terminal' -> release", ns["_perm_poll"](r["id"], None, always, 3001)["state"] == "release")

r = ns["_perm_open"]({"session_id": "s4", "idle_s": 600}, away5, 4000)
c.check("away: still idle -> pending", ns["_perm_poll"](r["id"], 601, away5, 4001) == {"state": "pending"})
c.check("away: input again -> release", ns["_perm_poll"](r["id"], 1, away5, 4002)["state"] == "release")

r = ns["_perm_open"]({"session_id": "s5"}, always, 5000)
c.check("mode switched off mid-hold -> release", ns["_perm_poll"](r["id"], None, {"mode": "off", "idle_min": 5}, 5001)["state"] == "release")

r = ns["_perm_open"]({"session_id": "s6"}, always, 6000)
c.check("hook stopped polling -> expired", "s6" not in ns["_perm_pending_by_session"](6000 + 16))
c.check("expired can't be decided", not ns["_perm_decide"](r["id"], "allow"))

# ── the real hook script against a fake monitor running the functions above ──
cfg = {"v": {"mode": "away", "idle_min": 5}}
posts = []
clock_now = [10000.0]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        idle = float(qs["idle"][0]) if "idle" in qs else None
        self._reply(ns["_perm_poll"](qs["id"][0], idle, cfg["v"], clock_now[0]))

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        posts.append((self.path, data))
        if self.path == "/permission/request":
            self._reply(ns["_perm_open"](data, cfg["v"], clock_now[0]))
        else:
            self._reply({"ok": True})


srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]

work = tempfile.mkdtemp(prefix="aoc_rah_")
script = os.path.join(work, "aoc_permission.py")
with open(os.path.join(ROOT, "hooks", "aoc_permission.py"), encoding="utf-8") as f:
    src = f.read()
c.check("hook script targets the monitor port", 'AOC_URL = "http://127.0.0.1:5151"' in src)
src = src.replace("http://127.0.0.1:5151", f"http://127.0.0.1:{port}").replace("__MONITOR_SCRIPT__", work.replace("\\", "/") + "/monitor.py")
with open(script, "w", encoding="utf-8") as f:
    f.write(src)
spec = importlib.util.spec_from_file_location("aoc_permission", script)
hookmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hookmod)

HOOK = {"session_id": "sess-1", "cwd": "C:/proj", "tool_name": "Bash", "tool_input": {"command": "npm publish"}}


def run_with(idles, on_sleep=None):
    """idles: values idle() returns in turn (last one repeats)."""
    seq = list(idles)
    ticks = [0.0]

    def idle():
        return seq.pop(0) if len(seq) > 1 else seq[0]

    def sleep(_s):
        ticks[0] += 1
        clock_now[0] += 1
        if on_sleep:
            on_sleep(ticks[0])

    return hookmod.run(HOOK, sleep=sleep, clock=lambda: ticks[0], idle=idle)


def pending_id():
    p = ns["_perm_pending_by_session"](clock_now[0]).get("sess-1") or []
    return p[0]["id"] if p else None


posts.clear()
c.check("away, at the PC -> no output (terminal dialog)", run_with([3]) is None)
c.check("...and nothing marked waiting", not any(p == "/update" for p, _ in posts))

posts.clear()
out = run_with([600], lambda t: t == 2 and ns["_perm_decide"](pending_id(), "allow"))
c.check("away + allowed on the dashboard -> allow JSON",
        out == {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}})
ups = [d for p, d in posts if p == "/update"]
c.check("session marked NEEDS OK with the summary, then cleared",
        len(ups) == 2 and ups[0]["waiting_on_you"] is True and ups[0]["waiting_kind"] == "permission"
        and ups[0]["waiting_message"] == "Bash: npm publish" and ups[1]["waiting_on_you"] is False)
c.check("request carried tool + idle", any(p == "/permission/request" and d["tool_input"] == {"command": "npm publish"}
                                           and d["idle_s"] == 600 for p, d in posts))

out = run_with([600], lambda t: t == 1 and ns["_perm_decide"](pending_id(), "deny", "use npm pack"))
c.check("deny -> deny JSON with the message",
        out["hookSpecificOutput"]["decision"] == {"behavior": "deny", "message": "use npm pack"})
out = run_with([600], lambda t: t == 1 and ns["_perm_decide"](pending_id(), "deny"))
c.check("deny without a message -> default text", "AOC dashboard" in out["hookSpecificOutput"]["decision"]["message"])

posts.clear()
c.check("back at the PC mid-hold -> terminal dialog", run_with([600, 900, 900, 0.5]) is None)
c.check("...and the NEEDS OK stays (the terminal dialog is still open)",
        [d["waiting_on_you"] for p, d in posts if p == "/update"] == [True])
c.check("'answer in terminal' -> terminal dialog",
        run_with([600], lambda t: t == 1 and ns["_perm_decide"](pending_id(), "terminal")) is None)

cfg["v"] = {"mode": "always", "idle_min": 5}
real_max = hookmod.MAX_HOLD_S
hookmod.MAX_HOLD_S = 5
c.check("nobody answers -> gives up to the terminal dialog", run_with([0]) is None)
hookmod.MAX_HOLD_S = real_max


def stop_server(t):
    if t == 1:
        srv.shutdown(); srv.server_close()


c.check("monitor gone mid-hold -> terminal dialog", run_with([0], stop_server) is None)
c.check("monitor not running at all -> terminal dialog", run_with([600]) is None)

# as Claude Code runs it: stdin JSON (non-ASCII too), stdout empty, exit 0, fast
p = subprocess.run([sys.executable, script], input=json.dumps({**HOOK, "cwd": "C:/Počítač"}, ensure_ascii=False).encode("utf-8"),
                   capture_output=True, timeout=30)
c.check("script: no monitor -> exit 0, no output", p.returncode == 0 and p.stdout == b"")
p = subprocess.run([sys.executable, script], input=b"not json", capture_output=True, timeout=30)
c.check("script: garbage stdin -> exit 0, no output", p.returncode == 0 and p.stdout == b"")
c.check("decision_output: release -> None", hookmod.decision_output("release") is None)

shutil.rmtree(work, ignore_errors=True)
shutil.rmtree(data_dir, ignore_errors=True)
c.finish()
