# AOC server, part 11: A/B compare, continue, task queue, prompts, budget caps, danger guard, usage limit.
# monitor.py exec()s server/NN_*.py in number order into ONE shared
# namespace (its own) -- not an importable module: names from earlier
# parts are plain globals here, and __file__ is monitor.py.
# ── A/B model compare: the same first message, run headless (`claude -p`)
# once per model, each in its own throwaway git worktree off the repo's
# HEAD, so neither run touches your working copy. Runs may read and edit
# files in their copy only: shell tools are off, MCP servers are off and
# hooks are off, so a run can't push, delete, install or message anyone;
# each is capped by --max-budget-usd. Afterwards you keep one (its changes
# are committed on a branch and merged into your repo -- only when your
# working copy has no uncommitted changes) or discard both.
# %LOCALAPPDATA%\AOC\ab_runs.json holds the comparisons, ab_worktrees\ the
# copies. The _ab_git/_ab_wt_* helpers are deliberately private to this
# feature.
AB_RUNS_FILE = os.path.join(AOC_DATA_DIR, "ab_runs.json")
AB_WT_DIR = os.path.join(AOC_DATA_DIR, "ab_worktrees")
_AB_MODELS = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5", "haiku": "claude-haiku-4-5"}
_AB_LABELS = ("A", "B")
_AB_PROMPT_MAX = 8000
_AB_BUDGET_DEFAULT = 2.0
_AB_BUDGET_MAX = 20.0
_AB_TIMEOUT_S = 20 * 60
_AB_KEEP = 20
_AB_DIFF_MAX = 200_000
_AB_FINAL_MAX = 6000
_AB_STARTUP_TOKENS = 30_000   # system prompt + built-in tools of a bare -p run
_AB_NO_TOOLS = ("Bash", "PowerShell", "KillShell", "BashOutput")
_ab_lock = threading.Lock()
_ab_procs = {}                # (comparison id, label) -> Popen while it runs
_AB_LOADED_AT = time.time()


def _ab_validate(data, isdir=os.path.isdir):
    """Pure-ish: body -> ({cwd, prompt, models, budget}, None) or (None, error)."""
    if not isinstance(data, dict):
        return None, "bad request"
    base, err = _launch_validate({"cwd": data.get("cwd"), "prompt": data.get("prompt")}, isdir=isdir)
    if err:
        return None, err
    if not base["prompt"]:
        return None, "write the task both models get"
    if len(base["prompt"]) > _AB_PROMPT_MAX:
        return None, f"prompt too long for a comparison ({_AB_PROMPT_MAX:,} characters max)"
    models = data.get("models") or ["opus", "sonnet"]
    if (not isinstance(models, list) or len(models) != 2 or len(set(models)) != 2
            or any(m not in _AB_MODELS for m in models)):
        return None, "pick two different models"
    try:
        budget = float(data.get("budget") if data.get("budget") not in (None, "") else _AB_BUDGET_DEFAULT)
    except (TypeError, ValueError):
        return None, "budget must be a number"
    if not 0.1 <= budget <= _AB_BUDGET_MAX:
        return None, f"budget per run must be between $0.10 and ${_AB_BUDGET_MAX:.0f}"
    return {"cwd": base["cwd"], "prompt": base["prompt"], "models": list(models), "budget": round(budget, 2)}, None


def _ab_estimate(prompt, model_key):
    """Pure: rough (low, high) $ for one headless run -- a short task (3 API
    calls) and a long one (25). Context = startup + prompt, growing per call."""
    pi, po, pw, pr = _model_pricing(_AB_MODELS.get(model_key, ""))
    ctx = _AB_STARTUP_TOKENS + len(prompt or "") // 4

    def est(calls, out_per, grow):
        total = ctx * pw + calls * out_per * po
        for i in range(1, calls):
            total += (ctx + i * grow) * pr + grow * pw
        return total / 1_000_000
    return round(est(3, 400, 1500), 2), round(est(25, 600, 2500), 2)


def _ab_quote(data):
    """POST /ab/estimate: per run (low, high) $, the high end capped by the budget."""
    data = data if isinstance(data, dict) else {}
    models = data.get("models") if isinstance(data.get("models"), list) else ["opus", "sonnet"]
    try:
        budget = min(max(float(data.get("budget") or _AB_BUDGET_DEFAULT), 0.1), _AB_BUDGET_MAX)
    except (TypeError, ValueError):
        budget = _AB_BUDGET_DEFAULT
    runs = []
    for label, m in zip(_AB_LABELS, models[:2]):
        if m not in _AB_MODELS:
            continue
        lo, hi = _ab_estimate(str(data.get("prompt") or ""), m)
        runs.append({"label": label, "model": m, "low": min(lo, budget), "high": min(hi, budget),
                     "capped": hi > budget})
    return {"runs": runs, "budget": round(budget, 2), "max_total": round(budget * len(runs), 2)}


def _ab_argv(exe, model_key, budget):
    """Pure: the headless claude command line (prompt goes in on stdin)."""
    return [exe, "-p", "--model", model_key, "--output-format", "stream-json", "--verbose",
            "--no-session-persistence", "--permission-mode", "acceptEdits",
            "--disallowedTools", ",".join(_AB_NO_TOOLS), "--strict-mcp-config",
            "--max-budget-usd", f"{budget:.2f}", "--settings", '{"disableAllHooks": true}']


def _ab_parse_stream(lines):
    """Pure: stream-json lines of `claude -p --verbose` -> run facts. Cost =
    the result line's total_cost_usd, else summed from usage (each API call
    once: a message repeats per content block)."""
    r = {"cost": None, "turns": 0, "tool_calls": 0, "errors": 0, "denials": 0, "final": "",
         "is_error": False, "stop": "", "model": "", "api_ms": None}
    seen_msg, seen_tool, usage_cost = set(), set(), 0.0
    for line in lines:
        try:
            d = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(d, dict):
            continue
        t = d.get("type")
        if t == "system" and d.get("subtype") == "init":
            r["model"] = d.get("model") or r["model"]
        elif t == "assistant":
            m = d.get("message") or {}
            for c in m.get("content") or ():
                if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("id") not in seen_tool:
                    seen_tool.add(c.get("id"))
                    r["tool_calls"] += 1
            mid = m.get("id")
            u = m.get("usage") or {}
            if mid and mid not in seen_msg and u:
                seen_msg.add(mid)
                cc = u.get("cache_creation") or {}
                usage_cost += _calc_cost(u.get("input_tokens") or 0, u.get("output_tokens") or 0,
                                         u.get("cache_creation_input_tokens") or 0, u.get("cache_read_input_tokens") or 0,
                                         m.get("model") or r["model"], cc.get("ephemeral_1h_input_tokens") or 0)
        elif t == "user":
            for c in (d.get("message") or {}).get("content") or ():
                if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("is_error"):
                    text = c.get("content")
                    text = " ".join(x.get("text", "") for x in text if isinstance(x, dict)) if isinstance(text, list) else str(text or "")
                    if re.search(r"permission|not allowed|denied|disallowed", text, re.I):
                        r["denials"] += 1
                    else:
                        r["errors"] += 1
        elif t == "result":
            if d.get("total_cost_usd") is not None:
                r["cost"] = float(d["total_cost_usd"])
            r["turns"] = int(d.get("num_turns") or 0)
            r["final"] = str(d.get("result") or "")[:_AB_FINAL_MAX]
            r["is_error"] = bool(d.get("is_error"))
            r["stop"] = str(d.get("subtype") or "")
            r["api_ms"] = d.get("duration_api_ms")
    if r["cost"] is None:
        r["cost"] = usage_cost
    r["cost"] = round(r["cost"], 4)
    return r


# Git-worktree plumbing for A/B: git calls and worktree removal go through the
# shared _wt_* API (parallel sessions); A/B keeps its own folders/branch names
# and, unlike parallel sessions, also works on a detached HEAD.
def _ab_git(args, cwd, timeout=60):
    """-> (returncode, stdout, stderr), UTF-8 -- the shared _wt_git with A/B's longer timeout."""
    return _wt_git(list(args), cwd, timeout=timeout)


def _ab_repo(cwd):
    """(repo root, HEAD sha) of the folder, or (None, error)."""
    rc, root, _ = _ab_git(["rev-parse", "--show-toplevel"], cwd)
    if rc != 0 or not root.strip():
        return None, "that folder is not inside a git repository"
    rc, head, _ = _ab_git(["rev-parse", "--verify", "HEAD"], cwd)
    if rc != 0:
        return None, "the repository has no commit yet"
    return (os.path.realpath(root.strip()), head.strip()), None


def _ab_wt_add(repo, path, branch, head):
    rc, _o, err = _ab_git(["worktree", "add", "-b", branch, path, head], repo)
    return None if rc == 0 else (err.strip() or "git worktree add failed")[:300]


def _ab_wt_remove(repo, path, branch=None):
    """Removes the worktree (and its branch when given) via the shared
    _wt_remove; a folder git refuses to remove is deleted anyway. Never raises."""
    import shutil
    if _wt_remove(repo, path or "", branch, git=_ab_git) and path and os.path.exists(path):
        shutil.rmtree(path, ignore_errors=True)
        _ab_git(["worktree", "prune"], repo)
        if branch:
            _ab_git(["branch", "-D", branch], repo)


