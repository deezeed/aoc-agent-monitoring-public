"""Orchestration (server/17_orchestration.py): plan checks, DAG scheduling,
agent contracts, retries, the supervisor's decisions, metrics -- and the
engine end to end on a real temp git repo with fake `claude -p` processes
(a fake agent edits files and writes its JSON answer; nothing is billed)."""
import ast, json, os, re, shutil, sqlite3, subprocess, sys, tempfile, threading, time

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions, read_source, SERVER_DIR
from lib.check import Checker

c = Checker()
src = read_source(os.path.join(SERVER_DIR, "17_orchestration.py"))
names = []
for node in ast.parse(src).body:
    if isinstance(node, ast.FunctionDef):
        names.append(node.name)
    elif isinstance(node, ast.Assign):
        names += [t.id for t in node.targets if isinstance(t, ast.Name)]
names = [n for n in names if n.startswith(("_orch", "_ORCH", "ORCH_"))]

data_dir = tempfile.mkdtemp()
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
base_g = {"os": os, "json": json, "re": re, "time": time, "sqlite3": sqlite3, "subprocess": subprocess,
          "threading": threading, "_NO_WINDOW": NO_WINDOW, "AOC_DATA_DIR": data_dir,
          "_log_bg_error": lambda where, e: print("bg error", where, e), "_own_claude_pids": set(),
          "_is_claude_pid_alive": lambda pid: False, "_snapshot_processes": lambda: [], "_find_claude_exe": lambda: "claude.exe",
          "_load_json_file": lambda path, default=None: default}
wt = exec_functions(["_wt_git", "_wt_err", "_wt_remove", "_wt_repo", "_wt_conflicts", "_parse_porcelain_z"],
                    dict(base_g))
src_names = [n for n in names if n != "_orch_lock"]
o = exec_functions(src_names, dict(base_g, **wt))
o["_orch_lock"] = threading.RLock()
# exec_functions gives each its own globals dict: share one so functions see each other
g = o["_orch_step"].__globals__
for k, v in o.items():
    g[k] = v
g["_orch_lock"] = threading.RLock()
O = o


def T(tid, role="developer", deps=(), budget=1.0, state="pending", **kw):
    t = {"id": tid, "role": role, "title": tid.title(), "instructions": "do " + tid, "done_criteria": "it works",
         "depends_on": list(deps), "model": "sonnet", "budget_usd": budget, "state": state, "attempt": 0,
         "feedback": "", "cost_usd": 0.0, "commits": 0, "turns": 0, "session_id": "", "branch": "", "output": None,
         "error": "", "started": None, "finished": None, "pid": None, "path": "", "job_base": ""}
    t.update(kw)
    return t


# ── plan validation ──
plan = [{"id": "Research API", "role": "research", "title": "Look up", "instructions": "x", "done_criteria": "y",
         "depends_on": [], "model": "haiku", "budget_usd": 99},
        {"id": "dev", "role": "developer", "title": "Build", "instructions": "x", "done_criteria": "y",
         "depends_on": ["research-api"], "model": "gpt", "budget_usd": "0.01"}]
v, err = O["_orch_validate_tasks"](plan)
c.check("valid plan: ids slugged, budget clamped, unknown model -> sonnet",
        err is None and v[0]["id"] == "research-api" and v[0]["budget_usd"] == 5.0 and v[1]["budget_usd"] == 0.1
        and v[1]["model"] == "sonnet")
c.check("empty plan rejected", O["_orch_validate_tasks"]([])[1] == "the plan has no tasks")
c.check("an agent outside the team rejected", "not an agent of this run" in O["_orch_validate_tasks"]([dict(plan[0], role="ceo")])[1])
c.check("unknown dependency rejected", "unknown task" in O["_orch_validate_tasks"]([dict(plan[1], depends_on=["nope"])])[1])
cyc = [dict(plan[0], id="a", depends_on=["b"]), dict(plan[0], id="b", depends_on=["a"])]
c.check("cycle rejected", O["_orch_validate_tasks"](cyc)[1] == "the dependencies go in a circle")
c.check("duplicate ids rejected", "two tasks" in O["_orch_validate_tasks"]([plan[0], plan[0]])[1])
c.check("too many tasks rejected", "at most" in O["_orch_validate_tasks"]([dict(plan[0], id=f"t{i}") for i in range(9)])[1])
c.check("missing done criterion rejected", "done criterion" in O["_orch_validate_tasks"]([dict(plan[0], done_criteria=" ")])[1])
new, err = O["_orch_validate_tasks"]([dict(plan[0], id="qa", role="qa", depends_on=["dev"])], existing=[T("dev")], max_new=2)
c.check("added tasks may depend on existing ones", err is None and new[0]["depends_on"] == ["dev"])
c.check("added task can't reuse an id", "two tasks" in O["_orch_validate_tasks"]([dict(plan[0], id="dev")], existing=[T("dev")])[1])

# ── waves / topo ──
ts = [T("a"), T("b"), T("c", deps=["a", "b"]), T("d", deps=["c"])]
c.check("waves: parallel first, then dependents", O["_orch_waves"](ts) == [["a", "b"], ["c"], ["d"]])
c.check("topo keeps plan order", O["_orch_topo"](ts) == ["a", "b", "c", "d"])

# ── scheduling ──
run = {"max_parallel": 2, "budget_usd": 10.0}
c.check("starts independent tasks up to max_parallel",
        O["_orch_next"](run, [T("a"), T("b"), T("e"), T("c", deps=["a"])], 0) == [("start", "a"), ("start", "b")])
c.check("a running task takes a slot",
        O["_orch_next"](run, [T("a", state="running"), T("b"), T("e")], 0) == [("start", "b")])
c.check("dependent waits for its input", O["_orch_next"](run, [T("a", state="running"), T("c", deps=["a"])], 0) == [])
c.check("dependent starts once inputs are done",
        O["_orch_next"](run, [T("a", state="done"), T("c", deps=["a"])], 0) == [("start", "c")])
acts = O["_orch_next"](run, [T("a", state="failed"), T("c", deps=["a"]), T("d", deps=["c"])], 0)
c.check("failed input blocks the chain, then review",
        acts[0][:2] == ("block", "c") and acts[1][:2] == ("block", "d") and acts[-1] == ("supervise",))
acts = O["_orch_next"]({"max_parallel": 3, "budget_usd": 2.0}, [T("a", budget=1.5), T("b", budget=1.0)], 0.2)
c.check("budget: a task that would exceed the run budget is skipped",
        acts[0] == ("start", "a") and acts[1][:2] == ("skip", "b"))
c.check("all done -> supervise", O["_orch_next"](run, [T("a", state="done"), T("b", state="done")], 0) == [("supervise",)])

# ── agent command line + contract ──
argv = O["_orch_agent_argv"]("claude.exe", T("x", role="research", budget=0.5))
c.check("research agent: read + web tools only, never prompts, own budget",
        argv[argv.index("--tools") + 1] == "Read,Grep,Glob,WebSearch,WebFetch"
        and argv[argv.index("--permission-mode") + 1] == "dontAsk" and argv[argv.index("--max-budget-usd") + 1] == "0.50"
        and "Edit" not in argv[argv.index("--allowedTools") + 1])
dev = O["_orch_agent_argv"]("claude.exe", T("x"))
c.check("developer agent may edit and run commands", "Edit" in dev[dev.index("--tools") + 1] and "Bash" in dev[dev.index("--tools") + 1])
qa = O["_orch_agent_argv"]("c", T("x", role="qa"))
c.check("qa agent can run tests but can't edit", "Edit" not in qa[qa.index("--tools") + 1] and "Bash" in qa[qa.index("--tools") + 1])
c.check("agents keep AOC's hooks (danger guard): no disableAllHooks", "disableAllHooks" not in " ".join(dev))
c.check("no prompt argument: the task is the stdin message (not 'follow stdin', refused live as an injection)",
        dev[1:3] == ["-p", "--model"] and not any("stdin" in a for a in dev)
        and not any("stdin" in a for a in O["_orch_llm_argv"]("c", {}, "opus", 1.0)))
schema = json.loads(dev[dev.index("--json-schema") + 1])
c.check("contract requires status, summary, result, errors", {"status", "summary", "result", "errors"} <= set(schema["required"]))
pl = O["_orch_llm_argv"]("c", O["_ORCH_PLAN_SCHEMA"], "opus", 1.0, read_tools=True)
c.check("planner: no hooks, not saved, read-only tools",
        "disableAllHooks" in " ".join(pl) and "--no-session-persistence" in pl and pl[pl.index("--tools") + 1] == "Read,Grep,Glob")
sv = O["_orch_llm_argv"]("c", O["_ORCH_DECISION_SCHEMA"], "opus", 1.0)
c.check("supervisor: no tools", sv[sv.index("--tools") + 1] == "")

ok = json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.12, "num_turns": 4,
                 "session_id": "s-1", "structured_output": {"status": "done", "summary": "s", "result": "r",
                                                            "files_changed": [], "for_next": "", "errors": []}})
p = O["_orch_parse"]("warning line\n" + ok)
c.check("parse: structured answer, cost, turns, session (stderr noise before the JSON ignored)",
        p["ok"] and p["data"]["status"] == "done" and p["cost"] == 0.12 and p["turns"] == 4 and p["session_id"] == "s-1")
c.check("parse: garbage -> error", not O["_orch_parse"]("boom")["ok"])
c.check("parse: no structured_output -> contract error",
        "JSON format" in O["_orch_parse"](json.dumps({"subtype": "success", "result": "hi"}))["error"])
c.check("parse: budget stop named", O["_orch_parse"](json.dumps({"subtype": "error_max_budget_usd", "is_error": True,
                                                                   "total_cost_usd": 1.0}))["error"] == "stopped at its budget limit")
c.check("check: done with empty result is invalid", O["_orch_check_result"]({"status": "done", "summary": "x", "result": ""}) != "")
c.check("check: valid", O["_orch_check_result"]({"status": "failed", "summary": "x", "result": ""}) == "")

pr = O["_orch_task_prompt"]("Ship it", T("qa", role="qa", deps=["dev"], feedback="tests were not run"),
                            [T("dev", output={"summary": "built", "result": "added x", "for_next": "run npm test"}, commits=2, branch="aoc/dev")],
                            "main")
c.check("task prompt: goal, task, inputs, hand-off note, commits hint, retry feedback",
        all(s in pr for s in ("TEAM GOAL: Ship it", "YOUR TASK (qa)", "added x", "run npm test",
                              "git log main..HEAD", "THIS IS A RETRY", "tests were not run")))

