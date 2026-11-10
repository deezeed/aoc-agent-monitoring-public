"""Checkpoint a dead background agent (POST /agents/background/checkpoint):
its uncommitted worktree changes become a "WIP checkpoint: <name>" commit on
the agent's own branch -- never on master/main/the base branch, never on a
detached HEAD, never while it may still be working -- and with relaunch a
new session continues from it (prompt: task, WIP sha, file list, last
message). Runs against a real temp git repo + worktree with an injected
collector and launcher: no real agent, no real claude."""
import sys, os, re, json, subprocess, tempfile, threading, time

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions, read_monitor_source
from lib.check import Checker

c = Checker()


def real_git(args, cwd, timeout=30):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    return r.returncode, r.stdout, r.stderr


NAMES = ["_BG_PROTECTED_BRANCHES", "_bg_parse_jsonl", "_bg_entry_text", "_bg_last_assistant_text", "_bg_checkpoint_message",
         "_bg_checkpoint_blocker", "_bg_checkpoint_prompt", "_bg_agent_checkpoint", "_bg_agent_jsonl",
         "_parse_porcelain_z", "_wt_err"]
ns = exec_functions(NAMES, {"os": os, "re": re, "json": json, "time": time, "threading": threading,
                            "_bg_agents_cache": [None, 5.0], "_wt_stranded_cache": [0.0, ["x"]],
                            "_own_status_payload": lambda: {}, "_log_bg_error": lambda *a: None,
                            "_wt_git": real_git})
last_text, msg, blocker, prompt, checkpoint = (ns["_bg_last_assistant_text"], ns["_bg_checkpoint_message"],
                                               ns["_bg_checkpoint_blocker"], ns["_bg_checkpoint_prompt"],
                                               ns["_bg_agent_checkpoint"])

# ── pure pieces ──
E = [{"type": "user", "message": {"content": "go"}},
     {"type": "assistant", "message": {"content": [{"type": "text", "text": "Half done: parser ok, tests next."}]}},
     {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}},
     {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
     {"type": "assistant", "isApiErrorMessage": True, "message": {"content": [{"type": "text", "text": "You've hit your limit"}]}}]
c.check("last message: the last real text, API error + tool-only turns skipped", last_text(E) == "Half done: parser ok, tests next.")
c.check("last message: none -> ''", last_text([]) == "" and last_text([E[0]]) == "")
c.check("last message: trimmed from the end", last_text([{"type": "assistant", "message": {"content": "x" * 50 + "END"}}], 10) == "…" + ("x" * 7) + "END")
c.check("commit subject", msg({"name": "Fix  the\nparser"}) == "WIP checkpoint: Fix the parser")
c.check("commit subject: long name cut", len(msg({"name": "a" * 300})) == len("WIP checkpoint: ") + 80)
c.check("commit subject: no name -> agent id", msg({"id": "ab12"}) == "WIP checkpoint: agent ab12")

WT = {"path": "C:/x", "branch": "worktree-agent-1", "base": "master", "dirty": 3, "detached": False}
IT = lambda **o: {"id": "a1", "name": "Fix parser", "state": "dead", "reason": "hit a plan limit", "session_id": "s1",
                  "worktree": dict(WT, **o.pop("wt", {})), **o}
c.check("blocker: dead + dirty + own branch -> ok", blocker(IT()) == "" and blocker(IT(state="failed")) == "")
c.check("blocker: still working / quiet / finished", all("not dead" in blocker(IT(state=s)) for s in ("working", "quiet", "finished")))
c.check("blocker: no worktree", "no worktree" in blocker(dict(IT(), worktree=None)))
c.check("blocker: detached HEAD / no branch", "detached" in blocker(IT(wt={"detached": True})) and "detached" in blocker(IT(wt={"branch": ""})))
c.check("blocker: master / main / the base branch", all("own branch" in blocker(IT(wt={"branch": b})) for b in ("master", "main"))
        and "own branch" in blocker(IT(wt={"branch": "feat", "base": "feat"})))
c.check("blocker: nothing uncommitted", "nothing uncommitted" in blocker(IT(wt={"dirty": 0})))
p = prompt(IT(), "Write the parser", ["a.py", "b/c.py"], "Half done.", "0123456789abcdef")
c.check("prompt: task, branch, reason", "Write the parser" in p and "branch worktree-agent-1" in p and "hit a plan limit" in p)
c.check("prompt: WIP sha + subject + files", "0123456789" in p and "0123456789a" not in p and '"WIP checkpoint: Fix parser"' in p
        and "- a.py\n- b/c.py" in p)
c.check("prompt: last message + continue + amend/squash, no push", "Half done." in p and "Continue from there" in p
        and "amend or squash the WIP commit" in p and "Don't merge, push" in p)
c.check("prompt: long file list capped", "… and 10 more" in prompt(IT(), "t", [f"f{i}" for i in range(50)], "", "s"))
c.check("prompt: unreadable task / no message still says what to do",
        "could not be read" in prompt(IT(), "", [], "", "s") and "git show HEAD" in prompt(IT(), "", [], "", "s"))

