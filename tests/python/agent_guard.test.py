"""A Claude Code agent is not the owner (audit FIND-016 / FIND-018).

Before: with no token, any program on this PC was the owner -- an agent's
curl switched the danger guard off (POST /danger_guard {"mode":"off"}) and
read the owner's token from /auth/info. Now a request from a program under
Claude Code's CLI reads like a viewer plus what AOC's own hooks post, and a
held dangerous command is answered by the owner only, with who answered in
its log line. Unit checks of server/18_users.py's helpers, then a real
preview (AOC_AGENT_GUARD=all: every tokenless caller is an agent) over HTTP."""
import sys, os, json, shutil, socket, subprocess, tempfile, threading, time, urllib.request, urllib.error
from collections import namedtuple

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions, MONITOR_PATH
from lib.check import Checker

c = Checker()
ns = exec_functions(
    ["_OWNER_ONLY_EXACT", "_GET_CHANGES", "_OWNER_ONLY_PREFIX", "_VIEWER_POST", "_MEMBER_POST", "_MEMBER_DELETE_PREFIX",
     "_user_allowed", "_AGENT_USER", "_AGENT_POST", "_AGENT_GET", "_agent_allowed", "_agent_guard_on", "_agent_guard_all",
     "_is_agent_proc",
     "_loopback_caller_pid", "_agent_caller", "_perm_decide", "_perm_decide_refusal", "_danger_log_refused_entry",
     "_agent_refused_last", "_agent_refused_log"],
    {"os": os, "time": time, "threading": threading, "AOC_PREVIEW": False, "PORT": 5151,
     "_perm_lock": threading.Lock(), "_perm_requests": {}, "_perm_decided_by": {}})

# ── who is an agent ──
iap = ns["_is_agent_proc"]
c.check("Claude Code CLI (npm) is an agent",
        iap("claude.exe", r"C:\Users\m\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe"))
c.check("Claude Code CLI (native, ~/.local/bin) is an agent", iap("claude.exe", r"C:\Users\m\.local\bin\claude.exe"))
c.check("the desktop app's own Claude Code is an agent",
        iap("claude.exe", r"C:\Users\m\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude-code\2.1\claude.exe"))
c.check("the desktop app itself (its browser may show the dashboard) is not",
        not iap("claude.exe", r"C:\Users\m\AppData\Local\AnthropicClaude\app-1.2.3\claude.exe")
        and not iap("Claude.exe", r"C:\Program Files\WindowsApps\Claude_1.2.3_x64__pzs8sxrjxfjjc\app\Claude.exe"))
c.check("node running Claude Code is an agent, other node is not",
        iap("node.exe", r"C:\nodejs\node.exe", r"node C:\npm\node_modules\@anthropic-ai\claude-code\cli.js")
        and not iap("node.exe", r"C:\nodejs\node.exe", "node server.js"))
c.check("anything else is not", not iap("python.exe", r"C:\Python\python.exe") and not iap("", ""))

Conn = namedtuple("Conn", "laddr raddr pid")
Addr = namedtuple("Addr", "ip port")
conns = [Conn(Addr("127.0.0.1", 5151), Addr("127.0.0.1", 50001), 999),       # the server's side
         Conn(Addr("127.0.0.1", 50001), Addr("127.0.0.1", 5151), 4242),      # the caller's side
         Conn(Addr("127.0.0.1", 50002), None, 7)]
c.check("caller pid: the connection whose local port is the client's",
        ns["_loopback_caller_pid"](50001, 5151, conns) == 4242 and ns["_loopback_caller_pid"](50009, 5151, conns) is None)


class P:
    def __init__(self, pid, name, exe, parents=(), cmd=()):
        self.pid, self._n, self._e, self._p, self._c = pid, name, exe, list(parents), list(cmd)

    def name(self): return self._n
    def exe(self): return self._e
    def cmdline(self): return self._c
    def parents(self): return self._p


claude = P(10, "claude.exe", r"C:\npm\claude-code\bin\claude.exe")
procs = {4242: P(4242, "curl.exe", r"C:\Windows\curl.exe", [P(11, "bash.exe", "b"), claude, P(1, "explorer.exe", "e")])}
ac = ns["_agent_caller"]
c.check("a program under Claude Code -> named agent", ac(50001, 5151, conns, procs.__getitem__) == "claude.exe (pid 10)")
procs[4242] = P(4242, "chrome.exe", "c", [P(1, "explorer.exe", "e")])
c.check("a program not under it -> '' (the owner, as before)", ac(50001, 5151, conns, procs.__getitem__) == "")
c.check("caller not found / process gone -> '' (fails open)",
        ac(50009, 5151, conns, procs.__getitem__) == "" and ac(50001, 5151, conns, lambda pid: 1 / 0) == "")

# ── what an agent may do ──
aa = ns["_agent_allowed"]
for m, p in [("GET", "/status"), ("GET", "/permission/poll"), ("GET", "/reply/poll"), ("POST", "/update"),
             ("POST", "/permission/request"), ("POST", "/reply/request")]:
    c.check(f"agent may: {m} {p} (reads / AOC's hooks)", aa(m, p))
for m, p in [("POST", "/danger_guard"), ("GET", "/auth/info"), ("POST", "/auth/token"), ("POST", "/permission/decide"),
             ("POST", "/orch/merge"), ("POST", "/orch/gate"), ("POST", "/orch/approve"), ("POST", "/orch/create"),
             ("POST", "/restart"), ("POST", "/budget_caps"), ("POST", "/users/invite"), ("GET", "/terminal/ws"),
             ("POST", "/reply/send"), ("DELETE", "/session/x")]:
    c.check(f"agent may not: {m} {p}", not aa(m, p))