# ── supervisor decisions ──
tasks = [T("a", state="failed", attempt=2), T("b", state="done", attempt=1), T("c", deps=["b"], state="done", attempt=1)]
act, ch, err = O["_orch_apply_decision"](tasks, {"action": "retry", "retry": [{"id": "a", "feedback": "f"}, {"id": "zz", "feedback": ""}]}, False)
c.check("retry: known task under the reviewed limit", act == "retry" and ch["retry"] == [("a", "f")])
act, ch, _ = O["_orch_apply_decision"]([T("a", state="failed", attempt=3)], {"action": "retry", "retry": [{"id": "a", "feedback": "f"}], "report": "r"}, False)
c.check("retry past the limit -> finish", act == "finish")
act, ch, _ = O["_orch_apply_decision"](tasks, {"action": "retry", "retry": [{"id": "a", "feedback": "f"}], "report": "r"}, True)
c.check("last round forces finish", act == "finish" and ch["report"] == "r")
act, ch, err = O["_orch_apply_decision"](tasks, {"action": "add_tasks", "tasks": [dict(plan[0], id="qa", role="qa", depends_on=["c"])]}, False)
c.check("add_tasks: validated against existing", act == "add_tasks" and ch["add"][0]["depends_on"] == ["c"])
act, ch, err = O["_orch_apply_decision"](tasks, {"action": "add_tasks", "tasks": [dict(plan[0], depends_on=["nope"])], "report": ""}, False)
c.check("add_tasks invalid -> finish with the reason", act == "finish" and "invalid" in err)
c.check("dependents of a retried task re-run too", O["_orch_dependents"](tasks, "b") == {"c"})

# ── metrics ──
run_m = {"created": 100.0, "updated": 500.0, "finished": 400.0, "state": "review", "rounds": 1}
tm = [T("a", state="done", cost_usd=0.6, attempt=2, started=110, finished=170),
      T("b", state="failed", cost_usd=0.2, attempt=1, started=110, finished=130)]
ev = [{"kind": "plan_ready", "cost_usd": 0.3, "human": 0}, {"kind": "approved", "cost_usd": 0, "human": 1},
      {"kind": "supervise", "cost_usd": 0.1, "human": 0}, {"kind": "merged", "cost_usd": 0, "human": 1}]
m = O["_orch_metrics"](run_m, tm, ev)
c.check("metrics: coordination vs work cost", m["cost_coordination"] == 0.4 and m["cost_work"] == 0.8 and m["coordination_share"] == 0.333)
c.check("metrics: duration, success rate, retries, human interventions, avg latency",
        m["duration_s"] == 300 and m["success_rate"] == 0.5 and m["retries"] == 1 and m["human_interventions"] == 2
        and m["avg_task_s"] == 40)


# ── engine end to end on a real repo with fake agents ──
def git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


# leftovers: a real test run leaves caches behind (found live: __pycache__ got committed)
c.check("junk: caches, deps, logs", all(O["_orch_is_junk"](p) for p in (
    "__pycache__/calc.cpython-39.pyc", "src/__pycache__/x.pyc", "web/node_modules/a/index.js", ".pytest_cache/v/x",
    "run.log", "a/.DS_Store", "x.pyc")))
c.check("not junk: real files", not any(O["_orch_is_junk"](p) for p in ("calc.py", "build.py", "docs/coverage.md", "src/log.py")))
lr = tempfile.mkdtemp()
git("init", "-q", "-b", "main", cwd=lr); git("config", "user.email", "t@t", cwd=lr); git("config", "user.name", "t", cwd=lr)
open(os.path.join(lr, "calc.py"), "w").write("a = 1\n")
git("add", "-A", cwd=lr); git("commit", "-qm", "init", cwd=lr)
open(os.path.join(lr, "calc.py"), "a").write("b = 2\n")
os.makedirs(os.path.join(lr, "__pycache__")); open(os.path.join(lr, "__pycache__", "calc.pyc"), "wb").write(b"\0")
os.makedirs(os.path.join(lr, "node_modules", "x")); open(os.path.join(lr, "node_modules", "x", "i.js"), "w").write("1")
open(os.path.join(lr, "new_mod.py"), "w").write("c = 3\n")
O["_orch_commit_leftovers"]({"id": "r"}, T("dev"), lr, wt["_wt_git"])
committed = git("show", "--name-only", "--format=", "HEAD", cwd=lr).stdout.split()
c.check("leftovers: tracked edits + new source files committed, caches/deps left out",
        sorted(committed) == ["calc.py", "new_mod.py"])
head = git("rev-parse", "HEAD", cwd=lr).stdout
O["_orch_commit_leftovers"]({"id": "r"}, T("dev"), lr, wt["_wt_git"])
c.check("leftovers: only junk left -> no empty commit", git("rev-parse", "HEAD", cwd=lr).stdout == head)
shutil.rmtree(lr, ignore_errors=True)


repo = tempfile.mkdtemp()
git("init", "-q", "-b", "main", cwd=repo)
git("config", "user.email", "t@t", cwd=repo); git("config", "user.name", "t", cwd=repo)
open(os.path.join(repo, "app.txt"), "w").write("v1\n")
git("add", "-A", cwd=repo); git("commit", "-qm", "init", cwd=repo)
g["ORCH_DB"] = os.path.join(data_dir, "orchestration.db")
g["ORCH_DIR"] = os.path.join(data_dir, "orch")

behaviour = {}     # task id -> list of answers per attempt (callables or dicts)
calls = []


def answer(base, so, cost=0.1, subtype="success"):
    with open(base + ".out", "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "result", "subtype": subtype, "is_error": subtype != "success", "total_cost_usd": cost,
                            "num_turns": 3, "session_id": "sess-" + os.path.basename(base), "structured_output": so}))


def fake_spawn(argv, cwd, stdin_text, base):
    os.makedirs(os.path.dirname(base), exist_ok=True)
    calls.append((os.path.basename(base), cwd, stdin_text))
    kind = os.path.basename(base).rsplit("-", 1)[0]
    if kind == "plan":
        answer(base, {"summary": "two devs then qa", "single_agent": False, "tasks": [
            {"id": "api", "role": "developer", "title": "API", "instructions": "write api.txt", "done_criteria": "api.txt",
             "depends_on": [], "model": "sonnet", "budget_usd": 1},
            {"id": "ui", "role": "developer", "title": "UI", "instructions": "write ui.txt", "done_criteria": "ui.txt",
             "depends_on": [], "model": "sonnet", "budget_usd": 1},
            {"id": "qa", "role": "qa", "title": "Check", "instructions": "check both", "done_criteria": "both exist",
             "depends_on": ["api", "ui"], "model": "haiku", "budget_usd": 0.5}]}, cost=0.3)
        return 1
    if kind.startswith("supervise"):
        answer(base, behaviour["supervise"].pop(0), cost=0.05)
        return 2
    tid = kind
    step = behaviour[tid].pop(0)
    so = step(cwd) if callable(step) else step
    answer(base, so)
    return 3


def done(summary="ok", result="did it"):
    return {"status": "done", "summary": summary, "result": result, "files_changed": [], "for_next": "", "errors": []}


def write(name, text, res=None):
    def f(cwd):
        open(os.path.join(cwd, name), "w").write(text)
        return res or done(result="wrote " + name)
    return f


def qa_check(cwd):
    ok_ = os.path.exists(os.path.join(cwd, "api.txt")) and os.path.exists(os.path.join(cwd, "ui.txt"))
    return done(result="both files present") if ok_ else {"status": "failed", "summary": "missing", "result": "",
                                                          "files_changed": [], "for_next": "", "errors": ["missing"]}


behaviour.update({
    "api": [write("api.txt", "api\n")],
    "ui": [{"status": "failed", "summary": "could not", "result": "", "files_changed": [], "for_next": "", "errors": ["x"]},
           write("ui.txt", "ui\n")],
    "qa": [qa_check],
    "supervise": [{"action": "finish", "reason": "all good", "report": "API + UI added, QA passed", "retry": [], "tasks": []}],
})
kw = dict(exe="claude.exe", spawn=fake_spawn, alive=lambda pid: False, kill=lambda pid: None)
r = O["_orch_create"]({"goal": "Add the api and the ui files", "cwd": repo, "max_parallel": 2, "budget_usd": 10},
                      exe="claude.exe", spawn=fake_spawn)
c.check("create: run planning", r["ok"])
rid = r["id"]
plan_in = calls[0][2]
c.check("planner sees the goal and the repo files", "Add the api and the ui files" in plan_in and "app.txt" in plan_in)


def step(n=1):
    for _ in range(n):
        conn = O["_orch_connect"]()
        O["_orch_step"](conn, rid, **kw)
        conn.close()
    return O["_orch_get"](rid)


v = step()
c.check("plan answer -> plan_ready with 3 tasks in 2 waves, nothing runs before approval",
        v["run"]["state"] == "plan_ready" and [t["id"] for t in v["tasks"]] == ["api", "ui", "qa"]
        and v["waves"] == [["api", "ui"], ["qa"]] and not any(t["state"] == "running" for t in v["tasks"]))
c.check("edit plan: invalid edit refused", not O["_orch_action"]("plan", {"id": rid, "tasks": []})["ok"])
c.check("approve", O["_orch_action"]("approve", {"id": rid})["ok"])
v = step()
st = {t["id"]: t["state"] for t in v["tasks"]}
c.check("approved: api + ui start in parallel, qa waits", st == {"api": "running", "ui": "running", "qa": "pending"})
v = step()
st = {t["id"]: t for t in v["tasks"]}
c.check("api done with its own commit kept on its branch",
        st["api"]["state"] == "done" and st["api"]["commits"] == 1 and st["api"]["branch"].startswith("aoc/orch-"))
c.check("ui failed once -> retried with the reason as feedback",
        st["ui"]["attempt"] == 2 and "could not" in (st["ui"]["feedback"] or "") and st["ui"]["state"] == "running")
c.check("worktree folders of finished attempts are gone",
        not os.path.exists(os.path.join(repo, ".claude", "worktrees", f"aoc-orch-{rid}-api-1")))
v = step()
v = step()
st = {t["id"]: t for t in v["tasks"]}
c.check("qa ran on a branch with both developers' work and passed", st["qa"]["state"] == "done"
        and "both files present" in st["qa"]["output"]["result"])
c.check("qa's prompt carried its inputs' results", any("wrote api.txt" in x[2] and "YOUR TASK (qa)" in x[2] for x in calls))
c.check("qa (no edits) keeps no branch", st["qa"]["branch"] == "" and st["qa"]["commits"] == 0)
v = step(2)
c.check("supervisor finished -> review with its report", v["run"]["state"] == "review" and "QA passed" in v["run"]["report"])
m = v["metrics"]
c.check("metrics of the run: costs split, 1 retry, success", m["cost_coordination"] == 0.35 and m["retries"] == 1
        and m["done"] == 3 and m["human_interventions"] == 2)
r = O["_orch_action"]("merge", {"id": rid})
c.check("merge: both developer branches into main", r["ok"] and set(r["merged"]) == {"api", "ui"})
c.check("main has both files, worktrees + branches cleaned up",
        os.path.exists(os.path.join(repo, "api.txt")) and os.path.exists(os.path.join(repo, "ui.txt"))
        and "aoc/orch" not in git("branch", cwd=repo).stdout and O["_orch_get"](rid)["run"]["state"] == "merged")

# second run: a conflict at merge time, a supervisor retry, cancel/discard
open(os.path.join(repo, "app.txt"), "w").write("v1\n")
calls.clear()
behaviour.clear()
r = O["_orch_create"]({"goal": "Change app.txt in two ways", "cwd": repo, "budget_usd": 10}, exe="claude.exe", spawn=fake_spawn)
rid = r["id"]
c.check("a second run in the same repo is refused while one is in progress",
        not O["_orch_create"]({"goal": "Another goal here", "cwd": repo}, exe="claude.exe", spawn=fake_spawn)["ok"])