def _ab_wt_snapshot(wt):
    """Stage everything in the worktree -> (diffstat, diff text, truncated)."""
    _ab_git(["add", "-A"], wt)
    _rc, num, _e = _ab_git(["diff", "--cached", "--numstat", "HEAD"], wt)
    files = [{"path": p, "ins": i, "dels": d} for p, (i, d) in _parse_numstat(num).items()]
    _rc, diff, _e = _ab_git(["diff", "--cached", "HEAD"], wt)
    stat = {"files": len(files), "ins": sum(f["ins"] or 0 for f in files),
            "dels": sum(f["dels"] or 0 for f in files), "list": files[:200]}
    return stat, diff[:_AB_DIFF_MAX], len(diff) > _AB_DIFF_MAX


def _ab_load():
    d = _load_json_file(AB_RUNS_FILE, [])
    return d if isinstance(d, list) else []


def _ab_save(items):
    os.makedirs(AOC_DATA_DIR, exist_ok=True)
    items = sorted(items, key=lambda x: x.get("created") or 0)[-_AB_KEEP:]
    tmp = AB_RUNS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    os.replace(tmp, AB_RUNS_FILE)


def _ab_update(cid, label=None, **fields):
    """Merge fields into a comparison (or one of its runs); returns it."""
    with _ab_lock:
        items = _ab_load()
        for c in items:
            if c.get("id") != cid:
                continue
            target = c if label is None else next((r for r in c.get("runs") or () if r.get("label") == label), None)
            if target is not None:
                target.update(fields)
            if label is None and fields.get("state") in ("kept", "discarded"):
                for r in c.get("runs") or ():      # the copies are gone: so are their diffs
                    r.pop("diff", None)
            if label is not None and all(r.get("state") not in ("queued", "running") for r in c.get("runs") or ()) \
                    and c.get("state") == "running":
                c["state"] = "done"
            _ab_save(items)
            return c
    return None


def _ab_overall(c, loaded_at=None, live=None):
    """Pure: a comparison as shown -- runs still 'running' that this AOC
    process never started (it restarted mid-run) are reported as stopped."""
    loaded_at = _AB_LOADED_AT if loaded_at is None else loaded_at
    live = _ab_procs if live is None else live
    c = json.loads(json.dumps(c))
    if c.get("state") == "running" and (c.get("created") or 0) < loaded_at:
        for r in c.get("runs") or ():
            if r.get("state") in ("queued", "running") and (c.get("id"), r.get("label")) not in live:
                r["state"], r["error"] = "error", "AOC restarted during the run"
        if all(r.get("state") not in ("queued", "running") for r in c.get("runs") or ()):
            c["state"] = "done"
    return c


def _ab_run_one(cid, label, argv, wt, prompt, popen=None, timeout=_AB_TIMEOUT_S, clock=time.time):
    """Runs one model in its worktree (blocking; called on a thread)."""
    start = clock()
    _ab_update(cid, label, state="running", started=start)
    lines, other = [], []
    try:
        # stderr into stdout: a full stderr pipe nobody reads would stall the run
        p = (popen or subprocess.Popen)(argv, cwd=wt, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, creationflags=_NO_WINDOW)
    except Exception as e:
        _ab_update(cid, label, state="error", error=f"could not start Claude Code: {e}"[:300], ended=clock())
        return
    _ab_procs[(cid, label)] = p
    _own_claude_pids.add(p.pid)
    killer = threading.Timer(timeout, lambda: p.kill())
    killer.daemon = True
    killer.start()
    try:
        try:
            p.stdin.write(prompt.encode("utf-8"))
            p.stdin.close()
        except OSError:
            pass
        last_push = clock()
        for raw in p.stdout:
            s = raw.decode("utf-8", "replace")
            if s.lstrip().startswith("{"):
                lines.append(s)
            elif s.strip():
                other = (other + [s.strip()])[-20:]
            if clock() - last_push >= 2:
                last_push = clock()
                part = _ab_parse_stream(lines)
                _ab_update(cid, label, live={k: part[k] for k in ("tool_calls", "errors", "denials", "cost")})
        p.wait()
    finally:
        killer.cancel()
        _ab_procs.pop((cid, label), None)
        _own_claude_pids.discard(p.pid)
    end = clock()
    res = _ab_parse_stream(lines)
    timed_out = end - start >= timeout - 1
    stat, diff, trunc = _ab_wt_snapshot(wt) if os.path.isdir(wt) else ({"files": 0, "ins": 0, "dels": 0, "list": []}, "", False)
    stopped = (cid, label) in _ab_stopped
    state = "stopped" if stopped else "error" if (not lines or (res["is_error"] and res["stop"] != "error_max_budget_usd")) else "done"
    error = ("stopped" if stopped else "timed out" if timed_out else
             "" if state == "done" else (res["final"] or "\n".join(other) or "no output")[-300:])
    _ab_update(cid, label, state=state, ended=end, wall_s=round(end - start, 1), live=None, error=error,
               diffstat=stat, diff=diff, diff_truncated=trunc,
               **{k: res[k] for k in ("cost", "turns", "tool_calls", "errors", "denials", "final", "stop", "model")})


_ab_stopped = set()


def _ab_start(data, now=None, exe=None, popen=None, spawn=None):
    """POST /ab/start -> {"ok", "id"} or {"ok": False, "error"}."""
    if not isinstance(data, dict) or data.get("confirm") is not True:
        return {"ok": False, "error": "confirm the cost first"}
    args, err = _ab_validate(data)
    if err:
        return {"ok": False, "error": err}
    repo, err = _ab_repo(args["cwd"])
    if err:
        return {"ok": False, "error": err}
    root, head = repo
    exe = exe or _find_claude_exe()
    if not exe:
        return {"ok": False, "error": "Claude Code (claude) not found on this PC"}
    now = now or time.time()
    with _ab_lock:
        if any(_ab_overall(c).get("state") == "running" for c in _ab_load()):
            return {"ok": False, "error": "an A/B comparison is already running"}
        cid = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + "-" + os.urandom(2).hex()
        runs = []
        for label, m in zip(_AB_LABELS, args["models"]):
            runs.append({"label": label, "model": m, "branch": f"aoc-ab/{cid}-{label.lower()}-{m}",
                         "wt": os.path.join(AB_WT_DIR, f"{cid}-{label.lower()}"), "state": "queued"})
        os.makedirs(AB_WT_DIR, exist_ok=True)
        for r in runs:
            e = _ab_wt_add(root, r["wt"], r["branch"], head)
            if e:
                for x in runs:
                    _ab_wt_remove(root, x["wt"], x["branch"])
                return {"ok": False, "error": "could not make a copy of the repo: " + e}
        rel = os.path.relpath(os.path.realpath(args["cwd"]), root)
        rec = {"id": cid, "created": now, "cwd": args["cwd"], "repo": root, "head": head, "prompt": args["prompt"],
               "budget": args["budget"], "state": "running", "kept": None, "runs": runs}
        items = _ab_load()
        items.append(rec)
        _ab_save(items)
    spawn = spawn or (lambda fn, *a: threading.Thread(target=fn, args=a, daemon=True, name="ab-run").start())
    for r in runs:
        wt_cwd = os.path.normpath(os.path.join(r["wt"], rel)) if rel != "." else r["wt"]
        spawn(_ab_run_one, cid, r["label"], _ab_argv(exe, r["model"], args["budget"]), wt_cwd, args["prompt"], popen)
    return {"ok": True, "id": cid}


def _ab_get(cid):
    c = next((c for c in _ab_load() if c.get("id") == cid), None)
    return _ab_overall(c) if c else None


def _ab_list():
    """GET /ab: comparisons newest first, without diffs/answers."""
    out = []
    for c in sorted(_ab_load(), key=lambda c: -(c.get("created") or 0)):
        c = _ab_overall(c)
        c["runs"] = [{k: v for k, v in r.items() if k not in ("diff", "final")} for r in c.get("runs") or ()]
        out.append(c)
    return {"items": out}


def _ab_stop(c):
    for r in c.get("runs") or ():
        p = _ab_procs.get((c["id"], r["label"]))
        if p:
            _ab_stopped.add((c["id"], r["label"]))
            try:
                p.kill()
            except Exception:
                pass


def _ab_discard(cid):
    """POST /ab/discard: stop what still runs, remove both copies + branches."""
    c = _ab_get(cid)
    if not c:
        return {"ok": False, "error": "not found"}
    if c.get("state") in ("kept", "discarded"):
        return {"ok": True}
    _ab_stop(c)
    for _ in range(50):
        if not any((cid, r["label"]) in _ab_procs for r in c["runs"]):
            break
        time.sleep(0.1)
    for r in c["runs"]:
        _ab_wt_remove(c["repo"], r.get("wt"), r.get("branch"))
    _ab_update(cid, state="discarded", diff_dropped=True)
    return {"ok": True}


