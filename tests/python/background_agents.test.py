"""Background agents (GET /agents/background, Today -> Background agents):
the subagent transcripts + .meta.json Claude Code writes, linked to the
hook's ag_<hash> cards and to agent worktrees; working / quiet / dead /
finished / failed per agent (a plan-limit death read from the transcript's
last entry), the claude.exe holding the worktree lock, the relaunch prompt,
and that /status never waits on the git calls (cached, refreshed in the
background)."""
import sys, os, re, json, time, tempfile, threading, hashlib

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions, read_monitor_source
from lib.check import Checker

c = Checker()
NAMES = ["_BG_AGENT_QUIET_S", "_BG_AGENT_DEAD_S", "_BG_AGENT_KEEP_S", "_BG_AGENT_WINDOW_S", "_BG_AGENT_MAX",
         "_BG_TAIL_BYTES", "_BG_STATE_ORDER",
         "_bg_agent_aoc_id", "_bg_lock_pid", "_bg_parse_jsonl", "_bg_entry_text", "_bg_tail_state", "_bg_task_text",
         "_bg_agent_state", "_bg_read_tail", "_bg_read_task", "_bg_worktree_facts", "_bg_agents_collect",
         "_bg_relaunch_prompt", "_bg_agent_relaunch", "_bg_agent_jsonl",
         "_wt_porcelain", "_wt_stranded_id", "_fmt_idle"]
ALIVE = {12096}
ns = exec_functions(NAMES, {"os": os, "re": re, "json": json, "time": time, "threading": threading,
                            "_WT_STRANDED_IDLE_S": 1800, "_bg_task_cache": {}, "_bg_agents_cache": [None, 0.0],
                            "_is_claude_pid_alive": lambda p: p in ALIVE,
                            "_log_bg_error": lambda *a: None})
NOW = 1_800_000_000.0

# ── pure pieces ──
c.check("aoc id = the hook's ag_ + md5(tool_use_id)[:10]",
        ns["_bg_agent_aoc_id"]("toolu_01RG") == "ag_" + hashlib.md5(b"toolu_01RG").hexdigest()[:10])
c.check("no tool use id -> no aoc id", ns["_bg_agent_aoc_id"]("") == "")
c.check("lock pid read from git's lock reason", ns["_bg_lock_pid"]("claude agent agent-a7e1 (pid 12096)") == 12096)
c.check("no pid in the lock reason -> None", ns["_bg_lock_pid"]("") is None and ns["_bg_lock_pid"]("locked by me") is None)
wt = ns["_wt_porcelain"]("worktree C:/r\nHEAD aa\nbranch refs/heads/master\n\nworktree C:/r/.claude/worktrees/agent-x\n"
                         "HEAD bb\nbranch refs/heads/worktree-agent-x\nlocked claude agent agent-x (pid 12096)\n\n"
                         "worktree C:/r/.claude/worktrees/agent-y\nHEAD cc\nlocked\n")
c.check("porcelain keeps the lock reason", wt[1].get("locked") is True and wt[1].get("lock_reason") == "claude agent agent-x (pid 12096)")
c.check("a bare 'locked' line has no reason", wt[2].get("locked") is True and "lock_reason" not in wt[2])

parse = ns["_bg_parse_jsonl"]
c.check("tail read: a partial first line is dropped", parse('ial":1}\n{"a":1}\n{"b":2}\n', drop_first=True) == [{"a": 1}, {"b": 2}])
c.check("garbage + blank lines skipped, CRLF ok", parse('{"a":1}\r\nnot json\n\n[1]\n{"b":2}') == [{"a": 1}, {"b": 2}])

tail = ns["_bg_tail_state"]
A = lambda stop, text="x", **kw: {"type": "assistant", "message": {"stop_reason": stop, "content": [{"type": "text", "text": text}]}, **kw}
U = {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}
c.check("ended its turn -> finished", tail([U, A("end_turn")]) == {"finished": True, "api_error": "", "error_kind": ""})
c.check("last entry a tool result -> still running", tail([A("tool_use"), U])["finished"] is False)
c.check("mid tool call -> still running", tail([U, A("tool_use")])["finished"] is False)
c.check("attachments/system after the last turn are ignored",
        tail([A("end_turn"), {"type": "attachment"}, {"type": "system"}])["finished"] is True)
