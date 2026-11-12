"""Orchestrate's own claude processes are AOC's workers, not your sessions
(server/17_orchestration.py): the env mark at spawn (_orch_worker_env), how
a session is recognised as one (_orch_worker_tag / _orch_worker_path /
_orch_worker_of), /status's split into your sessions, the workers and
their held commands (_orch_split_workers), the /update handler dropping a
worker's waiting_on_you (_orch_mark_worker), the push name, the "hooks
outdated" hint -- and the hooks for real: hooks/aoc_hook.py reports a
worker to the port of the AOC that started it, tagged, and
hooks/aoc_permission.py never holds anything for a worker."""
import sys, os, json, importlib.util, re, subprocess, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
O = exec_functions(["ORCH_ENV_RUN", "ORCH_ENV_TASK", "ORCH_ENV_PORT", "_ORCH_WORKER_PATH_RE", "_orch_worker_env",
                    "_orch_worker_path", "_orch_worker_of", "_orch_split_workers", "_orch_worker_tag",
                    "_orch_mark_worker", "_orch_worker_label", "_ORCH_HOOK_MARK", "_orch_hooks_stale",
                    "_orch_hooks_hint", "_orch_open_in_folders", "_orch_proc_env"],
                   {"os": os, "re": re, "PORT": 5151,
                    "_orch_settings": lambda: {"billing": "api"}, "_orch_catalog_load": lambda: None,
                    "_orch_catalog_cache": {"mcp": ["claude_ai_Vercel"]}, "_orch_catalog": lambda: None,
                    "_orch_subscription_env": lambda: {"PATH": "x"}, "_orch_mcp_deny": lambda a, m: list(m)})

# ── the env mark ──
base = {"PATH": "C:/bin", "ANTHROPIC_API_KEY": "k"}
e = O["_orch_worker_env"](base, "1112-172316", "api")
c.check("env: a copy marked with run, task and this AOC's port",
        e == dict(base, AOC_ORCH_RUN="1112-172316", AOC_ORCH_TASK="api", AOC_ORCH_PORT="5151") and "AOC_ORCH_RUN" not in base)
c.check("env: a given port wins; long ids are cut",
        O["_orch_worker_env"]({}, "r" * 200, None, 5233) == {"AOC_ORCH_RUN": "r" * 80, "AOC_ORCH_TASK": "", "AOC_ORCH_PORT": "5233"})
kw, deny, ready = O["_orch_proc_env"](tag=("R1", "plan"))
c.check("proc env (API billing): the inherited environment, marked",
        ready and deny == [] and kw["env"]["AOC_ORCH_RUN"] == "R1" and kw["env"]["AOC_ORCH_TASK"] == "plan"
        and kw["env"].get("PATH") == os.environ.get("PATH"))
c.check("proc env without a tag: unchanged (nothing to mark)", O["_orch_proc_env"]() == ({}, [], True))
kw, deny, ready = O["_orch_proc_env"](billing="subscription", tag=("R1", "judge"))
c.check("proc env (subscription): its own env, marked too",
        ready and kw["env"] == {"PATH": "x", "AOC_ORCH_RUN": "R1", "AOC_ORCH_TASK": "judge", "AOC_ORCH_PORT": "5151"}
        and deny == ["claude_ai_Vercel"])

# ── recognising a worker ──
wp = O["_orch_worker_path"]
c.check("path: a task agent's worktree -> its slot",
        wp(r"C:\repo\.claude\worktrees\aoc-orch-1112-172316-api-1") == "orch-1112-172316-api-1"
        and wp("/home/x/repo/.claude/worktrees/aoc-orch-1112-172316-api-2/src") == "orch-1112-172316-api-2")
c.check("path: Claude Code's projects-dir name for it",
        wp("C--repo--claude-worktrees-aoc-orch-1112-172316-qa-1") == "orch-1112-172316-qa-1")