def _ab_keep(cid, label):
    """POST /ab/keep: commit that run's changes on its branch and merge it
    into the repo's current branch; the other copy is discarded. Refused
    while your working copy has uncommitted changes. A conflicting merge is
    aborted and the branch is left for you to merge by hand."""
    c = _ab_get(cid)
    if not c:
        return {"ok": False, "error": "not found"}
    if c.get("state") != "done":
        return {"ok": False, "error": "still running" if c.get("state") == "running" else "already " + str(c.get("state"))}
    run = next((r for r in c["runs"] if r.get("label") == label), None)
    if not run or run.get("state") not in ("done", "stopped"):
        return {"ok": False, "error": "that run has nothing to keep"}
    if not (run.get("diffstat") or {}).get("files"):
        return {"ok": False, "error": "that run changed no files -- discard instead"}
    repo, wt = c["repo"], run["wt"]
    rc, dirty, _e = _ab_git(["status", "--porcelain", "--untracked-files=no"], repo)
    if rc != 0 or dirty.strip():
        return {"ok": False, "error": "your working copy has uncommitted changes -- commit or stash them first"}
    first = re.sub(r"\s+", " ", c.get("prompt") or "").strip()[:60]
    _ab_git(["add", "-A"], wt)
    rc, _o, err = _ab_git(["commit", "-q", "-m", f"A/B {run['model']}: {first}"], wt)
    if rc != 0:
        return {"ok": False, "error": "could not commit in the copy: " + (err.strip() or "git commit failed")[:200]}
    rc, _o, err = _ab_git(["merge", "--no-ff", "--no-edit", "-m", f"Merge A/B run {label} ({run['model']})", run["branch"]], repo)
    if rc != 0:
        _ab_git(["merge", "--abort"], repo)
        for r in c["runs"]:
            _ab_wt_remove(repo, r.get("wt"), None if r is run else r.get("branch"))
        _ab_update(cid, state="kept", kept=label, merge_error=True)
        return {"ok": False, "error": f"merge conflict -- the changes stay on branch {run['branch']}; merge it yourself",
                "branch": run["branch"]}
    _rc, sha, _e = _ab_git(["rev-parse", "HEAD"], repo)
    for r in c["runs"]:
        _ab_wt_remove(repo, r.get("wt"), None if r is run else r.get("branch"))
    _ab_git(["branch", "-d", run["branch"]], repo)
    _ab_update(cid, state="kept", kept=label, merged=sha.strip())
    return {"ok": True, "merged": sha.strip()}


# ── Continue a project (Today -> "Pick up where you left off") ───────────
# Most sessions start with the same kind of message: "continue with X"
# ("pokračuj v AOC", typos included). Projects come from the auto-memory
# indexes ([Name](project_x.md) entries); past sessions are matched to them
# by their first message, and each card offers that same opening again in
# the folder it last ran in -- or reopens the last conversation itself.
# Sessions that match no project fall back to cards per folder.
_RESUME_DAYS = 30
_RESUME_CACHE_S = 60
_RESUME_MAX_CARDS = 8
_RESUME_VERB = re.compile(r"^(?:p\w{0,3}kra\w*|continue\w*|resume\w*|let'?s continue)\s+(?:(?:v|vo|s|so|na|with|on|in)\s+)?(.+)$")
_resume_cache = {}


def _resume_norm(s):
    """Pure: lowercase, no diacritics, single spaces."""
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", s.lower()).strip()


def _resume_match(text, names):
    """Pure: (project name, is_continue_prompt) for a session's first
    message, or (None, False). A "continue with X" message is matched
    typo-tolerantly against the start of X; any other message only when it
    names a project as a whole word."""
    import difflib
    t = _resume_norm(text)[:300]
    m = _RESUME_VERB.match(t)
    if m:
        rest = m.group(1).strip(" .!?:")
        best, score = None, 0.0
        for n in names:
            nn = _resume_norm(n)
            r = difflib.SequenceMatcher(None, rest[:len(nn)], nn).ratio() if nn else 0
            if r > score:
                best, score = n, r
        if best and score >= 0.8:
            return best, True
    for n in sorted(names, key=lambda n: -len(n)):
        nn = _resume_norm(n)
        if len(nn) >= 3 and re.search(r"(?<![a-z0-9])" + re.escape(nn) + r"(?![a-z0-9])", t):
            return n, False
    return None, False


