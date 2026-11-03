"""Stranded worktrees (monitor.py _wt_stranded_*): porcelain parsing, which
worktrees count (under .claude/worktrees only, work left, no open session,
idle long enough), AOC-managed ones keep their base branch, and the actions:
Commit WIP (blocked when clean / detached), Relaunch (a session in the
folder with a resume prompt; mocked, never a real claude), Discard (worktree
+ branch gone, worktrees.json entry dropped, locked refused), unknown or
no-longer-stranded ids refused. Real git, temp repos."""
import sys, os, json, re, time, tempfile, subprocess, threading

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
tmp = os.path.realpath(tempfile.mkdtemp(prefix="aoc_wts_"))
reg = os.path.join(tmp, "data", "worktrees.json")
ns = exec_functions(["_load_json_file", "_parse_porcelain_z",
                     "WORKTREES_FILE", "_wt_lock", "_wt_status_cache",
                     "_wt_git", "_wt_err", "_wt_load", "_wt_save", "_wt_remove", "_wt_shortstat", "_wt_sessions_in",
                     "_WT_STRANDED_IDLE_S", "_WT_STRANDED_CACHE_S", "_WT_STRANDED_MAX_DIRS", "_WT_WIP_MESSAGE",
                     "_wt_stranded_cache", "_wt_repo_root_cache", "_wt_porcelain", "_wt_stranded_id", "_wt_in_claude_dir",
                     "_wt_stranded_reason", "_fmt_idle", "_wt_main_of", "_wt_stranded_facts", "_wt_stranded_scan",
                     "_wt_stranded_dirs", "_wt_stranded_list", "_wt_stranded_find", "_wt_resume_prompt", "_wt_stranded_act"],
                    {"os": os, "re": re, "time": time, "json": json, "subprocess": subprocess, "threading": threading,
                     "AOC_DATA_DIR": os.path.join(tmp, "data"), "_NO_WINDOW": 0x08000000 if os.name == "nt" else 0,
                     "_launch_known_dirs": lambda: [], "_log_bg_error": lambda *a: None,
                     "_launch_session": lambda d: {"ok": False, "error": "real launch must not run in tests"}})


def git(cwd, *args):
    r = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# ── pure bits ──
pp = ns["_wt_porcelain"]("worktree C:/r\nHEAD aaa\nbranch refs/heads/main\n\n"
                          "worktree C:/r/.claude/worktrees/agent-1\nHEAD bbb\nbranch refs/heads/worktree-agent-1\nlocked\n\n"
                          "worktree C:/r/.claude/worktrees/x\nHEAD ccc\ndetached\nprunable gitdir file points to non-existent location\n")
c.check("porcelain: 3 worktrees, main first", [os.path.basename(w["path"]) for w in pp] == ["r", "agent-1", "x"])
c.check("porcelain: branch without refs/heads, locked, detached, prunable",
        pp[0]["branch"] == "main" and pp[1]["branch"] == "worktree-agent-1" and pp[1].get("locked")
        and pp[2].get("detached") and pp[2].get("prunable") and "branch" not in pp[2])
c.check("porcelain: empty", ns["_wt_porcelain"]("") == [])
inc = ns["_wt_in_claude_dir"]
c.check("in .claude/worktrees: direct child only", inc("C:\\r", "C:\\r\\.claude\\worktrees\\agent-1")
        and not inc("C:\\r", "C:\\r\\.claude\\worktrees") and not inc("C:\\r", "C:\\r\\.claude\\worktrees\\a\\b")
        and not inc("C:\\r", "C:\\other\\wt"))
c.check("id: stable + case-insensitive on Windows", ns["_wt_stranded_id"]("C:\\R\\x") == ns["_wt_stranded_id"]("c:\\r\\x")
        if os.name == "nt" else len(ns["_wt_stranded_id"]("/r/x")) == 12)