step()
conn = O["_orch_connect"]()
O["_orch_edit_plan"](conn, rid, {"tasks": [
    {"id": "one", "role": "developer", "title": "One", "instructions": "x", "done_criteria": "y", "depends_on": [], "model": "haiku", "budget_usd": 1},
    {"id": "two", "role": "developer", "title": "Two", "instructions": "x", "done_criteria": "y", "depends_on": [], "model": "haiku", "budget_usd": 1}]}, time.time())
conn.commit(); conn.close()
behaviour.update({"one": [write("app.txt", "one\n")], "two": [write("app.txt", "two\n"), write("app.txt", "two again\n")],
                  "supervise": [{"action": "retry", "reason": "redo two", "report": "", "retry": [{"id": "two", "feedback": "be nicer"}], "tasks": []},
                                {"action": "finish", "reason": "ok", "report": "done", "retry": [], "tasks": []}]})
O["_orch_action"]("approve", {"id": rid})
v = step(4)
two = next(t for t in v["tasks"] if t["id"] == "two")
c.check("supervisor retry re-ran the task with its feedback", two["attempt"] == 2 and any("be nicer" in x[2] for x in calls))
v = step(3)
c.check("then finished", v["run"]["state"] == "review")
r = O["_orch_action"]("merge", {"id": rid})
c.check("merge stops at a conflict and names it; nothing half-merged",
        not r["ok"] and "conflict" in r["error"] and r["merged"] == ["one"]
        and git("status", "--porcelain", cwd=repo).stdout.strip() == "" and O["_orch_get"](rid)["run"]["state"] == "review")
r = O["_orch_action"]("discard", {"id": rid})
c.check("discard cleans the remaining branches", r["ok"] and "aoc/orch" not in git("branch", cwd=repo).stdout)
c.check("worktree list clean", git("worktree", "list", cwd=repo).stdout.count("\n") == 1)

shutil.rmtree(repo, ignore_errors=True)

# budgets (found live: a plan of $5 + $3 managers for a $4 run -- nothing would have started)
fit, scaled = O["_orch_fit_budgets"]([T("a", role="manager", budget=5.0), T("b", role="manager", budget=3.0), T("q", role="qa", budget=0.1)], 4.0)
c.check("plan over budget scaled to 85% of the run, never under a role's minimum",
        scaled and abs(sum(t["budget_usd"] for t in fit) - 3.4) < 0.15 and fit[2]["budget_usd"] == 0.1 and fit[1]["budget_usd"] >= 0.5)
c.check("plan within budget untouched", O["_orch_fit_budgets"]([T("a", budget=1.0)], 4.0) == ([T("a", budget=1.0)], False))
c.check("planner is told the budget", "the whole run has $4.00" in O["_orch_plan_prompt"]("g", "r", "b", "", "", "flat", 4.0)
        and "your team has $1.50" in O["_orch_plan_prompt"]("g", "r", "b", "", "", "team", 1.5))

# ── hierarchical: managers with their own teams ──
mgr = dict(plan[0], id="backend", role="manager", budget_usd=100)
c.check("manager refused in a flat plan", "hierarchical" in O["_orch_validate_tasks"]([mgr])[1])
v, err = O["_orch_validate_tasks"]([mgr], allow_manager=True)
c.check("manager allowed in a hierarchical plan, budget clamped to the team range", err is None and v[0]["budget_usd"] == 20.0)
c.check("schemas: manager only in the hierarchical copy, constants stay flat",
        "manager" in O["_orch_schema"](O["_ORCH_PLAN_SCHEMA"], True)["properties"]["tasks"]["items"]["properties"]["role"]["enum"]
        and "manager" not in O["_ORCH_PLAN_SCHEMA"]["properties"]["tasks"]["items"]["properties"]["role"]["enum"])
c.check("allows manager: hierarchical top only", O["_orch_allows_manager"]({"mode": "hierarchical", "parent_id": ""})
        and not O["_orch_allows_manager"]({"mode": "hierarchical", "parent_id": "x"}) and not O["_orch_allows_manager"]({"mode": "flat"}))
hp = O["_orch_plan_prompt"]("g", "r", "main", "", "", "hierarchical")
c.check("hierarchical planner prompt explains managers and when not to use them", "manager: owns a WORKSTREAM" in hp and "plan a flat team" in hp)
c.check("team planner prompt is a team lead's", "WORKSTREAM GOAL: g" in O["_orch_plan_prompt"]("g", "r", "b", "", "", "team"))
cg = O["_orch_child_goal"]({"goal": "Ship v2"}, T("frontend", role="manager", deps=["backend"], feedback="use the new API"),
                           [T("backend", role="manager", output={"summary": "API done", "result": "GET /items", "for_next": "call /items"},
                              commits=3, branch="aoc/b")])
c.check("team goal: workstream, overall goal, inputs, branch note, feedback",
        all(x in cg for x in ("do frontend", "larger goal: Ship v2", "GET /items", "call /items", "already on this workstream", "use the new API")))
co = O["_orch_child_output"]({"report": "Backend ready.\n\nDetails here."},
                             [T("api", state="done", output={"summary": "built api", "for_next": "use /items"}), T("x", state="failed", error="boom")])
c.check("team -> manager output in the agents' contract", co["status"] == "done" and co["summary"] == "Backend ready."
        and "api (developer): built api" in co["result"] and co["for_next"] == "use /items" and co["errors"] == ["x: boom"])
m = O["_orch_metrics"]({"created": 0, "finished": 10, "state": "review", "rounds": 1},
                       [T("backend", role="manager", state="done", cost_usd=2.0)], [{"kind": "plan_ready", "cost_usd": 0.5, "human": 0}],
                       team_coordination=0.4)
c.check("metrics: a team's planning/reviews count as coordination, not work",
        m["cost_coordination"] == 0.9 and m["cost_work"] == 1.6 and m["cost_total"] == 2.5 and m["teams"] == 1)

repo = tempfile.mkdtemp()
git("init", "-q", "-b", "main", cwd=repo)
git("config", "user.email", "t@t", cwd=repo); git("config", "user.name", "t", cwd=repo)
open(os.path.join(repo, "readme.txt"), "w").write("app\n")
git("add", "-A", cwd=repo); git("commit", "-qm", "init", cwd=repo)
H, hcalls = {}, []


def hspawn(argv, cwd, stdin_text, base):
    run_dir, kind = os.path.basename(os.path.dirname(base)), os.path.basename(base).rsplit("-", 1)[0]
    team = run_dir.split("-")[-2] if re.search(r"-t\d+$", run_dir) else "top"
    hcalls.append((team, kind, cwd, stdin_text, argv, os.path.exists(os.path.join(cwd, "api.txt"))))
    os.makedirs(os.path.dirname(base), exist_ok=True)
    step = H[f"{team}:{kind}"].pop(0)
    so = step(cwd) if callable(step) else step
    answer(base, so, cost=0.1 if kind in ("plan", "supervise") else 0.2)
    return 5


def plan_of(*tasks):
    return {"summary": "plan", "single_agent": False, "tasks": [dict(
        {"instructions": "x", "done_criteria": "y", "depends_on": [], "model": "sonnet", "budget_usd": 1}, **t) for t in tasks]}


def finish(report):
    return {"action": "finish", "reason": "ok", "report": report, "retry": [], "tasks": []}


def ui_needs_api(cwd):
    if not os.path.exists(os.path.join(cwd, "api.txt")):
        return {"status": "blocked", "summary": "no api", "result": "", "files_changed": [], "for_next": "", "errors": ["api.txt missing"]}
    return write("ui.txt", "ui\n")(cwd)


H.update({
    "top:plan": [plan_of({"id": "backend", "role": "manager", "title": "Backend", "budget_usd": 3},
                         {"id": "frontend", "role": "manager", "title": "Frontend", "depends_on": ["backend"], "budget_usd": 3},
                         {"id": "qa", "role": "qa", "title": "Check all", "depends_on": ["backend", "frontend"]})],
    "backend:plan": [plan_of({"id": "api", "role": "developer", "title": "API"})],
    "backend:api": [write("api.txt", "api\n")],
    "backend:supervise": [finish("Backend ready.\n\nAPI in api.txt")],
    "frontend:plan": [plan_of({"id": "ui", "role": "developer", "title": "UI"})],
    "frontend:ui": [ui_needs_api],
    "frontend:supervise": [finish("Frontend ready.")],
    "top:qa": [qa_check],
    "top:supervise": [finish("Both workstreams delivered, QA passed")],
})
hkw = dict(exe="claude.exe", spawn=hspawn, alive=lambda pid: False, kill=lambda pid: None)


def tick(n=1):
    for _ in range(n):
        conn = O["_orch_connect"]()
        for (r_,) in conn.execute("SELECT id FROM runs WHERE state IN ('planning','running','supervising') ORDER BY created").fetchall():
            O["_orch_step"](conn, r_, **hkw)
        conn.close()


r = O["_orch_create"]({"goal": "Build the backend and the frontend", "cwd": repo, "mode": "hierarchical", "budget_usd": 20},
                      exe="claude.exe", spawn=hspawn)
rid = r["id"]
pl_argv = hcalls[0][4]
c.check("hierarchical create: the planner's schema allows managers",
        "manager" in pl_argv[pl_argv.index("--json-schema") + 1] and "HIERARCHICAL run" in hcalls[0][3])
tick()
v = O["_orch_get"](rid)
c.check("top plan ready: 2 managers + qa, waves backend -> frontend -> qa",
        v["run"]["state"] == "plan_ready" and v["waves"] == [["backend"], ["frontend"], ["qa"]] and v["run"]["mode"] == "hierarchical")
O["_orch_action"]("approve", {"id": rid})
tick()
v = O["_orch_get"](rid)
be = next(t for t in v["tasks"] if t["id"] == "backend")
c.check("manager started: a team run on its own branch, planning", be["state"] == "running" and be["team"]["state"] == "planning"
        and be["branch"].startswith("aoc/orch-") and be["child_id"] == f"{rid}-backend-t1")
c.check("team planner is the manager (team prompt, cwd repo, no managers in its schema)",
        hcalls[-1][0] == "backend" and "WORKSTREAM GOAL" in hcalls[-1][3]
        and "manager" not in hcalls[-1][4][hcalls[-1][4].index("--json-schema") + 1])
tick(2)
child = O["_orch_get"](f"{rid}-backend-t1")
c.check("team plan auto-approved (the run's approval covers it), logged on both runs",
        child["run"]["state"] in ("running", "supervising", "review") and any(e["kind"] == "approved" and not e["human"] for e in child["events"])
        and any(e["kind"] == "team_approved" for e in O["_orch_get"](rid)["events"]))
c.check("team run knows its parent", child["parent"]["id"] == rid and child["parent"]["task"] == "backend" and child["run"]["base"] == be["branch"])
c.check("a team's run can't be merged or discarded on its own",
        "run above" in O["_orch_action"]("merge", {"id": f"{rid}-backend-t1"})["error"])