def _resume_projects_from_index(text):
    """Pure: [(name, file, hook)] from a MEMORY.md index -- project entries only."""
    out = []
    for m in re.finditer(r"^- \[([^\]]+)\]\(((?:[^)]*[\\/])?project_[^)\s]*\.md)\)\s*(?:—|-|:)?\s*(.*)$", text or "", re.M):
        out.append((m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
    return out


def _resume_cards(projects, sessions, home, now, open_ids=()):
    """Pure: project cards. projects = [(name, file, hook)], sessions =
    [{id, cwd, title, cost, last, first}] newest first. Each card: name,
    hook, cwd, prompt ("continue with <name>" in your usual verb, spelled
    right; a folder card repeats that folder's last opening), last session,
    30-day count/cost, resume_id, open."""
    names = [p[0] for p in projects]
    hooks = {p[0]: p[2] for p in projects}
    by, verbs, rest = {}, [], []
    for s in sessions:
        name, cont = _resume_match(s.get("first") or "", names)
        if cont:
            verbs.append(s["first"].strip().split()[0])
        if name:
            by.setdefault(name, {"sessions": [], "prompt": None})["sessions"].append(s)
        else:
            rest.append(s)
    word = max(set(verbs), key=verbs.count) if verbs else "Continue"   # your usual verb
    prep = "v" if word.lower().startswith(("pokr", "pkr")) else "with"
    homen = os.path.normcase(os.path.normpath(home))
    open_ids = set(open_ids)

    def card(name, hook, group, folder_card=False):
        ss = group["sessions"]
        last = ss[0]
        cwd = last.get("cwd") or home
        return {"name": name, "hook": hook, "cwd": cwd, "folder": folder_card,
                "prompt": group.get("prompt") or f"{word} {prep} {name}",
                "last": {"id": last["id"], "title": last.get("title") or "", "at": last.get("last") or 0},
                "sessions": len(ss), "cost": round(sum(s.get("cost") or 0 for s in ss), 2),
                "resume_id": last["id"],
                "open": [s["id"] for s in ss if s["id"] in open_ids]}

    cards = [card(n, hooks.get(n, ""), g) for n, g in by.items()]
    folders = {}
    for s in rest if len(cards) < 3 else ():   # no memory projects to speak of: cards per folder
        cwd = s.get("cwd") or ""
        k = os.path.normcase(os.path.normpath(cwd)) if cwd else ""
        if not cwd or k == homen or re.search(r"[\\/](temp|tmp|scratchpad)([\\/]|$)", k):
            continue
        folders.setdefault(os.path.normcase(os.path.normpath(cwd)), {"sessions": [], "prompt": None})["sessions"].append(s)
    for g in folders.values():
        first = g["sessions"][0]
        g["prompt"] = re.sub(r"\s+", " ", first.get("first") or "").strip()[:200] or None
        cards.append(card(os.path.basename(first["cwd"].rstrip("\\/")) or first["cwd"], "", g, folder_card=True))
    cards.sort(key=lambda c: -(c["last"]["at"] or 0))
    return cards[:_RESUME_MAX_CARDS]


def _resume_sessions(days, now):
    """[{id, cwd, title, cost, last, first}] of the last `days`, newest first."""
    if not os.path.exists(TRANSCRIPT_INDEX_DB):
        return []
    conn = _transcript_index_connect()
    try:
        meta = conn.execute("SELECT session_id, cwd, title, run_cost, last_call_epoch FROM sessions_meta "
                            "WHERE last_call_epoch >= ? ORDER BY last_call_epoch DESC", (now - days * 86400,)).fetchall()
        want = {r[0] for r in meta}
        first = {}
        for sid, ts, text in conn.execute("SELECT session_id, ts, substr(text, 1, 300) FROM msgs WHERE role = 'user'"):
            if sid in want and (sid not in first or (ts or "") < first[sid][0]):
                first[sid] = (ts or "", text or "")
    finally:
        conn.close()
    return [{"id": r[0], "cwd": r[1] or "", "title": r[2] or "", "cost": r[3] or 0, "last": r[4] or 0,
             "first": first.get(r[0], ("", ""))[1]} for r in meta if r[0] in first]


def _resume_report(open_ids=(), now=None):
    """GET /resume_projects (cached a minute; `open` is filled per call)."""
    now = now or time.time()
    hit = _resume_cache.get("v")
    if not hit or now - hit[0] >= _RESUME_CACHE_S:
        home = os.path.expanduser("~")
        projects, seen = [], set()
        for f in _mem_discover(home, []):
            if f["kind"] != "index":
                continue
            try:
                for p in _resume_projects_from_index(_mem_read(f["path"])):
                    if p[0].lower() not in seen:
                        seen.add(p[0].lower())
                        projects.append(p)
            except (OSError, ValueError):
                continue
        hit = (now, projects, _resume_sessions(_RESUME_DAYS, now), home)
        _resume_cache["v"] = hit
    return {"days": _RESUME_DAYS, "cards": _resume_cards(hit[1], hit[2], hit[3], now, open_ids)}


# ── Task queue: start a session later -- at a set time and/or once the
# plan limits allow. %LOCALAPPDATA%\AOC\queue.json holds the tasks; a worker
# checks every 20 s and starts at most one task a minute through
# _launch_session, so a reset doesn't fire the whole queue at once.
QUEUE_FILE = os.path.join(AOC_DATA_DIR, "queue.json")
_QUEUE_LIMIT_PCT = 95.0      # a window at/over this counts as "not available yet"
_QUEUE_MIN_GAP_S = 60
_QUEUE_KEEP_DONE_S = 24 * 3600
_QUEUE_MAX = 50
_queue_lock = threading.Lock()
_queue_last_launch = [0.0]


def _queue_limits_ok(view) -> bool:
    """Pure: may a queued task use the plan now? (No limit data = yes.)"""
    for w in (view or {}).get("windows", []):
        if w.get("status") == "hit":
            return False
        if isinstance(w.get("pct"), (int, float)) and w["pct"] >= _QUEUE_LIMIT_PCT:
            return False
    return True


def _queue_waiting_on(task, view, now) -> str:
    """Pure: what a queued task waits for -- 'time', 'limits' or '' (due)."""
    if task.get("at") and now < task["at"]:
        return "time"
    if task.get("limits", True) and not _queue_limits_ok(view):
        return "limits"
    return ""


def _queue_next_due(tasks, view, now, last_launch):
    """Pure: the queued task to start now (oldest due first), or None."""
    if now - last_launch < _QUEUE_MIN_GAP_S:
        return None
    due = [t for t in tasks if t.get("state") == "queued" and not _queue_waiting_on(t, view, now)]
    return min(due, key=lambda t: (t.get("at") or 0, t["created"])) if due else None


def _queue_prune(tasks, now):
    """Pure: drop finished tasks older than a day."""
    return [t for t in tasks if t.get("state") == "queued"
            or now - (t.get("finished_at") or t.get("created") or 0) < _QUEUE_KEEP_DONE_S]


def _queue_load():
    data = _load_json_file(QUEUE_FILE, [])
    return [t for t in data if isinstance(t, dict) and t.get("id")] if isinstance(data, list) else []


def _queue_save(tasks):
    os.makedirs(AOC_DATA_DIR, exist_ok=True)
    tmp = QUEUE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False)
    os.replace(tmp, QUEUE_FILE)


def _queue_add(data, now=None) -> dict:
    """POST /queue {cwd, prompt, model, at?, limits?} -> {"ok", "task"}."""
    now = now or time.time()
    args, err = _launch_validate(data)
    if err:
        return {"ok": False, "error": err}
    if not args["prompt"]:
        return {"ok": False, "error": "a queued task needs a first message"}
    at = data.get("at")
    if at not in (None, "", 0):
        try:
            at = int(float(at))
        except (TypeError, ValueError):
            return {"ok": False, "error": "bad start time"}
        if at < now - 60 or at > now + 14 * 86400:
            return {"ok": False, "error": "pick a start time within the next 14 days"}
    else:
        at = None
    limits = data.get("limits", True) is not False
    if at is None and not limits:
        return {"ok": False, "error": "nothing to wait for -- use Start session instead"}
    task = {"id": _secrets.token_hex(6), "cwd": args["cwd"], "prompt": args["prompt"], "model": args["model"],
            "at": at, "limits": limits, "created": now, "state": "queued"}
    if args.get("resume"):
        task["resume"] = args["resume"]   # continue that conversation (usage-limit auto-continue)
    with _queue_lock:
        tasks = _queue_prune(_queue_load(), now)
        if sum(1 for t in tasks if t.get("state") == "queued") >= _QUEUE_MAX:
            return {"ok": False, "error": f"the queue is full ({_QUEUE_MAX} tasks)"}
        tasks.append(task)
        _queue_save(tasks)
    return {"ok": True, "task": task}


def _queue_cancel(tid, now=None) -> bool:
    """POST /queue/cancel {id}: a queued task -> cancelled; a finished one is removed."""
    now = now or time.time()
    with _queue_lock:
        tasks = _queue_load()
        for t in tasks:
            if t.get("id") == tid:
                if t.get("state") == "queued":
                    t["state"], t["finished_at"] = "cancelled", now
                else:
                    tasks.remove(t)
                _queue_save(tasks)
                return True
    return False


def _queue_view(view, now) -> list:
    """The /status 'queue' block: tasks with what each waits for."""
    with _queue_lock:
        tasks = _queue_prune(_queue_load(), now)
    out = []
    for t in sorted(tasks, key=lambda t: (t.get("state") != "queued", t.get("at") or 0, t["created"])):
        row = {k: t.get(k) for k in ("id", "cwd", "model", "at", "limits", "created", "state",
                                     "finished_at", "pid", "error", "resume")}
        row["prompt"] = (t.get("prompt") or "")[:300]
        row["waiting_on"] = _queue_waiting_on(t, view, now) if t.get("state") == "queued" else ""
        out.append(row)
    return out


def _queue_tick(now=None, launch=None, view=None, open_ids=None):
    """One worker pass: start the next due task, if any. Returns it or None."""
    now = now or time.time()
    view = _rate_limits_snapshot() if view is None else view
    if open_ids is None:
        # read OUTSIDE _queue_lock: a /status rebuild takes it (_queue_view)
        with _queue_lock:
            resumes = any(t.get("state") == "queued" and t.get("resume") for t in _queue_load())
        open_ids = _open_session_ids() if resumes else set()
    with _queue_lock:
        tasks = _queue_load()
        task = _queue_next_due(tasks, view, now, _queue_last_launch[0])
        if not task:
            return None
        if task.get("resume") and task["resume"] in open_ids:
            # a second claude.exe on the same conversation would fork it: the
            # one still open in its terminal has to be continued there
            task.update(state="failed", finished_at=now, pid=None,
                        error="still open in a terminal -- type pokračuj there")
            _queue_save(_queue_prune(tasks, now))
        else:
            _queue_last_launch[0] = now
            req = {"cwd": task["cwd"], "prompt": task["prompt"], "model": task["model"]}
            if task.get("resume"):
                req.update(resume=task["resume"], keep_prompt=True)
            res = (launch or _launch_session)(req, now=now)
            if not res.get("ok") and "every few seconds" in str(res.get("error")):
                _queue_last_launch[0] = now - _QUEUE_MIN_GAP_S + 10   # a manual launch just ran; retry soon
                return None
            task["state"] = "launched" if res.get("ok") else "failed"
            task["finished_at"] = now
            task["pid"] = res.get("pid")
            task["error"] = res.get("error") or ""
            _queue_save(_queue_prune(tasks, now))
    if task.get("resume"):
        _limit_stop_set_state(task["resume"], "continued" if task["state"] == "launched" else "stopped", now)
    try:
        name = os.path.basename(task["cwd"]) or task["cwd"]
        if task["state"] == "launched":
            _show_native_toast(f"▶ Queued task started · {name}", task["prompt"][:120], kind="queue")
        else:
            _show_native_toast(f"⚠ Queued task failed · {name}", task["error"][:120], kind="queue")
    except Exception:
        pass
    return task


# ── Prompt library: reusable first messages for New session / the queue ───
# %LOCALAPPDATA%\AOC\prompts.json; {{name}} in a template becomes a field to
# fill in the New session form ({{date}} / {{yesterday}} fill themselves).
# Until you save your own, three starters are offered.
PROMPTS_FILE = os.path.join(AOC_DATA_DIR, "prompts.json")
_PROMPTS_MAX = 100
_prompts_lock = threading.Lock()
_PROMPT_STARTERS = [
    {"id": "starter-tests", "name": "Fix failing tests",
     "text": "Run the test suite. For every failing test, find the root cause and fix the code (not the test, unless the test is wrong -- then say why). Re-run until green and summarize what you changed.",
     "cwd": "", "model": ""},
    {"id": "starter-review", "name": "Review my uncommitted changes",
     "text": "Review the uncommitted changes in this repo (git diff) for bugs, missed edge cases and leftover debug code. List findings by severity with file:line. Don't change anything.",
     "cwd": "", "model": ""},
    {"id": "starter-audit", "name": "Dependency audit",
     "text": "Audit this project's dependencies for known vulnerabilities and outdated major versions ({{date}}). Report what to upgrade first and why. Don't upgrade anything yet.",
     "cwd": "", "model": "sonnet"},
]


def _sanitize_prompt(data, now=None):
    """Pure: POST /prompts body -> template dict, or (None, error)."""
    if not isinstance(data, dict):
        return None, "bad request"
    name = " ".join(str(data.get("name") or "").split())[:80]
    text = str(data.get("text") or "").strip()
    if not name:
        return None, "give the template a name"
    if not text:
        return None, "the template is empty"
    if len(text) > 20000:
        return None, "template too long (20,000 characters max)"
    model = str(data.get("model") or "").strip().lower()
    if model not in _LAUNCH_MODELS:
        model = ""
    tid = str(data.get("id") or "")
    if not re.match(r"^[A-Za-z0-9_-]{1,40}$", tid) or tid.startswith("starter-"):
        tid = _secrets.token_hex(5)
    return {"id": tid, "name": name, "text": text, "cwd": str(data.get("cwd") or "").strip()[:500], "model": model,
            "updated": int(now or time.time())}, None


def _prompts_load():
    data = _load_json_file(PROMPTS_FILE, None)
    if not isinstance(data, list):
        return [dict(p) for p in _PROMPT_STARTERS]
    return [p for p in data if isinstance(p, dict) and p.get("id") and p.get("text")]


def _prompts_save(items):
    os.makedirs(AOC_DATA_DIR, exist_ok=True)
    tmp = PROMPTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items[:_PROMPTS_MAX], f, ensure_ascii=False)
    os.replace(tmp, PROMPTS_FILE)