lim = tail([U, A("stop_sequence", "You've hit your session limit · resets 12:20pm (Europe/Bratislava)",
                  isApiErrorMessage=True, error="rate_limit")])
c.check("plan limit death read from the transcript (real shape, 10-30)",
        lim["finished"] is False and lim["error_kind"] == "rate_limit" and lim["api_error"].startswith("You've hit your session limit"))
c.check("empty transcript -> nothing known", tail([]) == {"finished": False, "api_error": "", "error_kind": ""})

task = ns["_bg_task_text"]
fork_first = [{"type": "fork-context-ref"}, A("tool_use"),
              {"type": "user", "message": {"content": [{"type": "tool_result", "content":
               "<fork-boilerplate>\nYou are a worker fork.\n</fork-boilerplate>\n\nYour directive: Build the panel.\nTwo lines.\n"
               "You've inherited the conversation context above from a parent agent working in C:\\x."}]}}]
c.check("fork: the task is its directive, without the boilerplate", task(fork_first) == "Build the panel.\nTwo lines.")
c.check("plain subagent: the first user message",
        task([{"type": "user", "message": {"content": "Find the bug in X"}}, A("end_turn")]) == "Find the bug in X")
c.check("no user message -> ''", task([A("end_turn")]) == "")

st = ns["_bg_agent_state"]
base = {"hook_status": "running", "finished": False, "api_error": "", "error_kind": "", "session_active": True,
        "lock_pid": None, "lock_pid_alive": None, "last_activity": NOW - 60, "worktree": None}
S = lambda **kw: st({**base, **kw}, NOW)
c.check("recent transcript activity -> working", S() == ("working", ""))
c.check("15 min silent -> quiet", S(last_activity=NOW - 900) == ("quiet", "no output for 15 min"))
c.check("45 min silent -> dead", S(last_activity=NOW - 2700) == ("dead", "no activity for 45 min"))
c.check("plan limit -> dead at once, with the reset time",
        S(api_error="You've hit your session limit · resets 12:20pm", error_kind="rate_limit")
        == ("dead", "hit a plan limit: You've hit your session limit · resets 12:20pm"))
c.check("other API error -> dead", S(api_error="Overloaded")[1] == "stopped on an API error: Overloaded")
c.check("lock holder claude.exe gone -> dead", S(lock_pid=4060, lock_pid_alive=False)
        == ("dead", "the Claude Code process that ran it is gone"))
c.check("lock holder alive -> not dead because of it", S(lock_pid=12096, lock_pid_alive=True)[0] == "working")
c.check("session closed + quiet -> dead", S(session_active=False, last_activity=NOW - 700)[0] == "dead")
c.check("session closed but output a minute ago -> still working", S(session_active=False)[0] == "working")
c.check("unknown session (dismissed) doesn't kill it", S(session_active=None, last_activity=NOW - 700)[0] == "quiet")
c.check("ended its turn -> finished", S(finished=True) == ("finished", "nothing left in a worktree"))
c.check("hook says done -> finished, even when the tail wasn't read", S(hook_status="done")[0] == "finished")
c.check("finished beats an old timestamp", S(finished=True, last_activity=NOW - 99999)[0] == "finished")
c.check("finished with work waiting -> says so",
        S(finished=True, worktree={"dirty": 2, "ahead": 1}) == ("finished", "2 uncommitted files left; 1 commit waiting to be merged"))
c.check("hook says error -> failed", S(hook_status="error") == ("failed", "the Agent call failed"))

# ── collection over a fake ~/.claude/projects ──
tmp = tempfile.mkdtemp(prefix="aoc-bga-")
proj = os.path.join(tmp, "projects")
repo = os.path.join(tmp, "repo")
wt_dead = os.path.join(repo, ".claude", "worktrees", "agent-dead1")
wt_ok = os.path.join(repo, ".claude", "worktrees", "agent-work1")
for d in (wt_dead, wt_ok):
    os.makedirs(d)