rs = ns["_wt_stranded_reason"]
now = 2_000_000_000
base = {"exists": True, "dirty": 2, "ahead": 1, "last_activity": now - 3 * 3600}
c.check("reason: dirty + ahead + idle", rs(base, [], now) == "2 uncommitted files + 1 unmerged commit, untouched for 3 h")
c.check("reason: open session -> not stranded", rs(base, [{"id": "s"}], now) == "")
c.check("reason: busy recently -> not stranded", rs(dict(base, last_activity=now - 60), [], now) == "")
c.check("reason: nothing left -> not stranded", rs(dict(base, dirty=0, ahead=0), [], now) == "")
c.check("reason: folder gone -> not stranded", rs(dict(base, exists=False), [], now) == "")
c.check("idle format", ns["_fmt_idle"](95 * 60) == "1 h 35 min" and ns["_fmt_idle"](40 * 60) == "40 min"
        and ns["_fmt_idle"](3 * 86400) == "3 d")
p = ns["_wt_resume_prompt"]({"branch": "worktree-agent-1", "base": "main", "reason": "1 uncommitted file"})
c.check("resume prompt: branch, base log range, no merge/push", "worktree-agent-1" in p and "main..HEAD" in p
        and "Don't merge, push" in p)

# ── a repo with three worktrees under .claude/worktrees ──
repo = os.path.join(tmp, "Počítač repo")   # non-ASCII + space, like OneDrive\Počítač
os.makedirs(repo)
git(repo, "init", "-q", "-b", "main")
git(repo, "config", "user.email", "t@t")
git(repo, "config", "user.name", "t")
git(repo, "config", "core.autocrlf", "false")
write(os.path.join(repo, "a.txt"), "one\n")
git(repo, "add", ".")
git(repo, "commit", "-q", "-m", "init")
write(os.path.join(repo, ".git", "info", "exclude"), ".claude/\n")
wtd = os.path.join(repo, ".claude", "worktrees")
dead = os.path.join(wtd, "agent-dead")      # uncommitted + a commit: stranded
clean = os.path.join(wtd, "agent-clean")    # nothing left: fine
aoc = os.path.join(wtd, "aoc-1102-1-1")     # AOC parallel session with a commit
outside = os.path.join(tmp, "elsewhere-wt")  # dirty, but not under .claude/worktrees
git(repo, "worktree", "add", "-q", "-b", "worktree-agent-dead", dead)
git(repo, "worktree", "add", "-q", "-b", "worktree-agent-clean", clean)
git(repo, "worktree", "add", "-q", "-b", "aoc/1102-1-1", aoc)
git(repo, "worktree", "add", "-q", "-b", "elsewhere", outside)
write(os.path.join(dead, "b.txt"), "b\n")
git(dead, "add", ".")
git(dead, "commit", "-q", "-m", "half done")
write(os.path.join(dead, "a.txt"), "one\nc\nc\n")   # an uncommitted change to a tracked file: +2
write(os.path.join(aoc, "a.txt"), "one\ntwo\n")
git(aoc, "commit", "-q", "-am", "aoc work")
write(os.path.join(outside, "z.txt"), "z\n")
ns["_wt_save"]([{"id": "1102-1-1", "path": aoc, "repo": repo, "base": "main", "branch": "aoc/1102-1-1"}])

later = time.time() + 2 * 3600   # "two hours from now": everything is idle by then
scan = ns["_wt_stranded_scan"]
items = scan([repo], [], ns["_wt_load"](), now=later)
names = sorted(w["name"] for w in items)
c.check("scan: the dead agent + the AOC one; clean and outside-.claude ones skipped", names == ["agent-dead", "aoc-1102-1-1"])
d = next(w for w in items if w["name"] == "agent-dead")
c.check("scan: facts of the dead agent", d["dirty"] == 1 and d["ahead"] == 1 and d["kind"] == "agent"
        and d["branch"] == "worktree-agent-dead" and d["base"] == "main" and d["last_subject"] == "half done"
        and d["wip_add"] == 2 and d["repo_name"] == "Počítač repo")