def _prompts_upsert(data, now=None) -> dict:
    tpl, err = _sanitize_prompt(data, now)
    if err:
        return {"ok": False, "error": err}
    with _prompts_lock:
        items = _prompts_load()
        same = [i for i, p in enumerate(items) if p["id"] == tpl["id"] or p["name"].lower() == tpl["name"].lower()]
        if same:
            old = items[same[0]]
            tpl["id"] = old["id"] if not str(old["id"]).startswith("starter-") else tpl["id"]
            tpl["used"] = old.get("used", 0)
            items[same[0]] = tpl
        else:
            if len(items) >= _PROMPTS_MAX:
                return {"ok": False, "error": f"the library is full ({_PROMPTS_MAX} templates)"}
            items.append(tpl)
        _prompts_save(items)
    return {"ok": True, "template": tpl}


def _prompts_delete(tid) -> bool:
    with _prompts_lock:
        items = _prompts_load()
        kept = [p for p in items if p["id"] != tid]
        if len(kept) == len(items):
            return False
        _prompts_save(kept)
        return True


def _prompts_used(tid, now=None) -> bool:
    """Count a use (the list is sorted most-used first)."""
    with _prompts_lock:
        items = _prompts_load()
        for p in items:
            if p["id"] == tid:
                p["used"] = int(p.get("used", 0)) + 1
                p["last_used"] = int(now or time.time())
                _prompts_save(items)
                return True
    return False


def _prompts_list():
    return sorted(_prompts_load(), key=lambda p: (-int(p.get("used", 0)), p["name"].lower()))


# ── Hard budget caps: stop a session's tool calls past a $ limit ───────────
# A per-session cap and a daily cap (all of today's Claude Code spend). The
# worker below re-evaluates every 10 s into _budget_state; Claude Code asks
# POST /hook/pretool (an HTTP PreToolUse hook, see setup.py) before every
# tool call and gets a "deny" with the reason once a cap is reached, so
# Claude stops and tells you where it got to. "Let it continue" on the
# dashboard gives that session one more cap's worth (or lifts today's cap).
BUDGET_CAPS_FILE = os.path.join(AOC_DATA_DIR, "budget_caps.json")
_BUDGET_MAX_USD = 100000.0
_budget_lock = threading.Lock()
_budget_state = {"day": None, "sessions": {}, "today_spend": 0.0, "at": 0.0}


def _sanitize_budget_caps(data) -> dict:
    data = data if isinstance(data, dict) else {}

    def usd(v):
        try:
            v = round(float(v), 2)
        except (TypeError, ValueError):
            return None
        return v if 0 < v <= _BUDGET_MAX_USD else None

    overrides = {}
    for sid, cap in list((data.get("overrides") or {}).items())[-200:] if isinstance(data.get("overrides"), dict) else []:
        c = usd(cap)
        if c and str(sid):
            overrides[str(sid)[:100]] = c
    day_override = str(data.get("day_override") or "")
    return {"session_usd": usd(data.get("session_usd")), "day_usd": usd(data.get("day_usd")),
            "overrides": overrides,
            "day_override": day_override if re.match(r"^\d{4}-\d\d-\d\d$", day_override) else ""}


def _load_budget_caps() -> dict:
    return _sanitize_budget_caps(_load_json_file(BUDGET_CAPS_FILE, {}))


def _save_budget_caps(data) -> dict:
    """Saves the fields present in `data`; the rest keep their stored value."""
    merged = _load_budget_caps()
    if isinstance(data, dict):
        merged.update({k: v for k, v in data.items() if k in ("session_usd", "day_usd", "overrides", "day_override")})
    cfg = _sanitize_budget_caps(merged)
    os.makedirs(AOC_DATA_DIR, exist_ok=True)
    tmp = BUDGET_CAPS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    os.replace(tmp, BUDGET_CAPS_FILE)
    return cfg


def _budget_verdicts(caps, sessions, today_spend, today_str) -> dict:
    """Pure: caps + [(session_id, cost)] + today's total -> which caps are
    reached: {"day": {...}|None, "sessions": {sid: {...}}}."""
    day = None
    if caps.get("day_usd") and today_spend >= caps["day_usd"] and caps.get("day_override") != today_str:
        day = {"kind": "day", "spent": round(today_spend, 2), "cap": caps["day_usd"]}
    out = {}
    for sid, cost in sessions:
        cap = caps.get("overrides", {}).get(sid) or caps.get("session_usd")
        if cap and (cost or 0) >= cap:
            out[sid] = {"kind": "session", "spent": round(cost or 0, 2), "cap": cap}
    return {"day": day, "sessions": out}


def _budget_reason(v) -> str:
    """Pure: what Claude is told when a tool call is denied."""
    what = (f"this session has spent ${v['spent']:.2f} of its ${v['cap']:.2f} budget cap" if v["kind"] == "session"
            else f"today's Claude Code spend is ${v['spent']:.2f}, over the ${v['cap']:.2f} daily cap")
    return (f"AOC budget cap reached: {what}. Do not call any more tools. Stop now and tell the user "
            "briefly what is done and what is left; they can let the session continue from the AOC dashboard.")


def _budget_hook_reply(state, hook) -> dict:
    """Pure: POST /hook/pretool body (Claude Code's PreToolUse input) ->
    {} (go ahead) or the deny decision."""
    if not isinstance(hook, dict) or hook.get("hook_event_name", "PreToolUse") != "PreToolUse":
        return {}
    v = (state.get("sessions") or {}).get(str(hook.get("session_id") or "")) or state.get("day")
    if not v:
        return {}
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": _budget_reason(v)}}


def _budget_continue(data, caps, state, today_str):
    """Pure: POST /budget_caps/continue {session_id} -> (new caps, ok).
    A session over its cap gets one more cap's worth on top of what it has
    spent; the daily cap is lifted for the rest of today."""
    caps = {**caps, "overrides": dict(caps.get("overrides") or {})}
    sid = str((data or {}).get("session_id") or "")
    ok = False
    v = (state.get("sessions") or {}).get(sid)
    if v and caps.get("session_usd"):
        caps["overrides"][sid] = round(v["spent"] + caps["session_usd"], 2)
        ok = True
    if state.get("day") or (data or {}).get("day"):
        caps["day_override"] = today_str
        ok = True
    return caps, ok


def _budget_today_spend(live, today_str) -> float:
    """Today's Claude Code spend: sessions.cost of today's rows, with live
    sessions' current cost in place of their (older) saved value."""
    live = dict(live)
    total = 0.0
    with _db_lock:
        try:
            c = _db_conn()
            rows = c.execute("SELECT id, cost FROM sessions WHERE date = ?", (today_str,)).fetchall()
            c.close()
        except Exception:
            rows = []
    for r in rows:
        if r["id"] not in live:
            total += r["cost"] or 0
    return total + sum(v or 0 for v in live.values())


def _budget_tick(now=None):
    now = now or time.time()
    caps = _load_budget_caps()
    d = _own_status_payload() or {}
    sessions = [(s["id"], s.get("estimated_cost") or 0) for s in d.get("sessions_list") or [] if s.get("session_active") is not False]
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_spend = _budget_today_spend(sessions, today_str) if caps.get("day_usd") else 0.0
    v = _budget_verdicts(caps, sessions, today_spend, today_str)
    with _budget_lock:
        prev = dict(_budget_state)
        _budget_state.update(v, today_spend=round(today_spend, 2), at=now)
    names = {s["id"]: s.get("display_name") or s.get("project") or "Session" for s in d.get("sessions_list") or []}
    new = [(sid, x) for sid, x in v["sessions"].items() if sid not in (prev.get("sessions") or {})]
    if v["day"] and not prev.get("day"):
        new.append(("", v["day"]))
    for sid, x in new:
        title = f"⛔ Budget cap · {names.get(sid, 'Session')[:40]}" if sid else "⛔ Daily budget cap reached"
        body = f"${x['spent']:.2f} of ${x['cap']:.2f} -- tool calls are blocked until you let it continue"
        try:
            _show_native_toast(title, body, kind="budget_cap")
        except Exception:
            pass
        _notify_webhook("budget_cap", {"event": "budget_cap", "kind": x["kind"], "session_id": sid or None,
                                       "display_name": names.get(sid) if sid else None,
                                       "spent": x["spent"], "cap": x["cap"]})


def _budget_worker():
    time.sleep(20)
    while True:
        try:
            _budget_tick()
        except Exception as e:
            _log_bg_error("budget_worker", e)
        time.sleep(10)


threading.Thread(target=_budget_worker, daemon=True, name="budget-worker").start()


