"""Cost per commit, extracted straight from monitor.py: spotting `git commit`
Bash calls (_git_commit_calls) and the commit a result reports
(_commit_from_line: gitOperation or '[branch sha] subject'), indexing them
with the session's running spend (_transcript_index_file), resolving
`git commit -q` calls through git log (_resolve_quiet_commits, git faked),
and _commit_stats' per-commit deltas, repos and totals."""
import sys, os, json, re, sqlite3, tempfile, shutil, time, calendar
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
work = tempfile.mkdtemp(prefix="aoc_cc_")
ns = exec_functions(
    ["_TRANSCRIPT_INDEX_MAX_TEXT", "_TRANSCRIPT_INDEX_BYTES_PER_TICK", "_TRANSCRIPT_INDEX_VERSION",
     "_transcript_index_connect", "_COLD_IDLE_S", "_RECACHE_MIN_TOKENS", "_usage_line_sample",
     "_usage_add_samples", "_transcript_line_messages", "_GIT_COMMIT_LINE_RE", "_GIT_SHORTSTAT_RE",
     "_GIT_COMMIT_CMD_RE", "_git_commit_calls", "_tool_result_ids", "_commit_from_line", "_index_commit_line",
     "_parse_git_log_shortstat", "_resolve_quiet_commits", "_transcript_index_file", "_transcript_index_tick",
     "_commit_stats", "_MODEL_PRICING", "_CACHE_WRITE_1H_MULT", "_model_pricing", "_calc_cost", "_iso_to_epoch"],
    {"os": os, "json": json, "re": re, "sqlite3": sqlite3, "time": time, "calendar": calendar,
     "datetime": datetime, "TRANSCRIPT_INDEX_DB": "", "_decode_project_name": lambda e: "proj"})
calls, from_line = ns["_git_commit_calls"], ns["_commit_from_line"]
calc = ns["_calc_cost"]


def bash_call(tid, cmd, ts="2026-10-01T08:00:00Z", mid=None, out=100):
    return {"type": "assistant", "timestamp": ts, "requestId": "r" + (mid or tid), "cwd": "C:\\work\\repo",
            "message": {"id": mid or "m" + tid, "model": "claude-opus-5-5",
                        "content": [{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": cmd}}],
                        "usage": {"input_tokens": 10, "output_tokens": out, "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": 0}}}


def result(tid, stdout, ts="2026-10-01T08:00:05Z", git_op=None, cwd="C:\\work\\repo"):
    tr = {"stdout": stdout, "stderr": "", "interrupted": False}
    if git_op:
        tr["gitOperation"] = git_op
    return {"type": "user", "timestamp": ts, "cwd": cwd, "toolUseResult": tr,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": stdout}]}}


# ── spotting commit calls ──
c.check("git commit -m", calls(bash_call("t1", 'git add -A && git commit -m "x"')) == [("t1", "")])
c.check("git -C dir commit", calls(bash_call("t2", 'git -C "C:/a b/repo" commit -q -F -')) == [("t2", "C:/a b/repo")])
c.check("git -c k=v commit", calls(bash_call("t3", "git -c user.name=x commit -q")) == [("t3", "")])
c.check("not a commit", calls(bash_call("t4", "git log --oneline -1; git commit-tree x")) == []
        and calls(bash_call("t5", "git status")) == [])
c.check("non-Bash tool ignored", calls({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "id": "x", "name": "Write", "input": {"command": "git commit"}}]}}) == [])

# ── commit from a result ──
r = from_line(result("t1", "[master a1b2c3d] Fix the thing\n 3 files changed, 40 insertions(+), 2 deletions(-)"))
c.check("output line parsed", r["sha"] == "a1b2c3d" and r["branch"] == "master" and r["subject"] == "Fix the thing"
        and (r["files"], r["ins"], r["dels"]) == (3, 40, 2) and r["kind"] == "committed")
r = from_line(result("t1", "[main (root-commit) 0123abc] Init\n 1 file changed, 5 insertions(+)"))
c.check("root commit, insertions only", r["sha"] == "0123abc" and (r["files"], r["ins"], r["dels"]) == (1, 5, 0))
r = from_line(result("t1", "warning: x\n[feat/x 9f9f9f9] Pick\n", git_op={"commit": {"sha": "9f9f9f9", "kind": "cherry-picked"}}))
c.check("gitOperation kind + matching line", r["sha"] == "9f9f9f9" and r["kind"] == "cherry-picked" and r["branch"] == "feat/x")
r = from_line(result("t1", "", git_op={"commit": {"sha": "abcdef1", "kind": "committed"}}))
c.check("gitOperation without output line", r["sha"] == "abcdef1" and r["subject"] == "" and r["files"] is None)
c.check("quiet commit output -> None", from_line(result("t1", "573f715 CI: x")) is None)
c.check("assistant line -> None", from_line(bash_call("t1", "git commit")) is None)

# ── indexing with running cost ──
conn = ns["_transcript_index_connect"](os.path.join(work, "idx.db"))
c.check("schema v3 has commits", ns["_TRANSCRIPT_INDEX_VERSION"] == 3
        and conn.execute("SELECT COUNT(*) FROM commits").fetchone()[0] == 0)