def agent(session, aid, meta, lines, age):
    d = os.path.join(proj, "C--repo", session, "subagents")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"agent-{aid}.meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f)
    p = os.path.join(d, f"agent-{aid}.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(x) for x in lines) + "\n")
    os.utime(p, (NOW - age, NOW - age))
    return p


cwdline = lambda e: {**e, "cwd": "C:\\repo"}
agent("s1", "work1", {"agentType": "fork", "isFork": True, "requestShape": "background", "description": "R9 (4) panel",
                      "toolUseId": "toolu_W", "worktreePath": wt_ok, "worktreeBranch": "worktree-agent-work1"},
      [cwdline(U), cwdline(A("tool_use"))], 30)
dead_path = agent("s1", "dead1", {"agentType": "fork", "isFork": True, "requestShape": "background",
                                  "description": "R9 (3) resume", "toolUseId": "toolu_D", "worktreePath": wt_dead},
                  fork_first + [U, A("stop_sequence", "You've hit your session limit · resets 12:20pm",
                                     isApiErrorMessage=True, error="rate_limit")], 3000)
agent("s1", "done1", {"agentType": "fork", "requestShape": "background", "description": "R8 smoke", "toolUseId": "toolu_F"},
      [U, A("end_turn")], 600)
agent("s1", "fg1", {"agentType": "Explore", "requestShape": "foreground", "description": "search"}, [U, A("end_turn")], 60)
agent("s0", "old1", {"agentType": "fork", "requestShape": "background", "description": "last week"}, [U, A("end_turn")], 5 * 86400)
agent("s0", "olddead", {"agentType": "fork", "requestShape": "background", "description": "died 4 h ago"}, [U], 4 * 3600)
with open(os.path.join(proj, "C--repo", "s1", "subagents", "agent-bad.meta.json"), "w") as f:
    f.write("{not json")

calls = []


def fake_git(args, cwd, timeout=30):
    calls.append((tuple(args), cwd))
    if args[:2] == ["worktree", "list"]:
        return 0, (f"worktree {repo}\nHEAD aa\nbranch refs/heads/master\n\n"
                   f"worktree {wt_ok}\nHEAD bb\nbranch refs/heads/worktree-agent-work1\nlocked claude agent agent-work1 (pid 12096)\n\n"
                   f"worktree {wt_dead}\nHEAD cc\nbranch refs/heads/worktree-agent-dead1\nlocked claude agent agent-dead1 (pid 4060)\n"), ""
    if args[:1] == ["status"]:
        return 0, (" M monitor.py\0?? new.py\0" if cwd == wt_dead else ""), ""
    if args[:2] == ["log", "-1"] and args[2] == "--format=%ct":
        return 0, str(int(NOW - 7200)), ""
    if args[:2] == ["log", "-1"] and args[2].startswith("--format=%ct%x00"):
        return 0, f"{int(NOW - 7200)}\0Merge master into branch\n", ""
    if args[:2] == ["rev-list", "--count"]:
        return 0, "0\n", ""
    if args[:2] == ["diff", "--shortstat"]:
        return 0, " 1 file changed, 3 insertions(+)\n", ""
    return 1, "", "unexpected"


ns.update({"_wt_main_of": lambda p, git=None, now=None: repo,
           "_wt_stranded_facts": lambda main, w, base, git=None: {
               "exists": True, "dirty": 2 if w["path"] == os.path.normpath(wt_dead) else 0, "ahead": 0,
               "files": 0, "add": 0, "del": 0, "last_activity": NOW - 7200}})
hook_agents = [{"id": ns["_bg_agent_aoc_id"]("toolu_W"), "status": "running", "tokens_used": 0},
               {"id": ns["_bg_agent_aoc_id"]("toolu_F"), "status": "done", "tokens_used": 107094}]
sessions = [{"id": "s1", "display_name": "AOC s agentami", "session_active": True, "cwd": "C:\\repo"}]
items = ns["_bg_agents_collect"](proj, sessions, hook_agents, now=NOW, git=fake_git)
by = {x["id"]: x for x in items}
c.check("background agents found, foreground/old/broken skipped",
        sorted(by) == ["dead1", "done1", "olddead", "work1"])
c.check("dead first, then working, finished last", [x["id"] for x in items][:2] == ["dead1", "olddead"]
        and items[-1]["id"] == "done1")
d = by["dead1"]
c.check("plan-limit fork: dead with the reason", d["state"] == "dead" and d["reason"].startswith("hit a plan limit"))
c.check("its worktree: branch, uncommitted count, stranded id, last commit",
        d["worktree"]["branch"] == "worktree-agent-dead1" and d["worktree"]["dirty"] == 2
        and len(d["worktree"]["stranded_id"]) == 12 and d["worktree"]["last_commit_subject"] == "Merge master into branch"
        and d["worktree"]["last_commit_at"] == int(NOW - 7200))
c.check("lock holder pid 4060 is dead", d["lock_pid"] == 4060 and d["lock_pid_alive"] is False)
w = by["work1"]
c.check("live fork: working, linked to its hook card + session", w["state"] == "working"
        and w["aoc_id"] == ns["_bg_agent_aoc_id"]("toolu_W") and w["session_name"] == "AOC s agentami"
        and w["lock_pid_alive"] is True and w["cwd"] == "C:\\repo")
c.check("last activity = the transcript's mtime (newer than the commit)", abs(w["last_activity"] - (NOW - 30)) < 2)
f = by["done1"]
c.check("finished agent without a worktree: finished, hook tokens kept",
        f["state"] == "finished" and f["worktree"] is None and f["tokens_used"] == 107094)
c.check("a dead agent without worktree stays for a few hours", by["olddead"]["state"] == "dead")
c.check("one `git worktree list` per repo, not per agent",
        sum(1 for a, _ in calls if a[:2] == ("worktree", "list")) == 1)
later = ns["_bg_agents_collect"](proj, sessions, hook_agents, now=NOW + 7 * 3600, git=fake_git)
c.check("after 7 h the worktree-less dead/finished ones drop off, worktree ones stay",
        sorted(x["id"] for x in later) == ["dead1", "work1"])

# ── relaunch ──
c.check("relaunch prompt: worktree, reason, git hints, the task",
        all(s in ns["_bg_relaunch_prompt"](d, "Build the panel.") for s in
            ("branch worktree-agent-dead1", "hit a plan limit", "git status", "Its task was:\n\nBuild the panel.",
             "Don't merge, push or delete")))
c.check("relaunch prompt without a task says so", "could not be read" in ns["_bg_relaunch_prompt"](f, ""))
launched = []
ns["_own_status_payload"] = lambda: {"sessions_list": sessions, "agents": hook_agents}
coll = lambda **kw: ns["_bg_agents_collect"](proj, kw["sessions"], kw["agents"], now=NOW, git=fake_git)
real_expand = os.path.expanduser
ns["os"] = os
os.path.expanduser = lambda p: tmp if p == "~" else real_expand(p)
try:
    os.makedirs(os.path.join(tmp, ".claude"), exist_ok=True)
    if not os.path.exists(os.path.join(tmp, ".claude", "projects")):
        os.rename(proj, os.path.join(tmp, ".claude", "projects"))
    proj = os.path.join(tmp, ".claude", "projects")
    r = ns["_bg_agent_relaunch"]("dead1", launch=lambda data: launched.append(data) or {"ok": True, "pid": 1}, collect=coll)
    c.check("dead agent relaunched in its worktree with its task",
            r["ok"] and launched and launched[0]["cwd"] == d["worktree"]["path"]
            and "Its task was:\n\nBuild the panel.\nTwo lines." in launched[0]["prompt"])
    launched.clear()
    r = ns["_bg_agent_relaunch"]("work1", launch=lambda data: launched.append(data) or {"ok": True}, collect=coll)
    c.check("a working agent is not relaunched", not r["ok"] and "working" in r["error"] and not launched)
    r = ns["_bg_agent_relaunch"]("nope", launch=lambda data: launched.append(data) or {"ok": True}, collect=coll)
    c.check("unknown id -> refused", not r["ok"] and not launched)
finally:
    os.path.expanduser = real_expand

# ── wiring ──
src = read_monitor_source()
c.check("/status payload doesn't call the collector", "_bg_agents_collect(" not in
        src[src.index("def _build_status_payload_uncached"):src.index("def _build_status_payload_uncached") + 20000])
snap = src[src.index("def _bg_agents_snapshot"):src.index("def _bg_relaunch_prompt")]
c.check("snapshot: non-blocking lock + background thread", "acquire(blocking=False)" in snap and "threading.Thread(" in snap)
c.check("GET /agents/background routed", 'path_no_qs == "/agents/background"' in src)
rl = src[src.index('path_no_qs == "/agents/background/relaunch"'):][:600]
c.check("relaunch is this-PC only, never through the tunnel", "_is_direct_loopback()" in rl and "_tunnel_status" in rl)

c.finish()