# ── Dangerous-command guard: your OK before Claude runs a destructive command ─
# A second HTTP PreToolUse hook (shell tools only, see setup.py) asks POST
# /hook/danger before every Bash / PowerShell call. A harmless command is
# answered at once ({} = carry on as usual). A dangerous one -- force push,
# git reset --hard, rm -rf / Remove-Item -Recurse on a broad path, DROP /
# TRUNCATE / DELETE without WHERE, wiping a disk or a dev database -- is held
# as a "Needs your OK" prompt on the dashboard and your phone, whatever
# permission mode the session runs in (auto / bypass included). Allow runs
# it, Deny tells Claude why, "Answer in terminal" hands it to Claude Code's
# own dialog; no answer within hold_min minutes = denied. If AOC is not
# running the hook fails open, like the budget hook.
DANGER_GUARD_FILE = os.path.join(AOC_DATA_DIR, "danger_guard.json")
_DANGER_MODES = ("hold", "off")
_DANGER_HOLD_MAX_MIN = 60          # setup.py's hook timeout (3700 s) must stay above this
_DANGER_MAX_PATTERNS = 50
_DANGER_RULES = (
    ("force_push", "Force push", "git push --force / -f / +branch: rewrites history others may have"),
    ("git_discard", "Discard uncommitted work", "git reset --hard, git clean -f, git checkout -- . / git restore ., git stash clear"),
    ("remote_branch_delete", "Delete a remote branch", "git push --delete / git push origin :branch"),
    ("rm_broad", "Recursive delete of a broad path", "rm -r, Remove-Item -Recurse, rd /s on /, ~, a drive, a folder near the root or home, ., .., * or $VARIABLE/"),
    ("sql_destroy", "Destroy database data", "DROP TABLE/DATABASE/SCHEMA, TRUNCATE, DELETE FROM without WHERE (also inside python -c, psql, sqlite3 ...)"),
    ("db_reset", "Reset a dev database", "prisma migrate reset, prisma db push --force-reset, dropdb, supabase db reset, docker compose down -v, docker volume rm/prune"),
    ("disk_wipe", "Format or overwrite a disk", "format X:, diskpart, mkfs, dd of=/dev/..., Clear-Disk, Format-Volume"),
)
_DANGER_RULE_IDS = tuple(r[0] for r in _DANGER_RULES)
# heads whose arguments are only text -- a commit message about "DROP TABLE" is fine
_DANGER_TEXT_HEADS = {"git", "echo", "printf", "write-host", "write-output", "grep", "rg", "findstr",
                      "select-string", "gh", "cat", "type", "less", "head", "tail", "sls"}
_DANGER_RM_HEADS = {"rm", "remove-item", "ri", "del", "erase", "rd", "rmdir"}
_DANGER_SQL = (
    re.compile(r"\bdrop\s+(table|database|schema)\b", re.I),
    re.compile(r"\btruncate\s+(table\s+)?[\w.\"`\[]", re.I),
    re.compile(r"\bdelete\s+from\s+[\w.\"`\[\]]+(?![^;'\"]*\bwhere\b)", re.I),
)
_danger_cfg_cache = {"mtime": None, "cfg": None}


def _sanitize_danger_guard(data) -> dict:
    data = data if isinstance(data, dict) else {}
    mode = data.get("mode") if data.get("mode") in _DANGER_MODES else "hold"
    try:
        hold_min = int(data.get("hold_min", 10))
    except (TypeError, ValueError):
        hold_min = 10
    off = data.get("off_rules") if isinstance(data.get("off_rules"), list) else []
    pats = []
    for p in data.get("patterns") if isinstance(data.get("patterns"), list) else []:
        p = str(p or "").strip()[:200]
        if not p or p in pats:
            continue
        try:
            re.compile(p)
        except re.error:
            continue
        pats.append(p)
    return {"mode": mode, "hold_min": max(1, min(hold_min, _DANGER_HOLD_MAX_MIN)),
            "off_rules": [r for r in _DANGER_RULE_IDS if r in off],
            "patterns": pats[:_DANGER_MAX_PATTERNS]}


def _load_danger_guard() -> dict:
    """Read on every shell tool call, so cached by the file's mtime."""
    try:
        mtime = os.path.getmtime(DANGER_GUARD_FILE)
    except OSError:
        mtime = None
    if _danger_cfg_cache["cfg"] is None or _danger_cfg_cache["mtime"] != mtime:
        _danger_cfg_cache.update(mtime=mtime, cfg=_sanitize_danger_guard(_load_json_file(DANGER_GUARD_FILE, {})))
    return _danger_cfg_cache["cfg"]


def _save_danger_guard(data) -> dict:
    """Saves the fields present in `data`; the rest keep their stored value.
    Patterns that don't compile are reported back in "rejected"."""
    merged = dict(_load_danger_guard())
    rejected = []
    if isinstance(data, dict):
        merged.update({k: v for k, v in data.items() if k in ("mode", "hold_min", "off_rules", "patterns")})
        for p in data.get("patterns") or [] if isinstance(data.get("patterns"), list) else []:
            try:
                re.compile(str(p))
            except re.error:
                rejected.append(str(p)[:200])
    cfg = _sanitize_danger_guard(merged)
    os.makedirs(AOC_DATA_DIR, exist_ok=True)
    tmp = DANGER_GUARD_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    os.replace(tmp, DANGER_GUARD_FILE)
    _danger_cfg_cache.update(mtime=None, cfg=None)
    return {**cfg, "rejected": rejected}


def _danger_unquote(w) -> str:
    w = str(w)
    if len(w) >= 2 and w[0] == w[-1] and w[0] in "'\"":
        w = w[1:-1]
    return w


def _danger_broad_path(p) -> bool:
    """Pure: is deleting this path recursively a disaster? Root, a drive,
    home, anything at most two levels under a drive/root or one under home,
    the current/parent folder, a bare wildcard, or $VARIABLE/ (an empty
    variable = deletes from /)."""
    p = _danger_unquote(p).strip().replace("\\", "/")
    if not p:
        return False
    if re.match(r"^\$(\{\w+\}|\w+|env:\w+)/\*?$", p, re.I):
        return True
    p = re.sub(r"/\*$", "", p.rstrip("/")) or "/"
    if p.lower() in ("/", "*", ".", "..", "~", "$home", "${home}", "$env:userprofile"):
        return True
    if all(c in (".", "..", "*", "") for c in p.split("/")):
        return True
    m = re.match(r"^(~|\$home|\$\{home\}|\$env:userprofile)(/.*)?$", p, re.I)
    if m:
        return len([c for c in (m.group(2) or "").split("/") if c]) <= 1
    m = re.match(r"^([a-z]:|/[a-z](?=/|$)|/mnt/[a-z](?=/|$))(/.*)?$", p, re.I)
    if m:
        rest = [c for c in (m.group(2) or "").split("/") if c]
        return len(rest) <= (3 if rest[:1] and rest[0].lower() == "users" else 2)
    if p.startswith("/"):
        comps = [c for c in p.split("/") if c]
        return len(comps) <= (3 if comps[:1] == ["home"] else 1)
    return False


def _danger_rm_hit(words) -> bool:
    """Pure: a simple command that recursively deletes a broad path."""
    head = _perm_head(words[0])
    if head == "sudo" and len(words) > 1:
        return _danger_rm_hit(words[1:])
    if head not in _DANGER_RM_HEADS:
        return False
    cmdish = head in ("rd", "rmdir", "del", "erase")
    recursive, targets, skip = False, [], False
    for w in words[1:]:
        u = _danger_unquote(w)
        lw = u.lower()
        if skip:
            skip = False
            continue
        if cmdish and re.match(r"^/[a-z]$", lw):
            recursive = recursive or lw == "/s"
            continue
        if lw.startswith("--"):
            recursive = recursive or lw == "--recursive"
            continue
        if lw.startswith("-") and len(lw) > 1:
            if head == "rm" and re.match(r"^-[a-z]{1,4}$", lw) and "r" in lw:
                recursive = True
            elif "-recurse".startswith(lw) and len(lw) >= 2:
                recursive = True
            elif lw in ("-filter", "-include", "-exclude"):
                skip = True
            continue
        targets.append(u)
    return recursive and any(_danger_broad_path(t) for t in targets)


def _danger_git_hits(words) -> list:
    """Pure: built-in rule ids a git simple command trips."""
    if _perm_head(words[0]) != "git":
        return []
    args = [_danger_unquote(w) for w in words[1:]]
    while args[:1] and args[0] in ("-C", "-c", "--git-dir", "--work-tree") and len(args) > 1:
        args = args[2:]
    if not args:
        return []
    sub, rest = args[0].lower(), args[1:]
    hits = []
    if sub == "push":
        if any(a in ("-f", "--force", "--force-with-lease", "--force-if-includes", "--mirror") or a.startswith("--force-with-lease=")
               or (re.match(r"^-[a-z]+$", a) and "f" in a[1:]) for a in rest) \
                or any(a.startswith("+") for a in rest if not a.startswith("-")):
            hits.append("force_push")
        pos = [a for a in rest if not a.startswith("-")]
        if any(a in ("-d", "--delete") for a in rest) or any(a.startswith(":") and len(a) > 1 for a in pos[1:]):
            hits.append("remote_branch_delete")
    elif sub == "reset" and "--hard" in rest:
        hits.append("git_discard")
    elif sub == "clean" and any(re.match(r"^-[a-z]*f", a, re.I) or a == "--force" for a in rest):
        hits.append("git_discard")
    elif sub in ("checkout", "restore") and "--staged" not in rest and any(a in (".", ":/", "*") for a in rest) \
            and (sub == "restore" or "--" in rest):
        hits.append("git_discard")
    elif sub == "stash" and rest[:1] == ["clear"]:
        hits.append("git_discard")
    return hits


def _danger_db_hit(words) -> bool:
    w = [_danger_unquote(x).lower() for x in words]
    head = _perm_head(w[0])
    if head in ("npx", "pnpm", "yarn", "bunx", "pnpx") and len(w) > 1:
        w, head = w[1:], _perm_head(w[1])
    j = " ".join(w[1:])
    if head == "prisma":
        return bool(re.search(r"\bmigrate\s+reset\b", j) or "--force-reset" in w)
    if head == "dropdb":
        return True
    if head == "supabase":
        return bool(re.search(r"\bdb\s+reset\b", j))
    if head in ("docker", "docker-compose", "podman"):
        return bool(re.search(r"\bdown\b.*(\s|^)(-v|--volumes)\b", j) or re.search(r"\bvolume\s+(rm|prune)\b", j)
                    or re.search(r"\bsystem\s+prune\b.*--volumes", j))
    return False