tick(16)
v = O["_orch_get"](rid)
st = {t["id"]: t for t in v["tasks"]}
c.check("backend done: team integrated onto the workstream branch, report as its result",
        st["backend"]["state"] == "done" and st["backend"]["commits"] >= 1 and "Backend ready." in st["backend"]["output"]["summary"]
        and O["_orch_get"](f"{rid}-backend-t1")["run"]["state"] == "merged")
c.check("frontend's team built on the backend's work (api.txt on its branch)", st["frontend"]["state"] == "done")
c.check("team branches cleaned once integrated", not any("backend-t1-api" in b for b in git("branch", cwd=repo).stdout.split()))
c.check("direct qa after the managers saw both workstreams", st["qa"]["state"] == "done")
fp = next(x for x in hcalls if x[0] == "frontend" and x[1] == "plan")
c.check("the frontend manager planned in its workstream's worktree, with the backend's work in it",
        fp[5] and "aoc-orch-" in fp[2] and not os.path.exists(fp[2]))
tick(3)
v = O["_orch_get"](rid)
c.check("top review: report, team costs rolled up", v["run"]["state"] == "review" and "QA passed" in v["run"]["report"]
        and st["backend"]["cost_usd"] > 0.25 and v["metrics"]["teams"] == 2 and v["metrics"]["cost_coordination"] > 0.5)
lst = O["_orch_list"]()["runs"]
c.check("runs list: top-level only, needs you at review", [x["id"] for x in lst if x["id"].startswith(rid)] == [rid]
        and next(x for x in lst if x["id"] == rid)["needs_you"])
r = O["_orch_action"]("merge", {"id": rid})
c.check("merge: the workstream branches into main", r["ok"] and set(r["merged"]) == {"backend", "frontend"}
        and os.path.exists(os.path.join(repo, "api.txt")) and os.path.exists(os.path.join(repo, "ui.txt"))
        and "aoc/orch" not in git("branch", cwd=repo).stdout and git("worktree", "list", cwd=repo).stdout.count("\n") == 1)

# approve each team's plan; cancelling the run cancels its teams
H.update({"top:plan": [plan_of({"id": "docs", "role": "manager", "title": "Docs"})],
          "docs:plan": [plan_of({"id": "write", "role": "developer", "title": "Write"})]})
r = O["_orch_create"]({"goal": "Write the docs for it all", "cwd": repo, "mode": "hierarchical", "approve_teams": True},
                      exe="claude.exe", spawn=hspawn)
rid = r["id"]
tick(); O["_orch_action"]("approve", {"id": rid}); tick(4)
v = O["_orch_get"](rid)
docs = v["tasks"][0]
c.check("approve_teams: the team plan waits for you", docs["state"] == "running" and docs["team"]["state"] == "plan_ready"
        and next(x for x in O["_orch_list"]()["runs"] if x["id"] == rid)["needs_you"])
r = O["_orch_action"]("cancel", {"id": rid})
c.check("cancel the run -> its team is cancelled, branches gone",
        r["ok"] and O["_orch_get"](docs["child_id"])["run"]["state"] == "cancelled" and "aoc/orch" not in git("branch", cwd=repo).stdout)
shutil.rmtree(repo, ignore_errors=True)

# ── agent library: your own agents, MCP servers, skills, a run's team ──
good = {"name": "Shop Analyst", "brief": "You analyse the e-shop's sales and products and report what changed.",
        "tools": ["Read", "WebSearch", "Nope"], "mcp": ["claude.ai Shopify", "bad;name"], "skills": ["xlsx"],
        "model": "haiku", "budget_usd": 99, "sensitive": True}
a, err = O["_orch_validate_agent"](good)
c.check("agent: id from the name, unknown tools/MCP names dropped, budget clamped, sensitive kept",
        err is None and a["id"] == "shop-analyst" and a["tools"] == ["Read", "WebSearch"] and a["mcp"] == ["claude.ai Shopify"]
        and a["skills"] == ["xlsx"] and a["budget_usd"] == 5.0 and a["sensitive"] is True and a["model"] == "haiku")
c.check("agent: a built-in id refused", "built-in" in O["_orch_validate_agent"](dict(good, id="developer"))[1])
c.check("agent: a role description is required", "describe" in O["_orch_validate_agent"](dict(good, brief="short"))[1])
c.check("agent: needs a tool or an MCP server", "at least one" in O["_orch_validate_agent"](dict(good, tools=[], mcp=[]))[1])
c.check("agent edits files only with Edit/Write", O["_orch_agent_edits"]({"tools": ["Read", "Write"]})
        and not O["_orch_agent_edits"]({"tools": ["Read", "Bash"]}))
c.check("MCP server -> permission rule for all its tools",
        O["_orch_mcp_prefix"]("claude.ai Google Drive") == "mcp__claude_ai_Google_Drive"
        and O["_orch_mcp_prefix"]("plugin:design:slack") == "mcp__plugin_design_slack")
listing = "\n".join([
    "⚠ claude.ai connectors are disabled because ANTHROPIC_API_KEY ...", "Checking MCP server health…", "",
    "[mcp-sdk] SEP-2352: stored OAuth credential has no 'issuer' stamp",
    "claude.ai Shopify: https://setup.shopify.com/mcp - √ Connected",
    "claude.ai Google Drive: https://drivemcp.googleapis.com/mcp/v1 - √ Connected",
    "plugin:design:slack: https://mcp.slack.com/mcp (HTTP) - ! Needs authentication",
    "plugin:design:gmail:  (HTTP) - - Not configured"])
ml = O["_orch_parse_mcp_list"](listing)
c.check("`claude mcp list` parsed: servers + connected, noise skipped",
        [(x["name"], x["connected"]) for x in ml] == [("claude.ai Shopify", True), ("claude.ai Google Drive", True),
                                                       ("plugin:design:slack", False), ("plugin:design:gmail", False)])
c.check("SKILL.md front matter", O["_orch_parse_skill"]("---\nname: xlsx\ndescription: \"Spreadsheets\"\n---\nbody") == ("xlsx", "Spreadsheets")
        and O["_orch_parse_skill"]("no front matter") == ("", ""))
sk = tempfile.mkdtemp()
for rel, nm in (("skills/a/SKILL.md", "alpha"), ("plugins/p/x/skills/b/SKILL.md", "beta"), ("skills/.trash/c/SKILL.md", "gone")):
    os.makedirs(os.path.dirname(os.path.join(sk, rel)), exist_ok=True)
    open(os.path.join(sk, rel), "w").write(f"---\nname: {nm}\ndescription: d\n---\n")
c.check("skills found in ~/.claude/skills and plugins, hidden folders skipped",
        [x["name"] for x in O["_orch_scan_skills"](sk)] == ["alpha", "beta"])
shutil.rmtree(sk, ignore_errors=True)
env = O["_orch_subscription_env"]()
c.check("connector agents run without the API key", "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_AUTH_TOKEN" not in env)
av = O["_orch_agent_argv"]("claude.exe", T("x", role="shop-analyst", budget=0.5), a)
c.check("custom agent argv: its tools (+Skill), its MCP servers allowed, brief with skills + services",
        av[av.index("--tools") + 1] == "Read,WebSearch,Skill"
        and av[av.index("--allowedTools") + 1] == "Read,WebSearch,Skill,mcp__claude_ai_Shopify"
        and "e-shop" in av[av.index("--append-system-prompt") + 1] and "xlsx" in av[av.index("--append-system-prompt") + 1]
        and "claude.ai Shopify" in av[av.index("--append-system-prompt") + 1])
c.check("a task without a copy of its agent runs as the built-in", O["_orch_task_agent"](T("q", role="qa"))["name"] == "QA")
txt = O["_orch_agents_text"]([O["_orch_builtin_agents"]()["developer"], a])
c.check("planner reads the team: ids, what each does, connectors, sensitive",
        "- developer (Developer" in txt and "edits files" in txt and "- shop-analyst (Shop Analyst" in txt
        and "connected services: claude.ai Shopify" in txt and "waits for the human's OK" in txt)
c.check("schema: the team's ids as roles", O["_orch_schema"](O["_ORCH_PLAN_SCHEMA"], False, ["shop-analyst", "qa"])
        ["properties"]["tasks"]["items"]["properties"]["role"]["enum"] == ["shop-analyst", "qa"])
c.check("validation: only the team's agents", O["_orch_validate_tasks"]([dict(plan[0], role="shop-analyst")], roles=["shop-analyst"])[1] is None
        and "not an agent" in O["_orch_validate_tasks"]([dict(plan[0], role="developer")], roles=["shop-analyst"])[1])

r = O["_orch_agent_save"](good)
c.check("save an agent", r["ok"] and any(x["id"] == "shop-analyst" and not x["builtin"] for x in O["_orch_agents_list"]()["agents"]))
c.check("built-ins listed first", [x["id"] for x in O["_orch_agents_list"]()["agents"]][:4] == ["research", "analyst", "developer", "qa"])
r = O["_orch_agent_save"](dict(good, id="shop-analyst-2", old_id="shop-analyst", name="Shop Analyst 2"))
ids = [x["id"] for x in O["_orch_agents_list"]()["agents"]]
c.check("rename an agent", r["ok"] and "shop-analyst-2" in ids and "shop-analyst" not in ids)
c.check("built-in can't be deleted", not O["_orch_agent_delete"]({"id": "qa"})["ok"])
O["_orch_agent_save"](dict(good, name="Writer", id="writer", tools=["Read", "Write"], mcp=[], skills=[], sensitive=False))

# a run with a custom team: the planner sees it, tasks keep a copy of their agent, the MCP agent runs on the subscription
repo = tempfile.mkdtemp()
git("init", "-q", "-b", "main", cwd=repo)
git("config", "user.email", "t@t", cwd=repo); git("config", "user.name", "t", cwd=repo)
open(os.path.join(repo, "a.txt"), "w").write("a\n")
git("add", "-A", cwd=repo); git("commit", "-qm", "init", cwd=repo)
tcalls = []


def tspawn(argv, cwd, stdin_text, base, env=None):
    os.makedirs(os.path.dirname(base), exist_ok=True)
    kind = os.path.basename(base).rsplit("-", 1)[0]
    tcalls.append((kind, argv, stdin_text, env))
    if kind == "plan":
        answer(base, plan_of({"id": "look", "role": "shop-analyst-2", "title": "Look at sales", "model": "haiku"},
                             {"id": "report", "role": "writer", "title": "Write report", "depends_on": ["look"]}))
    elif kind == "supervise":
        answer(base, finish("done"))
    elif kind == "report":
        open(os.path.join(cwd, "report.md"), "w").write("# report\n")
        answer(base, done(result="wrote report.md"))
    else:
        answer(base, done(result="sales fell 10%"))
    return 7


servers = [{"name": "claude.ai Shopify", "connected": True}, {"name": "claude.ai Vercel", "connected": True},
           {"name": "plugin:design:slack", "connected": False}]
c.check("deny every other MCP server (the connectors would not fit the context)",
        O["_orch_mcp_deny"](a, servers) == ["mcp__claude_ai_Vercel", "mcp__plugin_design_slack"]
        and O["_orch_mcp_deny"]({"mcp": []}, servers) == ["mcp__claude_ai_Shopify", "mcp__claude_ai_Vercel", "mcp__plugin_design_slack"])