c.check("path: your own worktrees and folders are not",
        wp(r"C:\repo\.claude\worktrees\agent-a98568") == "" and wp(r"C:\repo") == "" and wp(None) == ""
        and wp(r"C:\aoc-orch-notes") == "")
tag = O["_orch_worker_tag"]
c.check("tag: the hook's {orch: {run, task}} cleaned",
        tag({"orch": {"run": "1112-172316", "task": "api"}}) == {"run": "1112-172316", "task": "api"}
        and tag({"orch": {"run": "R<1>\"; x", "task": "t" * 200}}) == {"run": "R1x", "task": "t" * 80})
c.check("tag: none / no run / not a dict -> None",
        tag({}) is None and tag({"orch": {"task": "x"}}) is None and tag({"orch": "R1"}) is None and tag(None) is None)
of = O["_orch_worker_of"]
c.check("of: tagged by the hook", of({"orch": {"run": "R1", "task": "api"}}) == {"run": "R1", "task": "api", "slot": ""})
c.check("of: hooks from before the tag -- found by its folder or transcript dir",
        of({"cwd": r"C:\r\.claude\worktrees\aoc-orch-R-x-1"}) == {"run": "", "task": "", "slot": "orch-R-x-1"}
        and of({"transcript_dir": "C--r--claude-worktrees-aoc-orch-R-y-1"})["slot"] == "orch-R-y-1")
c.check("of: your session -> None", of({"cwd": r"C:\repo"}) is None and of(None) is None and of({"orch": {}}) is None)
c.check("label", O["_orch_worker_label"]({"task": "api"}) == "Orchestrate · api"
        and O["_orch_worker_label"]({"slot": "orch-R-x-1"}) == "Orchestrate · orch-R-x-1"
        and O["_orch_worker_label"](None) == "Orchestrate · agent")

# ── /status: yours vs workers ──
mine = {"id": "me", "project": "aoc", "cwd": r"C:\aoc", "session_active": True, "waiting_on_you": True, "perm_requests": []}
w1 = {"id": "w1", "project": "repo", "cwd": r"C:\repo\.claude\worktrees\aoc-orch-R1-api-1", "session_active": True,
      "orch": {"run": "R1", "task": "api"}, "estimated_cost": 0.42, "host_pid": 111, "model": "sonnet",
      "perm_requests": [{"id": "p1", "summary": "Bash: rm -rf dist", "danger": "recursive delete", "cwd": "x"}]}
w2 = {"id": "w2", "project": "repo", "cwd": r"C:\repo\.claude\worktrees\aoc-orch-R1-ui-1", "session_active": True,
      "host_pid": 222, "estimated_cost": 0.1}
w3 = {"id": "w3", "project": "repo", "cwd": r"C:\repo", "session_active": False, "orch": {"run": "R1", "task": "plan"}}
agents = [{"id": "a1", "session_id": "me"}, {"id": "a2", "session_id": "w1"}, {"id": "a3", "session_id": "w1"}]
perms = {"w1": w1["perm_requests"], "ghost": [{"id": "p2", "summary": "Bash: git push -f", "danger": "force push",
                                              "cwd": r"D:\p\.claude\worktrees\aoc-orch-0101-101010-docs-2"}],
         "stranger": [{"id": "p3", "summary": "Bash: rm x", "cwd": r"C:\elsewhere"}]}
yours, workers, holds, ags, foreign = O["_orch_split_workers"]([mine, w1, w2, w3], agents, perms, {222})
c.check("split: only your session stays", [s["id"] for s in yours] == ["me"])
c.check("split: workers, compact, cost kept, subagents counted",
        [w["id"] for w in workers] == ["w1", "w2", "w3"] and workers[0]["estimated_cost"] == 0.42
        and workers[0]["run"] == "R1" and workers[0]["task"] == "api" and workers[0]["agents"] == 2
        and workers[1]["slot"] == "orch-R1-ui-1" and workers[2]["session_active"] is False)
