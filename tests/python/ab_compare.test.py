"""A/B model compare (monitor.py): _ab_validate / _ab_estimate / _ab_quote
(pure), _ab_argv (headless, no shell, budget cap, prompt on stdin),
_ab_parse_stream (cost from the result line, else usage once per API call;
failed vs blocked calls), and the whole flow against a REAL temp git repo
(non-ASCII path) with a fake `claude`: two worktrees off HEAD, your working
copy untouched, diffstat per run, keep (commit + merge, refused while dirty,
conflict aborted and branch left), discard (copies + branches gone), a run
that never starts, and comparisons left 'running' by an AOC restart."""
import sys, os, json, re, tempfile, shutil, subprocess, threading, time

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
_check = c.check


def _check_detail(label, cond, detail=None):
    if not cond and detail is not None:
        print("   detail:", detail)
    _check(label, cond)


c.check = _check_detail
tmp = tempfile.mkdtemp(prefix="aoc_ab_")
data_dir = os.path.join(tmp, "data")
os.makedirs(data_dir)
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

ns = exec_functions(
    ["_MODEL_PRICING", "_model_pricing", "_LAUNCH_MODELS", "_launch_validate", "_load_json_file", "_parse_numstat",
     "_AB_MODELS", "_AB_LABELS", "_AB_PROMPT_MAX", "_AB_BUDGET_DEFAULT", "_AB_BUDGET_MAX", "_AB_TIMEOUT_S", "_AB_KEEP",
     "_AB_DIFF_MAX", "_AB_FINAL_MAX", "_AB_STARTUP_TOKENS", "_AB_NO_TOOLS",
     "_ab_validate", "_ab_estimate", "_ab_quote", "_ab_argv", "_ab_parse_stream", "_wt_git", "_wt_err", "_wt_remove", "_ab_git", "_ab_repo", "_ab_wt_add",
     "_ab_wt_remove", "_ab_wt_snapshot", "_ab_load", "_ab_save", "_ab_update", "_ab_overall", "_ab_run_one",
     "_ab_start", "_ab_get", "_ab_list", "_ab_stop", "_ab_discard", "_ab_keep"],
    {"os": os, "json": json, "re": re, "time": time, "threading": threading, "subprocess": subprocess,
     "_NO_WINDOW": NO_WINDOW, "AOC_DATA_DIR": data_dir, "AB_RUNS_FILE": os.path.join(data_dir, "ab_runs.json"),
     "AB_WT_DIR": os.path.join(data_dir, "ab_worktrees"), "_ab_lock": threading.Lock(), "_ab_procs": {},
     "_ab_stopped": set(), "_own_claude_pids": set(), "_AB_LOADED_AT": 0.0,
     "_calc_cost": lambda i, o, w, r, m, w1=0: (i + o + w + r) / 1e6, "_find_claude_exe": lambda: "claude.exe"})

# ── pure parts ──
v = ns["_ab_validate"]
args, err = v({"cwd": tmp, "prompt": "  add a test  ", "models": ["haiku", "sonnet"], "budget": "1.234"})
c.check("valid", err is None and args == {"cwd": os.path.normpath(tmp), "prompt": "add a test",
                                         "models": ["haiku", "sonnet"], "budget": 1.23})
c.check("defaults: opus vs sonnet, $2", v({"cwd": tmp, "prompt": "x"})[0]["models"] == ["opus", "sonnet"]
        and v({"cwd": tmp, "prompt": "x"})[0]["budget"] == 2.0)
c.check("prompt required", v({"cwd": tmp, "prompt": " "})[1] == "write the task both models get")
c.check("same model twice refused", v({"cwd": tmp, "prompt": "x", "models": ["opus", "opus"]})[1] == "pick two different models")
c.check("unknown model refused", v({"cwd": tmp, "prompt": "x", "models": ["opus", "gpt"]})[1] == "pick two different models")
c.check("three models refused", v({"cwd": tmp, "prompt": "x", "models": ["opus", "sonnet", "haiku"]})[1] is not None)
c.check("budget range", "between" in v({"cwd": tmp, "prompt": "x", "budget": 50})[1]
        and "between" in v({"cwd": tmp, "prompt": "x", "budget": 0})[1])