dv = O["_orch_agent_argv"]("c", T("x", role="shop"), a, ["mcp__claude_ai_Vercel"])
c.check("argv carries the deny list", dv[dv.index("--disallowedTools") + 1] == "mcp__claude_ai_Vercel"
        and "--disallowedTools" not in O["_orch_agent_argv"]("c", T("x"), None))
cat_cache = g["_orch_catalog_cache"]
cat_cache.update(mcp=[], loaded=True, scanning=True)
r = O["_orch_create"]({"goal": "Report on last week's sales", "cwd": repo, "team": ["shop-analyst-2", "writer", "gone-agent"]},
                      exe="claude.exe", spawn=tspawn)
rid = r["id"]
pa = tcalls[0][1]
c.check("create with a team: unknown ids dropped, planner prompt + schema limited to the team",
        O["_orch_get"](rid)["run"]["team"] == ["shop-analyst-2", "writer"] and "- writer (Writer" in tcalls[0][2]
        and "- developer (" not in tcalls[0][2] and '"enum": ["shop-analyst-2", "writer"]' in pa[pa.index("--json-schema") + 1])
kw2 = dict(exe="claude.exe", spawn=tspawn, alive=lambda pid: False, kill=lambda pid: None)
conn = O["_orch_connect"](); O["_orch_step"](conn, rid, **kw2); conn.close()
v = O["_orch_get"](rid)
c.check("planned tasks keep a copy of their agent", v["tasks"][0]["agent"]["name"] == "Shop Analyst 2" and v["tasks"][1]["agent"]["id"] == "writer")
O["_orch_agent_save"](dict(good, id="shop-analyst-2", name="Changed later"))
O["_orch_action"]("approve", {"id": rid})
conn = O["_orch_connect"](); O["_orch_step"](conn, rid, **kw2); conn.close()
c.check("an MCP agent waits while the MCP server list is unknown", not any(x[0] == "look" for x in tcalls)
        and O["_orch_get"](rid)["tasks"][0]["state"] in ("pending", "gate"))
cat_cache.update(mcp=servers, scanning=False)
for _ in range(2):
    conn = O["_orch_connect"](); O["_orch_step"](conn, rid, **kw2); conn.close()
gv = O["_orch_get"](rid)
c.check("a sensitive agent's task stops at a gate, nothing runs, the run isn't reviewed",
        gv["tasks"][0]["state"] == "gate" and not any(x[0] == "look" for x in tcalls) and gv["run"]["state"] == "running"
        and any(e["kind"] == "task_gate" for e in gv["events"]))
c.check("only a waiting task can be allowed", not O["_orch_action"]("gate", {"id": rid, "task": "report", "allow": True})["ok"])
c.check("you allow it", O["_orch_action"]("gate", {"id": rid, "task": "look", "allow": True})["ok"])
for _ in range(6):
    conn = O["_orch_connect"](); O["_orch_step"](conn, rid, **kw2); conn.close()
look = next(x for x in tcalls if x[0] == "look")
c.check("allowed -> it ran; the OK is logged as yours",
        any(e["kind"] == "task_allowed" and e["human"] for e in O["_orch_get"](rid)["events"]))
c.check("the MCP agent ran on the subscription (no API key), with its connector allowed, as planned (not as edited later)",
        look[3] is not None and "ANTHROPIC_API_KEY" not in look[3]
        and "mcp__claude_ai_Shopify" in look[1][look[1].index("--allowedTools") + 1]
        and look[1][look[1].index("--disallowedTools") + 1] == "mcp__claude_ai_Vercel,mcp__plugin_design_slack"
        and "Changed later" not in " ".join(look[1]))
rep = next(x for x in tcalls if x[0] == "report")
c.check("an agent without MCP keeps the normal environment", rep[3] is None)
v = O["_orch_get"](rid)
st = {t["id"]: t for t in v["tasks"]}
c.check("the writer (Write tool) got its file committed; the analyst kept no branch",
        st["report"]["commits"] == 1 and st["look"]["branch"] == "" and v["run"]["state"] == "review")
O["_orch_action"]("discard", {"id": rid})
shutil.rmtree(repo, ignore_errors=True)

# ── workspaces: runs that aren't about code ──
wroot = os.path.realpath(tempfile.mkdtemp())   # CI temp is an 8.3 short path; git reports the long one
g["ORCH_WORKSPACES_DIR"] = wroot
path, err = O["_orch_workspace"]("Eshop Ops!")
c.check("workspace: slugged folder, a git repo with a first commit on main",
        err is None and path == os.path.join(wroot, "eshop-ops") and git("log", "--oneline", cwd=path).stdout.count("\n") == 1
        and git("symbolic-ref", "--short", "HEAD", cwd=path).stdout.strip() == "main")
c.check("workspace: opening it again keeps it", O["_orch_workspace"]("eshop-ops") == (path, None)
        and git("log", "--oneline", cwd=path).stdout.count("\n") == 1)
c.check("workspace: a name is needed", O["_orch_workspace"]("!!!")[1].startswith("name the workspace"))
c.check("workspaces listed", [w["name"] for w in O["_orch_workspaces"]()["workspaces"]] == ["eshop-ops"])
wp = O["_orch_plan_prompt"]("g", path, "main", "README.md", "", area="workspace")
c.check("planner told it's a workspace, not code", "WORKSPACE: " in wp and "not a code repository" in wp
        and "FILES IN THE WORKSPACE:" in wp and "REPO:" not in wp)
wcalls = []


def wspawn(argv, cwd, stdin_text, base, env=None):
    os.makedirs(os.path.dirname(base), exist_ok=True)
    kind = os.path.basename(base).rsplit("-", 1)[0]
    wcalls.append((kind, cwd, stdin_text))
    if kind == "plan":
        answer(base, plan_of({"id": "write-report", "role": "developer", "title": "Weekly report"}))
    elif kind == "supervise":
        answer(base, finish("report written"))
    else:
        os.makedirs(os.path.join(cwd, "reports"), exist_ok=True)
        open(os.path.join(cwd, "reports", "week-45.md"), "w").write("# week 45\n")
        answer(base, done(result="wrote reports/week-45.md"))
    return 9


r = O["_orch_create"]({"goal": "Write this week's store report", "workspace": "eshop-ops"}, exe="claude.exe", spawn=wspawn)
rid = r["id"]
v = O["_orch_get"](rid)
c.check("create in a workspace: kind, the workspace as its folder, planner prompt says so",
        r["ok"] and v["run"]["kind"] == "workspace" and v["run"]["repo"] == path and "WORKSPACE: " in wcalls[0][2])
kw3 = dict(exe="claude.exe", spawn=wspawn, alive=lambda pid: False, kill=lambda pid: None)
conn = O["_orch_connect"](); O["_orch_step"](conn, rid, **kw3); conn.close()
O["_orch_action"]("approve", {"id": rid})
for _ in range(5):
    conn = O["_orch_connect"](); O["_orch_step"](conn, rid, **kw3); conn.close()
v = O["_orch_get"](rid)
c.check("the agent worked in its own copy of the workspace", any(x[0] == "write-report" and x[1] != path for x in wcalls)
        and v["run"]["state"] == "review" and v["tasks"][0]["commits"] == 1)
r = O["_orch_action"]("merge", {"id": rid})
c.check("accept: the report lands in the workspace, versioned; nothing left behind",
        r["ok"] and os.path.exists(os.path.join(path, "reports", "week-45.md"))
        and "aoc/orch" not in git("branch", cwd=path).stdout and git("worktree", "list", cwd=path).stdout.count("\n") == 1)
c.check("a workspace run doesn't need a repo folder", not O["_orch_create"]({"goal": "Something to do here", "cwd": ""},
                                                                              exe="claude.exe", spawn=wspawn)["ok"])
# ── entities: an autonomous team with a mission ──
base_ent = {"name": "Store Watch", "mission": "Keep a weekly, accurate picture of the store's health and report it.",
            "where": "workspace", "workspace": "store-watch", "team": ["research", "developer"], "model": "haiku",
            "budget_run": 2, "budget_day": 5, "budget_month": 50, "schedule": {"type": "every", "hours": 24},
            "loop": True, "cooldown_min": 30}
ec, err = O["_orch_validate_entity"](base_ent)
c.check("entity: id from the name, schedule and budgets kept", err is None and ec["id"] == "store-watch"
        and ec["schedule"] == {"type": "every", "hours": 24.0} and ec["budget_run"] == 2 and ec["loop"] is True)
c.check("entity: budgets must grow", "grow" in O["_orch_validate_entity"](dict(base_ent, budget_run=10))[1])
c.check("entity: daily needs HH:MM", "HH:MM" in O["_orch_validate_entity"](dict(base_ent, schedule={"type": "daily", "at": "25:00"}))[1])
c.check("entity: a repo entity needs its folder", "repository" in O["_orch_validate_entity"](dict(base_ent, where="repo", cwd=""))[1])
c.check("entity: a mission is needed", "mission" in O["_orch_validate_entity"](dict(base_ent, mission="x"))[1])
due = O["_orch_entity_due"]
man = dict(ec, schedule={"type": "manual"}, loop=False)
c.check("due: manual -> only when you ask", due(man, {}, None, 1000)[0] is False and due(man, {"run_now": 1}, None, 1000)[:2] == (True, "you asked"))
c.check("due: loop -> first run at once, then after the cooldown once the last run ended",
        due(dict(man, loop=True), {}, None, 1000)[0] and not due(dict(man, loop=True), {"last_scout": 900}, 950, 1000)[0]
        and due(dict(man, loop=True), {"last_scout": 900}, 950, 950 + 30 * 60)[0]
        and not due(dict(man, loop=True), {"last_scout": 2000}, 950, 5000)[0])
c.check("due: loop's next time is shown", due(dict(man, loop=True), {"last_scout": 900}, 950, 1000)[2] == 950 + 1800)
ev_ = dict(man, schedule={"type": "every", "hours": 2})
c.check("due: every N hours since the last scout", due(ev_, {"last_scout": 1000}, None, 1000 + 7199)[0] is False
        and due(ev_, {"last_scout": 1000}, None, 1000 + 7200)[0] and due(ev_, {}, None, 5)[0])
lt = time.localtime(time.time())
slot = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 9, 30, 0, 0, 0, -1))
dl = dict(man, schedule={"type": "daily", "at": "09:30"})
c.check("due: daily at HH:MM, once per day", due(dl, {"last_scout": slot - 3600}, None, slot + 60)[0]
        and not due(dl, {"last_scout": slot + 30}, None, slot + 60)[0] and not due(dl, {}, None, slot - 60)[0]
        and due(dl, {}, None, slot - 60)[2] == slot)
day0 = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
c.check("spend: today and this month", O["_orch_entity_spend"]([(day0 + 10, 1.5), (day0 - 10, 2.0)], day0 + 100)[0] == 1.5)
c.check("limits: wait at 90 %", "5h plan limit is at 93" in O["_orch_limits_block"]({"windows": [{"label": "5h", "pct": 93.0}]})
        and O["_orch_limits_block"]({"windows": [{"label": "5h", "pct": 40.0}]}) == "" and O["_orch_limits_block"](None) == "")
me = O["_orch_memory_entry"]({"id": "r1", "goal": "Weekly report", "state": "discarded", "report": "Wrote it.\n\nDetails",
                              "created": 1, "updated": 9, "finished": 9, "rounds": 1}, [T("a", cost_usd=0.4)], [])