c.check("split: the workers' subagent cards leave the agent list", [a["id"] for a in ags] == ["a1"])
c.check("split: a worker's held command is an Orchestrate hold, with its run / task",
        holds[0]["id"] == "p1" and holds[0]["session_id"] == "w1" and holds[0]["run"] == "R1" and holds[0]["task"] == "api")
c.check("split: a held command of a worker AOC doesn't know (a preview's) is found by its folder",
        len(holds) == 2 and holds[1]["id"] == "p2" and holds[1]["session_id"] == "ghost"
        and holds[1]["slot"] == "orch-0101-101010-docs-2" and holds[1]["run"] == "")
c.check("split: a foreign held command (not a worker's folder) is not a hold", not any(h["id"] == "p3" for h in holds))
c.check("split: foreign = live workers whose claude.exe this AOC didn't start", foreign == 1)
c.check("split: nothing in -> nothing out", O["_orch_split_workers"]([], None, None) == ([], [], [], [], 0))
c.check("open in folders: your sessions + the workers (stranded-worktree check)",
        [s["id"] for s in O["_orch_open_in_folders"]({"sessions_list": [mine], "orch_workers": workers})] == ["me", "w1", "w2", "w3"]
        and O["_orch_open_in_folders"](None) == [])

# ── the /update handler's part ──
sess = {"cwd": r"C:\aoc"}
up = {"session_id": "s", "waiting_on_you": True}
c.check("mark: your session -> untouched", O["_orch_mark_worker"](sess, up) is False and up["waiting_on_you"] is True
        and "orch" not in sess)
sess = {"cwd": r"C:\aoc", "waiting_on_you": True, "waiting_kind": "turn", "waiting_message": "done"}
up = {"session_id": "s", "waiting_on_you": True, "orch": {"run": "R1", "task": "plan"}}
c.check("mark: a tagged worker -> tag kept, waiting_on_you dropped from the update and cleared",
        O["_orch_mark_worker"](sess, up) is True and sess["orch"] == {"run": "R1", "task": "plan"}
        and "waiting_on_you" not in up and sess["waiting_on_you"] is False and sess["waiting_kind"] == "" and sess["waiting_message"] == "")
sess = {"cwd": r"C:\r\.claude\worktrees\aoc-orch-R1-api-1"}
up = {"waiting_on_you": True}
c.check("mark: an untagged worker (old hooks) found by its folder", O["_orch_mark_worker"](sess, up) is True
        and "waiting_on_you" not in up and sess["waiting_on_you"] is False)
up = {"waiting_on_you": True}
c.check("mark: stays a worker on later updates without the tag",
        O["_orch_mark_worker"]({"orch": {"run": "R1", "task": "x"}}, up) is True and "waiting_on_you" not in up)

# ── hooks outdated ──
c.check("stale: deployed hook without the worker tag", O["_orch_hooks_stale"]("def main(): pass"))
c.check("stale: current hook / not deployed -> no hint",
        not O["_orch_hooks_stale"](open(os.path.join(ROOT, "hooks", "aoc_hook.py"), encoding="utf-8").read())
        and not O["_orch_hooks_stale"](None))
work = tempfile.mkdtemp(prefix="aoc_orchw_")
old = os.path.join(work, "aoc_hook.py")
with open(old, "w", encoding="utf-8") as f:
    f.write("# an old hook\n")
c.check("hint: an old deployed hook -> 'reinstall'", "reinstall" in O["_orch_hooks_hint"](old))
c.check("hint: no deployed hook -> ''", O["_orch_hooks_hint"](os.path.join(work, "missing.py")) == "")