c.check("budget not a number", v({"cwd": tmp, "prompt": "x", "budget": "lots"})[1] == "budget must be a number")
c.check("prompt too long", "too long" in v({"cwd": tmp, "prompt": "x" * 8001})[1])
c.check("missing folder", v({"cwd": os.path.join(tmp, "nope"), "prompt": "x"})[1] is not None)
c.check("garbage body", v("x")[1] == "bad request")

e = ns["_ab_estimate"]
lo_h, hi_h = e("fix it", "haiku")
lo_o, hi_o = e("fix it", "opus")
c.check("estimate: low < high", 0 < lo_h < hi_h and 0 < lo_o < hi_o)
c.check("estimate: haiku cheaper than opus", hi_h < hi_o and lo_h < lo_o)
c.check("estimate: longer prompt costs more", e("x" * 40000, "opus")[0] > lo_o)
q = ns["_ab_quote"]({"prompt": "x", "models": ["opus", "haiku"], "budget": 0.5})
c.check("quote: two runs, high capped by budget", [r["model"] for r in q["runs"]] == ["opus", "haiku"]
        and all(r["high"] <= 0.5 for r in q["runs"]) and q["max_total"] == 1.0)
c.check("quote: capped flag", any(r["capped"] for r in q["runs"]))
c.check("quote: garbage -> defaults, no crash", len(ns["_ab_quote"]("x")["runs"]) == 2)

argv = ns["_ab_argv"]("C:/c.exe", "sonnet", 1.5)
c.check("argv: headless stream-json", argv[:4] == ["C:/c.exe", "-p", "--model", "sonnet"]
        and "--output-format" in argv and argv[argv.index("--output-format") + 1] == "stream-json" and "--verbose" in argv)
c.check("argv: budget cap", argv[argv.index("--max-budget-usd") + 1] == "1.50")
c.check("argv: no shell, no MCP, no hooks", "Bash" in argv[argv.index("--disallowedTools") + 1]
        and "PowerShell" in argv[argv.index("--disallowedTools") + 1] and "--strict-mcp-config" in argv
        and json.loads(argv[argv.index("--settings") + 1]) == {"disableAllHooks": True})
c.check("argv: edits allowed, no session saved", "acceptEdits" in argv and "--no-session-persistence" in argv)


def stream(cost=0.0123, final="All done.", tools=2, err=1, denied=1, is_error=False, subtype="success", model="claude-sonnet-5"):
    out = [{"type": "system", "subtype": "init", "model": model}]
    for i in range(tools):
        msg = {"id": f"m{i}", "model": model, "usage": {"input_tokens": 1000, "output_tokens": 100},
               "content": [{"type": "text", "text": "x"}, {"type": "tool_use", "id": f"t{i}", "name": "Edit"}]}
        out.append({"type": "assistant", "message": msg})
        out.append({"type": "assistant", "message": msg})          # repeated per content block
    for i in range(err):
        out.append({"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True, "content": "File not found"}]}})
    for i in range(denied):
        out.append({"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True,
                                                             "content": [{"type": "text", "text": "Permission to use Bash has been denied."}]}]}})
    res = {"type": "result", "subtype": subtype, "is_error": is_error, "num_turns": tools + 1, "result": final,
           "duration_api_ms": 4200}
    if cost is not None:
        res["total_cost_usd"] = cost
    out.append(res)
    return [json.dumps(x) + "\n" for x in out]