def _danger_disk_hit(words) -> bool:
    w = [_danger_unquote(x).lower() for x in words]
    head = _perm_head(w[0])
    if head == "format" and any(re.match(r"^[a-z]:$", a) for a in w[1:]):
        return True
    if head in ("diskpart", "clear-disk", "format-volume", "initialize-disk") or head.startswith("mkfs"):
        return True
    return head == "dd" and any(a.startswith("of=/dev/") for a in w[1:])


def _danger_check(command, shell, cfg) -> list:
    """Pure: a shell command -> [{"id", "label", "why", "part"}] of the rules
    it trips (empty = harmless). Quote-aware: each simple command of a chain
    is judged on its own, and text arguments (commit messages, echo, grep)
    never count as SQL."""
    command = str(command or "")
    if cfg.get("mode") == "off" or not command.strip():
        return []
    off = set(cfg.get("off_rules") or [])
    simple, _script = _perm_simple_commands(command, shell)
    found = {}

    def add(rid, part):
        if rid not in off and rid not in found:
            r = next(x for x in _DANGER_RULES if x[0] == rid)
            found[rid] = {"id": rid, "label": r[1], "why": r[2], "part": part[:300]}

    texts = []
    for words in simple:
        if not words:
            continue
        part = " ".join(words)
        for rid in _danger_git_hits(words):
            add(rid, part)
        if _danger_rm_hit(words):
            add("rm_broad", part)
        if _danger_db_hit(words):
            add("db_reset", part)
        if _danger_disk_hit(words):
            add("disk_wipe", part)
        if _perm_head(words[0]) not in _DANGER_TEXT_HEADS:
            texts.append(part)
    # heredoc bodies were dropped by the splitter -- SQL piped into psql /
    # sqlite3 / python lives there
    for m in re.finditer(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n(.*?)\n\s*\2\s*(?=\n|$)", command, re.S):
        feeder = _perm_simple_commands(command[command.rfind("\n", 0, m.start()) + 1:m.start()], shell)[0]
        if not feeder or _perm_head(feeder[-1][0]) not in _DANGER_TEXT_HEADS:   # cat <<EOF > notes.md is text
            texts.append(m.group(3))
    for t in texts:
        if any(rx.search(t) for rx in _DANGER_SQL):
            add("sql_destroy", t)
            break
    out = list(found.values())
    for p in cfg.get("patterns") or []:
        try:
            if re.search(p, command, re.I):
                out.append({"id": "pattern", "label": "Your pattern", "why": p, "part": command[:300]})
                break
        except re.error:
            continue
    return out


def _danger_reply(decision, reason) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                   "permissionDecisionReason": reason}}


def _danger_open(hook, hits, now) -> str:
    """Add a held dangerous command to the remote-approve prompts (shown as
    "Needs your OK" on the session) and tell the phone. Returns its id."""
    tool = str(hook.get("tool_name") or "Bash")
    ti = hook.get("tool_input") if isinstance(hook.get("tool_input"), dict) else {}
    cmd = str(ti.get("command") or "")
    sid = str(hook.get("session_id") or "")
    why = "; ".join(h["label"] for h in hits)
    detail = (str(ti.get("description") or "") + "\n\n" if ti.get("description") else "") + \
        "AOC holds this because: " + "; ".join(f"{h['label']} ({h['why']})" for h in hits)
    rid = _secrets.token_hex(8)
    with _perm_lock:
        _perm_expire(now)
        _perm_requests[rid] = {
            "id": rid, "session_id": sid, "tool_name": tool, "summary": f"{tool}: {cmd}".strip()[:300],
            "detail": detail[:2000], "created": now, "last_poll": now, "state": "pending", "message": "",
            "danger": why[:200], "cwd": str(hook.get("cwd") or "")[:300],
        }
    with _status_cond:
        _status_version[0] += 1
        _status_cond.notify_all()
    name = _push_session_name(sid, hook.get("cwd"))
    try:
        _push_notify(f"⚠ {name}: dangerous command", f"{why} -- {cmd[:160]}", tag="perm-" + rid, urgent=True)
    except Exception as e:
        _log_bg_error("push", e)
    try:
        _show_native_toast(f"⚠ {name[:40]} wants to run a dangerous command", f"{why}: {cmd[:120]} -- Allow or Deny in AOC")
    except Exception:
        pass
    return rid


def _danger_wait(rid, hold_s, now_fn=time.time, sleep_fn=time.sleep, gone_fn=lambda: False):
    """Hold until the dashboard answers: -> (state, message). state is
    allow | deny | release, "timeout", or "gone" (Claude Code hung up --
    Esc, session closed)."""
    deadline = now_fn() + hold_s
    while True:
        now = now_fn()
        with _perm_lock:
            req = _perm_requests.get(rid)
            if req is None:
                return "gone", ""
            if req["state"] != "pending":
                del _perm_requests[rid]
                return req["state"], req["message"]
            if now >= deadline or gone_fn():
                del _perm_requests[rid]
                return ("timeout" if now >= deadline else "gone"), ""
            req["last_poll"] = now
        sleep_fn(0.5)


def _danger_decision(state, message, hits, hold_min) -> dict:
    """Pure: the hold's outcome -> the PreToolUse reply for Claude Code."""
    what = "; ".join(h["label"] for h in hits) or "dangerous command"
    if state == "allow":
        return _danger_reply("allow", f"AOC: the user approved this command ({what}).")
    if state == "release":
        return _danger_reply("ask", f"AOC flagged this as dangerous ({what}); the user wants to decide here.")
    if state == "deny":
        msg = f" They said: {message}" if message else ""
        return _danger_reply("deny", f"AOC: the user denied this dangerous command ({what}).{msg} "
                                     "Do not run it in another form; ask the user how to go on.")
    if state == "timeout":
        return _danger_reply("deny", f"AOC held this dangerous command ({what}) for the user's OK for {hold_min} min "
                                     "and got no answer, so it was not run. Do not retry it in another form; stop, "
                                     "tell the user what you wanted to run and why, and wait for them.")
    return {}


def _danger_hook(hook, cfg=None, wait=_danger_wait, now=None, gone_fn=lambda: False) -> dict:
    """POST /hook/danger body (Claude Code's PreToolUse input) -> {} for a
    harmless command, else holds and returns allow / deny / ask."""
    if not isinstance(hook, dict) or hook.get("hook_event_name", "PreToolUse") != "PreToolUse":
        return {}
    tool = str(hook.get("tool_name") or "")
    if tool not in ("Bash", "PowerShell"):
        return {}
    cfg = cfg or _load_danger_guard()
    ti = hook.get("tool_input") if isinstance(hook.get("tool_input"), dict) else {}
    hits = _danger_check(ti.get("command"), tool, cfg)
    if not hits:
        return {}
    t0 = now or time.time()
    rid = _danger_open(hook, hits, t0)
    state, message = wait(rid, cfg["hold_min"] * 60, gone_fn=gone_fn)
    try:
        _danger_log_append(_danger_log_entry(hook, hits, state, t0, time.time() - t0))
    except Exception as e:
        _log_bg_error("danger log", e)
    return _danger_decision(state, message, hits, cfg["hold_min"])


# Every held command and how it ended, newest last (Settings -> Notify ->
# DANGEROUS COMMANDS shows the recent ones). Trimmed to the newest
# _DANGER_LOG_KEEP lines once it passes twice that.
DANGER_LOG_FILE = os.path.join(AOC_DATA_DIR, "danger_log.jsonl")
_DANGER_LOG_KEEP = 500
_DANGER_OUTCOMES = {"allow": "allowed", "deny": "denied", "release": "answered in terminal",
                    "timeout": "no answer, denied", "gone": "cancelled"}
_danger_log_lock = threading.Lock()


def _danger_log_entry(hook, hits, state, at, held_s) -> dict:
    """Pure: one log line for a held command."""
    ti = hook.get("tool_input") if isinstance(hook.get("tool_input"), dict) else {}
    return {"t": round(at, 1), "sid": str(hook.get("session_id") or "")[:64], "cwd": str(hook.get("cwd") or "")[:300],
            "tool": str(hook.get("tool_name") or "")[:20], "cmd": str(ti.get("command") or "")[:300],
            "rules": [h["label"] for h in hits], "outcome": _DANGER_OUTCOMES.get(state, str(state)),
            "held_s": round(max(0.0, held_s), 1)}


def _danger_log_append(entry, path=None) -> None:
    path = path or DANGER_LOG_FILE
    with _danger_log_lock:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > 2 * _DANGER_LOG_KEEP:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines[-_DANGER_LOG_KEEP:])