# ── hooks/aoc_hook.py for real ──
spec = importlib.util.spec_from_file_location("aoc_hook_mod", os.path.join(ROOT, "hooks", "aoc_hook.py"))
hookmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hookmod)
ow = hookmod._orch_worker
c.check("hook: your session -> (None, None)", ow({}) == (None, None) and ow({"AOC_ORCH_RUN": "  "}) == (None, None))
c.check("hook: a worker -> its tag and port",
        ow({"AOC_ORCH_RUN": "R1", "AOC_ORCH_TASK": "api", "AOC_ORCH_PORT": "5233"}) == ({"run": "R1", "task": "api"}, 5233))
c.check("hook: a bad / missing port -> tag, no port (stays on the default)",
        ow({"AOC_ORCH_RUN": "R1", "AOC_ORCH_PORT": "x"}) == ({"run": "R1", "task": ""}, None)
        and ow({"AOC_ORCH_RUN": "R1", "AOC_ORCH_PORT": "70000"})[1] is None and ow({"AOC_ORCH_RUN": "R1"})[1] is None)
probed = []
real_urlopen, hookmod.urllib.request.urlopen = hookmod.urllib.request.urlopen, lambda *a, **k: probed.append(a)
hookmod.ORCH_WORKER = {"run": "R1", "task": "api"}
c.check("hook: a worker never probes or starts a monitor", hookmod.ensure_monitor() is True and probed == [])
hookmod.urllib.request.urlopen, hookmod.ORCH_WORKER = real_urlopen, None

posts = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"{}")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        posts.append((self.server.server_port, self.path, json.loads(self.rfile.read(n) or b"{}")))
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"{}")


spawner, default = HTTPServer(("127.0.0.1", 0), H), HTTPServer(("127.0.0.1", 0), H)
for s in (spawner, default):
    threading.Thread(target=s.serve_forever, daemon=True).start()
hook = os.path.join(work, "aoc_hook.py")
with open(os.path.join(ROOT, "hooks", "aoc_hook.py"), encoding="utf-8") as f:
    src = f.read()
# the default AOC_URL points at a second fake monitor, never the live 5151
with open(hook, "w", encoding="utf-8") as f:
    f.write(src.replace('AOC_URL = "http://127.0.0.1:5151"', f'AOC_URL = "http://127.0.0.1:{default.server_port}"'))
env = dict(os.environ, USERPROFILE=work, HOME=work, TEMP=work, TMP=work, AOC_ORCH_RUN="1112-172316",
           AOC_ORCH_TASK="api", AOC_ORCH_PORT=str(spawner.server_port))
ev = {"session_id": "sess-w", "cwd": work, "transcript_path": "", "hook_event_name": "Stop"}
subprocess.run([sys.executable, hook], input=json.dumps(ev).encode(), env=env, timeout=60)
ups = [p for p in posts if p[1] == "/update"]
c.check("hook run: a worker's Stop goes to the AOC that started it, tagged",
        len(ups) == 1 and ups[0][0] == spawner.server_port and ups[0][2]["orch"] == {"run": "1112-172316", "task": "api"}
        and ups[0][2]["session_id"] == "sess-w")
c.check("hook run: nothing reaches the default monitor", not any(p[0] == default.server_port for p in posts))
for s in (spawner, default):
    s.shutdown()

# ── hooks/aoc_permission.py: never holds for a worker ──
spec = importlib.util.spec_from_file_location("aoc_perm_mod", os.path.join(ROOT, "hooks", "aoc_permission.py"))
perm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(perm)


class Read(BaseException):
    """Not an Exception: main()'s own try/except must not swallow it."""


class NoStdin:
    @property
    def buffer(self):
        raise Read("a worker's prompt must not be read, let alone held")


real_stdin, sys.stdin = sys.stdin, NoStdin()
try:
    c.check("permission hook: a worker -> returns at once, reads nothing", perm.main({"AOC_ORCH_RUN": "R1"}) is None)
    raised = False
    try:
        perm.main({})
    except Read:
        raised = True
    c.check("permission hook: your session -> it does read the prompt", raised)
finally:
    sys.stdin = real_stdin
c.finish()