p = ns["_ab_parse_stream"]
r = p(stream())
c.check("parse: result line cost", r["cost"] == 0.0123 and r["final"] == "All done." and r["turns"] == 3)
c.check("parse: tool calls once each", r["tool_calls"] == 2)
c.check("parse: failed vs blocked", r["errors"] == 1 and r["denials"] == 1)
c.check("parse: model from init", r["model"] == "claude-sonnet-5" and r["api_ms"] == 4200)
r = p(stream(cost=None, tools=3, err=0, denied=0) + ["not json\n", "[1]\n"])
c.check("parse: no result cost -> usage, each API call once", abs(r["cost"] - 3 * 1100 / 1e6) < 1e-9)
r = p(stream(is_error=True, subtype="error_max_budget_usd"))
c.check("parse: budget stop", r["is_error"] and r["stop"] == "error_max_budget_usd")
c.check("parse: empty", p([])["cost"] == 0 and p([])["tool_calls"] == 0)


# ── flow against a real git repo ──
def git(*a, cwd=None):
    return subprocess.run(["git", "-c", "core.quotepath=off"] + list(a), cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", creationflags=NO_WINDOW)


repo = os.path.join(tmp, "Počítač repo")
os.makedirs(os.path.join(repo, "app"))
git("init", "-q", "-b", "main", cwd=repo)
git("config", "user.email", "t@t", cwd=repo)
git("config", "user.name", "t", cwd=repo)
with open(os.path.join(repo, "app", "a.txt"), "w", encoding="utf-8") as f:
    f.write("one\n")
with open(os.path.join(repo, "shared.txt"), "w", encoding="utf-8") as f:
    f.write("base\n")
git("add", "-A", cwd=repo)
git("commit", "-q", "-m", "init", cwd=repo)
head0 = git("rev-parse", "HEAD", cwd=repo).stdout.strip()

launched = []


class FakeProc:
    """A fake `claude -p`: edits files in its cwd and prints stream-json."""
    plan = {}
    n = 0

    def __init__(self, argv, cwd=None, **kw):
        FakeProc.n += 1
        self.pid = 90000 + FakeProc.n
        model = argv[argv.index("--model") + 1]
        launched.append({"argv": argv, "cwd": cwd, "kw": kw})
        job = FakeProc.plan.get(model, {})
        for rel, text in (job.get("files") or {}).items():
            path = os.path.join(cwd, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        lines = job.get("lines")
        if lines is None:
            lines = stream(cost=job.get("cost", 0.01), final=f"{model} done", model=_m(model))
        self.stdout = iter([l.encode("utf-8") for l in lines])
        self.got = b""
        outer = self

        class In:
            def write(self, b):
                outer.got += b

            def close(self):
                pass
        self.stdin = In()

    def wait(self):
        return 0

    def kill(self):
        pass


def _m(k):
    return ns["_AB_MODELS"][k]


sync = lambda fn, *a: fn(*a)
start = ns["_ab_start"]
c.check("start: needs confirm", start({"cwd": repo, "prompt": "x"})["error"] == "confirm the cost first")
plain = os.path.join(tmp, "plain")
os.makedirs(plain)
c.check("start: not a git repo", "not inside a git repository" in start({"cwd": plain, "prompt": "x", "confirm": True})["error"])

FakeProc.plan = {"haiku": {"files": {"a.txt": "one\nhaiku\n", "new.txt": "n\n"}, "cost": 0.02},   # cwd = <copy>/app
                 "sonnet": {"files": {"a.txt": "one\nsonnet\n"}, "cost": 0.05}}
res = start({"cwd": os.path.join(repo, "app"), "prompt": "edit a.txt", "models": ["haiku", "sonnet"], "budget": 1,
             "confirm": True}, exe="C:/claude.exe", popen=FakeProc, spawn=sync)
c.check("start ok", res.get("ok") is True and res.get("id"), res)
cid = res["id"]
cmp_ = ns["_ab_get"](cid)
runs = {r["label"]: r for r in cmp_["runs"]}
c.check("both runs done", cmp_["state"] == "done" and runs["A"]["state"] == "done" and runs["B"]["state"] == "done", cmp_)
c.check("runs in their own worktree subfolder", all(os.path.normcase(l["cwd"]).startswith(os.path.normcase(os.path.join(data_dir, "ab_worktrees")))
                                                   and l["cwd"].endswith("app") for l in launched))
c.check("prompt went in on stdin, not argv", all("edit a.txt" not in l["argv"] for l in launched))
c.check("stderr merged into stdout", all(l["kw"].get("stderr") == subprocess.STDOUT for l in launched))
c.check("worktrees off HEAD", all(git("rev-parse", "HEAD", cwd=r["wt"]).stdout.strip() == head0 for r in runs.values()))
c.check("your working copy untouched", open(os.path.join(repo, "app", "a.txt"), encoding="utf-8").read() == "one\n"
        and git("status", "--porcelain", cwd=repo).stdout.strip() == "")
c.check("diffstat A", runs["A"]["diffstat"]["files"] == 2 and runs["A"]["diffstat"]["ins"] == 2, runs["A"]["diffstat"])
c.check("diffstat B", runs["B"]["diffstat"]["files"] == 1 and "+sonnet" in runs["B"]["diff"])
c.check("cost/answer/model per run", runs["A"]["cost"] == 0.02 and runs["B"]["cost"] == 0.05
        and runs["A"]["final"] == "haiku done" and runs["B"]["model"] == _m("sonnet"))
c.check("wall time recorded", runs["A"]["wall_s"] is not None and runs["A"]["live"] is None)
lst = ns["_ab_list"]()["items"]
c.check("list: no diffs/answers", lst and lst[0]["id"] == cid and all("diff" not in r and "final" not in r for r in lst[0]["runs"]))

# keep: dirty working copy refused
with open(os.path.join(repo, "shared.txt"), "w", encoding="utf-8") as f:
    f.write("dirty\n")
c.check("keep refused while dirty", "uncommitted" in ns["_ab_keep"](cid, "B")["error"])
git("checkout", "--", "shared.txt", cwd=repo)
c.check("keep unknown label", ns["_ab_keep"](cid, "Z")["error"] == "that run has nothing to keep")
k = ns["_ab_keep"](cid, "B")
c.check("keep B merged", k.get("ok") and open(os.path.join(repo, "app", "a.txt"), encoding="utf-8").read() == "one\nsonnet\n", k)
c.check("merge commit on your branch", git("rev-parse", "HEAD", cwd=repo).stdout.strip() == k["merged"]
        and git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo).stdout.strip() == "main")
c.check("both copies + branches gone", not any(os.path.exists(r["wt"]) for r in runs.values())
        and "aoc-ab/" not in git("branch", "--list", cwd=repo).stdout)
after = ns["_ab_get"](cid)
c.check("record: kept B, diffs dropped", after["state"] == "kept" and after["kept"] == "B"
        and all("diff" not in r for r in after["runs"]))
c.check("keep twice refused", ns["_ab_keep"](cid, "B")["error"] == "already kept")

# discard
FakeProc.plan = {"opus": {"files": {"x.txt": "x\n"}}, "haiku": {"files": {}}}
cid2 = start({"cwd": repo, "prompt": "y", "models": ["opus", "haiku"], "confirm": True}, exe="c", popen=FakeProc, spawn=sync)["id"]
c2 = ns["_ab_get"](cid2)
c.check("no-change run: keep refused", "changed no files" in ns["_ab_keep"](cid2, "B")["error"])
c.check("discard ok", ns["_ab_discard"](cid2) == {"ok": True})
c.check("discard: copies + branches gone", not any(os.path.exists(r["wt"]) for r in c2["runs"])
        and "aoc-ab/" not in git("branch", "--list", cwd=repo).stdout
        and "ab_worktrees" not in git("worktree", "list", cwd=repo).stdout)
c.check("discard: state", ns["_ab_get"](cid2)["state"] == "discarded" and ns["_ab_discard"](cid2) == {"ok": True})
c.check("discard unknown", ns["_ab_discard"]("nope")["error"] == "not found")

# merge conflict: your branch moved on and touched the same file
FakeProc.plan = {"opus": {"files": {"shared.txt": "opus\n"}}, "sonnet": {"files": {"shared.txt": "sonnet\n"}}}
cid3 = start({"cwd": repo, "prompt": "z", "confirm": True}, exe="c", popen=FakeProc, spawn=sync)["id"]
with open(os.path.join(repo, "shared.txt"), "w", encoding="utf-8") as f:
    f.write("mine\n")
git("commit", "-qam", "mine", cwd=repo)
k = ns["_ab_keep"](cid3, "A")
br = [r for r in ns["_ab_get"](cid3)["runs"] if r["label"] == "A"][0]["branch"]
c.check("conflict: refused, branch named", not k["ok"] and "merge conflict" in k["error"] and k["branch"] == br)
c.check("conflict: merge aborted, your file intact", git("status", "--porcelain", cwd=repo).stdout.strip() == ""
        and open(os.path.join(repo, "shared.txt"), encoding="utf-8").read() == "mine\n")
c.check("conflict: kept branch left, other removed", br in git("branch", "--list", cwd=repo).stdout
        and "-b-sonnet" not in git("branch", "--list", cwd=repo).stdout)
c.check("conflict: record", ns["_ab_get"](cid3)["merge_error"] is True)
git("branch", "-D", br, cwd=repo)

# a run that never starts / prints only errors
def boom(*a, **k):
    raise OSError("no such exe")


FakeProc.plan = {"opus": {"lines": ["Error: not logged in\n"]}}
cid4 = start({"cwd": repo, "prompt": "w", "models": ["opus", "haiku"], "confirm": True}, exe="c",
             popen=lambda argv, **kw: boom() if "haiku" in argv else FakeProc(argv, **kw), spawn=sync)["id"]
r4 = {r["label"]: r for r in ns["_ab_get"](cid4)["runs"]}
c.check("never started -> error", r4["B"]["state"] == "error" and "could not start" in r4["B"]["error"])
c.check("no json output -> error with its text", r4["A"]["state"] == "error" and "not logged in" in r4["A"]["error"])
c.check("comparison finished", ns["_ab_get"](cid4)["state"] == "done")
ns["_ab_discard"](cid4)

# one at a time; a restart turns stale 'running' into an error
items = ns["_ab_load"]()
items.append({"id": "live", "created": time.time(), "repo": repo, "state": "running", "prompt": "p",
              "runs": [{"label": "A", "state": "running"}, {"label": "B", "state": "done"}]})
ns["_ab_save"](items)
ns["_AB_LOADED_AT"] = time.time() - 100
c.check("second start refused while one runs", "already running" in start({"cwd": repo, "prompt": "q", "confirm": True},
                                                                         exe="c", popen=FakeProc, spawn=sync)["error"])
ns["_AB_LOADED_AT"] = time.time() + 100
o = ns["_ab_get"]("live")
c.check("restart: stale run -> error, comparison done", o["state"] == "done" and o["runs"][0]["state"] == "error"
        and "restarted" in o["runs"][0]["error"])
ns["_ab_procs"][("live", "A")] = object()
c.check("restart: a run this process owns stays running", ns["_ab_get"]("live")["runs"][0]["state"] == "running")
ns["_ab_procs"].clear()
ns["_ab_save"]([{"id": str(i), "created": i} for i in range(30)])
c.check("keeps the newest 20", [x["id"] for x in ns["_ab_load"]()] == [str(i) for i in range(10, 30)])

shutil.rmtree(tmp, ignore_errors=True)
c.finish()