c.check("memory: outcome in your words, cost, first paragraph", me["outcome"] == "dropped by you" and me["cost"] == 0.4 and me["summary"] == "Wrote it.")
sp = O["_orch_scout_prompt"](ec, [me], [{"goal": "Now running", "state": "running"}], "README.md", "", (1.0, 3.0), time.time())
c.check("scout reads the mission, money left, memory (dropped = don't repeat), what's in progress",
        "MISSION:" in sp and "today $4.00" in sp and "Weekly report -> dropped by you" in sp and "Now running [running]" in sp
        and "idle" in sp and "THE TEAM" not in sp)
sp_t = O["_orch_scout_prompt"](ec, [], [], "", "", (0, 0), time.time(), [O["_orch_builtin_agents"]()["research"]])
c.check("scout sees its team and is told to judge by what the team can find out", "THE TEAM" in sp_t and "- research (Research" in sp_t
        and "not by what you know" in sp_t)

# the whole cycle: made paused -> run now -> scout -> run (approved by the entity) -> your OK -> memory -> loop -> idle
wroot2 = os.path.realpath(tempfile.mkdtemp())
g["ORCH_WORKSPACES_DIR"] = wroot2
ecalls, scout_answers = [], []


def espawn(argv, cwd, stdin_text, base, env=None):
    os.makedirs(os.path.dirname(base), exist_ok=True)
    kind = os.path.basename(base).rsplit("-", 1)[0]
    ecalls.append((kind, cwd, stdin_text, argv))
    if kind == "scout":
        answer(base, scout_answers.pop(0), cost=0.05)
    elif kind == "plan":
        answer(base, plan_of({"id": "report", "role": "developer", "title": "Store report"}))
    elif kind == "supervise":
        answer(base, finish("report ready"))
    else:
        open(os.path.join(cwd, "health.md"), "w").write("ok\n")
        answer(base, done(result="wrote health.md"), cost=0.3)
    return 11


r = O["_orch_entity_save"](base_ent)
c.check("entity saved, made paused", r["ok"] and O["_orch_entities_list"]()["entities"][0]["paused"])
ekw = dict(exe="claude.exe", spawn=espawn, alive=lambda pid: False, kill=lambda pid: None, limits={})


def etick(now, n=1):
    for _ in range(n):
        conn = O["_orch_connect"]()
        O["_orch_entity_tick"](conn, "store-watch", now, **ekw)
        for (rid_,) in conn.execute("SELECT id FROM runs WHERE state IN ('planning','running','supervising')").fetchall():
            O["_orch_step"](conn, rid_, now, **{k: v for k, v in ekw.items() if k != "limits"})
        conn.commit(); conn.close()


T0 = time.time()
etick(T0)
c.check("paused: nothing happens", not ecalls and O["_orch_entities_list"]()["entities"][0]["state"] == "paused")
c.check("run now", O["_orch_entity_action"]("run", {"id": "store-watch"})["ok"])
scout_answers.append({"action": "run", "goal": "Write this week's store health report to health.md", "reason": "weekly", "mode": "flat"})
etick(T0 + 1)
sc = ecalls[0]
c.check("scout started in its workspace with the mission, read-only, no hooks", sc[0] == "scout" and sc[1] == os.path.join(wroot2, "store-watch")
        and "MISSION:" in sc[2] and sc[3][sc[3].index("--tools") + 1] == "Read,Grep,Glob" and "disableAllHooks" in " ".join(sc[3]))
etick(T0 + 2)
e = O["_orch_entities_list"]()["entities"][0]
rid = e["runs"][0]["id"] if e["runs"] else ""
c.check("the scout's goal became a run of the entity (its team, budget, model)", bool(rid)
        and O["_orch_get"](rid)["run"]["entity_id"] == "store-watch" and O["_orch_get"](rid)["run"]["budget_usd"] == 2
        and O["_orch_get"](rid)["run"]["team"] == ["research", "developer"] and e["state"] == "running")
etick(T0 + 3, 2)
ev_run = O["_orch_get"](rid)
c.check("the entity approved its plan itself (not a human approval)",
        any(x["kind"] == "approved" and not x["human"] and "approved by entity" in x["message"] for x in ev_run["events"]))
etick(T0 + 4, 5)
e = O["_orch_entities_list"]()["entities"][0]
c.check("results wait for you: entity waiting_you, no new scout", O["_orch_get"](rid)["run"]["state"] == "review"
        and e["state"] == "waiting_you" and sum(1 for x in ecalls if x[0] == "scout") == 1)
c.check("you accept", O["_orch_action"]("merge", {"id": rid}, now=T0 + 5)["ok"])
etick(T0 + 5)
e = O["_orch_entities_list"]()["entities"][0]
c.check("memory: the run, accepted by you", e["memory"][0]["run_id"] == rid and e["memory"][0]["outcome"] == "accepted by you")
c.check("spend counted (run + scout)", e["spend_day"] >= 0.35)
c.check("paused entity: run now still works, but nothing by itself", e["paused"])
O["_orch_entity_action"]("pause", {"id": "store-watch", "paused": False})
etick(T0 + 6)
c.check("loop: waits out the cooldown", sum(1 for x in ecalls if x[0] == "scout") == 1
        and O["_orch_entities_list"]()["entities"][0]["state"] == "idle")
scout_answers.append({"action": "idle", "goal": "", "reason": "the report is fresh", "mode": "flat"})
etick(T0 + 6 + 31 * 60, 2)
e = O["_orch_entities_list"]()["entities"][0]
c.check("loop: after the cooldown the scout looks again; it says idle -> no run",
        sum(1 for x in ecalls if x[0] == "scout") == 2 and len(e["runs"]) == 1 and "the report is fresh" in e["note"])
sp2 = [x for x in ecalls if x[0] == "scout"][1][2]
c.check("the second scout read the memory", "Write this week's store health report" in sp2 and "accepted by you" in sp2)
etick(T0 + 6 + 62 * 60)
c.check("idle said -> no new scout until the schedule (24 h)", sum(1 for x in ecalls if x[0] == "scout") == 2)
O["_orch_entity_save"](dict(base_ent, budget_day=2.2, budget_run=2))
O["_orch_entity_action"]("run", {"id": "store-watch"})
etick(T0 + 6 + 63 * 60)
e = O["_orch_entities_list"]()["entities"][0]
c.check("budget: today's money would run out -> it waits, says why", e["state"] == "waiting" and "today's budget" in e["note"]
        and sum(1 for x in ecalls if x[0] == "scout") == 2)
O["_orch_entity_save"](dict(base_ent))
O["_orch_entity_action"]("run", {"id": "store-watch"})
conn = O["_orch_connect"](); O["_orch_entity_tick"](conn, "store-watch", T0 + 6 + 64 * 60, **dict(ekw, limits={"windows": [{"label": "weekly", "pct": 95}]})); conn.commit(); conn.close()
c.check("plan limits nearly used up -> it waits", "weekly plan limit is at 95" in O["_orch_entities_list"]()["entities"][0]["note"])
c.check("forget clears its memory", O["_orch_entity_action"]("forget", {"id": "store-watch"})["ok"]
        and O["_orch_entities_list"]()["entities"][0]["memory"] == [])
c.check("delete", O["_orch_entity_action"]("delete", {"id": "store-watch"})["ok"] and O["_orch_entities_list"]()["entities"] == [])
# ── billing: API key or the Claude subscription ──
pe = O["_orch_proc_env"]
cat_cache.update(mcp=servers, scanning=False, loaded=True)
c.check("api billing, no MCP: the normal environment", pe(None, "api") == ({}, [], True))
kw_, deny_, ready_ = pe(None, "subscription")
c.check("subscription: no API key, every MCP server denied (none needed)", ready_ and "ANTHROPIC_API_KEY" not in kw_["env"]
        and deny_ == ["mcp__claude_ai_Shopify", "mcp__claude_ai_Vercel", "mcp__plugin_design_slack"])
c.check("subscription + an MCP agent: only its servers kept", pe({"mcp": ["claude.ai Vercel"]}, "subscription")[1]
        == ["mcp__claude_ai_Shopify", "mcp__plugin_design_slack"])
cat_cache.update(mcp=[], scanning=True)
c.check("subscription without the server list: not ready", pe(None, "subscription")[2] is False and pe(None, "api")[2] is True)
cat_cache.update(mcp=servers, scanning=False)
c.check("planner/supervisor argv carry the deny list", O["_orch_llm_argv"]("c", {}, "opus", 1.0, True, ["mcp__x"])[-1] == "mcp__x")
c.check("an empty credit balance explained", "switch Orchestrate's billing to your Claude subscription"
        in O["_orch_explain_error"]("Credit balance is too low") and O["_orch_explain_error"]("boom") == "boom")
setf = tempfile.mkdtemp()
g["ORCH_SETTINGS_FILE"] = os.path.join(setf, "s.json")
g["_load_json_file"] = lambda path, default=None: (json.load(open(path)) if os.path.exists(path) else default)
c.check("settings: api by default, saved, refused when unknown", O["_orch_settings"]()["billing"] == "api"
        and O["_orch_settings_save"]({"billing": "subscription"})["billing"] == "subscription"
        and O["_orch_settings"]()["billing"] == "subscription" and not O["_orch_settings_save"]({"billing": "free"})["ok"])
O["_orch_settings_save"]({"billing": "api"})
shutil.rmtree(setf, ignore_errors=True)
shutil.rmtree(wroot, ignore_errors=True)
shutil.rmtree(wroot2, ignore_errors=True)

# an agent that says it wrote files that aren't in its folder (found live: Git Bash's /tmp path) is retried
prepo = os.path.realpath(tempfile.mkdtemp())
git("init", "-q", "-b", "main", cwd=prepo); git("config", "user.email", "t@t", cwd=prepo); git("config", "user.name", "t", cwd=prepo)
open(os.path.join(prepo, "a.txt"), "w").write("a\n"); git("add", "-A", cwd=prepo); git("commit", "-qm", "init", cwd=prepo)
pc = []


def pspawn(argv, cwd, stdin_text, base, env=None):
    os.makedirs(os.path.dirname(base), exist_ok=True)
    kind = os.path.basename(base).rsplit("-", 1)[0]
    pc.append((kind, stdin_text))
    if kind == "plan":
        answer(base, plan_of({"id": "note", "role": "developer", "title": "Note"}))
    elif kind == "supervise":
        answer(base, finish("ok"))
    elif sum(1 for x in pc if x[0] == "note") == 1:
        answer(base, dict(done(result="wrote notes/x.md"), files_changed=["notes/x.md"]))   # wrote it elsewhere
    else:
        os.makedirs(os.path.join(cwd, "notes"), exist_ok=True)
        open(os.path.join(cwd, "notes", "x.md"), "w").write("x\n")
        answer(base, dict(done(result="wrote notes/x.md"), files_changed=["notes/x.md"]))
    return 13