def _danger_log_read(limit=30, path=None) -> list:
    """Newest first; a broken line is skipped."""
    try:
        with open(path or DANGER_LOG_FILE, encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _danger_test(command, shell, cfg) -> dict:
    """Pure: POST /danger_guard/test -- what the guard would do with a
    command, without holding or running anything. Judged as if the guard
    were on, so it also answers "would this be caught?" while it is off."""
    shell = shell if shell in ("Bash", "PowerShell") else "Bash"
    hits = _danger_check(command, shell, {**cfg, "mode": "hold"})
    return {"hits": hits, "would_hold": bool(hits) and cfg.get("mode") != "off", "mode": cfg.get("mode")}


def _danger_hook_installed(settings) -> bool:
    """Pure: is AOC's /hook/danger PreToolUse entry in ~/.claude/settings.json?"""
    return "/hook/danger" in json.dumps(((settings or {}).get("hooks") or {}).get("PreToolUse") or [])


def _danger_view(cfg, settings) -> dict:
    """GET /danger_guard: settings + the built-in rules + hook state."""
    return {**cfg, "rules": [{"id": r[0], "label": r[1], "why": r[2]} for r in _DANGER_RULES],
            "hook_installed": _danger_hook_installed(settings)}


def _socket_hung_up(sock) -> bool:
    """The request body is read, so a readable socket means the client
    closed (or reset) the connection."""
    import select as _select
    try:
        r, _, _ = _select.select([sock], [], [], 0)
        return bool(r) and sock.recv(1, _socket.MSG_PEEK) == b""
    except Exception:
        return True


# ── Stopped by the usage limit: continue that exact conversation ─────────
# When the 5-hour / weekly plan limit hits, Claude Code writes a synthetic
# assistant line (error "rate_limit", quotaLimits.status "rejected" with
# resetsAt) and the session just sits there. The transcript scanner records
# it per session in %LOCALAPPDATA%\AOC\limit_stops.json (scanned files are
# only the last 30 min's, so it has to outlive that); a later prompt or a
# real reply in the same transcript clears it. Today + Inbox list them:
# after the reset one click resumes the conversation (`claude --resume <id>
# "pokračuj"`); before it, an explicit per-session opt-in queues exactly
# that for the reset time (the task queue's one-a-minute slot and limit
# check apply). A session still open in its terminal is never resumed a
# second time -- that would fork the conversation -- it gets "type it there".
LIMIT_STOPS_FILE = os.path.join(AOC_DATA_DIR, "limit_stops.json")
_LIMIT_CONTINUE_PROMPT = "pokračuj"
_LIMIT_STOP_KEEP_S = 3 * 86400     # after the reset (or the stop, reset unknown)
_LIMIT_STOP_MAX = 10
_limit_stops_lock = threading.Lock()
_LOCAL_CMD_RE = re.compile(r"^\s*<(command-name|command-message|local-command-stdout|local-command-caveat)>")


def _limit_stop_event(obj):
    """Pure: one transcript line -> ("stop", {kind, resets_at, at}),
    ("clear", None) when the conversation went on, or (None, None)."""
    if not isinstance(obj, dict):
        return None, None
    t = obj.get("type")
    ql = obj.get("quotaLimits") if isinstance(obj.get("quotaLimits"), dict) else {}
    if t == "assistant" and (obj.get("error") == "rate_limit" or ql.get("status") == "rejected"):
        r = ql.get("resetsAt")
        return "stop", {"kind": str(ql.get("rateLimitType") or ""),
                        "resets_at": int(r) if isinstance(r, (int, float)) and not isinstance(r, bool) and r > 0 else None,
                        "at": _iso_to_epoch(obj.get("timestamp"))}
    if obj.get("isSidechain") or obj.get("isMeta"):
        return None, None
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    if t == "assistant" and msg.get("model") and msg.get("model") != "<synthetic>" and not obj.get("isApiErrorMessage"):
        return "clear", None
    if t == "user":
        c = msg.get("content")
        texts = [c] if isinstance(c, str) else [b.get("text") for b in c if isinstance(b, dict) and b.get("type") == "text"] \
            if isinstance(c, list) else []
        if any(isinstance(x, str) and x.strip() and not _LOCAL_CMD_RE.match(x) for x in texts):
            return "clear", None   # you typed something (a /command checking the limit doesn't count)
    return None, None


def _limit_stops_apply(stops, sid, event, info, cwd, title, now):
    """Pure: the stops dict after one session's latest event. A stop you
    dismissed / continued stays so until a NEWER stop arrives."""
    stops = dict(stops or {})
    if event == "clear":
        stops.pop(sid, None)
    elif event == "stop" and sid:
        prev = stops.get(sid) or {}
        at = info.get("at") or now
        if prev and (prev.get("at") or 0) >= at:
            if title and not prev.get("title"):
                stops[sid] = {**prev, "title": title}
            return stops
        stops[sid] = {"id": sid, "cwd": cwd or prev.get("cwd") or "", "title": title or prev.get("title") or "",
                      "kind": info.get("kind") or "", "resets_at": info.get("resets_at"), "at": at,
                      "state": "stopped", "task": None}
    return stops


def _limit_stops_prune(stops, now):
    """Pure: drop stops whose reset (or, unknown, the stop) is days old."""
    return {k: v for k, v in (stops or {}).items()
            if isinstance(v, dict) and now - (v.get("resets_at") or v.get("at") or 0) < _LIMIT_STOP_KEEP_S}


def _limit_stops_view(stops, open_ids, queue, view, now):
    """Pure: the /status 'limit_stops' list -- stopped (or queued to
    continue) sessions, newest first. ready = the limit has reset (unknown
    reset time: the plan-limit view says there's room); auto = a queued
    auto-continue task is still waiting."""
    queued = {t.get("id") for t in (queue or []) if t.get("state") == "queued"}
    out = []
    for v in _limit_stops_prune(stops, now).values():
        if v.get("state") not in ("stopped", "queued"):
            continue
        r = v.get("resets_at")
        out.append({"id": v["id"], "cwd": v.get("cwd") or "", "title": v.get("title") or "",
                    "kind": v.get("kind") or "", "resets_at": r, "at": v.get("at"),
                    "ready": (now >= r) if r else _queue_limits_ok(view),
                    "open": v["id"] in (open_ids or ()),
                    "auto": v.get("state") == "queued" and v.get("task") in queued, "task": v.get("task")})
    out.sort(key=lambda x: -(x["at"] or 0))
    return out[:_LIMIT_STOP_MAX]


def _limit_stops_load():
    data = _load_json_file(LIMIT_STOPS_FILE, {})
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def _limit_stops_save(stops):
    os.makedirs(AOC_DATA_DIR, exist_ok=True)
    tmp = LIMIT_STOPS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(stops, f, ensure_ascii=False)
    os.replace(tmp, LIMIT_STOPS_FILE)


def _limit_stops_changed():
    """Wake /events clients (call it outside _limit_stops_lock)."""
    with _status_cond:
        _status_version[0] += 1
        _status_cond.notify_all()


def _limit_stop_record(sid, event, info, cwd, title, now=None):
    """Scanner hook: one session's last stop/clear event of this pass."""
    now = now or time.time()
    with _limit_stops_lock:
        old = _limit_stops_load()
        new = _limit_stops_prune(_limit_stops_apply(old, sid, event, info or {}, cwd, title, now), now)
        changed = new != old
        if changed:
            _limit_stops_save(new)
    if changed:
        _limit_stops_changed()


def _limit_stop_set_state(sid, state, now=None, task=None):
    now = now or time.time()
    with _limit_stops_lock:
        stops = _limit_stops_load()
        if sid not in stops:
            return False
        stops[sid] = {**stops[sid], "state": state, "task": task}
        _limit_stops_save(_limit_stops_prune(stops, now))
    _limit_stops_changed()
    return True


def _open_session_ids():
    """Ids of Claude Code sessions open right now (from the cached /status)."""
    p = _own_status_payload() or {}
    return {s.get("id") for s in p.get("sessions_list") or [] if s.get("session_active") is not False}


def _limit_stop_action(data, now=None, launch=None, open_ids=None) -> dict:
    """POST /limit_stop {id, action: continue | auto_on | auto_off | dismiss}."""
    now = now or time.time()
    sid, action = str(data.get("id") or ""), str(data.get("action") or "")
    with _limit_stops_lock:
        stop = _limit_stops_load().get(sid)
    if not stop:
        return {"ok": False, "error": "that stop is gone already"}
    open_ids = _open_session_ids() if open_ids is None else open_ids
    if action in ("continue", "auto_on") and sid in open_ids:
        return {"ok": False, "error": "still open in a terminal -- type pokračuj there"}
    req = {"cwd": stop.get("cwd"), "prompt": _LIMIT_CONTINUE_PROMPT, "resume": sid, "keep_prompt": True}
    if action == "continue":
        res = (launch or _launch_session)(req, now=now)
        if res.get("ok"):
            if stop.get("task"):
                _queue_cancel(stop["task"], now)
            _limit_stop_set_state(sid, "continued", now)
        return res
    if action == "auto_on":
        r = stop.get("resets_at")
        res = _queue_add({**req, "at": r if r and r > now else None, "limits": True}, now)
        if res.get("ok"):
            _limit_stop_set_state(sid, "queued", now, task=res["task"]["id"])
        return res
    if action in ("auto_off", "dismiss"):
        if stop.get("task"):
            _queue_cancel(stop["task"], now)
        _limit_stop_set_state(sid, "stopped" if action == "auto_off" else "dismissed", now)
        return {"ok": True}
    return {"ok": False, "error": "unknown action"}


def _queue_worker():
    time.sleep(_STARTUP_GRACE_S / 6)
    while True:
        try:
            _queue_tick()
        except Exception as e:
            _log_bg_error("queue_worker", e)
        time.sleep(20)


threading.Thread(target=_queue_worker, daemon=True, name="queue-worker").start()