# ── against a real repo + worktree ──
d = tempfile.mkdtemp()
repo = os.path.join(d, "repo")
os.makedirs(repo)
g = lambda *a, cwd=repo: real_git(list(a), cwd)
g("init", "-q", "-b", "master"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
open(os.path.join(repo, "a.py"), "w").write("x = 1\n")
g("add", "-A"); g("commit", "-qm", "init")
wtp = os.path.join(repo, ".claude", "worktrees", "agent-1")
g("worktree", "add", "-q", "-b", "worktree-agent-1", wtp)
g("config", "user.email", "t@t", cwd=wtp); g("config", "user.name", "t", cwd=wtp)
open(os.path.join(wtp, "a.py"), "a").write("y = 2\n")
open(os.path.join(wtp, "new.py"), "w").write("z = 3\n")
master_head = g("rev-parse", "master")[1].strip()

state = {"it": IT(wt={"path": wtp, "dirty": 2})}
collect = lambda **k: [state["it"]]
launched = []
launch = lambda data: (launched.append(data), {"ok": True, "session": "new"})[1]
tail = [{"type": "assistant", "message": {"content": "Parser done, now the tests."}}]
kw = dict(collect=collect, launch=launch, find_jsonl=lambda sid, aid: "T.jsonl",
          read_tail=lambda p: tail, read_task=lambda p: "Write the parser")

c.check("unknown id -> gone", "gone" in checkpoint("nope", **kw)["error"])
state["it"] = IT(state="working", wt={"path": wtp, "dirty": 2})
r = checkpoint("a1", **kw)
c.check("working agent -> refused, nothing committed", not r["ok"] and "not dead" in r["error"]
        and g("status", "--porcelain", cwd=wtp)[1].strip() != "")
state["it"] = IT(wt={"path": wtp, "dirty": 2, "branch": "other-branch"})
r = checkpoint("a1", **kw)
c.check("worktree checked out on another branch than listed -> refused", not r["ok"] and "no longer on other-branch" in r["error"])

state["it"] = IT(wt={"path": wtp, "dirty": 2})
r = checkpoint("a1", **kw)
c.check("checkpoint: ok, sha, branch, 2 files, nothing launched", r["ok"] and len(r["sha"]) == 40 and r["branch"] == "worktree-agent-1"
        and r["files"] == 2 and r["launched"] is False and launched == [])
c.check("checkpoint: worktree clean afterwards", g("status", "--porcelain", cwd=wtp)[1].strip() == "")
c.check("checkpoint: commit subject on the agent's branch", g("log", "-1", "--format=%s", "worktree-agent-1")[1].strip() == "WIP checkpoint: Fix parser")
c.check("checkpoint: both files in it (new file too)", set(g("show", "--name-only", "--format=", "worktree-agent-1")[1].split()) == {"a.py", "new.py"})
c.check("checkpoint: master untouched", g("rev-parse", "master")[1].strip() == master_head)
c.check("checkpoint: caches invalidated", ns["_bg_agents_cache"][1] == 0.0 and ns["_wt_stranded_cache"][1] is None)
r = checkpoint("a1", **kw)
c.check("again with nothing left -> nothing uncommitted", not r["ok"] and "nothing uncommitted" in r["error"])

open(os.path.join(wtp, "new.py"), "a").write("w = 4\n")
r = checkpoint("a1", relaunch=True, **kw)
c.check("checkpoint + relaunch: committed and launched", r["ok"] and r["launched"] is True and len(launched) == 1)
pr = launched[0]["prompt"] if launched else ""
c.check("relaunch: in the worktree", launched and launched[0]["cwd"] == wtp)
c.check("relaunch prompt: task, WIP sha, file, last message", "Write the parser" in pr and r["sha"][:10] in pr
        and "- new.py" in pr and "Parser done, now the tests." in pr)
open(os.path.join(wtp, "new.py"), "a").write("v = 5\n")
r = checkpoint("a1", relaunch=True, **dict(kw, launch=lambda data: {"ok": False, "error": "no claude"}))
c.check("launch fails -> still checkpointed, says so", r["ok"] and r["launched"] is False and r["launch_error"] == "no claude"
        and g("status", "--porcelain", cwd=wtp)[1].strip() == "")

# a worktree on master itself is never committed on
g("worktree", "add", "-q", "--detach", os.path.join(repo, ".claude", "worktrees", "agent-2"))
state["it"] = IT(wt={"path": repo, "dirty": 1, "branch": "master"})
open(os.path.join(repo, "a.py"), "a").write("q = 9\n")
r = checkpoint("a1", **kw)
c.check("main checkout on master -> refused, master untouched", not r["ok"] and g("rev-parse", "master")[1].strip() == master_head)

# ── route ──
src = read_monitor_source()
seg = src[src.index('elif path_no_qs == "/agents/background/checkpoint":'):]
seg = seg[:seg.index("elif path_no_qs", 10)]
c.check("route: this PC only, not through the tunnel", "_is_direct_loopback()" in seg and '_tunnel_status[0] in ("starting", "ready")' in seg)
c.check("route: passes id + relaunch", '_bg_agent_checkpoint(str(data.get("id") or ""), relaunch=bool(data.get("relaunch")))' in seg)
c.check("relaunch reuses the transcript finder", "jsonl = _bg_agent_jsonl(it[\"session_id\"], aid)" in src)
c.finish()