rp = O["_orch_create"]({"goal": "Write a short note please", "cwd": prepo}, exe="claude.exe", spawn=pspawn)["id"]
pkw = dict(exe="claude.exe", spawn=pspawn, alive=lambda pid: False, kill=lambda pid: None)
conn = O["_orch_connect"](); O["_orch_step"](conn, rp, **pkw); conn.close()
O["_orch_action"]("approve", {"id": rp})
for _ in range(4):
    conn = O["_orch_connect"](); O["_orch_step"](conn, rp, **pkw); conn.close()
nt = O["_orch_get"](rp)["tasks"][0]
c.check("reported files missing from its folder -> retried with that feedback, then done with the commit",
        nt["attempt"] == 2 and nt["state"] == "done" and nt["commits"] == 1 and "none of them is in your folder" in nt["feedback"]
        and "never one printed by `pwd`" in [x for x in pc if x[0] == "note"][1][1])
O["_orch_action"]("discard", {"id": rp})
shutil.rmtree(prepo, ignore_errors=True)

# ── what waits for you: Inbox items + one notification each ──
aconn = sqlite3.connect(":memory:")
aconn.row_factory = sqlite3.Row
g["ORCH_DB"] = ":memory:"
aconn = O["_orch_connect"](":memory:")
ins = lambda rid, state, **kw: aconn.execute(
    "INSERT INTO runs (id, created, updated, goal, repo, state, parent_id, parent_task, entity_id, approve_teams) VALUES (?,?,?,?,?,?,?,?,?,?)",
    (rid, 1, kw.get("updated", 1), "Goal " + rid, "r", state, kw.get("parent", ""), kw.get("ptask", ""), kw.get("entity", ""), kw.get("teams", 0)))
ins("p1", "plan_ready", updated=5); ins("rv", "review", updated=3); ins("en", "plan_ready", entity="store")
ins("ch", "plan_ready", parent="p9", ptask="docs", teams=1, updated=7); ins("ch2", "plan_ready", parent="p9", teams=0)
ins("chr", "review", parent="p9"); ins("g1", "running", updated=9)
aconn.execute("INSERT INTO tasks (run_id, id, seq, role, title, state, agent) VALUES ('g1','post',0,'poster','Post to the shop','gate',?)",
              (json.dumps({"name": "Shop Poster"}),))
items = O["_orch_attention_items"](aconn)
c.check("attention: your plan, results to review, a gate, a team plan you approve yourself -- oldest first",
        [i["key"] for i in items] == ["review:rv", "plan:p1", "team_plan:ch", "gate:g1:post"])
c.check("attention: not an entity's plan (it approves its own), not an auto-approved team plan, not a team's review",
        not any(i["run_id"] in ("en", "ch2", "chr") for i in items))
c.check("attention: the gate names the agent", items[-1]["detail"] == "Shop Poster" and items[-1]["task_id"] == "post")
sent = []
nfile = tempfile.mkdtemp()
g["ORCH_NOTIFIED_FILE"] = os.path.join(nfile, "n.json")
g["_orch_attention_state"].update(items=[], notified=None)
g["_status_cond"] = threading.Condition(); g["_status_version"] = [0]
O["_orch_attention_refresh"](aconn, notify=lambda t, m, kind=None: sent.append((t, m, kind)))
c.check("each new item notified once, as the 'orchestration' kind; the dashboard is bumped",
        len(sent) == 4 and all(k == "orchestration" for _, _, k in sent) and g["_status_version"][0] == 1
        and sent[-1][0].startswith("✋") and "Shop Poster: Post to the shop" == sent[-1][1])
O["_orch_attention_refresh"](aconn, notify=lambda t, m, kind=None: sent.append((t, m, kind)))
c.check("nothing new -> no notification, no bump", len(sent) == 4 and g["_status_version"][0] == 1)
g["_orch_attention_state"].update(items=[], notified=None)   # a monitor reload
O["_orch_attention_refresh"](aconn, notify=lambda t, m, kind=None: sent.append((t, m, kind)))
c.check("after a reload: not announced again (remembered on disk)", len(sent) == 4)
aconn.close()
shutil.rmtree(nfile, ignore_errors=True)

# ── stats across runs ──
mk = lambda cost, coord, secs: {"cost_total": cost, "cost_coordination": coord, "duration_s": secs}
dev_a = {"name": "Developer", "tools": ["Edit"]}
rows = [({"state": "merged", "mode": "flat", "entity_id": ""},
         [T("a", state="done", cost_usd=0.5, attempt=2, started=0, finished=60, agent=dev_a, model="sonnet"),
          T("q", role="qa", state="failed", cost_usd=0.1, attempt=1, started=0, finished=20, model="haiku")], mk(1.0, 0.4, 100)),
        ({"state": "discarded", "mode": "hierarchical", "entity_id": ""},
         [T("m", role="manager", state="done", cost_usd=2.0, model="sonnet"), T("p", state="pending")], mk(3.0, 0.6, 300)),
        ({"state": "merged", "mode": "flat", "entity_id": "store"}, [T("b", state="done", cost_usd=0.3, agent=dev_a, model="sonnet")], mk(0.5, 0.1, 50))]
st = O["_orch_stats_from"](rows)
c.check("stats: totals, kept rate, coordination share", st["runs"] == 3 and st["merged"] == 2 and st["discarded"] == 1
        and st["kept_rate"] == 0.667 and st["cost"] == 4.5 and st["coordination_share"] == round(1.1 / 4.5, 3) and st["avg_s"] == 150)
ag = {a["name"]: a for a in st["by_agent"]}
c.check("stats per agent: success, cost, time, retries; unfinished tasks left out",
        ag["Developer"]["n"] == 2 and ag["Developer"]["success"] == 1.0 and ag["Developer"]["avg_cost"] == 0.4
        and ag["Developer"]["retries"] == 1 and ag["Developer"]["avg_s"] == 60 and ag["QA"]["success"] == 0.0 and "Manager" in ag
        and sum(a["n"] for a in st["by_agent"]) == 4)
c.check("stats per model and per kind of run", {m["name"]: m["n"] for m in st["by_model"]} == {"sonnet": 3, "haiku": 1}
        and {k["name"]: (k["runs"], k["kept"], k["dropped"]) for k in st["by_kind"]}
        == {"one team": (1, 1, 0), "hierarchical": (1, 0, 1), "entity": (1, 1, 0)})
c.check("stats: nothing yet", O["_orch_stats_from"]([])["runs"] == 0 and O["_orch_stats_from"]([])["kept_rate"] is None)

# watching a running agent: its transcript folder is named after its worktree
c.check("the transcript folder Claude Code uses for a path (as seen live)",
        os.path.basename(O["_orch_project_dir"](r"C:\Users\marek\AppData\Local\Temp\orch-ent-data\workspaces\ai-news\.claude\worktrees\aoc-orch-1", "B"))
        == "C--Users-marek-AppData-Local-Temp-orch-ent-data-workspaces-ai-news--claude-worktrees-aoc-orch-1")
pb = tempfile.mkdtemp()
wt_ = r"C:\work\tree-1"
pd = O["_orch_project_dir"](wt_, pb)
os.makedirs(pd)
open(os.path.join(pd, "old-session.jsonl"), "w").write("{}\n")
time.sleep(0.05)
open(os.path.join(pd, "new-session.jsonl"), "w").write("{}\n")
c.check("live session = the newest transcript there; none yet -> ''", O["_orch_live_session"](wt_, pb) == "new-session"
        and O["_orch_live_session"](r"C:\other", pb) == "")
shutil.rmtree(pb, ignore_errors=True)

# estimate before approval
hx = O["_orch_history_index"]([("Developer", "sonnet", 0.4, 60), ("Developer", "sonnet", 0.6, 120), ("QA", "haiku", 0.1, 30),
                               ("Research", "sonnet", 0.2, None)])
c.check("history: per agent + model and per model", hx[("Developer", "sonnet")] == (0.5, 90.0, 2) and hx["sonnet"][2] == 3
        and hx[("Research", "sonnet")][1] is None)
ets = [T("a", model="sonnet", budget=2.0), T("b", model="sonnet", budget=2.0), T("q", role="qa", deps=["a", "b"], model="haiku", budget=1.0)]
est = O["_orch_estimate"](ets, [["a", "b"], ["q"]], hx, 2)
c.check("estimate: known agents from history, the unknown one at half its budget; time = slowest per wave",
        est["cost"] == round(0.5 + 0.5 + 0.5, 2) and est["from_history"] == 2 and est["guessed"] == 1 and est["seconds"] == 90 + 180)
c.check("estimate: more tasks than slots take turns", O["_orch_estimate"](ets, [["a", "b"], ["q"]], hx, 1)["seconds"] == 90 + 90 + 180)
c.check("estimate: history never above the task's budget", O["_orch_estimate"]([T("a", model="sonnet", budget=0.3)], [["a"]], hx, 3)["cost"] == 0.3)
c.check("estimate: nothing known", O["_orch_estimate"](ets, [["a", "b"], ["q"]], {}, 3) == {"cost": 2.5, "seconds": 360, "from_history": 0, "guessed": 3})

# partial merge: what you keep must include what it was built on
mp = [T("api", state="done", commits=1, branch="b1"), T("ui", deps=["api"], state="done", commits=1, branch="b2"),
      T("docs", state="done", commits=1, branch="b3"), T("qa", role="qa", deps=["ui"], state="done")]
c.check("keep: plan order", O["_orch_merge_pick"](mp, ["docs", "api"]) == (["api", "docs"], None))
c.check("keep ui without api -> refused, says why", "'ui' is built on api" in O["_orch_merge_pick"](mp, ["ui"])[1])
c.check("keep ui with api", O["_orch_merge_pick"](mp, ["ui", "api"]) == (["api", "ui"], None))
c.check("keep nothing with work -> refused", O["_orch_merge_pick"](mp, ["qa"])[1].startswith("pick at least one"))

# partial merge for real: two independent developers, keep one
g["ORCH_DB"] = os.path.join(data_dir, "orchestration.db")   # the attention block above used :memory:
pm = os.path.realpath(tempfile.mkdtemp())
git("init", "-q", "-b", "main", cwd=pm); git("config", "user.email", "t@t", cwd=pm); git("config", "user.name", "t", cwd=pm)
open(os.path.join(pm, "r.txt"), "w").write("r\n"); git("add", "-A", cwd=pm); git("commit", "-qm", "init", cwd=pm)


def pmspawn(argv, cwd, stdin_text, base, env=None):
    os.makedirs(os.path.dirname(base), exist_ok=True)
    kind = os.path.basename(base).rsplit("-", 1)[0]
    if kind == "plan":
        answer(base, plan_of({"id": "keepme", "role": "developer", "title": "Keep"}, {"id": "dropme", "role": "developer", "title": "Drop"}))
    elif kind == "supervise":
        answer(base, finish("both done"))
    else:
        open(os.path.join(cwd, kind + ".txt"), "w").write(kind + "\n")
        answer(base, done(result="wrote " + kind))
    return 21


rpm = O["_orch_create"]({"goal": "Make two independent files", "cwd": pm}, exe="claude.exe", spawn=pmspawn)["id"]
pmk = dict(exe="claude.exe", spawn=pmspawn, alive=lambda pid: False, kill=lambda pid: None)
conn = O["_orch_connect"](); O["_orch_step"](conn, rpm, **pmk); conn.close()
O["_orch_action"]("approve", {"id": rpm})
for _ in range(5):
    conn = O["_orch_connect"](); O["_orch_step"](conn, rpm, **pmk); conn.close()