a = next(w for w in items if w["name"] == "aoc-1102-1-1")
c.check("scan: AOC-managed keeps its id + base", a["kind"] == "aoc" and a["aoc_id"] == "1102-1-1" and a["ahead"] == 1 and not a["dirty"])
c.check("scan: found from a subfolder / worktree too", len(scan([dead], [], [], now=later)) == 2)
c.check("scan: not idle yet right now", scan([repo], [], [], now=time.time()) == [])
c.check("scan: an open session in the worktree keeps it off the list",
        [w["name"] for w in scan([repo], [{"id": "s1", "cwd": dead, "session_active": True}], [], now=later)] == ["aoc-1102-1-1"])
c.check("scan: a closed session doesn't", len(scan([repo], [{"id": "s1", "cwd": dead, "session_active": False}], [], now=later)) == 2)
c.check("scan: non-repo + missing dirs ignored", scan([tmp, os.path.join(tmp, "nope")], [], [], now=later) == [])

# ── list / actions (with time pushed 2 h ahead so the fresh worktrees count as idle) ──
ns["time"] = type("FakeTime", (), {"time": staticmethod(lambda: later)})
lst = ns["_wt_stranded_list"]([{"id": "x", "cwd": repo, "session_active": True}])
c.check("list: dirs from sessions + worktrees.json", sorted(w["name"] for w in lst["items"]) == ["agent-dead", "aoc-1102-1-1"])
sess = [{"id": "x", "cwd": repo, "session_active": True}]   # the repo itself is open somewhere
act = lambda kind, wid, **kw: ns["_wt_stranded_act"](kind, wid, sess, **kw)
c.check("act: unknown id refused", not act("commit", "nope")["ok"])
c.check("act: unknown action refused", act("explode", a["id"])["error"] == "unknown action")
c.check("commit: nothing uncommitted -> refused", "nothing uncommitted" in act("commit", a["id"])["error"])
r = act("commit", d["id"])
c.check("commit WIP: everything committed on its branch", r["ok"] and r["branch"] == "worktree-agent-dead"
        and git(dead, "log", "-1", "--format=%s") == "WIP: rescued by AOC" and git(dead, "status", "--porcelain") == "")

launched = []
r = act("relaunch", d["id"], launch=lambda data: launched.append(data) or {"ok": True, "pid": 1})
c.check("relaunch: a session in the worktree with the resume prompt", r["ok"] and launched[0]["cwd"] == dead
        and "worktree-agent-dead" in launched[0]["prompt"] and "finish it" in launched[0]["prompt"])
c.check("relaunch: launcher error passed through", act("relaunch", d["id"], launch=lambda data: {"ok": False, "error": "busy"})["error"] == "busy")

git(repo, "worktree", "lock", aoc)
c.check("discard: locked worktree refused", "locked" in act("discard", a["id"])["error"])
git(repo, "worktree", "unlock", aoc)
r = act("discard", a["id"])
c.check("discard: worktree + branch gone, worktrees.json entry dropped", r["ok"] and not os.path.isdir(aoc)
        and "aoc/1102-1-1" not in git(repo, "branch", "--list") and ns["_wt_load"]() == [])
r = act("discard", d["id"])
c.check("discard: agent worktree + branch gone", r["ok"] and not os.path.isdir(dead)
        and "worktree-agent-dead" not in git(repo, "branch", "--list"))
c.check("discard: second time -> no longer stranded", not act("discard", d["id"])["ok"])
c.check("main repo untouched", git(repo, "status", "--porcelain") == "" and git(repo, "log", "-1", "--format=%s") == "init")

git(repo, "worktree", "remove", "--force", clean)
git(repo, "worktree", "remove", "--force", outside)
c.finish()
