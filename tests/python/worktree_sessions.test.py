"""Parallel sessions in git worktrees (monitor.py _wt_*): validation, all-or-
nothing creation, launch with a fake claude (one console per worktree, cwd
inside it, subfolder kept), status (dirty / ahead / behind / diff stat),
linking dashboard sessions by folder, merge (blocked while open / dirty /
nothing to merge / wrong branch checked out / dirty repo / conflicts; clean
merge removes worktree + branch), discard, diff. Real git, temp repos."""
import sys, os, json, re, time, tempfile, shutil, subprocess, threading

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
tmp = os.path.realpath(tempfile.mkdtemp(prefix="aoc_wt_"))
reg = os.path.join(tmp, "data", "worktrees.json")
exe = [os.path.join(tmp, "claude.exe")]
ns = exec_functions(["_LAUNCH_MODELS", "_LAUNCH_MIN_GAP_S", "_launch_last", "_launch_validate", "_launch_argv",
                     "_folder_trusted", "_load_json_file", "_parse_porcelain_z",
                     "WORKTREES_FILE", "_WT_MAX_PARALLEL", "_WT_MAX", "_WT_STATUS_CACHE_S", "_wt_lock", "_wt_status_cache",
                     "_wt_git", "_wt_err", "_wt_load", "_wt_save", "_wt_validate", "_wt_names", "_wt_repo", "_wt_remove",
                     "_wt_create", "_wt_launch", "_wt_shortstat", "_wt_status", "_wt_sessions_in", "_wt_list", "_wt_find",
                     "_wt_conflicts", "_wt_merge", "_wt_discard", "_wt_diff"],
                    {"os": os, "re": re, "time": time, "json": json, "subprocess": subprocess, "threading": threading,
                     "AOC_DATA_DIR": os.path.join(tmp, "data"), "_NO_WINDOW": 0x08000000 if os.name == "nt" else 0,
                     "_find_claude_exe": lambda: exe[0]})


def git(cwd, *args):
    r = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


repo = os.path.join(tmp, "Počítač repo")   # non-ASCII + space, like OneDrive\Počítač
os.makedirs(os.path.join(repo, "sub"))
git(repo, "init", "-q", "-b", "main")
git(repo, "config", "user.email", "t@t")
git(repo, "config", "user.name", "t")
git(repo, "config", "core.autocrlf", "false")
write(os.path.join(repo, "a.txt"), "one\ntwo\nthree\n")
write(os.path.join(repo, "sub", "s.txt"), "s\n")
git(repo, "add", ".")
git(repo, "commit", "-q", "-m", "init")

# ── pure bits ──
v = ns["_wt_validate"]
c.check("validate: count default 1", v({"cwd": repo, "prompt": "x"})[0]["count"] == 1)
c.check("validate: 4 ok, 5 refused, 0 refused", v({"cwd": repo, "count": 4})[0]["count"] == 4
        and v({"cwd": repo, "count": 5})[1] and v({"cwd": repo, "count": 0})[1])
c.check("validate: garbage count", v({"cwd": repo, "count": "x"})[1] == "bad count")
c.check("validate: launch rules still apply", v({"cwd": "rel"})[1] and v({"cwd": repo, "model": "gpt"})[1] == "unknown model")
c.check("validate: no resume", "fresh" in v({"cwd": repo, "resume": "12345678-1234-1234-1234-123456789abc"})[1])
names = ns["_wt_names"]("C:\\r", "1030-141200", 2)
c.check("names: id, branch, path under .claude/worktrees", names[1] == ("1030-141200-2", "aoc/1030-141200-2",
        os.path.join("C:\\r", ".claude", "worktrees", "aoc-1030-141200-2")))
ss = ns["_wt_shortstat"]
c.check("shortstat", ss(" 3 files changed, 10 insertions(+), 2 deletions(-)") == {"files": 3, "add": 10, "del": 2}
        and ss(" 1 file changed, 1 deletion(-)") == {"files": 1, "add": 0, "del": 1} and ss("") == {"files": 0, "add": 0, "del": 0})
c.check("conflicts parse", ns["_wt_conflicts"]("abc123\na.txt\nb/c.txt\n\nmsg") == ["a.txt", "b/c.txt"])
c.check("err: last non-hint line", ns["_wt_err"]("fatal: x\nhint: y\n", "fb") == "fatal: x" and ns["_wt_err"]("", "fb") == "fb")

# ── repo facts ──
facts, err = ns["_wt_repo"](os.path.join(repo, "sub"))
c.check("repo: top, branch, sha from a subfolder", err is None and facts["repo"] == os.path.normpath(repo)
        and facts["base"] == "main" and facts["base_sha"] == git(repo, "rev-parse", "HEAD"))