c.check("the agent user reads like a viewer and isn't shown as present",
        ns["_AGENT_USER"]["role"] == "viewer" and ns["_AGENT_USER"]["background"] is True)
c.check("the guard is on in the live AOC only (or AOC_AGENT_GUARD=1)", ns["_agent_guard_on"]() is True)
ns["AOC_PREVIEW"] = True
os.environ.pop("AOC_AGENT_GUARD", None)
c.check("... off in a preview", ns["_agent_guard_on"]() is False)
os.environ["AOC_AGENT_GUARD"] = "1"
c.check("... on in a preview with AOC_AGENT_GUARD=1", ns["_agent_guard_on"]() is True)
os.environ.pop("AOC_AGENT_GUARD", None)

# ── a held dangerous command: the owner answers, the log says who (FIND-018) ──
reqs = ns["_perm_requests"]
reqs["d1"] = {"id": "d1", "state": "pending", "message": "", "danger": "Force push"}
reqs["p1"] = {"id": "p1", "state": "pending", "message": "", "danger": ""}
owner, member = {"role": "owner", "name": "Marek"}, {"role": "member", "name": "Jana"}
ref = ns["_perm_decide_refusal"]
c.check("a member can't answer a dangerous command", ref("d1", member) == "only the owner can answer a dangerous command")
c.check("... the owner can, and a member still answers an ordinary prompt",
        ref("d1", owner) == "" and ref("p1", member) == "" and ref("gone", member) == "")
c.check("the answer to a dangerous command keeps who gave it",
        ns["_perm_decide"]("d1", "allow", "", by="Marek (owner)") and ns["_perm_decided_by"] == {"d1": "Marek (owner)"})
c.check("an ordinary prompt's answer isn't kept (nothing would pop it)",
        ns["_perm_decide"]("p1", "deny", "", by="Jana (member)") and "p1" not in ns["_perm_decided_by"])

# ── refused calls go to the danger log, once a minute per route ──
logged = []
rl = ns["_agent_refused_log"]
c.check("first refusal logged", rl("POST", "/danger_guard", "claude.exe (pid 10)", now=1000, append=logged.append))
c.check("a retry loop isn't", not rl("POST", "/danger_guard", "x", now=1030, append=logged.append))
c.check("another route / a minute later is", rl("GET", "/auth/info", "x", now=1031, append=logged.append)
        and rl("POST", "/danger_guard", "x", now=1061, append=logged.append))
e = logged[0]
c.check("log line: what, who, outcome", e["cmd"] == "POST /danger_guard" and e["outcome"] == "agent refused"
        and e["by"] == "claude.exe (pid 10)" and e["rules"] == ["Touch AOC's own safety switches"])

os.environ["AOC_AGENT_GUARD"] = "all"
ns["AOC_PREVIEW"] = True
in_preview = ns["_agent_guard_all"]()
ns["AOC_PREVIEW"] = False
c.check("AOC_AGENT_GUARD=all: every caller an agent -- in a preview only, never live",
        in_preview is True and ns["_agent_guard_all"]() is False)
os.environ.pop("AOC_AGENT_GUARD", None)

# ── live: a preview that takes every tokenless caller for an agent ──
# (which caller is an agent is the unit checks' job above; this is the
# HTTP path: the refusal, its message, its log line, the hooks' routes)
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
work = tempfile.mkdtemp(prefix="aoc_agent_")
data = os.path.join(work, "data")
env = {**os.environ, "AOC_PORT": str(port), "AOC_DATA_DIR": data, "AOC_AGENT_GUARD": "all"}
proc = subprocess.Popen([sys.executable, MONITOR_PATH, "--headless"], env=env, cwd=os.path.dirname(MONITOR_PATH),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def call(method, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()[:300]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


try:
    up = False
    for _ in range(120):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=2).read()
            up = True
            break
        except Exception:
            if proc.poll() is not None:
                break
            time.sleep(0.5)
    c.check("preview up", up)
    c.check("agent: reads /status", call("GET", "/status")[0] == 200)
    st, body = call("GET", "/auth/info")
    c.check("agent: no owner token from /auth/info", st == 403 and '"token"' not in body)
    st, body = call("POST", "/danger_guard", {"mode": "hold"})
    c.check("agent: can't change the danger guard -- and is told why", st == 403 and "Claude Code agent" in body)
    c.check("agent: can't answer a prompt", call("POST", "/permission/decide", {"id": "x", "decision": "allow"})[0] == 403)
    c.check("agent: AOC's hooks still post their status",
            call("POST", "/update", {"session_id": "agent-test", "cwd": "C:/x", "agents": []})[0] != 403)
    log = os.path.join(data, "danger_log.jsonl")
    lines = [json.loads(x) for x in open(log, encoding="utf-8")] if os.path.exists(log) else []
    c.check("the refusal is in the danger log ('Recently held')",
            any(x["cmd"] == "POST /danger_guard" and x["outcome"] == "agent refused" for x in lines))
finally:
    proc.kill()
    proc.wait(10)
    shutil.rmtree(work, ignore_errors=True)
    try:
        os.remove(os.path.join(os.path.dirname(MONITOR_PATH), f"monitor-{port}.pid"))
    except OSError:
        pass
c.finish()