r = O["_orch_action"]("merge", {"id": rpm, "only": ["keepme"]})
c.check("partial merge: only the kept task's work lands, the other branch is gone, logged",
        r["ok"] and r["merged"] == ["keepme"] and r["dropped"] == ["dropme"] and os.path.exists(os.path.join(pm, "keepme.txt"))
        and not os.path.exists(os.path.join(pm, "dropme.txt")) and "aoc/orch" not in git("branch", cwd=pm).stdout
        and any("left out: dropme" in e["message"] for e in O["_orch_get"](rpm)["events"]))
shutil.rmtree(pm, ignore_errors=True)

# the same goal with one agent: compare
bl = os.path.realpath(tempfile.mkdtemp())
git("init", "-q", "-b", "main", cwd=bl); git("config", "user.email", "t@t", cwd=bl); git("config", "user.name", "t", cwd=bl)
open(os.path.join(bl, "r.txt"), "w").write("r\n"); git("add", "-A", cwd=bl); git("commit", "-qm", "init", cwd=bl)
blc = []


def blspawn(argv, cwd, stdin_text, base, env=None):
    os.makedirs(os.path.dirname(base), exist_ok=True)
    kind = os.path.basename(base).rsplit("-", 1)[0]
    blc.append(kind)
    if kind == "plan":
        answer(base, plan_of({"id": "a", "role": "developer", "title": "A"}, {"id": "b", "role": "developer", "title": "B"}))
    elif kind == "supervise":
        answer(base, finish("team: two files"))
    elif kind == "solo":
        open(os.path.join(cwd, "all.txt"), "w").write("1\n2\n3\n")
        answer(base, done(summary="did it alone", result="wrote all.txt with three lines"))
    else:
        open(os.path.join(cwd, kind + ".txt"), "w").write(kind + "\n")
        answer(base, done(result="wrote " + kind))
    return 31


rbl = O["_orch_create"]({"goal": "Write the files we need here", "cwd": bl}, exe="claude.exe", spawn=blspawn)["id"]
blk = dict(exe="claude.exe", spawn=blspawn, alive=lambda pid: False, kill=lambda pid: None)
conn = O["_orch_connect"](); O["_orch_step"](conn, rbl, **blk); conn.close()
c.check("compare before the plan is approved -> refused", "approve the plan first" in O["_orch_baseline"]({"id": rbl})["error"])
O["_orch_action"]("approve", {"id": rbl})
r = O["_orch_baseline"]({"id": rbl})
sid_ = r["id"]
c.check("one-agent version: same goal, one developer task, running at once", r["ok"] and sid_ == rbl + "-solo"
        and O["_orch_get"](sid_)["run"]["baseline_of"] == rbl and [t["role"] for t in O["_orch_get"](sid_)["tasks"]] == ["developer"]
        and O["_orch_get"](sid_)["run"]["state"] == "running")
c.check("only once per run", "already" in O["_orch_baseline"]({"id": rbl})["error"])
c.check("a one-agent version can't itself be compared", "only a team's own run" in O["_orch_baseline"]({"id": sid_})["error"])
c.check("judge: refused while a version still runs (nothing started)",
        "both versions must be done" in O["_orch_judge"]({"id": rbl}, run=lambda *a, **k: 1 / 0)["error"])
for _ in range(5):
    for x in (rbl, sid_):
        conn = O["_orch_connect"](); O["_orch_step"](conn, x, **blk); conn.close()
sv = O["_orch_get"](sid_)
c.check("the solo run: no planner, no review -- its answer is the report", sv["run"]["state"] == "review"
        and "three lines" in sv["run"]["report"] and blc.count("plan") == 1 and blc.count("supervise") == 1)
cmp_ = O["_orch_compare"](rbl)
c.check("compare: both sides, costs, the diff of each (files / lines)", cmp_["baseline"] == sid_
        and cmp_["team"]["diff"] == {"files": 2, "insertions": 2, "deletions": 0}
        and cmp_["solo"]["diff"] == {"files": 1, "insertions": 3, "deletions": 0}
        and cmp_["team"]["coordination"] > 0 and cmp_["solo"]["coordination"] == 0)
O["_orch_judging"].add(rbl)
c.check("judge: one comparison per run at a time", "already comparing" in O["_orch_judge"]({"id": rbl}, run=lambda *a, **k: 1 / 0)["error"])
O["_orch_judging"].discard(rbl)
jp = O["_orch_judge_prompt"]("G", "team rep", "diff T", "solo rep", "diff S")
c.check("judge prompt: both reports and diffs, cost left out of the judgement", all(x in jp for x in ("TEAM -- report:", "team rep", "diff S", "don't weigh it")))
judged = O["_orch_judge"]({"id": rbl}, run=lambda *a, **k: type("R", (), {"stdout": json.dumps({"subtype": "success", "total_cost_usd": 0.05,
    "structured_output": {"better": "solo", "reasons": "Simpler and complete.", "risks": "check the line endings"}})})())
c.check("judge: the verdict, kept on the team's run, logged", judged["ok"] and judged["better"] == "solo"
        and O["_orch_compare"](rbl)["verdict"]["better"] == "solo" and any(e["kind"] == "judged" for e in O["_orch_get"](rbl)["events"]))
c.check("the pair is linked both ways: run detail + list", O["_orch_get"](rbl)["baseline"] == sid_ and O["_orch_get"](sid_)["baseline"] == ""
        and next(x for x in O["_orch_list"]()["runs"] if x["id"] == sid_)["baseline_of"] == rbl
        and next(x for x in O["_orch_list"]()["runs"] if x["id"] == rbl)["baseline_of"] == "")
r = O["_orch_action"]("merge", {"id": sid_})
c.check("merging the one-agent version drops the team's run (its branches gone), logged", r["ok"]
        and O["_orch_get"](sid_)["run"]["state"] == "merged" and O["_orch_get"](rbl)["run"]["state"] == "discarded"
        and os.path.exists(os.path.join(bl, "all.txt")) and not os.path.exists(os.path.join(bl, "a.txt"))
        and "aoc/orch" not in git("branch", cwd=bl).stdout
        and any("other version" in e["message"] for e in O["_orch_get"](rbl)["events"]))
c.check("a run that is over can't be compared any more", "this run is over" in O["_orch_baseline"]({"id": rbl})["error"])
shutil.rmtree(bl, ignore_errors=True)

# house rules: every agent, the planner and the review get them
setf = tempfile.mkdtemp()
g["ORCH_SETTINGS_FILE"] = os.path.join(setf, "s.json")
c.check("rules: none by default; saving rules keeps billing and the other way round",
        O["_orch_settings"]()["rules"] == "" and O["_orch_settings_save"]({"billing": "subscription"})["ok"]
        and O["_orch_settings_save"]({"rules": "  - never commit .env\r\n- run the tests  "})["rules"] == "- never commit .env\n- run the tests"
        and O["_orch_settings"]()["billing"] == "subscription" and O["_orch_settings_save"]({"billing": "api"})["rules"].startswith("- never"))
c.check("rules: too long or nothing sent -> refused, the saved ones stay",
        "too long" in O["_orch_settings_save"]({"rules": "x" * 4001})["error"] and not O["_orch_settings_save"]({})["ok"]
        and O["_orch_settings"]()["rules"].startswith("- never"))
c.check("rules text: none -> nothing; the review is told a broken rule means not done",
        O["_orch_rules_text"]("", "agent") == "" and "not done" in O["_orch_rules_text"]("R", "supervise")
        and "plan only work" in O["_orch_rules_text"]("R", "plan") and O["_orch_rules_text"]("R", "agent").endswith("\nR"))
hr = os.path.realpath(tempfile.mkdtemp())
git("init", "-q", "-b", "main", cwd=hr); git("config", "user.email", "t@t", cwd=hr); git("config", "user.name", "t", cwd=hr)
open(os.path.join(hr, "r.txt"), "w").write("r\n"); git("add", "-A", cwd=hr); git("commit", "-qm", "init", cwd=hr)
seen = {}


def hrspawn(argv, cwd, stdin_text, base, env=None):
    os.makedirs(os.path.dirname(base), exist_ok=True)
    kind = os.path.basename(base).rsplit("-", 1)[0]
    seen[kind] = (argv, stdin_text)
    if kind == "plan":
        answer(base, plan_of({"id": "a", "role": "developer", "title": "A"}))
    else:
        open(os.path.join(cwd, "a.txt"), "w").write("a\n")
        answer(base, done(result="wrote a"))
    return 41


rhr = O["_orch_create"]({"goal": "Write the file we need here", "cwd": hr}, exe="claude.exe", spawn=hrspawn)["id"]
hk = dict(exe="claude.exe", spawn=hrspawn, alive=lambda pid: False, kill=lambda pid: None)
conn = O["_orch_connect"](); O["_orch_step"](conn, rhr, **hk); conn.close()
O["_orch_action"]("approve", {"id": rhr})
conn = O["_orch_connect"](); O["_orch_step"](conn, rhr, **hk); conn.close()
sp = seen["a"][0][seen["a"][0].index("--append-system-prompt") + 1] if "a" in seen else ""
c.check("the planner reads the rules; the agent gets them in its system prompt, after its role",
        "HOUSE RULES" in seen["plan"][1] and "- run the tests" in seen["plan"][1]
        and sp.index("developer agent") < sp.index("House rules from the human") < sp.index("- never commit .env"))
O["_orch_action"]("cancel", {"id": rhr})
O["_orch_settings_save"]({"rules": ""})
c.check("rules cleared -> the agent's prompt has none", "House rules" not in O["_orch_agent_argv"]("claude.exe", {"model": "sonnet", "budget_usd": 1, "role": "developer"},
        None, (), O["_orch_settings"]()["rules"])[-1])
shutil.rmtree(hr, ignore_errors=True)
shutil.rmtree(setf, ignore_errors=True)

# a process that answers nothing: what it said on stderr is the reason
rd = tempfile.mkdtemp()
b1, b2, b3 = (os.path.join(rd, n) for n in ("ok", "crash", "none"))
open(b1 + ".out", "w").write('{"subtype":"success"}'); open(b1 + ".err", "w").write("noise")
open(b2 + ".out", "w").write(""); open(b2 + ".err", "w").write("x" * 500 + "Error: Invalid API key -- run /login")
c.check("answer read: JSON as is; none -> the stderr tail, shown in the error; nothing at all -> empty output",
        O["_orch_read_out"](b1) == '{"subtype":"success"}'
        and "Invalid API key -- run /login" in O["_orch_parse"](O["_orch_read_out"](b2))["error"]
        and len(O["_orch_read_out"](b2)) < 330 and O["_orch_parse"](O["_orch_read_out"](b3))["error"] == "no answer (empty output)")
sp_ = []
O["_orch_spawn"](["x"], rd, "line1\nline2", os.path.join(rd, "sp"), popen=lambda *a, **k: sp_.append(1) or type("P", (), {"pid": 7})())
c.check("the prompt file is written byte for byte (\\n, not \\r\\n)", open(os.path.join(rd, "sp.in"), "rb").read() == b"line1\nline2")
shutil.rmtree(rd, ignore_errors=True)
c.finish()