nogit = os.path.join(tmp, "plain")
os.makedirs(nogit)
c.check("repo: not a repo", "not inside a git repo" in ns["_wt_repo"](nogit)[1])
empty = os.path.join(tmp, "empty")
os.makedirs(empty)
git(empty, "init", "-q", "-b", "main")
c.check("repo: no commits", "no commits" in ns["_wt_repo"](empty)[1])
det = os.path.join(tmp, "det")
shutil.copytree(repo, det)
git(det, "checkout", "-q", "--detach")
c.check("repo: detached HEAD refused", "detached" in ns["_wt_repo"](det)[1])

# ── create: all or nothing ──
git(repo, "branch", "aoc/clash-2")
made, err = ns["_wt_create"](facts, "clash", 3)
c.check("create: second branch exists -> error", made is None and "worktree add failed" in err)
c.check("create: rolled back the first one", not os.path.isdir(os.path.join(repo, ".claude", "worktrees", "aoc-clash-1"))
        and "aoc/clash-1" not in git(repo, "branch", "--list", "aoc/clash-1"))
git(repo, "branch", "-D", "aoc/clash-2")

# ── launch with a fake Popen ──
started = []


class P:
    def __init__(self, argv, cwd=None, creationflags=0):
        started.append((argv, cwd))
        self.pid = 4000 + len(started)


ns["_launch_last"][0] = 0
r = ns["_wt_launch"]({"cwd": os.path.join(repo, "sub"), "prompt": 'fix "it" & go', "model": "sonnet", "count": 2},
                     now=1_800_000_000, popen=P, path=reg)
c.check("launch ok, 2 items", r["ok"] and len(r["items"]) == 2)
w1, w2 = r["items"]
c.check("launch: worktrees exist on new branches", os.path.isdir(w1["path"]) and os.path.isdir(w2["path"])
        and git(w1["path"], "rev-parse", "--abbrev-ref", "HEAD") == w1["branch"] and w1["branch"].startswith("aoc/"))
c.check("launch: one claude per worktree, in the same subfolder, prompt as one arg",
        [s[1] for s in started] == [os.path.join(w1["path"], "sub"), os.path.join(w2["path"], "sub")]
        and started[0][0] == [exe[0], "--model", "sonnet", 'fix "it" & go'])
c.check("launch: pids + group recorded", w1["pid"] == 4001 and w1["group"] == w2["group"] and (w1["n"], w1["of"]) == (1, 2))
c.check("launch: registry saved", len(ns["_wt_load"](reg)) == 2)
r2 = ns["_wt_launch"]({"cwd": repo, "count": 1}, now=1_800_000_001, popen=P, path=reg)
c.check("launch: rate limit", not r2["ok"] and "few seconds" in r2["error"])
exe[0] = None
ns["_launch_last"][0] = 0
c.check("launch: no claude", "not found" in ns["_wt_launch"]({"cwd": repo}, now=1_800_000_100, popen=P, path=reg)["error"])
exe[0] = os.path.join(tmp, "claude.exe")
ns["_launch_last"][0] = 0
c.check("launch: not a repo", "git repo" in ns["_wt_launch"]({"cwd": nogit}, now=1_800_000_200, popen=P, path=reg)["error"])
ns["_WT_MAX"] = 3
ns["_launch_last"][0] = 0
c.check("launch: max worktrees", "merge or discard" in ns["_wt_launch"]({"cwd": repo, "count": 2}, now=1_800_000_300, popen=P, path=reg)["error"])
ns["_WT_MAX"] = 40

# ── status / list / sessions ──
L = lambda sessions=(): {x["id"]: x for x in ns["_wt_list"](sessions, path=reg, now=time.time() + 1000)["items"]}
ns["_wt_status_cache"].clear()
it = L()
c.check("status: fresh worktree clean, 0 ahead", it[w1["id"]]["dirty"] == 0 and it[w1["id"]]["ahead"] == 0 and it[w1["id"]]["files"] == 0)
write(os.path.join(w1["path"], "a.txt"), "one\nTWO\nthree\nfour\n")
write(os.path.join(w1["path"], "new.txt"), "n\n")
ns["_wt_status_cache"].clear()
c.check("status: dirty counts tracked + untracked", L()[w1["id"]]["dirty"] == 2)
git(w1["path"], "add", ".")
git(w1["path"], "commit", "-q", "-m", "w1 change")
ns["_wt_status_cache"].clear()
s1 = L()[w1["id"]]
c.check("status: ahead 1, diffstat, subject", s1["dirty"] == 0 and s1["ahead"] == 1 and s1["behind"] == 0
        and (s1["files"], s1["add"], s1["del"]) == (2, 3, 1) and s1["last_subject"] == "w1 change")