proj = os.path.join(work, "projects", "p")
os.makedirs(proj)
tf = os.path.join(proj, "sess-1.jsonl")
lines = [
    bash_call("a", 'git commit -m "one"', "2026-10-01T08:00:00Z", out=1000),
    result("a", "[master 1111111] One\n 2 files changed, 10 insertions(+), 1 deletion(-)", "2026-10-01T08:00:03Z"),
    bash_call("b", "git commit -q -m two", "2026-10-01T09:00:00Z", out=3000),
]
with open(tf, "w", encoding="utf-8") as f:
    f.write("".join(json.dumps(x) + "\n" for x in lines))
ns["_transcript_index_tick"](conn, os.path.join(work, "projects"))
one = conn.execute("SELECT kind, cost_at, branch, subject, ins, dels FROM commits WHERE sha = '1111111'").fetchone()
c1 = calc(10, 1000, 0, 0, "claude-opus-5-5")
c.check("commit stored with cost so far", one[0] == "committed" and abs(one[1] - c1) < 1e-6 and one[2:] == ("master", "One", 10, 1))
c.check("call without result yet -> pending", conn.execute("SELECT kind FROM commits WHERE sha = 'tu:b'").fetchone() == ("pending",))
with open(tf, "a", encoding="utf-8") as f:  # the result lands in the next tick
    f.write(json.dumps(result("b", "", "2026-10-01T09:00:04Z", cwd="C:\\work\\other")) + "\n")
ns["_transcript_index_tick"](conn, os.path.join(work, "projects"))
q = conn.execute("SELECT kind, cwd, end_epoch FROM commits WHERE sha = 'tu:b'").fetchone()
c.check("quiet result -> kind quiet, repo dir from the result line", q[0] == "quiet" and q[1] == "C:\\work\\other" and q[2])
c.check("running cost kept across ticks", abs(conn.execute("SELECT run_cost FROM sessions_meta").fetchone()[0]
                                              - (c1 + calc(10, 3000, 0, 0, "claude-opus-5-5"))) < 1e-6)

# ── resolving quiet commits through git log ──
ep = ns["_iso_to_epoch"]("2026-10-01T09:00:02Z")
log = ("2222222222222222222222222222222222222222\x1f%d\x1fHEAD -> main, origin/main\x1fTwo\n\n"
       " 4 files changed, 30 insertions(+), 5 deletions(-)\n"
       "1111111aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x1f%d\x1f\x1fOne\n\n 2 files changed, 10 insertions(+)\n"
       "3333333333333333333333333333333333333333\x1f%d\x1f\x1fLong before\n") % (ep, ep - 1, ep - 5000)
seen_args = []


def fake_git(cwd, args):
    seen_args.append((cwd, args))
    return log if cwd == "C:\\work\\other" else None


c.check("not resolved while the result is fresh", ns["_resolve_quiet_commits"](conn, fake_git, ep + 5) == 0)
c.check("resolved later", ns["_resolve_quiet_commits"](conn, fake_git, ep + 100) == 1 and seen_args[0][0] == "C:\\work\\other")
two = conn.execute("SELECT kind, branch, subject, files, ins, dels FROM commits WHERE sha = '2222222'").fetchone()
c.check("sha, branch, subject and stats from git log", two == ("committed", "main", "Two", 4, 30, 5))
c.check("already-recorded and out-of-window commits skipped",
        conn.execute("SELECT COUNT(*) FROM commits WHERE sha LIKE '3333%' OR sha LIKE '1111111a%'").fetchone()[0] == 0)
conn.execute("INSERT INTO commits (session_id, sha, kind, cwd, epoch, end_epoch, day) VALUES ('s9', 'tu:z', 'quiet', 'C:\\x', ?, ?, '2026-10-01')", (ep, ep))
ns["_resolve_quiet_commits"](conn, fake_git, ep + 100)
c.check("no match -> none (not retried)", conn.execute("SELECT kind FROM commits WHERE sha = 'tu:z'").fetchone() == ("none",))

# ── stats ──
st = ns["_commit_stats"](conn, "2026-10-01")
cm = {x["sha"]: x for x in st["commits"]}
c2 = calc(10, 3000, 0, 0, "claude-opus-5-5")
c.check("two real commits, newest first", [x["sha"] for x in st["commits"]] == ["2222222", "1111111"])
c.check("first commit costs the session so far", abs(cm["1111111"]["cost"] - round(c1, 4)) < 1e-4)
c.check("next commit costs only the work since", abs(cm["2222222"]["cost"] - round(c2, 4)) < 1e-4)
c.check("repo = folder of the commit's dir", cm["2222222"]["repo"] == "other" and cm["1111111"]["repo"] == "repo")
c.check("by_repo rows", {r["repo"]: r["commits"] for r in st["by_repo"]} == {"repo": 1, "other": 1})
c.check("totals", st["total"]["commits"] == 2 and st["total"]["ins"] == 40 and st["total"]["sessions"] == 1)
st2 = ns["_commit_stats"](conn, "2026-10-02")
c.check("period filter", st2["total"]["commits"] == 0 and st2["commits"] == [])

conn.close()
shutil.rmtree(work, ignore_errors=True)
c.finish()