c.check("status: cached 10 s", ns["_wt_status"](w1, now=time.time() + 1000)["ahead"] == 1)
sess = [{"id": "s1", "cwd": os.path.join(w1["path"], "sub"), "session_active": True, "waiting_on_you": True, "estimated_cost": 0.5},
        {"id": "s2", "cwd": w1["path"] + "-other", "session_active": True},
        {"id": "s3", "cwd": repo, "session_active": True}]
link = L(sess)[w1["id"]]["sessions"]
c.check("sessions: matched by folder (subfolder yes, sibling prefix / repo no)", [s["id"] for s in link] == ["s1"]
        and link[0]["waiting"] and link[0]["active"])
c.check("list: repo_name, newest first", L()[w1["id"]]["repo_name"] == "Počítač repo")

# ── merge ──
M = lambda wid, sessions=(): ns["_wt_merge"](wid, sessions, path=reg)
c.check("merge: unknown", M("nope")["error"] == "unknown worktree")
c.check("merge: blocked while a session is open", "still open" in M(w1["id"], sess)["error"])
c.check("merge: nothing to merge", "no new commits" in M(w2["id"])["error"])
write(os.path.join(w2["path"], "x.txt"), "x\n")
c.check("merge: dirty worktree", "uncommitted" in M(w2["id"])["error"])
os.remove(os.path.join(w2["path"], "x.txt"))
git(repo, "checkout", "-q", "-b", "other")
c.check("merge: repo on another branch", "check out main" in M(w1["id"])["error"])
git(repo, "checkout", "-q", "main")
write(os.path.join(repo, "sub", "s.txt"), "dirty\n")
c.check("merge: dirty repo", "uncommitted changes" in M(w1["id"])["error"])
git(repo, "checkout", "--", "sub/s.txt")
# a conflicting commit on main
write(os.path.join(repo, "a.txt"), "one\nZWEI\nthree\n")
git(repo, "commit", "-qam", "main change")
head_before = git(repo, "rev-parse", "HEAD")
rc = M(w1["id"])
c.check("merge: conflict found without touching the repo", not rc["ok"] and rc["conflicts"] == ["a.txt"]
        and "a.txt" in rc["error"] and git(repo, "rev-parse", "HEAD") == head_before
        and git(repo, "status", "--porcelain", "--untracked-files=no") == "")
git(repo, "reset", "-q", "--hard", "HEAD~1")
ok = M(w1["id"])
c.check("merge: clean merge ok", ok["ok"] and ok["sha"] == git(repo, "rev-parse", "HEAD") and not ok["cleanup_error"])
c.check("merge: no-ff commit on main with the change", git(repo, "log", "-1", "--format=%P").count(" ") == 1
        and open(os.path.join(repo, "a.txt"), encoding="utf-8").read() == "one\nTWO\nthree\nfour\n")
c.check("merge: worktree + branch removed, registry entry gone", not os.path.isdir(w1["path"])
        and not git(repo, "branch", "--list", w1["branch"]) and w1["id"] not in L())

# ── diff + discard ──
write(os.path.join(w2["path"], "b.txt"), "committed\n")
git(w2["path"], "add", ".")
git(w2["path"], "commit", "-q", "-m", "w2")
write(os.path.join(w2["path"], "a.txt"), "wip\n")
d = ns["_wt_diff"](w2["id"], path=reg)
c.check("diff: committed + not committed parts", d["ok"] and "+committed" in d["diff"] and "not committed yet" in d["diff"] and "+wip" in d["diff"])
c.check("diff: unknown", not ns["_wt_diff"]("nope", path=reg)["ok"])
sess2 = [{"id": "z", "cwd": w2["path"], "session_active": True}]
D = lambda wid, sessions=(), force=False: ns["_wt_discard"](wid, sessions, force=force, path=reg)
c.check("discard: blocked while open", "still open" in D(w2["id"], sess2)["error"])
c.check("discard: force with dirty + unmerged work", D(w2["id"], sess2, force=True)["ok"])
c.check("discard: folder + branch gone, registry empty", not os.path.isdir(w2["path"])
        and not git(repo, "branch", "--list", w2["branch"]) and ns["_wt_load"](reg) == [])
c.check("discard: unknown", D("nope")["error"] == "unknown worktree")
c.check("git worktree list clean", git(repo, "worktree", "list").count("\n") == 0)

shutil.rmtree(tmp, ignore_errors=True)
c.finish()
