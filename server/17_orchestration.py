# AOC server, part 17: orchestration (a goal -> a plan of agents -> DAG run -> review -> merge).
# monitor.py exec()s server/NN_*.py in number order into ONE shared
# namespace (its own) -- not an importable module: names from earlier
# parts are plain globals here, and __file__ is monitor.py.
# ── Orchestration: one goal, a plan of specialised agents, run as a DAG ────
# You give AOC a goal and a repo. An orchestrator (`claude -p`, read-only
# tools) writes a plan: a few tasks, each with a role (research / analyst /
# developer / qa), exact instructions, a done criterion, its dependencies, a
# model and a budget. You check / edit the plan and approve it. Then AOC runs
# the tasks as headless `claude -p` agents -- independent ones side by side,
# dependent ones after their inputs -- each in its own git worktree (on a
# branch that already has its inputs' commits), with only its role's tools,
# answering in a fixed JSON contract. A failed or invalid answer is retried
# once with the error. When nothing is left to run, the orchestrator reviews
# the results and finishes (report), retries a task with feedback or adds
# tasks (at most _ORCH_MAX_ROUNDS times). You approve the merge of the
# developers' branches into the branch the run started from.
#
# Every process (planner, supervisor, agents) writes its answer to a file
# under %LOCALAPPDATA%\AOC\orch\<run>\ and is polled by pid, so a monitor
# reload in the middle of a run loses nothing. State, outputs, errors,
# approvals and costs live in orchestration.db. Agents keep AOC's hooks: they
# show up as sessions and the danger guard still holds destructive commands
# for your OK. Mutations only from this PC (like worktrees).
#
# Hierarchical mode: the top plan may also have *manager* tasks -- a
# workstream (backend, frontend, docs ...) with its own goal and budget. When
# a workstream is due, AOC gives it a branch (the base + its inputs' work) and
# starts a child run on it: the manager plans its own team, the team runs on
# the same engine, the manager reviews it, and AOC merges the team's branches
# into the workstream branch. That branch is the manager task's result, and
# the top orchestrator reviews the managers as it does agents. One level of
# managers only. You approve the top plan (team plans are approved by it,
# unless the run asks you to approve each one) and the final merge.
ORCH_DB = os.path.join(AOC_DATA_DIR, "orchestration.db")
ORCH_DIR = os.path.join(AOC_DATA_DIR, "orch")
_ORCH_ROLES = {
    # role: (label, tools it has, may it change files)
    "research":  ("Research",  ["Read", "Grep", "Glob", "WebSearch", "WebFetch"], False),
    "analyst":   ("Analyst",   ["Read", "Grep", "Glob"], False),
    "developer": ("Developer", ["Read", "Grep", "Glob", "Edit", "Write", "Bash", "PowerShell"], True),
    "qa":        ("QA",        ["Read", "Grep", "Glob", "Bash", "PowerShell"], False),
    # not an agent: a child run with its own team (hierarchical mode only)
    "manager":   ("Manager",   [], False),
}
_ORCH_AGENT_ROLES = ("research", "analyst", "developer", "qa")
_ORCH_ROLE_BRIEF = {
    "research": "You are the research agent of a small team. Find facts (in the repo and on the web) and report them "
                "with sources. You cannot change files.",
    "analyst": "You are the analyst agent of a small team. Read the code and the inputs you are given, reason about "
               "them and report findings and recommendations. You cannot change files.",
    "developer": "You are the developer agent of a small team. Make the change in this folder (a git worktree on its "
                 "own branch). Keep it focused on your task. Run the relevant tests if there are any. You do not need "
                 "to commit: AOC commits what you leave in the folder.",
    "qa": "You are the QA agent of a small team. Check the work in this folder against the done criteria: run the "
          "tests, read the diff (git diff against the base, git log), try edge cases. Do not fix anything; report "
          "what passes and what fails, precisely. Answer status 'failed' if the criteria are not met.",
}
_ORCH_MODELS = ("haiku", "sonnet", "opus")
_ORCH_STATES = ("scheduled", "planning", "plan_ready", "running", "supervising", "review", "merged", "discarded", "cancelled", "failed")
_ORCH_ACTIVE = ("planning", "running", "supervising")
_ORCH_MAX_TASKS = 8              # in the first plan
_ORCH_MAX_TASKS_TOTAL = 12       # incl. what the supervisor adds
_ORCH_MAX_ATTEMPTS = 2           # a failed attempt is retried by itself once
_ORCH_MAX_ATTEMPTS_REVIEWED = 3  # ... and once more if the orchestrator's review asks
_ORCH_MAX_ROUNDS = 3             # supervisor reviews per run
_ORCH_MAX_PARALLEL = 4
_ORCH_TASK_TIMEOUT_S = 30 * 60
_ORCH_LLM_TIMEOUT_S = 10 * 60
_ORCH_TASK_BUDGET = (0.1, 5.0)   # $ per task, clamped
_ORCH_MANAGER_BUDGET = (0.5, 20.0)   # $ per workstream (its whole team), clamped
_ORCH_MANAGER_TIMEOUT_S = 6 * 3600
_ORCH_MODES = ("flat", "hierarchical")
_ORCH_RUN_BUDGET = (0.5, 50.0)   # $ per run, clamped
_ORCH_PLANNER_BUDGET = 1.0
_ORCH_LATER_MAX_DAYS = 60        # a run for later starts within this
_orch_lock = threading.RLock()

_ORCH_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["done", "failed", "blocked"]},
        "summary": {"type": "string", "description": "2-4 sentences: what you did and the outcome"},
        "result": {"type": "string", "description": "the deliverable: findings, report, test results, or what changed and why"},
        "files_changed": {"type": "array", "items": {"type": "string"}},
        "for_next": {"type": "string", "description": "what the agents after you need to know (empty if nothing)"},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "summary", "result", "files_changed", "for_next", "errors"],
    "additionalProperties": False,
}
_ORCH_TASK_ITEM = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "short slug, a-z0-9 and -"},
        "role": {"type": "string", "enum": list(_ORCH_AGENT_ROLES)},
        "title": {"type": "string"},
        "instructions": {"type": "string", "description": "exactly what to do, with the inputs it needs"},
        "done_criteria": {"type": "string", "description": "how to tell it is done, checkable"},
        "depends_on": {"type": "array", "items": {"type": "string"}},
        "model": {"type": "string", "enum": list(_ORCH_MODELS)},
        "budget_usd": {"type": "number"},
    },
    "required": ["id", "role", "title", "instructions", "done_criteria", "depends_on", "model", "budget_usd"],
    "additionalProperties": False,
}
_ORCH_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "the approach in 2-3 sentences"},
        "single_agent": {"type": "boolean", "description": "true if one agent can do it all"},
        "tasks": {"type": "array", "items": _ORCH_TASK_ITEM},
    },
    "required": ["summary", "single_agent", "tasks"],
    "additionalProperties": False,
}
_ORCH_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["finish", "retry", "add_tasks", "fail"]},
        "reason": {"type": "string"},
        "report": {"type": "string", "description": "for finish/fail: the final report for the human"},
        "retry": {"type": "array", "items": {
            "type": "object", "properties": {"id": {"type": "string"}, "feedback": {"type": "string"}},
            "required": ["id", "feedback"], "additionalProperties": False}},
        "tasks": {"type": "array", "items": _ORCH_TASK_ITEM},
    },
    "required": ["action", "reason", "report", "retry", "tasks"],
    "additionalProperties": False,
}


def _orch_schema(base, allow_manager, roles=None):
    """Pure: the plan / decision schema for a run: its team's agent ids as the
    task roles (default the built-in ones), manager added in a hierarchical
    top level (a deep copy -- the module constants stay as they are)."""
    s = json.loads(json.dumps(base))
    s["properties"]["tasks"]["items"]["properties"]["role"]["enum"] = \
        list(roles or _ORCH_AGENT_ROLES) + (["manager"] if allow_manager else [])
    return s


# ── Agent library: the built-in agents + your own ─────────────────────────
# An agent = a role brief (its system prompt), the built-in tools it has,
# MCP servers (claude.ai connectors / plugins) and skills it may use, a
# default model and budget, and "sensitive" (each of its tasks waits for
# your OK before it starts). A run has a team (agent ids); the planner may
# only use those, and each task keeps a copy of its agent as planned.
_ORCH_TOOLS = ("Read", "Grep", "Glob", "Edit", "Write", "Bash", "PowerShell", "WebSearch", "WebFetch")
_ORCH_AGENT_KEYS = ("id", "name", "brief", "tools", "model", "budget_usd", "mcp", "skills", "sensitive")


def _orch_builtin_agents():
    """Pure: the four built-in agents, in the library's shape."""
    return {rid: {"id": rid, "name": _ORCH_ROLES[rid][0], "brief": _ORCH_ROLE_BRIEF[rid],
                  "tools": list(_ORCH_ROLES[rid][1]), "model": "sonnet", "budget_usd": 1.0, "mcp": [], "skills": [],
                  "sensitive": False, "builtin": True} for rid in _ORCH_AGENT_ROLES}


def _orch_agent_edits(agent):
    """Pure: does the agent change files (its leftovers get committed)?"""
    return bool({"Edit", "Write"} & set((agent or {}).get("tools") or []))


def _orch_validate_agent(data):
    """Pure: POST /orch/agent_save body -> (agent, None) or (None, error)."""
    if not isinstance(data, dict):
        return None, "bad request"
    aid = re.sub(r"[^a-z0-9-]+", "-", str(data.get("id") or data.get("name") or "").strip().lower()).strip("-")[:32]
    if not aid:
        return None, "give the agent a name"
    if aid in _ORCH_ROLES:
        return None, f"'{aid}' is a built-in agent -- pick another id"
    name = str(data.get("name") or aid).strip()[:60]
    brief = str(data.get("brief") or "").strip()
    if len(brief) < 20:
        return None, "describe the agent's role (its system prompt, 20 characters at least)"
    if len(brief) > 6000:
        return None, "the role description is too long (6,000 characters max)"
    tools = [t for t in _ORCH_TOOLS if t in (data.get("tools") or [])]
    mcp = [str(m).strip()[:80] for m in (data.get("mcp") or []) if re.fullmatch(r"[A-Za-z0-9_.:\- ]{1,80}", str(m).strip())]
    skills = [str(k).strip()[:80] for k in (data.get("skills") or []) if re.fullmatch(r"[A-Za-z0-9_.:\- ]{1,80}", str(k).strip())]
    if not tools and not mcp:
        return None, "give the agent at least one tool or MCP server"
    model = str(data.get("model") or "sonnet").strip().lower()
    if model not in _ORCH_MODELS:
        return None, "unknown model"
    return {"id": aid, "name": name, "brief": brief, "tools": tools, "model": model,
            "budget_usd": _orch_clamp(data.get("budget_usd"), _ORCH_TASK_BUDGET, 1.0),
            "mcp": mcp[:12], "skills": skills[:12], "sensitive": data.get("sensitive") is True, "builtin": False}, None


def _orch_mcp_prefix(server):
    """Pure: an MCP server's name -> the permission rule for all its tools
    ('claude.ai Google Drive' -> 'mcp__claude_ai_Google_Drive')."""
    return "mcp__" + re.sub(r"[^A-Za-z0-9]+", "_", str(server)).strip("_")


def _orch_parse_mcp_list(out):
    """Pure: `claude mcp list` -> [{name, connected}] (the health line of each server)."""
    servers = []
    for line in str(out or "").splitlines():
        m = re.match(r"^(.+?): (\S*)\s*\(?\w*\)?\s*- (.+)$", line.strip())
        if not m or m.group(1).startswith(("[", "⚠", "Checking")):
            continue
        servers.append({"name": m.group(1).strip(), "connected": "Connected" in m.group(3)})
    return servers


def _orch_parse_skill(text):
    """Pure: a SKILL.md -> (name, description) from its front matter, ('', '') if none."""
    m = re.match(r"^---\s*\n(.*?)\n---", str(text or "").replace("\r\n", "\n"), re.S)
    if not m:
        return "", ""
    fm = m.group(1)
    name = re.search(r"^name:\s*['\"]?(.+?)['\"]?\s*$", fm, re.M)
    desc = re.search(r"^description:\s*['\"]?(.+?)['\"]?\s*$", fm, re.M)
    return (name.group(1).strip() if name else ""), (desc.group(1).strip()[:200] if desc else "")


def _orch_scan_skills(root=None, limit=200):
    """Skills Claude Code can load: ~/.claude/skills and plugin skills."""
    root = root or os.path.join(os.path.expanduser("~"), ".claude")
    found, seen = [], set()
    for base in (os.path.join(root, "skills"), os.path.join(root, "plugins")):
        for dirpath, dirnames, files in os.walk(base):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "node_modules"]
            if dirpath[len(base):].count(os.sep) > 7:
                dirnames[:] = []
            if "SKILL.md" in files:
                try:
                    with open(os.path.join(dirpath, "SKILL.md"), encoding="utf-8", errors="replace") as f:
                        name, desc = _orch_parse_skill(f.read(4000))
                except OSError:
                    continue
                if name and name not in seen:
                    seen.add(name)
                    found.append({"name": name, "description": desc})
                if len(found) >= limit:
                    return found
    return sorted(found, key=lambda s: s["name"].lower())


_orch_catalog_cache = {"at": 0.0, "mcp": [], "skills": [], "scanning": False, "error": "", "loaded": False}
_ORCH_CATALOG_TTL_S = 15 * 60
ORCH_CATALOG_FILE = os.path.join(AOC_DATA_DIR, "orch_catalog.json")


def _orch_catalog_load():
    """The last scan from disk (an agent with MCP needs the server list right
    after a monitor reload, and a scan takes a minute)."""
    c = _orch_catalog_cache
    if c["loaded"]:
        return
    c["loaded"] = True
    d = _load_json_file(ORCH_CATALOG_FILE, {})
    if isinstance(d, dict) and isinstance(d.get("mcp"), list):
        c.update(mcp=d["mcp"], skills=d.get("skills") or [], at=float(d.get("at") or 0))


def _orch_subscription_env():
    """The environment for an agent that needs claude.ai connectors: without
    the API key (with it set, Claude Code turns the connectors off and bills
    the key); the agent then runs on your Claude subscription."""
    env = dict(os.environ)
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(k, None)
    return env


ORCH_SETTINGS_FILE = os.path.join(AOC_DATA_DIR, "orch_settings.json")
_ORCH_BILLING = ("api", "subscription")
_ORCH_RULES_MAX = 4000


def _orch_settings():
    """Orchestration settings: billing = "api" (ANTHROPIC_API_KEY, if set) or
    "subscription" (your claude.ai login, plan limits apply); rules = your
    house rules, given to every agent, the planner and the review."""
    d = _load_json_file(ORCH_SETTINGS_FILE, {})
    d = d if isinstance(d, dict) else {}
    return {"billing": d.get("billing") if d.get("billing") in _ORCH_BILLING else "api",
            "rules": str(d.get("rules") or "")[:_ORCH_RULES_MAX],
            "api_key_set": bool(os.environ.get("ANTHROPIC_API_KEY"))}


def _orch_settings_save(data):
    """POST /orch/settings {billing?, rules?}: what you send changes, the rest stays."""
    data = data or {}
    cur = _orch_settings()
    if "billing" in data:
        if data["billing"] not in _ORCH_BILLING:
            return {"ok": False, "error": "billing: api or subscription"}
        cur["billing"] = data["billing"]
    if "rules" in data:
        rules = str(data["rules"] or "").replace("\r\n", "\n").strip()
        if len(rules) > _ORCH_RULES_MAX:
            return {"ok": False, "error": f"the house rules are too long ({_ORCH_RULES_MAX:,} characters max)"}
        cur["rules"] = rules
    if "billing" not in data and "rules" not in data:
        return {"ok": False, "error": "nothing to save"}
    os.makedirs(AOC_DATA_DIR, exist_ok=True)
    with open(ORCH_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump({"billing": cur["billing"], "rules": cur["rules"]}, f, ensure_ascii=False)
    return dict(_orch_settings(), ok=True)


def _orch_rules_text(rules, kind):
    """Pure: your house rules as the planner / an agent / the review reads them ('' = none)."""
    rules = str(rules or "").strip()
    if not rules:
        return ""
    lead = {"plan": "HOUSE RULES (the human's, for every agent) -- plan only work that keeps them:",
            "supervise": "HOUSE RULES (the human's, for every agent) -- a result that breaks one is not done; send it back:"}
    return "\n\n" + lead.get(kind, "House rules from the human -- they apply to every agent and come before your task:") + "\n" + rules


def _orch_proc_env(agent=None, billing=None):
    """How to start one claude process: -> (spawn kwargs, MCP deny list, ready).
    On the subscription (chosen, or the agent needs connectors) the API key
    is dropped and every MCP server it doesn't need is denied -- all of them
    together don't fit a model's context. Until AOC knows the server list it
    isn't ready (the caller tries again next tick)."""
    billing = billing or _orch_settings()["billing"]
    if billing != "subscription" and not (agent or {}).get("mcp"):
        return {}, [], True
    _orch_catalog_load()
    if not _orch_catalog_cache["mcp"]:
        _orch_catalog()
        return {}, [], False
    return {"env": _orch_subscription_env()}, _orch_mcp_deny(agent or {}, _orch_catalog_cache["mcp"]), True


def _orch_limit_hit(err):
    """Pure: does this error say the Claude plan's usage limit is used up?
    ("You've hit your session limit · resets 6:20pm", "... usage limit reached")"""
    return bool(re.search(r"hit your (\w+ )?limit|usage limit reached", str(err or ""), re.I))


def _orch_explain_error(err):
    """Pure: an API error -> what to do about it."""
    if "credit balance is too low" in str(err).lower():
        return (str(err) + " -- the API key's account has no credit left. Add credit, or switch Orchestrate's "
                "billing to your Claude subscription.")
    if _orch_limit_hit(err) and "plan's usage limit" not in str(err):
        return (str(err) + " -- your Claude plan's usage limit is used up; trying again before it resets "
                "can't work. Start the run again after the reset.")
    return str(err)


def _orch_catalog_refresh(run=None):
    """Background: `claude mcp list` (as an agent with connectors sees it) + skills."""
    c = _orch_catalog_cache
    if c["scanning"]:
        return
    c["scanning"] = True
    try:
        exe = _find_claude_exe()
        out = ""
        if exe:
            r = (run or subprocess.run)([exe, "mcp", "list"], capture_output=True, text=True, encoding="utf-8",
                                       errors="replace", timeout=180, env=_orch_subscription_env(), creationflags=_NO_WINDOW)
            out = (r.stdout or "") + "\n" + (r.stderr or "")
        servers = _orch_parse_mcp_list(out)
        if servers or not c["mcp"]:   # a failed scan keeps the last good list
            c["mcp"] = servers
        c["skills"] = _orch_scan_skills()
        c["error"] = "" if exe else "Claude Code (claude) not found"
        try:
            os.makedirs(AOC_DATA_DIR, exist_ok=True)
            with open(ORCH_CATALOG_FILE, "w", encoding="utf-8") as f:
                json.dump({"at": time.time(), "mcp": c["mcp"], "skills": c["skills"]}, f)
        except OSError:
            pass
    except Exception as e:
        c["error"] = str(e)[:200]
        _log_bg_error("orchestration catalog", e)
    finally:
        c["at"], c["scanning"] = time.time(), False


def _orch_catalog(fresh=False):
    """GET /orch/catalog: tools, MCP servers, skills (cached; refreshed in the background)."""
    c = _orch_catalog_cache
    _orch_catalog_load()
    if (fresh or time.time() - c["at"] > _ORCH_CATALOG_TTL_S) and not c["scanning"]:
        threading.Thread(target=_orch_catalog_refresh, daemon=True, name="orch-catalog").start()
    return {"ok": True, "tools": list(_ORCH_TOOLS), "models": list(_ORCH_MODELS), "mcp": c["mcp"], "skills": c["skills"],
            "scanning": c["scanning"] or not c["at"], "error": c["error"]}


# ── storage ───────────────────────────────────────────────────────────────
def _orch_connect(path=None):
    conn = sqlite3.connect(path or ORCH_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, created REAL, updated REAL, goal TEXT, repo TEXT, base TEXT, base_sha TEXT,
            state TEXT, model TEXT, max_parallel INTEGER, budget_usd REAL, plan_summary TEXT,
            single_agent INTEGER DEFAULT 0, report TEXT DEFAULT '', error TEXT DEFAULT '', rounds INTEGER DEFAULT 0,
            job_kind TEXT DEFAULT '', job_pid INTEGER, job_base TEXT DEFAULT '', job_started REAL, finished REAL);
        CREATE TABLE IF NOT EXISTS tasks (
            run_id TEXT, id TEXT, seq INTEGER, role TEXT, title TEXT, instructions TEXT, done_criteria TEXT,
            depends_on TEXT, model TEXT, budget_usd REAL, state TEXT, attempt INTEGER DEFAULT 0,
            feedback TEXT DEFAULT '', branch TEXT DEFAULT '', path TEXT DEFAULT '', pid INTEGER,
            job_base TEXT DEFAULT '', started REAL, finished REAL, cost_usd REAL DEFAULT 0, turns INTEGER DEFAULT 0,
            session_id TEXT DEFAULT '', output TEXT DEFAULT '', error TEXT DEFAULT '', commits INTEGER DEFAULT 0,
            added_round INTEGER DEFAULT 0, PRIMARY KEY (run_id, id));
        CREATE TABLE IF NOT EXISTS events (
            run_id TEXT, ts REAL, kind TEXT, task_id TEXT DEFAULT '', message TEXT DEFAULT '',
            cost_usd REAL DEFAULT 0, human INTEGER DEFAULT 0);
        CREATE INDEX IF NOT EXISTS events_run ON events(run_id, ts);
        CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, data TEXT, updated REAL);
        CREATE TABLE IF NOT EXISTS entities (
            id TEXT PRIMARY KEY, data TEXT, state TEXT DEFAULT 'idle', paused INTEGER DEFAULT 0,
            memory TEXT DEFAULT '[]', next_due REAL, last_scout REAL, run_now INTEGER DEFAULT 0,
            job_pid INTEGER, job_base TEXT DEFAULT '', job_started REAL, note TEXT DEFAULT '',
            created REAL, updated REAL);
        CREATE TABLE IF NOT EXISTS entity_events (
            entity_id TEXT, ts REAL, kind TEXT, message TEXT DEFAULT '', cost_usd REAL DEFAULT 0, run_id TEXT DEFAULT '');
        CREATE INDEX IF NOT EXISTS entity_events_e ON entity_events(entity_id, ts);
        CREATE TABLE IF NOT EXISTS schedules (
            id TEXT PRIMARY KEY, data TEXT, paused INTEGER DEFAULT 0, last_fire REAL, next_due REAL,
            last_run TEXT DEFAULT '', last_result TEXT DEFAULT '', created REAL, updated REAL);
        CREATE TABLE IF NOT EXISTS schedule_events (
            schedule_id TEXT, ts REAL, kind TEXT, message TEXT DEFAULT '', run_id TEXT DEFAULT '', user TEXT DEFAULT '');
        CREATE INDEX IF NOT EXISTS schedule_events_s ON schedule_events(schedule_id, ts);
    """)
    # columns added after the first release (CREATE TABLE IF NOT EXISTS doesn't add them)
    have = {t: {r[1] for r in conn.execute(f"PRAGMA table_info({t})")} for t in ("runs", "tasks", "events", "entity_events")}
    for table, col, decl in (("runs", "mode", "TEXT DEFAULT 'flat'"), ("runs", "parent_id", "TEXT DEFAULT ''"),
                             ("runs", "parent_task", "TEXT DEFAULT ''"), ("runs", "approve_teams", "INTEGER DEFAULT 0"),
                             ("tasks", "child_id", "TEXT DEFAULT ''"), ("runs", "team", "TEXT DEFAULT '[]'"),
                             ("tasks", "agent", "TEXT DEFAULT ''"), ("runs", "kind", "TEXT DEFAULT 'repo'"),
                             ("runs", "entity_id", "TEXT DEFAULT ''"), ("tasks", "approved", "INTEGER DEFAULT 0"),
                             ("events", "user", "TEXT DEFAULT ''"), ("entity_events", "user", "TEXT DEFAULT ''"),
                             ("runs", "baseline_of", "TEXT DEFAULT ''"), ("runs", "verdict", "TEXT DEFAULT ''"),
                             ("runs", "start_at", "REAL"), ("runs", "schedule_id", "TEXT DEFAULT ''")):
        if col not in have[table]:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    return conn


def _orch_who():
    """The person behind the request being served ('' for AOC's own background work)."""
    try:
        u = _current_user()
    except NameError:
        return ""
    return "" if u.get("background") else str(u.get("name") or "")


def _orch_event(conn, run_id, kind, message="", task_id="", cost=0.0, human=False, now=None):
    conn.execute("INSERT INTO events (run_id, ts, kind, task_id, message, cost_usd, human, user) VALUES (?,?,?,?,?,?,?,?)",
                 (run_id, now or time.time(), kind, task_id, str(message)[:2000], float(cost or 0), 1 if human else 0,
                  _orch_who() if human else ""))


def _orch_set_run(conn, run_id, now=None, **fields):
    fields["updated"] = now or time.time()
    conn.execute(f"UPDATE runs SET {', '.join(k + '=?' for k in fields)} WHERE id=?", (*fields.values(), run_id))


def _orch_set_task(conn, run_id, tid, **fields):
    conn.execute(f"UPDATE tasks SET {', '.join(k + '=?' for k in fields)} WHERE run_id=? AND id=?",
                 (*fields.values(), run_id, tid))


def _orch_load(conn, run_id):
    """-> (run dict, [task dicts] in plan order) or (None, [])."""
    r = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not r:
        return None, []
    tasks = [dict(t) for t in conn.execute("SELECT * FROM tasks WHERE run_id=? ORDER BY seq", (run_id,))]
    for t in tasks:
        try:
            t["depends_on"] = json.loads(t["depends_on"] or "[]")
        except ValueError:
            t["depends_on"] = []
        try:
            t["output"] = json.loads(t["output"]) if t["output"] else None
        except ValueError:
            t["output"] = None
        try:
            t["agent"] = json.loads(t["agent"]) if t.get("agent") else None
        except ValueError:
            t["agent"] = None
    run = dict(r)
    try:
        run["team"] = json.loads(run.get("team") or "[]")
    except ValueError:
        run["team"] = []
    return run, tasks


def _orch_custom_agents(conn):
    """Your agents (the agents table), by id."""
    out = {}
    for aid, data in conn.execute("SELECT id, data FROM agents ORDER BY id").fetchall():
        try:
            out[aid] = dict(json.loads(data), id=aid, builtin=False)
        except (ValueError, TypeError):
            continue
    return out


def _orch_library(conn):
    """Every agent a team can have: the built-ins, then yours."""
    lib = _orch_builtin_agents()
    lib.update(_orch_custom_agents(conn))
    return lib


def _orch_team(run, library):
    """Pure: the run's team as agents (default: the built-ins; an agent
    deleted since is left out)."""
    ids = (run or {}).get("team") or list(_ORCH_AGENT_ROLES)
    return [library[i] for i in ids if i in library]


def _orch_task_agent(task):
    """Pure: the agent a task runs as -- the copy made when it was planned,
    else the built-in agent of that id."""
    a = task.get("agent")
    if isinstance(a, dict) and isinstance(a.get("tools"), list):
        return a
    return _orch_builtin_agents().get(task.get("role")) or {
        "id": task.get("role"), "name": str(task.get("role") or ""), "brief": "", "tools": [], "model": "sonnet",
        "budget_usd": 1.0, "mcp": [], "skills": [], "sensitive": False, "builtin": False}


# ── pure: plan checks, scheduling, prompts ────────────────────────────────
def _orch_clamp(x, lo_hi, default):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return default
    return round(min(max(x, lo_hi[0]), lo_hi[1]), 2)


def _orch_topo(tasks):
    """Pure: task ids in an order where every task comes after its
    dependencies (plan order kept where free), None on a cycle."""
    deps = {t["id"]: list(t.get("depends_on") or []) for t in tasks}
    out, done = [], set()
    while len(out) < len(tasks):
        ready = [t["id"] for t in tasks if t["id"] not in done and all(d in done for d in deps[t["id"]])]
        if not ready:
            return None
        out.append(ready[0])
        done.add(ready[0])
    return out


def _orch_waves(tasks):
    """Pure: [[ids that can run together], ...] -- the plan's levels."""
    level = {}
    for tid in _orch_topo(tasks) or []:
        t = next(x for x in tasks if x["id"] == tid)
        level[tid] = 1 + max([level[d] for d in t.get("depends_on") or []] or [-1])
    waves = []
    for t in tasks:
        if t["id"] in level:
            while len(waves) <= level[t["id"]]:
                waves.append([])
            waves[level[t["id"]]].append(t["id"])
    return waves


def _orch_validate_tasks(raw, existing=(), max_new=_ORCH_MAX_TASKS, allow_manager=False, roles=None):
    """Pure: planner / supervisor / edited tasks -> (clean task list, None)
    or (None, error). `existing` = tasks already in the run (new ones may
    depend on them, their ids are taken). Manager tasks only in a
    hierarchical run's top level. roles = the agent ids the run's team has
    (default: the built-in agents)."""
    allowed = set(roles or _ORCH_AGENT_ROLES)
    if not isinstance(raw, list) or not raw:
        return None, "the plan has no tasks"
    if len(raw) > max_new:
        return None, f"at most {max_new} tasks"
    taken = {t["id"] for t in existing}
    out = []
    for i, t in enumerate(raw):
        if not isinstance(t, dict):
            return None, f"task {i + 1} is not an object"
        tid = re.sub(r"[^a-z0-9-]+", "-", str(t.get("id") or "").strip().lower()).strip("-")[:32] or f"t{i + 1}"
        if tid in taken:
            return None, f"two tasks are called '{tid}'"
        taken.add(tid)
        role = re.sub(r"[^a-z0-9-]+", "-", str(t.get("role") or "").strip().lower()).strip("-")
        if role != "manager" and role not in allowed:
            return None, f"task '{tid}': '{role}' is not an agent of this run's team"
        if role == "manager" and not allow_manager:
            return None, f"task '{tid}': a manager needs a hierarchical run (and managers can't have managers)"
        title = str(t.get("title") or "").strip()[:120]
        instructions = str(t.get("instructions") or "").strip()[:8000]
        criteria = str(t.get("done_criteria") or "").strip()[:2000]
        if not title or not instructions or not criteria:
            return None, f"task '{tid}' needs a title, instructions and a done criterion"
        deps = t.get("depends_on") or []
        if not isinstance(deps, list):
            return None, f"task '{tid}': depends_on must be a list"
        model = str(t.get("model") or "sonnet").strip().lower()
        out.append({"id": tid, "role": role, "title": title, "instructions": instructions, "done_criteria": criteria,
                    "depends_on": [re.sub(r"[^a-z0-9-]+", "-", str(d).strip().lower()).strip("-") for d in deps],
                    "model": model if model in _ORCH_MODELS else "sonnet",
                    "budget_usd": _orch_clamp(t.get("budget_usd"),
                                              _ORCH_MANAGER_BUDGET if role == "manager" else _ORCH_TASK_BUDGET,
                                              3.0 if role == "manager" else 1.0)})
    known = {t["id"] for t in existing} | {t["id"] for t in out}
    for t in out:
        bad = [d for d in t["depends_on"] if d not in known or d == t["id"]]
        if bad:
            return None, f"task '{t['id']}' depends on unknown task(s): {', '.join(bad)}"
    if _orch_topo(list(existing) + out) is None:
        return None, "the dependencies go in a circle"
    return out, None


def _orch_next(run, tasks, spent):
    """Pure: what the engine does next for a running run.
    -> [("start", id)] / [("gate", id)] / [("block", id, why)] / [("skip", id, why)] / [("supervise",)] / [].
    A task of a sensitive agent stops at a gate (state "gate") until you
    allow it; while one waits the run isn't reviewed."""
    state = {t["id"]: t["state"] for t in tasks}
    acts = []
    for t in tasks:
        if t["state"] != "pending":
            continue
        bad = [d for d in t["depends_on"] if state.get(d) in ("failed", "blocked", "skipped")]
        if bad:
            acts.append(("block", t["id"], "input task(s) did not finish: " + ", ".join(bad)))
            state[t["id"]] = "blocked"
    running = sum(1 for t in tasks if t["state"] == "running")
    slots = max(0, int(run.get("max_parallel") or 1) - running)
    # a running manager's cost_usd is its team's spend so far, already in `spent`: only the rest of its
    # budget is still to come. (A plain task's cost_usd is its earlier attempts': this one may use it all.)
    reserved = spent + sum(max(0.0, t["budget_usd"] - float(t.get("cost_usd") or 0)) if t["role"] == "manager"
                           else t["budget_usd"] for t in tasks if t["state"] == "running")
    for t in tasks:
        if state[t["id"]] != "pending" or not all(state.get(d) == "done" for d in t["depends_on"]):
            continue
        if reserved + t["budget_usd"] > float(run.get("budget_usd") or 0) + 1e-9:
            acts.append(("skip", t["id"], "the run's budget would be exceeded"))
            state[t["id"]] = "skipped"
            continue
        if _orch_task_agent(t).get("sensitive") and not t.get("approved"):
            acts.append(("gate", t["id"]))
            state[t["id"]] = "gate"
            continue
        if slots <= 0:
            continue
        acts.append(("start", t["id"]))
        state[t["id"]] = "running"
        slots -= 1
        reserved += t["budget_usd"]
    if not any(s in ("pending", "running", "gate") for s in state.values()):
        # skipped by the budget just now -> nothing left anyway; a pending
        # task waiting on a running one keeps the run going
        acts.append(("supervise",))
    return acts


def _orch_clip(s, n):
    s = str(s or "")
    return s if len(s) <= n else s[:n] + " [...]"


def _orch_task_prompt(goal, task, inputs, base):
    """Pure: what an agent gets on stdin."""
    lines = [f"TEAM GOAL: {goal}", "", f"YOUR TASK ({task['id']}): {task['title']}", task["instructions"], "",
             f"DONE WHEN: {task['done_criteria']}"]
    if inputs:
        lines += ["", "INPUTS FROM THE TASKS BEFORE YOU:"]
        for t in inputs:
            o = t.get("output") or {}
            lines += [f"--- {t['id']} ({t['role']}): {t['title']}",
                      "summary: " + _orch_clip(o.get("summary"), 1500),
                      "result: " + _orch_clip(o.get("result"), 6000)]
            if o.get("for_next"):
                lines.append("for you: " + _orch_clip(o.get("for_next"), 2000))
            if t.get("commits") and t.get("branch"):
                lines.append(f"(its commits are already on your branch; `git log {base}..HEAD` shows them)")
    if task.get("feedback"):
        lines += ["", "THIS IS A RETRY. What was wrong last time:", task["feedback"]]
    lines += ["", "Work only in the current folder. Create and edit files with paths RELATIVE to it (e.g. "
                  "notes/report.md) -- never an absolute path, and never one printed by `pwd` in Bash (on Windows it "
                  "shows /tmp/... or /c/... paths that the file tools read as a different folder).",
              "When you are done, answer in the required JSON format: status "
                  "'done' only if the done criterion is met, 'failed' if you could not do it, 'blocked' if an input "
                  "is missing. Be concrete in 'result'."]
    return "\n".join(lines)


def _orch_mcp_deny(agent, servers):
    """Pure: the MCP servers an agent must NOT load -- every known one but
    its own. Found live: without the API key every claude.ai connector loads
    (~379k tokens of tool definitions, over the model's limit) and
    --allowedTools doesn't trim them; a deny rule does (~34k)."""
    mine = set((agent or {}).get("mcp") or [])
    return [_orch_mcp_prefix(m["name"]) for m in servers or [] if m.get("name") and m["name"] not in mine]


def _orch_agent_argv(exe, task, agent=None, deny=(), rules=""):
    """Pure: the headless agent's command line (a list, never a shell).
    dontAsk = anything outside the role's tools is refused, never prompted;
    the danger guard hook still holds destructive commands for your OK. No
    prompt argument: the task arrives on stdin AS the message (a "follow
    stdin" prompt + piped text was refused live as a prompt injection)."""
    agent = agent or _orch_task_agent(task)
    tools = list(agent.get("tools") or []) + (["Skill"] if agent.get("skills") else [])
    allowed = tools + [_orch_mcp_prefix(m) for m in agent.get("mcp") or []]
    brief = agent.get("brief") or ""
    if agent.get("skills"):
        brief += " Skills you can use (Skill tool) when they fit: " + ", ".join(agent["skills"]) + "."
    if agent.get("mcp"):
        brief += " Connected services (MCP) you can use: " + ", ".join(agent["mcp"]) + "."
    brief += _orch_rules_text(rules, "agent")
    return [exe, "-p", "--model", task["model"],
            "--output-format", "json", "--json-schema", json.dumps(_ORCH_RESULT_SCHEMA),
            "--permission-mode", "dontAsk", "--tools", ",".join(tools), "--allowedTools", ",".join(allowed),
            "--max-budget-usd", f"{task['budget_usd']:.2f}",
            "--append-system-prompt", brief] + (["--disallowedTools", ",".join(deny)] if deny else [])


def _orch_llm_argv(exe, schema, model, budget, read_tools=False, deny=()):
    """Pure: planner / supervisor command line: no hooks (not a session of
    yours), not saved, the plan's JSON schema; the planner may read the repo."""
    argv = [exe, "-p", "--model", model, "--no-session-persistence",
            "--settings", '{"disableAllHooks": true}', "--output-format", "json",
            "--json-schema", json.dumps(schema), "--max-budget-usd", f"{budget:.2f}"]
    if read_tools:
        argv += ["--permission-mode", "dontAsk", "--tools", "Read,Grep,Glob", "--allowedTools", "Read,Grep,Glob"]
    else:
        argv += ["--tools", ""]
    if deny:
        argv += ["--disallowedTools", ",".join(deny)]
    return argv


def _orch_parse(text):
    """Pure: a `claude -p --output-format json` answer -> {ok, data, cost,
    turns, session_id, error}. Garbage, an error or a missing/invalid
    structured_output is ok=False with the reason."""
    out = {"ok": False, "data": None, "cost": 0.0, "turns": 0, "session_id": "", "error": ""}
    body = str(text or "")
    start = body.find("{")
    try:
        d = json.loads(body[start:]) if start >= 0 else None
    except ValueError:
        d = None
    if not isinstance(d, dict):
        out["error"] = "no answer (" + (_orch_clip(body.strip(), 400) or "empty output") + ")"
        return out
    out["cost"] = float(d.get("total_cost_usd") or 0)
    out["turns"] = int(d.get("num_turns") or 0)
    out["session_id"] = str(d.get("session_id") or "")
    so = d.get("structured_output")
    if d.get("is_error") or d.get("subtype") not in (None, "success"):
        out["error"] = _orch_clip(d.get("result") or d.get("subtype") or "error", 300)
        if "budget" in str(d.get("subtype") or ""):
            out["error"] = "stopped at its budget limit"
        return out
    if not isinstance(so, dict):
        out["error"] = "the answer did not follow the required JSON format"
        return out
    out["ok"], out["data"] = True, so
    return out


def _orch_check_result(data):
    """Pure: an agent's structured answer -> error text ('' = valid)."""
    if not isinstance(data, dict):
        return "no answer"
    if data.get("status") not in ("done", "failed", "blocked"):
        return "status must be done / failed / blocked"
    if not str(data.get("summary") or "").strip():
        return "the summary is empty"
    if data.get("status") == "done" and not str(data.get("result") or "").strip():
        return "status done but the result is empty"
    return ""


def _orch_fit_budgets(tasks, total):
    """Pure: scale the tasks' budgets down (proportionally, never under a
    role's minimum) so they take at most 85% of the run's budget -- the rest
    is for planning and reviews. -> (tasks, scaled?)."""
    cap = round(float(total) * 0.85, 2)
    have = sum(t["budget_usd"] for t in tasks)
    if have <= cap + 1e-9:
        return tasks, False
    f = cap / have
    return [dict(t, budget_usd=max((_ORCH_MANAGER_BUDGET if t["role"] == "manager" else _ORCH_TASK_BUDGET)[0],
                                   round(t["budget_usd"] * f, 2))) for t in tasks], True


def _orch_agents_text(agents):
    """Pure: the team, as the planner reads it (a task's role = an agent id)."""
    lines = ["AGENTS of this team (a task's role must be one of these ids):"]
    for a in agents:
        what = []
        if _orch_agent_edits(a):
            what.append("edits files in its own git worktree; its commits flow to the tasks that depend on it")
        else:
            what.append("does not change files")
        if a.get("tools"):
            what.append("tools: " + ", ".join(a["tools"]))
        if a.get("mcp"):
            what.append("connected services: " + ", ".join(a["mcp"]))
        if a.get("skills"):
            what.append("skills: " + ", ".join(a["skills"]))
        if a.get("sensitive"):
            what.append("each of its tasks waits for the human's OK first")
        lines.append(f"- {a['id']} ({a.get('name') or a['id']}, default model {a.get('model') or 'sonnet'}): "
                     + _orch_clip(a.get("brief"), 400) + " [" + "; ".join(what) + "]")
    return chr(10).join(lines)


def _orch_plan_prompt(goal, repo, base, files, notes, mode="flat", budget=None, agents=None, area="repo"):
    """Pure: the planner's instructions. mode: flat (a team), hierarchical
    (the top of a run with managers), team (a manager planning its team).
    area: repo (a code repository) or workspace (AOC's folder of documents)."""
    if mode == "team":
        who = ("You are the manager (team lead) of ONE workstream of a larger goal, leading a small team of AI "
               "coding agents (Claude Code, headless). Plan how your team delivers the WORKSTREAM GOAL below; "
               "other workstreams are handled by other teams.")
    else:
        who = ("You are the orchestrator of a small team of AI coding agents (Claude Code, headless). Plan how the "
               "team reaches the GOAL below in the repository you are in.")
    roles = _orch_agents_text(agents or list(_orch_builtin_agents().values()))
    extra = []
    if mode == "hierarchical":
        roles += (chr(10) + "- manager: owns a WORKSTREAM -- its instructions are the workstream's goal (what to deliver, "
                  f"where, constraints); it plans and runs its own team of up to {_ORCH_MAX_TASKS} agents and "
                  "returns the integrated result on one branch. Its budget_usd covers its whole team "
                  f"({_ORCH_MANAGER_BUDGET[0]:g}-{_ORCH_MANAGER_BUDGET[1]:g}); its model leads the team (sonnet "
                  "usually, opus for hard ones).")
        extra = ["- This is a HIERARCHICAL run: split a large goal into 2-4 managers for separable areas (e.g. "
                 "backend, frontend, docs, tests) that touch different files; add direct agents only for small "
                 "cross-cutting steps (e.g. a final qa over everything). If the goal is small, plan a flat team or "
                 "one developer instead -- managers cost a planning and a review each."]
    if area == "workspace":
        where = (f"WORKSPACE: {repo} -- not a code repository: a folder of documents and data (reports, notes, "
                 "exports) that AOC keeps in git. Agents that write files (Write/Edit) put what they produce here, "
                 "as files with clear names; their results are kept when the human accepts the run. Agents with "
                 "connected services (MCP) act on those services -- plan read steps before any step that changes "
                 "something outside, and say in the instructions exactly what may be changed.")
    else:
        where = f"REPO: {repo} (branch {base})"
    return "\n".join([
        who + " You may read the " + ("workspace" if area == "workspace" else "repo")
        + " (Read/Grep/Glob) to plan well; you do not do the work yourself.",
        "", f"{'WORKSTREAM GOAL' if mode == 'team' else 'GOAL'}: {goal}", where, "",
        roles,
        "Rules:"] + extra + [
        "- Fewest agents that do the job well. If one agent can do it, plan ONE task and set single_agent "
        "true. Pick the agent whose description fits each task best. Add a task only when it clearly adds "
        "quality, speed or a needed check.",
        "- Independent tasks run in parallel; use depends_on only for real inputs. Developers working in "
        "parallel must touch different files (their branches are merged later).",
        "- A qa task after developers is worth it when there are tests or clear acceptance checks.",
        "- Instructions must be self-contained: the agent sees only its task, the goal and its inputs' results.",
        "- done_criteria must be checkable. model: haiku for simple lookups, sonnet for most work, opus only for "
        "hard reasoning. budget_usd per task: 0.2-3 typical.",
        (f"- BUDGET: {'your team' if mode == 'team' else 'the whole run'} has ${float(budget):.2f}; the tasks' "
         f"budget_usd together must stay under ${float(budget) * 0.8:.2f} (the rest pays for planning and reviews)."
         if budget else "- Keep budgets modest."),
        f"- At most {_ORCH_MAX_TASKS} tasks.",
        "", ("FILES IN THE WORKSPACE:" if area == "workspace" else "TOP OF THE REPO (git ls-files):"), files or "(empty)",
        "", "PROJECT NOTES (CLAUDE.md / README):",
        notes or "(none)",
    ])


def _orch_supervise_prompt(run, tasks, last_round):
    lead = "the manager of one workstream, leading a team" if run.get("parent_id") else "the orchestrator of a team"
    lines = [f"You are {lead} of AI coding agents. Review where the run stands and decide.",
             "", f"GOAL: {run['goal']}", f"PLAN: {run.get('plan_summary') or ''}",
             f"Review round {int(run.get('rounds') or 0) + 1} of {_ORCH_MAX_ROUNDS}. "
             f"Spent so far ${float(run.get('_spent') or 0):.2f} of ${float(run['budget_usd']):.2f}.", "", "TASKS:"]
    for t in tasks:
        o = t.get("output") or {}
        lines.append(f"--- {t['id']} [{t['role']}] {t['title']} -> {t['state'].upper()} "
                     f"(attempts {t['attempt']}, ${t['cost_usd']:.2f}, commits {t['commits']})")
        lines.append("done when: " + _orch_clip(t["done_criteria"], 400))
        if o:
            lines += ["summary: " + _orch_clip(o.get("summary"), 1200), "result: " + _orch_clip(o.get("result"), 3000)]
            if o.get("errors"):
                lines.append("errors: " + _orch_clip("; ".join(map(str, o["errors"])), 800))
        if t.get("error"):
            lines.append("failure: " + _orch_clip(t["error"], 600))
    reader = ("the orchestrator above you: what your team delivered, on which files, how it was checked, open "
              "issues" if run.get("parent_id") else
              "the human: what was done, by which task, what to check before merging, open issues")
    lines += ["", "Decide ONE action:",
              f"- finish: the goal is met (or as far as it sensibly goes). Write 'report' for {reader}. Plain "
              "text, short lines, no markdown.",
              "- retry: re-run failed / not-good-enough tasks (ids in 'retry' with precise feedback). A task runs "
              f"at most {_ORCH_MAX_ATTEMPTS_REVIEWED} times in all; done tasks can be retried too (their later tasks then re-run).",
              "- add_tasks: add the missing tasks ('tasks', same format as a plan; they may depend on existing ids).",
              "- fail: the goal cannot be reached; explain in 'report'.",
              "Use empty lists / empty report for the fields your action does not need. Do not add work that the "
              "goal did not ask for."]
    if any("hits the same conflict" in str(t.get("error") or "") for t in tasks):
        lines.append("A task above failed because the work of its inputs conflicts (they changed the same lines): "
                     "retrying that task can't help. Retry one of the conflicting tasks with feedback to build its "
                     "change so it can't collide, or finish and tell the reader which tasks conflict -- at merge time "
                     "they can keep one of them.")
    if last_round:
        lines.append("This is the LAST review round: you must answer finish or fail.")
    return "\n".join(lines)


def _orch_allows_manager(run):
    """Pure: may this run's plan have manager tasks? (hierarchical top level only)"""
    return run.get("mode") == "hierarchical" and not run.get("parent_id")


def _orch_apply_decision(tasks, decision, last_round, allow_manager=False, roles=None):
    """Pure: the supervisor's decision -> (action, changes, error).
    changes = {"retry": [(id, feedback)], "add": [tasks], "report": str}."""
    if not isinstance(decision, dict):
        return "fail", {"report": "the orchestrator gave no decision"}, "no decision"
    action = decision.get("action")
    report = str(decision.get("report") or decision.get("reason") or "").strip()
    if last_round and action not in ("finish", "fail"):
        action = "finish"
    if action == "retry":
        by_id = {t["id"]: t for t in tasks}
        retry = []
        for r in decision.get("retry") or []:
            t = by_id.get(str((r or {}).get("id") or ""))
            if t and t["attempt"] < _ORCH_MAX_ATTEMPTS_REVIEWED and t["state"] != "running":
                retry.append((t["id"], str(r.get("feedback") or "")[:3000]))
        if not retry:
            return "finish", {"report": report or "nothing could be retried"}, ""
        return "retry", {"retry": retry}, ""
    if action == "add_tasks":
        room = _ORCH_MAX_TASKS_TOTAL - len(tasks)
        if room <= 0:
            return "finish", {"report": report or "the task limit is reached"}, ""
        new, err = _orch_validate_tasks(decision.get("tasks") or [], existing=tasks, max_new=room,
                                        allow_manager=allow_manager, roles=roles)
        if err:
            return "finish", {"report": report}, "the added tasks were invalid: " + err
        return "add_tasks", {"add": new}, ""
    if action == "fail":
        return "fail", {"report": report or "the orchestrator gave up"}, ""
    return "finish", {"report": report}, ""


def _orch_dependents(tasks, tid):
    """Pure: ids of every task that (transitively) depends on tid."""
    out, frontier = set(), {tid}
    while frontier:
        nxt = {t["id"] for t in tasks if set(t["depends_on"]) & frontier and t["id"] not in out}
        out |= nxt
        frontier = nxt
    return out


def _orch_metrics(run, tasks, events, now=None, team_coordination=0.0):
    """Pure: cost split (coordination = planner + supervisor, work = agents),
    timing, success, retries and human interventions of one run. A manager
    task's cost is its whole team's; team_coordination = what of that went to
    the managers' own planning + reviews (counted as coordination, not work)."""
    now = now or time.time()
    coord = sum(e["cost_usd"] for e in events if e["kind"] in ("plan_ready", "plan_failed", "supervise"))
    work = sum(t["cost_usd"] for t in tasks) - team_coordination
    coord += team_coordination
    ended = run.get("finished") or (now if run["state"] in _ORCH_ACTIVE else run.get("updated") or now)
    finished = [t for t in tasks if t["state"] in ("done", "failed")]
    lat = [t["finished"] - t["started"] for t in tasks if t.get("finished") and t.get("started")]
    total = coord + work
    return {"cost_total": round(total, 4), "cost_coordination": round(coord, 4), "cost_work": round(work, 4),
            "coordination_share": round(coord / total, 3) if total else 0.0,
            "duration_s": round(max(0.0, ended - run["created"]), 1),
            "tasks": len(tasks), "done": sum(1 for t in tasks if t["state"] == "done"),
            "failed": sum(1 for t in tasks if t["state"] in ("failed", "blocked")),
            "success_rate": round(sum(1 for t in finished if t["state"] == "done") / len(finished), 3) if finished else None,
            "retries": sum(max(0, t["attempt"] - 1) for t in tasks),
            "human_interventions": sum(1 for e in events if e["human"]),
            "avg_task_s": round(sum(lat) / len(lat), 1) if lat else None,
            "review_rounds": int(run.get("rounds") or 0),
            "teams": sum(1 for t in tasks if t["role"] == "manager")}


def _orch_view(run, tasks, events, now=None, children=None, parent=None, team_coordination=0.0):
    """Pure: GET /orch/run payload. children = {task id: child run summary}
    (a hierarchical run's teams), parent = {id, goal, task} of a team's run."""
    return {"run": {k: run.get(k) for k in ("id", "created", "updated", "goal", "repo", "base", "state", "model",
                                             "max_parallel", "budget_usd", "plan_summary", "single_agent", "report",
                                             "error", "rounds", "finished", "mode", "approve_teams", "parent_id",
                                             "parent_task", "team", "kind", "entity_id", "baseline_of", "verdict", "start_at",
                                             "schedule_id")},
            "tasks": [dict({k: t.get(k) for k in ("id", "role", "title", "instructions", "done_criteria", "depends_on",
                                                   "model", "budget_usd", "state", "attempt", "feedback", "branch",
                                                   "started", "finished", "cost_usd", "turns", "session_id", "output",
                                                   "error", "commits", "child_id", "agent", "live_session", "in_history")},
                           team=(children or {}).get(t["id"])) for t in tasks],
            "waves": _orch_waves(tasks),
            "events": [dict(e) for e in events][-200:],
            "parent": parent,
            "metrics": _orch_metrics(run, tasks, events, now, team_coordination)}


# ── processes ─────────────────────────────────────────────────────────────
def _orch_spawn(argv, cwd, stdin_text, base, popen=None, env=None):
    """Start a detached `claude -p` whose stdin/stdout/stderr are files
    (<base>.in / .out / .err): it outlives a monitor reload, and its answer
    is read from the file when the pid is gone. -> pid."""
    os.makedirs(os.path.dirname(base), exist_ok=True)
    with open(base + ".in", "w", encoding="utf-8", newline="") as f:   # the prompt byte for byte (no \r\n)
        f.write(stdin_text)
    fin, fout, ferr = open(base + ".in", "rb"), open(base + ".out", "wb"), open(base + ".err", "wb")
    try:
        p = (popen or subprocess.Popen)(argv, cwd=cwd, stdin=fin, stdout=fout, stderr=ferr, env=env,
                                        creationflags=_NO_WINDOW | (0x00000200 if os.name == "nt" else 0))
    finally:
        fin.close(), fout.close(), ferr.close()
    _own_claude_pids.add(p.pid)
    return p.pid


def _orch_read_out(base):
    """The process's answer (<base>.out). No JSON in it -> the tail of what it
    said on stderr is added, so a crash / login / flag problem reads as itself
    instead of an empty "no answer"."""
    try:
        with open(base + ".out", encoding="utf-8", errors="replace") as f:
            out = f.read()
    except OSError:
        out = ""
    if "{" in out:
        return out
    try:
        with open(base + ".err", encoding="utf-8", errors="replace") as f:
            err = f.read().strip()
    except OSError:
        err = ""
    return (out.strip() + " " if out.strip() else "") + ("stderr: " + err[-300:] if err else "")


def _orch_kill(pid):
    if not pid:
        return
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=15,
                       creationflags=_NO_WINDOW)
    except Exception as e:
        _log_bg_error("orchestration kill", e)


def _orch_alive(pid):
    """Is that claude.exe still running? A process list that could not be
    read is "don't know", taken as running: read as gone, an agent still at
    work would be judged on its half-written answer (and failed or retried)."""
    if not pid:
        return False
    procs = _snapshot_processes()
    if procs is None:
        return True
    return any(p == pid and exe == "claude.exe" for p, exe in procs)


_ORCH_PID_SLACK_S = 120   # a process we start is up within this of the time we note for it


def _orch_pid_started(pid):
    """When process `pid` started (epoch seconds), None when that can't be read."""
    if not pid or os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32")   # own instance: no restype changes leak to other callers
        k32.OpenProcess.restype = wintypes.HANDLE
        h = k32.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            ft = [wintypes.FILETIME() for _ in range(4)]
            if not k32.GetProcessTimes(wintypes.HANDLE(h), *[ctypes.byref(f) for f in ft]):
                return None
        finally:
            k32.CloseHandle(wintypes.HANDLE(h))
        return ((ft[0].dwHighDateTime << 32) | ft[0].dwLowDateTime) / 1e7 - 11644473600.0
    except Exception:
        return None


def _orch_ours(pid, since):
    """Is the process now behind `pid` the one we started at `since`? A pid
    is only a number: after a reboot (or once ours ended) Windows gives it to
    another process -- one that started after ours. Unknown start -> yes."""
    started = _orch_pid_started(pid) if since else None
    return started is None or started <= float(since) + _ORCH_PID_SLACK_S


def _orch_stop(pid, since=None, alive=None, kill=None):
    """Kill `pid` only while it still runs and is ours -> True if killed.
    taskkill /T on a number blindly would take an unrelated process (and its
    children) down."""
    alive, kill = alive or _orch_alive, kill or _orch_kill
    if not pid or not alive(pid) or not _orch_ours(pid, since):
        return False
    kill(pid)
    return True


def _orch_repo_context(repo, git=None):
    """(files listing, notes) for the planner prompt."""
    git = git or _wt_git
    rc, out, _ = git(["ls-files"], repo, timeout=30)
    files = out.splitlines() if rc == 0 else []
    listing = "\n".join(files[:300]) + (f"\n... and {len(files) - 300} more files" if len(files) > 300 else "")
    notes = []
    for n in ("CLAUDE.md", "README.md"):
        try:
            with open(os.path.join(repo, n), encoding="utf-8", errors="replace") as f:
                notes.append(f"== {n}\n" + _orch_clip(f.read(), 4000))
        except OSError:
            pass
    return listing, "\n".join(notes)


# ── git per task ──────────────────────────────────────────────────────────
def _orch_task_names(repo, run_id, tid, attempt):
    slug = f"orch-{run_id}-{tid}-{attempt}"
    return f"aoc/{slug}", os.path.join(repo, ".claude", "worktrees", f"aoc-{slug}")


def _orch_prepare_worktree(run, task, tasks, git=None):
    """New worktree for one attempt of a task, off the run's base commit,
    with the commits of the developer tasks it depends on (transitively)
    merged in. -> (branch, path, None) or (None, None, error)."""
    git = git or _wt_git
    branch, path = _orch_task_names(run["repo"], run["id"], task["id"], task["attempt"] + 1)
    if os.path.isdir(path):
        _wt_remove(run["repo"], path, branch, git)
    git(["branch", "-D", branch], run["repo"])
    rc, _, er = git(["worktree", "add", "-b", branch, path, run["base_sha"]], run["repo"], timeout=120)
    if rc != 0:
        return None, None, "git worktree add failed: " + _wt_err(er, "unknown error")
    by_id = {t["id"]: t for t in tasks}
    order = _orch_topo(tasks) or []
    need, frontier = set(), set(task["depends_on"])
    while frontier:
        need |= frontier
        frontier = {d for f in frontier for d in by_id[f]["depends_on"]} - need
    merged = []
    for tid in order:
        d = by_id[tid]
        if tid not in need or not d.get("commits") or not d.get("branch"):
            continue
        rc, _, er = git(["merge", "--no-ff", "--no-edit", "-m", f"Input from {tid}: {d['title']}", d["branch"]],
                        path, timeout=120)
        if rc != 0:
            # found live: two workstreams edited the same lines of cli.py -> "merge failed" and nothing more
            # (git names the conflict on stdout), and the review retried the task twice into the same wall
            rc_u, unmerged, _ = git(["diff", "--name-only", "--diff-filter=U"], path)
            files = [f for f in unmerged.splitlines() if f.strip()] if rc_u == 0 else []
            git(["merge", "--abort"], path)
            _wt_remove(run["repo"], path, branch, git)
            if files:
                return None, None, (f"the work of '{tid}' conflicts with " + (", ".join(f"'{m}'" for m in merged) or "its other inputs")
                                    + " in " + ", ".join(files[:5]) + " -- they changed the same lines; "
                                    "running this task again hits the same conflict")
            return None, None, f"the work of '{tid}' conflicts with the other inputs: " + _wt_err(er, "merge failed")
        merged.append(tid)
    return branch, path, None


_ORCH_JUNK_DIRS = {"__pycache__", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "venv",
                   ".tox", ".next", ".turbo", ".parcel-cache", "coverage", ".nyc_output"}
_ORCH_JUNK_EXT = (".pyc", ".pyo", ".log", ".tmp")
_ORCH_JUNK_NAMES = {".DS_Store", "Thumbs.db", ".coverage"}


def _orch_is_junk(rel):
    """Pure: a new, untracked file that running the code or its tests leaves
    behind (caches, deps, logs) -- never part of a developer's commit."""
    parts = str(rel).replace("\\", "/").split("/")
    return bool(set(parts[:-1]) & _ORCH_JUNK_DIRS) or parts[-1] in _ORCH_JUNK_NAMES \
        or parts[-1].lower().endswith(_ORCH_JUNK_EXT)


def _orch_commit_leftovers(run, task, path, git):
    """Commit what a developer left uncommitted: changes to tracked files and
    new files that aren't caches/deps/logs (a repo without a .gitignore
    would otherwise get __pycache__ from the agent's test run)."""
    git(["add", "-u"], path)
    rc, out, _ = git(["ls-files", "--others", "--exclude-standard", "-z"], path)
    new = [f for f in (out.split("\0") if rc == 0 else []) if f and not _orch_is_junk(f)]
    for i in range(0, len(new), 100):
        git(["add", "--", *new[i:i + 100]], path)
    rc, staged, _ = git(["diff", "--cached", "--name-only"], path)
    if rc == 0 and staged.strip():
        git(["commit", "-q", "-m", f"{task['title']}\n\nAOC orchestration {run['id']}, task {task['id']}"], path)


def _orch_finish_worktree(run, task, tasks, git=None):
    """After an attempt: a developer's leftovers are committed; the folder
    always goes; the branch stays only if the task made commits of its own
    (not just its inputs merged in). -> own commits."""
    git = git or _wt_git
    path, branch = task.get("path"), task.get("branch")
    if path and os.path.isdir(path):
        if _orch_agent_edits(_orch_task_agent(task)):
            _orch_commit_leftovers(run, task, path, git)
        git(["worktree", "remove", "--force", path], run["repo"], timeout=60)
        git(["worktree", "prune"], run["repo"])
    commits = _orch_own_commits(run, task, tasks, git) if branch and _orch_agent_edits(_orch_task_agent(task)) else 0
    if branch and not commits:
        git(["branch", "-D", branch], run["repo"])
    return commits


def _orch_own_commits(run, task, tasks, git=None):
    """Commits on the task's branch that are neither the base's nor its
    inputs' (which were merged in)."""
    git = git or _wt_git
    inputs = [t["branch"] for t in tasks if t["id"] != task["id"] and t.get("branch") and t.get("commits")]
    rc, n, _ = git(["rev-list", "--count", "--no-merges", task["branch"], "^" + run["base_sha"],
                    *["^" + b for b in inputs]], run["repo"])
    return int(n.strip()) if rc == 0 and n.strip().isdigit() else 0


# ── engine ────────────────────────────────────────────────────────────────
def _orch_start_task(conn, run, task, tasks, exe, now, spawn=None, git=None):
    if not _orch_proc_env(_orch_task_agent(task))[2]:
        return   # the MCP server list isn't known yet (scanning): start next tick
    if task.get("branch"):   # a re-run replaces the previous attempt's work
        (git or _wt_git)(["branch", "-D", task["branch"]], run["repo"])
        task = dict(task, branch="", commits=0)
        tasks = [task if t["id"] == task["id"] else t for t in tasks]
    branch, path, err = _orch_prepare_worktree(run, task, tasks, git)
    attempt = task["attempt"] + 1
    if err:
        _orch_set_task(conn, run["id"], task["id"], state="failed", attempt=attempt, error=err, finished=now)
        _orch_event(conn, run["id"], "task_failed", err, task["id"], now=now)
        return
    inputs = [t for t in tasks if t["id"] in task["depends_on"]]
    base = os.path.join(ORCH_DIR, run["id"], f"{task['id']}-{attempt}")
    try:
        agent = _orch_task_agent(task)
        # claude.ai connectors only load without the API key: such an agent runs on your subscription
        kw, deny, _ready = _orch_proc_env(agent)
        pid = (spawn or _orch_spawn)(_orch_agent_argv(exe, task, agent, deny, _orch_settings()["rules"]), path,
                                     _orch_task_prompt(run["goal"], task, inputs, run["base"]), base, **kw)
    except OSError as e:
        _wt_remove(run["repo"], path, branch, git)
        _orch_set_task(conn, run["id"], task["id"], state="failed", attempt=attempt, error=f"could not start claude: {e}"[:300],
                       finished=now)
        _orch_event(conn, run["id"], "task_failed", f"could not start claude: {e}", task["id"], now=now)
        return
    _orch_set_task(conn, run["id"], task["id"], state="running", attempt=attempt, branch=branch, path=path, pid=pid,
                   job_base=base, started=now, finished=None, error="", output="", commits=0)
    _orch_event(conn, run["id"], "task_started",
                f"{_orch_task_agent(task).get('name') or task['role']} agent ({task['model']}, ${task['budget_usd']:.2f}), "
                f"attempt {attempt}",
                task["id"], now=now)


def _orch_poll_task(conn, run, task, tasks, now, alive=None, read=None, kill=None, git=None):
    """A running task: still working, timed out (killed), or finished ->
    done / retried once / failed."""
    alive, read, kill = alive or _orch_alive, read or _orch_read_out, kill or _orch_kill
    running = alive(task["pid"]) and _orch_ours(task["pid"], task["started"])
    if running and now - (task["started"] or now) <= _ORCH_TASK_TIMEOUT_S:
        return
    # over time: killed. Gone by itself (even past the time, e.g. AOC was off): its answer is read
    timed_out = running and _orch_stop(task["pid"], task["started"], alive, kill)
    # killed, it never reports what it spent: count its whole budget (it may well have used it)
    res = _orch_parse(read(task["job_base"])) if not timed_out else \
        {"ok": False, "data": None, "cost": float(task["budget_usd"] or 0), "turns": 0, "session_id": "",
         "error": "timed out"}
    err = res["error"] or _orch_check_result(res["data"])
    status = (res["data"] or {}).get("status") if not err else None
    task = dict(task, cost_usd=task["cost_usd"] + res["cost"])
    commits = _orch_finish_worktree(run, task, tasks, git)
    if not err and status == "done" and not commits and _orch_agent_edits(_orch_task_agent(task)) \
            and (res["data"] or {}).get("files_changed"):
        # found live: an agent wrote to the path Git Bash's `pwd` printed (/tmp/...), which the Write
        # tool on Windows reads as C:\tmp\... -- it reported the file, its folder had nothing
        err = ("you reported changed files (" + ", ".join(map(str, res["data"]["files_changed"][:5])) + ") but none of "
               "them is in your folder -- write with paths relative to your current folder, never a path from `pwd`")
        status = None
    fields = {"pid": None, "finished": now, "cost_usd": task["cost_usd"], "turns": task["turns"] + res["turns"],
              "session_id": res["session_id"] or task["session_id"], "commits": commits,
              "output": json.dumps(res["data"], ensure_ascii=False) if res["data"] else "",
              "branch": task["branch"] if commits else ""}
    if not err and status == "done":
        _orch_set_task(conn, run["id"], task["id"], state="done", error="", **fields)
        _orch_event(conn, run["id"], "task_done", (res["data"] or {}).get("summary", ""), task["id"], res["cost"], now=now)
        return
    why = _orch_explain_error(err) if err else \
        f"the agent answered '{status}': " + _orch_clip((res["data"] or {}).get("summary"), 400)
    # found live: at the plan's usage limit every attempt fails at once -- a retry only burns the attempt
    if task["attempt"] < _ORCH_MAX_ATTEMPTS and status != "blocked" and not _orch_limit_hit(err):
        fb = (task.get("feedback") + "\n" if task.get("feedback") else "") + "Previous attempt: " + why
        # approved=0: your OK was for one run of a sensitive agent -- the next attempt asks again
        _orch_set_task(conn, run["id"], task["id"], state="pending", error=why, feedback=fb[:3000], approved=0, **fields)
        _orch_event(conn, run["id"], "task_retry", why, task["id"], res["cost"], now=now)
        return
    _orch_set_task(conn, run["id"], task["id"], state="failed", error=why, **fields)
    _orch_event(conn, run["id"], "task_failed", why, task["id"], res["cost"], now=now)


# ── managers (hierarchical mode): a workstream = a child run with its own team ─
def _orch_child_id(run_id, tid, attempt):
    return f"{run_id}-{tid}-t{attempt}"


def _orch_child_goal(run, task, inputs):
    """Pure: what a manager's team works towards: the workstream, the run's
    goal around it and what the tasks before it delivered."""
    lines = [task["instructions"], "", f"DONE WHEN: {task['done_criteria']}", "",
             f"This is one workstream ({task['id']}: {task['title']}) of a larger goal: {_orch_clip(run['goal'], 3000)}"]
    for t in inputs:
        o = t.get("output") or {}
        lines += ["", f"INPUT FROM {t['id']} ({t['role']}): {t['title']}",
                  "summary: " + _orch_clip(o.get("summary"), 1500), "result: " + _orch_clip(o.get("result"), 4000)]
        if o.get("for_next"):
            lines.append("for you: " + _orch_clip(o.get("for_next"), 2000))
        if t.get("commits") and t.get("branch"):
            lines.append("(its commits are already on this workstream's branch)")
    if task.get("feedback"):
        lines += ["", "THIS WORKSTREAM IS BEING REDONE. What was wrong last time:", task["feedback"]]
    return "\n".join(lines)


def _orch_child_summary(child, tasks):
    """Pure: a team's run as its manager's task card shows it."""
    return {"id": child["id"], "state": child["state"], "tasks": len(tasks),
            "done": sum(1 for t in tasks if t["state"] == "done"),
            "running": sum(1 for t in tasks if t["state"] == "running"),
            "error": child.get("error") or "", "plan_summary": child.get("plan_summary") or ""}


def _orch_child_output(child, tasks):
    """Pure: a finished team -> its manager task's result, in the agents'
    contract (the run above reads it like any task's)."""
    done = [t for t in tasks if t["state"] == "done"]
    team = "\n".join(f"- {t['id']} ({t['role']}): " + _orch_clip((t.get("output") or {}).get("summary"), 400)
                     for t in done)
    nxt = "\n".join(_orch_clip((t.get("output") or {}).get("for_next"), 600) for t in done
                    if (t.get("output") or {}).get("for_next"))
    report = str(child.get("report") or "").strip()
    return {"status": "done",
            "summary": _orch_clip(report.split("\n\n")[0] if report else f"{len(done)} task(s) done", 600),
            "result": (report + "\n\nTEAM:\n" + team).strip(), "files_changed": [], "for_next": nxt,
            "errors": [f"{t['id']}: " + _orch_clip(t.get("error"), 300) for t in tasks if t["state"] in ("failed", "blocked")]}


def _orch_integrate(child, ctasks, target, git=None):
    """Merge a finished team's developer branches into its workstream branch,
    in a temporary worktree, plan order (each after its inputs).
    -> (merged ids, error or '')."""
    git = git or _wt_git
    repo = child["repo"]
    path = os.path.join(repo, ".claude", "worktrees", f"aoc-orch-{child['id']}-integrate")
    if os.path.isdir(path):
        git(["worktree", "remove", "--force", path], repo, timeout=60)
    rc, _, er = git(["worktree", "add", path, target], repo, timeout=120)
    if rc != 0:
        return [], "git worktree add failed: " + _wt_err(er, "unknown error")
    merged, err = [], ""
    try:
        by_id = {t["id"]: t for t in ctasks}
        for tid in _orch_topo(ctasks) or [t["id"] for t in ctasks]:
            t = by_id[tid]
            if t["state"] != "done" or not t.get("commits") or not t.get("branch"):
                continue
            if git(["merge-base", "--is-ancestor", t["branch"], "HEAD"], path)[0] == 0:
                merged.append(tid)
                continue
            rc, _, er = git(["merge", "--no-ff", "--no-edit", "-m",
                             f"Merge {t['branch']}: {t['title']} (AOC team {child['id']})", t["branch"]], path, timeout=120)
            if rc != 0:
                git(["merge", "--abort"], path)
                err = f"'{tid}' conflicts with the team's other work: " + _wt_err(er, "merge failed")
                break
            merged.append(tid)
    finally:
        git(["worktree", "remove", "--force", path], repo, timeout=60)
        git(["worktree", "prune"], repo)
    return merged, err


def _orch_start_manager(conn, run, task, tasks, exe, now, spawn=None, git=None):
    """A workstream is due: its branch (the base + its inputs' work) and a
    child run on it, whose planner and reviewer are the manager."""
    git = git or _wt_git
    if not _orch_proc_env()[2]:
        return   # the MCP server list isn't known yet: start next tick
    if task.get("branch"):   # a re-run replaces the previous attempt's work
        git(["branch", "-D", task["branch"]], run["repo"])
        task = dict(task, branch="", commits=0)
        tasks = [task if t["id"] == task["id"] else t for t in tasks]
    attempt = task["attempt"] + 1
    branch, path, err = _orch_prepare_worktree(run, task, tasks, git)
    sha = ""
    if not err:
        # the worktree stays while the manager plans (it reads the workstream as it is, its inputs' work
        # included -- found live: planning in the main checkout, a manager thought its input was missing)
        rc, sha, er = git(["rev-parse", "HEAD"], path)
        if rc != 0 or not sha.strip():
            err = "could not read the workstream branch: " + _wt_err(er, "git failed")
            _wt_remove(run["repo"], path, branch, git)
    if err:
        _orch_set_task(conn, run["id"], task["id"], state="failed", attempt=attempt, error=err, finished=now)
        _orch_event(conn, run["id"], "task_failed", err, task["id"], now=now)
        return
    cid = _orch_child_id(run["id"], task["id"], attempt)
    goal = _orch_child_goal(run, task, [t for t in tasks if t["id"] in task["depends_on"]])
    conn.execute("""INSERT OR REPLACE INTO runs (id, created, updated, goal, repo, base, base_sha, state, model,
                    max_parallel, budget_usd, mode, parent_id, parent_task, approve_teams, team, kind)
                    VALUES (?,?,?,?,?,?,?,'planning',?,?,?,'flat',?,?,?,?,?)""",
                 (cid, now, now, goal, run["repo"], branch, sha.strip(), task["model"], run["max_parallel"],
                  task["budget_usd"], run["id"], task["id"], int(run.get("approve_teams") or 0),
                  json.dumps(run.get("team") or []), run.get("kind") or "repo"))
    _orch_event(conn, cid, "created", f"team for workstream '{task['id']}' of run {run['id']}", now=now)
    child, _ = _orch_load(conn, cid)
    listing, notes = _orch_repo_context(path, git)
    try:
        agents = _orch_team(child, _orch_library(conn))
        _orch_start_llm(conn, child, "plan", _orch_plan_prompt(goal, run["repo"], branch, listing, notes, mode="team",
                                                                budget=task["budget_usd"], agents=agents,
                                                                area=run.get("kind") or "repo"),
                        _orch_schema(_ORCH_PLAN_SCHEMA, False, [a["id"] for a in agents]), exe, now, spawn,
                        read_tools=True, cwd=path)
    except OSError as e:
        _orch_set_run(conn, cid, now=now, state="failed", error=f"could not start claude: {e}"[:300], finished=now)
        _wt_remove(run["repo"], path, branch, git)
        _orch_set_task(conn, run["id"], task["id"], state="failed", attempt=attempt, error=f"could not start claude: {e}"[:300],
                       finished=now, child_id=cid)
        _orch_event(conn, run["id"], "task_failed", f"could not start claude: {e}", task["id"], now=now)
        return
    _orch_set_task(conn, run["id"], task["id"], state="running", attempt=attempt, branch=branch, path=path, pid=None,
                   job_base="", child_id=cid, started=now, finished=None, error="", output="", commits=0)
    _orch_event(conn, run["id"], "task_started",
                f"Manager ({task['model']}, ${task['budget_usd']:.2f} for its team) is planning, attempt {attempt}",
                task["id"], now=now)


def _orch_poll_manager(conn, run, task, tasks, now, git=None):
    """A running workstream: approve its team's plan (unless you approve each
    one), wait for the team, then put its work on the workstream branch ->
    done / retried once / failed."""
    git = git or _wt_git
    child, ctasks = _orch_load(conn, task.get("child_id") or "")
    if not child:
        _orch_set_task(conn, run["id"], task["id"], state="failed", error="its team's run is gone", finished=now)
        _orch_event(conn, run["id"], "task_failed", "its team's run is gone", task["id"], now=now)
        return
    spent = round(_orch_spent(conn, child["id"]), 6)
    if spent != round(task["cost_usd"], 6):
        _orch_set_task(conn, run["id"], task["id"], cost_usd=spent)
    if task.get("path") and child.get("job_kind") != "plan":   # planned: the manager's reading copy goes
        if os.path.isdir(task["path"]):
            git(["worktree", "remove", "--force", task["path"]], run["repo"], timeout=60)
            git(["worktree", "prune"], run["repo"])
        _orch_set_task(conn, run["id"], task["id"], path="")
        task = dict(task, path="")
    if child["state"] == "plan_ready":
        if not run.get("approve_teams"):
            _orch_set_run(conn, child["id"], now=now, state="running")
            _orch_event(conn, child["id"], "approved", f"team plan auto-approved (the run's plan approval covers it): "
                                                       f"{len(ctasks)} task(s)", now=now)
            _orch_event(conn, run["id"], "team_approved", f"{len(ctasks)} task(s): " + _orch_clip(child.get("plan_summary"), 300),
                        task["id"], now=now)
        return
    if child["state"] in _ORCH_ACTIVE:
        if now - (task["started"] or now) <= _ORCH_MANAGER_TIMEOUT_S:
            return
        _orch_halt(conn, child, ctasks, now, git, "the workstream took too long")
        _orch_set_run(conn, child["id"], now=now, state="cancelled", job_kind="", job_pid=None,
                      error="the workstream took too long", finished=now)
        child, ctasks = _orch_load(conn, child["id"])
        spent = round(_orch_spent(conn, child["id"]), 6)   # + the budgets of what was just killed
    team_failed = [t for t in ctasks if t["state"] in ("failed", "blocked")]
    if child["state"] == "review" and not child.get("error") and not any(t["state"] == "done" for t in ctasks):
        # found live: every team task failed (usage limit) and its review failed too -- the workstream was
        # still reported done, with nothing on its branch
        why = "its team finished none of its tasks" + \
            (" (" + _orch_clip(team_failed[0].get("error"), 400) + ")" if team_failed and team_failed[0].get("error") else "")
    elif child["state"] == "review" and not child.get("error"):
        merged, err = _orch_integrate(child, ctasks, task["branch"], git)
        if not err:
            # merged into the workstream branch, not into the checked-out base: -d would refuse
            _orch_cleanup(child, ctasks, git)
            _orch_set_run(conn, child["id"], now=now, state="merged", finished=now)
            _orch_event(conn, child["id"], "merged", f"{len(merged)} branch(es) into {task['branch']} (by the run above)", now=now)
            commits = _orch_own_commits(run, task, tasks, git)
            if not commits:
                git(["branch", "-D", task["branch"]], run["repo"])
            out = _orch_child_output(child, ctasks)
            _orch_set_task(conn, run["id"], task["id"], state="done", error="", finished=now, cost_usd=spent, commits=commits,
                           branch=task["branch"] if commits else "", output=json.dumps(out, ensure_ascii=False))
            _orch_event(conn, run["id"], "task_done", out["summary"], task["id"], now=now)
            return
        why = "its team's work could not be put together: " + err
    else:
        why = child.get("error") or f"its team's run ended {child['state']}"
    if child["state"] in ("review", "failed"):
        _orch_cleanup(child, ctasks, git)
        _orch_set_run(conn, child["id"], now=now, state="discarded", finished=child.get("finished") or now)
    if task.get("branch"):
        git(["branch", "-D", task["branch"]], run["repo"])
    by_you = child["state"] == "cancelled" and not child.get("error")
    fields = {"finished": now, "cost_usd": spent, "branch": "", "commits": 0, "error": why}
    if task["attempt"] < _ORCH_MAX_ATTEMPTS and not by_you and not _orch_limit_hit(why):
        fb = (task.get("feedback") + "\n" if task.get("feedback") else "") + "Previous attempt: " + why
        _orch_set_task(conn, run["id"], task["id"], state="pending", feedback=fb[:3000], **fields)
        _orch_event(conn, run["id"], "task_retry", why, task["id"], now=now)
        return
    _orch_set_task(conn, run["id"], task["id"], state="failed", **fields)
    _orch_event(conn, run["id"], "task_failed", why, task["id"], now=now)


def _orch_spent(conn, run_id):
    work = conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM tasks WHERE run_id=?", (run_id,)).fetchone()[0]
    coord = conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM events WHERE run_id=? AND kind IN "
                         "('plan_ready','plan_failed','supervise')", (run_id,)).fetchone()[0]
    return float(work) + float(coord)


def _orch_llm_budget(run, spent):
    """Pure: the planner's / supervisor's cap -- its usual budget, but not
    more than what the run has left (a flat $1 per review would let a run go
    past its budget round after round). At least $0.05: room to answer."""
    return round(min(_ORCH_PLANNER_BUDGET, max(0.05, float(run.get("budget_usd") or 0) - float(spent or 0))), 2)


def _orch_start_llm(conn, run, kind, prompt, schema, exe, now, spawn=None, read_tools=False, budget=None, cwd=None):
    """Start the planner / supervisor. False = not now (the MCP server list
    the subscription needs isn't known yet)."""
    kw, deny, ready = _orch_proc_env()
    if not ready:
        return False
    base = os.path.join(ORCH_DIR, run["id"], f"{kind}-{int(now)}")
    if kind in ("plan", "supervise"):
        prompt += _orch_rules_text(_orch_settings()["rules"], kind)
    budget = budget or _orch_llm_budget(run, _orch_spent(conn, run["id"]))
    pid = (spawn or _orch_spawn)(_orch_llm_argv(exe, schema, run["model"], budget, read_tools, deny),
                                 cwd or run["repo"], prompt, base, **kw)
    _orch_set_run(conn, run["id"], now=now, job_kind=kind, job_pid=pid, job_base=base, job_started=now)
    return True


def _orch_poll_llm(conn, run, tasks, now, alive=None, read=None, kill=None):
    """The planner's / supervisor's process: still thinking, or its answer
    -> plan_ready / next step."""
    alive, read, kill = alive or _orch_alive, read or _orch_read_out, kill or _orch_kill
    running = alive(run["job_pid"]) and _orch_ours(run["job_pid"], run["job_started"])
    if running and now - (run["job_started"] or now) <= _ORCH_LLM_TIMEOUT_S:
        return
    timed_out = running and _orch_stop(run["job_pid"], run["job_started"], alive, kill)
    # killed before it reported a cost: count the budget it was started with (nothing was spent since)
    res = _orch_parse(read(run["job_base"])) if not timed_out else \
        {"ok": False, "data": None, "cost": _orch_llm_budget(run, _orch_spent(conn, run["id"])),
         "error": "the orchestrator took too long"}
    kind = run["job_kind"]
    _orch_set_run(conn, run["id"], now=now, job_kind="", job_pid=None, job_base="", job_started=None)
    if kind == "plan":
        if not res["ok"]:
            _orch_event(conn, run["id"], "plan_failed", res["error"], cost=res["cost"], now=now)
            _orch_set_run(conn, run["id"], now=now, state="failed", error="planning failed: " + _orch_explain_error(res["error"]),
                          finished=now)
            return
        plan = res["data"]
        new, err = _orch_validate_tasks(plan.get("tasks"), allow_manager=_orch_allows_manager(run),
                                        roles=_orch_team_ids(conn, run))
        if err:
            _orch_event(conn, run["id"], "plan_failed", err, cost=res["cost"], now=now)
            _orch_set_run(conn, run["id"], now=now, state="failed", error="the plan was invalid: " + err, finished=now)
            return
        new, scaled = _orch_fit_budgets(new, run["budget_usd"])
        if scaled:
            _orch_event(conn, run["id"], "budgets_scaled", f"the plan's budgets were over 85% of ${run['budget_usd']:.2f} "
                                                          "and were scaled down to fit", now=now)
        _orch_insert_tasks(conn, run["id"], new, 0)
        _orch_set_run(conn, run["id"], now=now, state="plan_ready", plan_summary=str(plan.get("summary") or "")[:2000],
                      single_agent=1 if plan.get("single_agent") else 0)
        _orch_event(conn, run["id"], "plan_ready", f"{len(new)} task(s): " + str(plan.get("summary") or "")[:500],
                    cost=res["cost"], now=now)
        return
    # supervise
    rounds = int(run.get("rounds") or 0) + 1
    if not res["ok"]:
        _orch_event(conn, run["id"], "supervise", "no decision: " + res["error"], cost=res["cost"], now=now)
        _orch_set_run(conn, run["id"], now=now, state="review", rounds=rounds,
                      report="The orchestrator's review failed (" + res["error"] + "). Check the task results below.")
        _orch_event(conn, run["id"], "review_ready", "waiting for the run above" if run.get("parent_id")
                    else "waiting for your merge decision", now=now)
        return
    action, ch, err = _orch_apply_decision(tasks, res["data"], rounds >= _ORCH_MAX_ROUNDS, _orch_allows_manager(run),
                                           _orch_team_ids(conn, run))
    waiting = "waiting for the run above to take its work" if run.get("parent_id") else "waiting for your merge decision"
    msg = f"{action}: " + str(res["data"].get("reason") or "")[:600] + (f" ({err})" if err else "")
    _orch_event(conn, run["id"], "supervise", msg, cost=res["cost"], now=now)
    if action == "retry":
        again = set()
        for tid, fb in ch["retry"]:
            again.add(tid)
            again |= _orch_dependents(tasks, tid)
        for t in tasks:
            if t["id"] in again:
                fb = dict(ch["retry"]).get(t["id"], "")
                # a redo of a sensitive agent's task waits for your OK again (approved=0)
                _orch_set_task(conn, run["id"], t["id"], state="pending", feedback=fb or t["feedback"], error="",
                               approved=0)
        _orch_set_run(conn, run["id"], now=now, state="running", rounds=rounds)
    elif action == "add_tasks":
        _orch_insert_tasks(conn, run["id"], ch["add"], len(tasks), added_round=rounds)
        _orch_set_run(conn, run["id"], now=now, state="running", rounds=rounds)
    elif action == "fail":
        _orch_set_run(conn, run["id"], now=now, state="review", rounds=rounds, report=ch["report"],
                      error="the orchestrator could not reach the goal")
        _orch_event(conn, run["id"], "review_ready", "the orchestrator gave up -- read the report", now=now)
    else:
        _orch_set_run(conn, run["id"], now=now, state="review", rounds=rounds, report=ch["report"])
        _orch_event(conn, run["id"], "review_ready", waiting, now=now)


def _orch_insert_tasks(conn, run_id, tasks, seq0, added_round=0):
    """Each task keeps a copy of its agent as it was when planned."""
    lib = _orch_library(conn)
    for i, t in enumerate(tasks):
        agent = lib.get(t["role"]) if t["role"] != "manager" else None
        conn.execute("""INSERT INTO tasks (run_id, id, seq, role, title, instructions, done_criteria, depends_on,
                        model, budget_usd, state, added_round, agent) VALUES (?,?,?,?,?,?,?,?,?,?,'pending',?,?)""",
                     (run_id, t["id"], seq0 + i, t["role"], t["title"], t["instructions"], t["done_criteria"],
                      json.dumps(t["depends_on"]), t["model"], t["budget_usd"], added_round,
                      json.dumps(agent, ensure_ascii=False) if agent else ""))


def _orch_team_ids(conn, run):
    """The agent ids a run's plan may use."""
    return [a["id"] for a in _orch_team(run, _orch_library(conn))]


def _orch_step(conn, run_id, now=None, exe=None, spawn=None, alive=None, read=None, kill=None, git=None):
    """One engine tick for one run."""
    now = now or time.time()
    run, tasks = _orch_load(conn, run_id)
    if not run or run["state"] not in _ORCH_ACTIVE:
        return
    if run["job_kind"]:
        _orch_poll_llm(conn, run, tasks, now, alive, read, kill)
        conn.commit()
        return
    if run["state"] in ("planning", "supervising"):
        # planning / reviewing with no planner / reviewer process noted: nothing would ever move it on
        why = "the orchestrator's " + ("planner" if run["state"] == "planning" else "review") + \
              " never started (or its process was lost) -- start the run again"
        _orch_set_run(conn, run_id, now=now, state="failed", error=why, finished=now)
        _orch_event(conn, run_id, "failed", why, now=now)
        conn.commit()
        return
    if run["state"] != "running":
        return
    for t in tasks:
        if t["state"] == "running" and t["role"] == "manager":
            _orch_poll_manager(conn, run, t, tasks, now, git)
        elif t["state"] == "running":
            _orch_poll_task(conn, run, t, tasks, now, alive, read, kill, git)
    conn.commit()
    run, tasks = _orch_load(conn, run_id)
    acts = _orch_next(run, tasks, _orch_spent(conn, run_id))
    exe = exe or _find_claude_exe()
    for a in acts:
        if a[0] == "gate":
            t = next(t for t in tasks if t["id"] == a[1])
            _orch_set_task(conn, run_id, a[1], state="gate")
            _orch_event(conn, run_id, "task_gate", f"waits for your OK before it starts ({_orch_task_agent(t).get('name')} "
                        "is a sensitive agent)", a[1], now=now)
        elif a[0] in ("block", "skip"):
            _orch_set_task(conn, run_id, a[1], state="blocked" if a[0] == "block" else "skipped", error=a[2], finished=now)
            _orch_event(conn, run_id, "task_" + ("blocked" if a[0] == "block" else "skipped"), a[2], a[1], now=now)
        elif a[0] == "start":
            if not exe:
                _orch_set_run(conn, run_id, now=now, state="failed", error="Claude Code (claude) not found", finished=now)
                break
            run, tasks = _orch_load(conn, run_id)
            t = next(t for t in tasks if t["id"] == a[1])
            (_orch_start_manager if t["role"] == "manager" else _orch_start_task)(conn, run, t, tasks, exe, now, spawn, git)
        elif a[0] == "supervise" and run.get("baseline_of"):
            # the one-agent comparison: no orchestrator review (that's the point) -- its answer is the report
            run, tasks = _orch_load(conn, run_id)
            o = (tasks[0].get("output") or {}) if tasks else {}
            _orch_set_run(conn, run_id, now=now, state="review" if tasks and tasks[0]["state"] == "done" else "failed",
                          report=str(o.get("result") or o.get("summary") or "")[:6000],
                          error="" if tasks and tasks[0]["state"] == "done" else "the single agent didn't finish",
                          finished=now if not (tasks and tasks[0]["state"] == "done") else None)
            _orch_event(conn, run_id, "review_ready", "the one-agent version is ready to compare", now=now)
        elif a[0] == "supervise":
            run, tasks = _orch_load(conn, run_id)
            run["_spent"] = _orch_spent(conn, run_id)
            last = int(run.get("rounds") or 0) + 1 >= _ORCH_MAX_ROUNDS
            if not exe:
                _orch_set_run(conn, run_id, now=now, state="failed", error="Claude Code (claude) not found", finished=now)
                break
            if not _orch_proc_env()[2]:
                break
            if float(run["budget_usd"] or 0) - run["_spent"] < 0.05:
                # nothing left to pay a review with: the results go to you (or the run above) as they are
                _orch_set_run(conn, run_id, now=now, state="review",
                              report="The run's budget is used up, so the orchestrator did not review the results. "
                                     "Check the task results below.")
                _orch_event(conn, run_id, "review_ready", "budget used up -- " +
                            ("waiting for the run above" if run.get("parent_id") else "waiting for your merge decision"),
                            now=now)
                break
            _orch_set_run(conn, run_id, now=now, state="supervising")
            _orch_start_llm(conn, run, "supervise", _orch_supervise_prompt(run, tasks, last),
                            _orch_schema(_ORCH_DECISION_SCHEMA, _orch_allows_manager(run), _orch_team_ids(conn, run)),
                            exe, now, spawn)
    conn.commit()


def _orch_tick(now=None):
    if not os.path.exists(ORCH_DB):
        return
    with _orch_lock:
        conn = _orch_connect()
        try:
            for (rid,) in conn.execute("SELECT id FROM runs WHERE state IN ('planning','running','supervising')").fetchall():
                try:
                    _orch_step(conn, rid, now)
                except Exception as e:
                    # undo what the step half-wrote: the next run's commit would otherwise save it
                    conn.rollback()
                    _log_bg_error("orchestration step", e)
            # runs whose start time has come (also one missed while AOC was off: it starts now, once)
            for (rid,) in conn.execute("SELECT id FROM runs WHERE state='scheduled' AND start_at <= ?",
                                       (now or time.time(),)).fetchall():
                try:
                    _orch_start_due(conn, rid, now)
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    _log_bg_error("orchestration scheduled run", e)
            limits = None
            for (eid,) in conn.execute("SELECT id FROM entities").fetchall():
                try:
                    if limits is None:
                        limits = _rate_limits_snapshot() or {}
                    _orch_entity_tick(conn, eid, now, limits=limits)
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    _log_bg_error("orchestration entity", e)
            for (sid,) in conn.execute("SELECT id FROM schedules").fetchall():
                try:
                    if limits is None:
                        limits = _rate_limits_snapshot() or {}
                    _orch_schedule_tick(conn, sid, now, limits=limits)
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    _log_bg_error("orchestration schedule", e)
            try:
                _orch_attention_refresh(conn)
            except Exception as e:
                _log_bg_error("orchestration attention", e)
        finally:
            conn.close()


# ── Stats across runs (PDF ch. 9: measure before adding agents) ───────────
def _orch_stats_from(rows):
    """Pure: [(run, tasks, metrics)] of top-level runs -> totals, and per
    agent, per model and per kind of run (one team / hierarchical / an
    entity's): how often it worked, what it cost, how long it took."""
    def bucket():
        return {"n": 0, "done": 0, "failed": 0, "cost": 0.0, "secs": 0.0, "timed": 0, "retries": 0}
    agents, models, kinds = {}, {}, {}
    tot = {"runs": 0, "merged": 0, "discarded": 0, "failed": 0, "cancelled": 0, "cost": 0.0, "coordination": 0.0,
           "secs": 0.0}
    for run, tasks, m in rows:
        tot["runs"] += 1
        tot["cost"] += m["cost_total"]
        tot["coordination"] += m["cost_coordination"]
        tot["secs"] += m["duration_s"] or 0
        if run["state"] in ("merged", "discarded", "failed", "cancelled"):
            tot[run["state"]] += 1
        kind = "entity" if run.get("entity_id") else ("hierarchical" if run.get("mode") == "hierarchical" else "one team")
        k = kinds.setdefault(kind, {"runs": 0, "kept": 0, "dropped": 0, "cost": 0.0})
        k["runs"] += 1
        k["cost"] += m["cost_total"]
        k["kept"] += run["state"] == "merged"
        k["dropped"] += run["state"] == "discarded"
        for t in tasks:
            if t["state"] not in ("done", "failed", "blocked"):
                continue
            name = "Manager" if t["role"] == "manager" else (_orch_task_agent(t).get("name") or t["role"])
            for b in (agents.setdefault(name, bucket()), models.setdefault(t.get("model") or "?", bucket())):
                b["n"] += 1
                b["done" if t["state"] == "done" else "failed"] += 1
                b["cost"] += t.get("cost_usd") or 0
                b["retries"] += max(0, (t.get("attempt") or 1) - 1)
                if t.get("started") is not None and t.get("finished") is not None:
                    b["secs"] += t["finished"] - t["started"]
                    b["timed"] += 1

    def finish(d):
        return [dict(name=k, n=b["n"], success=round(b["done"] / b["n"], 3) if b["n"] else None,
                     avg_cost=round(b["cost"] / b["n"], 4) if b["n"] else 0, avg_s=round(b["secs"] / b["timed"], 1) if b["timed"] else None,
                     retries=b["retries"]) for k, b in sorted(d.items(), key=lambda kv: -kv[1]["n"])]
    ended = tot["merged"] + tot["discarded"]
    return {"runs": tot["runs"], "merged": tot["merged"], "discarded": tot["discarded"], "failed": tot["failed"],
            "cancelled": tot["cancelled"], "kept_rate": round(tot["merged"] / ended, 3) if ended else None,
            "cost": round(tot["cost"], 4), "avg_cost": round(tot["cost"] / tot["runs"], 4) if tot["runs"] else 0,
            "coordination_share": round(tot["coordination"] / tot["cost"], 3) if tot["cost"] else 0,
            "avg_s": round(tot["secs"] / tot["runs"], 1) if tot["runs"] else None,
            "by_agent": finish(agents), "by_model": finish(models),
            "by_kind": [dict(name=k, runs=v["runs"], kept=v["kept"], dropped=v["dropped"],
                             avg_cost=round(v["cost"] / v["runs"], 4) if v["runs"] else 0) for k, v in kinds.items()]}


def _orch_stats(days=90, now=None):
    """GET /orch/stats: the last `days` days of top-level runs."""
    if not os.path.exists(ORCH_DB):
        return dict(_orch_stats_from([]), ok=True, days=days)
    now = now or time.time()
    conn = _orch_connect()
    try:
        rows = []
        for (rid,) in conn.execute("SELECT id FROM runs WHERE COALESCE(parent_id,'')='' AND created >= ?",
                                   (now - days * 86400,)).fetchall():
            run, tasks = _orch_load(conn, rid)
            events = [dict(e) for e in conn.execute("SELECT * FROM events WHERE run_id=?", (rid,))]
            # a manager's team: its agents count too
            all_tasks = list(tasks)
            for t in tasks:
                if t.get("child_id"):
                    all_tasks += _orch_load(conn, t["child_id"])[1]
            rows.append((run, all_tasks, _orch_metrics(run, tasks, events, now,
                                                      team_coordination=_orch_team_coordination(conn, rid))))
        return dict(_orch_stats_from(rows), ok=True, days=days)
    finally:
        conn.close()


# ── What waits for you (Inbox, sidebar badge, toast + phone push) ─────────
ORCH_NOTIFIED_FILE = os.path.join(AOC_DATA_DIR, "orch_notified.json")
_orch_attention_state = {"items": [], "notified": None}


def _orch_attention_items(conn):
    """Plans to approve (not an entity's: it approves its own), team plans
    you approve yourself, sensitive tasks at their gate, results to merge."""
    items = []
    for r in conn.execute("SELECT id, goal, state, parent_id, parent_task, entity_id, updated, approve_teams FROM runs "
                          "WHERE state IN ('plan_ready', 'review')").fetchall():
        r = dict(r)
        kind = None
        if r["state"] == "plan_ready" and not r["entity_id"]:
            kind = ("team_plan" if r["approve_teams"] else None) if r["parent_id"] else "plan"
        elif r["state"] == "review" and not r["parent_id"]:
            kind = "review"
        if kind:
            # a team's goal is its workstream, then the context from the run above (DONE WHEN: ...): the title is the workstream
            goal = (r["goal"] or "").split("\n\nDONE WHEN:")[0] if r["parent_id"] else r["goal"]
            items.append({"key": f"{kind}:{r['id']}", "kind": kind, "run_id": r["id"], "task_id": "",
                          "title": _orch_clip(goal, 160), "detail": r.get("parent_task") or "",
                          "entity": r.get("entity_id") or "", "since": r["updated"]})
    # only a run still going: a gate left on a stopped one can't be answered (and would nag forever)
    for t in conn.execute("SELECT t.run_id, t.id, t.title, t.agent, r.goal, r.updated, r.entity_id FROM tasks t "
                          "JOIN runs r ON t.run_id = r.id WHERE t.state = 'gate' AND r.state IN "
                          "('planning','running','supervising')").fetchall():
        t = dict(t)
        try:
            agent = json.loads(t["agent"]) if t["agent"] else {}
        except ValueError:
            agent = {}
        items.append({"key": f"gate:{t['run_id']}:{t['id']}", "kind": "gate", "run_id": t["run_id"], "task_id": t["id"],
                      "title": _orch_clip(t["title"], 160), "detail": (agent or {}).get("name") or "",
                      "entity": t.get("entity_id") or "", "since": t["updated"]})
    return sorted(items, key=lambda i: i["since"] or 0)


def _orch_attention_message(item):
    """Pure: the toast / push for one new item."""
    return {"plan": ("🤖 Plan ready for your OK", item["title"]),
            "team_plan": ("🤖 A team's plan needs your OK", f"{item['detail']}: {item['title']}"),
            "gate": ("✋ A sensitive task needs your OK", f"{item['detail']}: {item['title']}"),
            "review": ("✅ Results ready to review", item["title"])}.get(item["kind"], ("🤖 Orchestration", item["title"]))


def _orch_attention_refresh(conn, notify=None, now=None):
    """After each tick: the new list for /status (a change bumps the
    dashboard), and a notification for each item not announced before
    (remembered on disk, so a monitor reload doesn't repeat them)."""
    st = _orch_attention_state
    items = _orch_attention_items(conn)
    if st["notified"] is None:
        d = _load_json_file(ORCH_NOTIFIED_FILE, [])
        st["notified"] = list(d) if isinstance(d, list) else []
    if [i["key"] for i in items] != [i["key"] for i in st["items"]]:
        st["items"] = items
        try:
            with _status_cond:
                _status_version[0] += 1
                _status_cond.notify_all()
        except NameError:
            pass
    new = [i for i in items if i["key"] not in st["notified"]]
    for i in new:
        title, msg = _orch_attention_message(i)
        try:
            (notify or _show_native_toast)(title, msg, kind="orchestration")
        except Exception as e:
            _log_bg_error("orchestration notify", e)
        st["notified"].append(i["key"])
    if new:
        st["notified"] = st["notified"][-300:]
        try:
            with open(ORCH_NOTIFIED_FILE, "w", encoding="utf-8") as f:
                json.dump(st["notified"], f)
        except OSError:
            pass
    return items


def _orch_worker():
    while True:
        try:
            _orch_tick()
        except Exception as e:
            _log_bg_error("orchestration worker", e)
        time.sleep(3)


# ── workspaces: where runs without a code repository work ─────────────────
# A workspace is a folder AOC makes and keeps in git under
# %LOCALAPPDATA%\AOC\workspaces\<name>: the same engine (worktrees,
# parallel agents, managers, retries) works on it, every report or export an
# agent writes is a commit, and accepting a run merges them in. Your own
# folders are never turned into git repositories.
ORCH_WORKSPACES_DIR = os.path.join(AOC_DATA_DIR, "workspaces")


def _orch_workspace_slug(name):
    return re.sub(r"[^a-z0-9-]+", "-", str(name or "").strip().lower()).strip("-")[:40]


def _orch_workspace(name, git=None, root=None):
    """The workspace's folder, made (git init + a first commit) if new.
    -> (path, None) or (None, error)."""
    git = git or _wt_git
    slug = _orch_workspace_slug(name)
    if not slug:
        return None, "name the workspace (letters, digits, -)"
    path = os.path.join(root or ORCH_WORKSPACES_DIR, slug)
    if os.path.isdir(os.path.join(path, ".git")):
        return path, None
    try:
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "README.md"), "w", encoding="utf-8") as f:
            f.write(f"# {slug}\n\nAOC workspace: what its agents produce (reports, notes, exports).\n")
    except OSError as e:
        return None, f"could not make the workspace folder: {e}"
    for args in (["init", "-q", "-b", "main"], ["config", "user.name", "AOC"], ["config", "user.email", "aoc@localhost"],
                 ["add", "-A"], ["commit", "-q", "-m", "Workspace " + slug]):
        rc, _, er = git(args, path)
        if rc != 0:
            return None, "git: " + _wt_err(er, "could not set the workspace up")
    return path, None


def _orch_workspaces(root=None):
    """GET /orch/workspaces: the workspaces there are, newest first."""
    root = root or ORCH_WORKSPACES_DIR
    out = []
    try:
        for n in os.listdir(root):
            p = os.path.join(root, n)
            if os.path.isdir(os.path.join(p, ".git")):
                out.append({"name": n, "path": p, "mtime": os.path.getmtime(p)})
    except OSError:
        pass
    return {"ok": True, "workspaces": sorted(out, key=lambda w: -w["mtime"])}


# ── Entities: an autonomous team with a mission ───────────────────────────
# An entity = a mission, where it works (a repo or a workspace), a team of
# agents, an orchestrator, budgets (per run / day / month) and when it acts
# (by hand, every N hours, daily or on weekdays at HH:MM; "loop" = again
# after each run, after a cooldown). When it's due and its budget and your plan limits
# allow, a scout (`claude -p`, read-only tools in its folder) reads the
# mission, the entity's memory and its recent runs and either names the next
# concrete goal or says there's nothing to do now. A goal becomes an ordinary
# run, whose plan the entity approves itself (within its per-run budget);
# merging / accepting the results stays yours. Every finished run goes into
# the entity's memory (goal, outcome -- you kept or dropped it -- cost,
# summary), which the next scout reads.
_ORCH_ENTITY_BUDGET_RUN = (0.5, 20.0)
_ORCH_ENTITY_BUDGET_DAY = (0.5, 200.0)
_ORCH_ENTITY_BUDGET_MONTH = (1.0, 2000.0)
_ORCH_ENTITY_MEMORY = 15            # finished runs remembered
_ORCH_ENTITY_LIMIT_PCT = 90.0       # pause while a plan-limit window is this full
_ORCH_ENTITY_SCOUT_BUDGET = 0.5
_ORCH_SCOUT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["run", "idle"]},
        "goal": {"type": "string", "description": "for run: one concrete goal a team can finish in one run"},
        "reason": {"type": "string", "description": "why this goal now, or why there is nothing to do"},
        "mode": {"type": "string", "enum": list(_ORCH_MODES)},
    },
    "required": ["action", "goal", "reason", "mode"],
    "additionalProperties": False,
}
_ORCH_RUN_ACTIVE = ("planning", "plan_ready", "running", "supervising", "review")


def _orch_validate_schedule(sched, types):
    """Pure: when something acts (an entity, a repeating run) -> ({type,
    hours | at}, None) or (None, error). An unknown type is types[0]."""
    sched = sched if isinstance(sched, dict) else {}
    stype = sched.get("type") if sched.get("type") in types else types[0]
    out = {"type": stype}
    if stype == "every":
        try:
            out["hours"] = min(max(float(sched.get("hours")), 0.5), 24 * 7)
        except (TypeError, ValueError):
            return None, "every how many hours?"
    if stype in ("daily", "weekdays"):
        at = str(sched.get("at") or "")
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at):
            return None, ("daily" if stype == "daily" else "on weekdays") + " at HH:MM"
        out["at"] = at
    return out, None


def _orch_slots(s, now):
    """Pure: a daily / weekdays schedule -> (its last slot at or before now,
    None if none in the last week; its next slot after now), local time."""
    hh, mm = (int(x) for x in s["at"].split(":"))
    lt = time.localtime(now)

    def slot(d):   # d days from today (mktime folds day 0 / 32 into the month around it, and finds DST)
        return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + d, hh, mm, 0, 0, 0, -1))
    ok = (lambda t: time.localtime(t).tm_wday < 5) if s.get("type") == "weekdays" else (lambda t: True)
    prev = next((t for t in (slot(-d) for d in range(8)) if t <= now and ok(t)), None)
    return prev, next(t for t in (slot(d) for d in range(8)) if t > now and ok(t))


def _orch_validate_entity(data):
    """Pure: POST /orch/entity_save body -> (entity config, None) or (None, error)."""
    if not isinstance(data, dict):
        return None, "bad request"
    eid = re.sub(r"[^a-z0-9-]+", "-", str(data.get("id") or data.get("name") or "").strip().lower()).strip("-")[:32]
    if not eid:
        return None, "name the entity"
    mission = str(data.get("mission") or "").strip()
    if len(mission) < 20:
        return None, "describe its mission (20 characters at least)"
    if len(mission) > 6000:
        return None, "the mission is too long (6,000 characters max)"
    where = "workspace" if data.get("where") == "workspace" else "repo"
    cwd, ws = str(data.get("cwd") or "").strip().strip('"'), _orch_workspace_slug(data.get("workspace"))
    if where == "repo" and not (cwd and os.path.isabs(cwd)):
        return None, "pick its repository folder (full path)"
    if where == "workspace" and not ws:
        return None, "name its workspace"
    model = str(data.get("model") or "opus").strip().lower()
    if model not in _ORCH_MODELS:
        return None, "unknown model"
    out_sched, err = _orch_validate_schedule(data.get("schedule"), ("manual", "every", "daily", "weekdays"))
    if err:
        return None, err
    try:
        par = min(max(int(data.get("max_parallel") or 2), 1), _ORCH_MAX_PARALLEL)
        cooldown = min(max(int(data.get("cooldown_min") or 10), 1), 24 * 60)
    except (TypeError, ValueError):
        return None, "bad number"
    b_run = _orch_clamp(data.get("budget_run"), _ORCH_ENTITY_BUDGET_RUN, 3.0)
    b_day = _orch_clamp(data.get("budget_day"), _ORCH_ENTITY_BUDGET_DAY, 10.0)
    b_month = _orch_clamp(data.get("budget_month"), _ORCH_ENTITY_BUDGET_MONTH, 100.0)
    if b_run > b_day or b_day > b_month:
        return None, "budgets must grow: per run <= per day <= per month"
    return {"id": eid, "name": str(data.get("name") or eid).strip()[:60], "mission": mission, "where": where,
            "cwd": cwd if where == "repo" else "", "workspace": ws if where == "workspace" else "",
            "team": [str(a) for a in (data.get("team") or [])][:20], "model": model,
            "mode": "hierarchical" if data.get("mode") == "hierarchical" else "flat", "max_parallel": par,
            "budget_run": b_run, "budget_day": b_day, "budget_month": b_month, "schedule": out_sched,
            "loop": data.get("loop") is True, "cooldown_min": cooldown}, None


def _orch_entity_due(cfg, ent, last_end, now):
    """Pure: is the entity due to look for work? -> (due, why, next_due or None).
    last_end = when its last run ended (None if none yet)."""
    if ent.get("run_now"):
        return True, "you asked", None
    last_scout = float(ent.get("last_scout") or 0)
    nexts = []
    if cfg.get("loop"):
        cool = cfg.get("cooldown_min", 10) * 60
        if last_end is None and not last_scout:
            return True, "loop: first run", None
        if last_end is not None and last_scout < last_end:
            if now >= last_end + cool:
                return True, "loop: the last run ended", None
            nexts.append(last_end + cool)
    s = cfg.get("schedule") or {}
    if s.get("type") == "every":
        at = last_scout + s["hours"] * 3600 if last_scout else now
        if now >= at:
            return True, f"every {s['hours']:g} h", None
        nexts.append(at)
    elif s.get("type") in ("daily", "weekdays"):
        prev, nxt = _orch_slots(s, now)
        lt = time.localtime(now)
        day0 = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        # slots missed while AOC was off: the newest fires once, not one per slot; never acted yet: today's only
        if prev is not None and last_scout < prev and (last_scout or prev >= day0):
            return True, ("daily" if s["type"] == "daily" else "weekdays") + f" at {s['at']}", None
        nexts.append(nxt)
    return False, "", (min(nexts) if nexts else None)


def _orch_entity_spend(costs, now):
    """Pure: [(epoch, $)] -> ($ today, $ this month) in local time."""
    lt = time.localtime(now)
    day0 = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    month0 = time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1))
    return (round(sum(c for t, c in costs if t >= day0), 4), round(sum(c for t, c in costs if t >= month0), 4))


def _orch_limits_block(view, pct=_ORCH_ENTITY_LIMIT_PCT):
    """Pure: the /status rate_limits block -> why the entity must wait ('' = fine)."""
    for w in (view or {}).get("windows") or []:
        if isinstance(w.get("pct"), (int, float)) and w["pct"] >= pct:
            return f"your {w.get('label') or w.get('kind')} plan limit is at {w['pct']:g} %"
    return ""


def _orch_memory_entry(run, tasks, events):
    """Pure: a finished run -> one memory line for its entity."""
    outcome = {"merged": "accepted by you", "discarded": "dropped by you", "failed": "failed",
               "cancelled": "cancelled"}.get(run["state"], run["state"])
    m = _orch_metrics(run, tasks, events)
    summary = str(run.get("report") or run.get("error") or "").strip().split("\n\n")[0]
    return {"run_id": run["id"], "ts": run.get("finished") or run.get("updated") or run["created"],
            "goal": _orch_clip(run["goal"], 300), "outcome": outcome, "cost": m["cost_total"],
            "summary": _orch_clip(summary, 500)}


def _orch_scout_prompt(cfg, memory, recent, files, notes, spend, now, agents=None):
    """Pure: what the scout gets: the mission, memory, what runs now, the money left."""
    lines = [f"You are the scout of an autonomous AI team called '{cfg['name']}'. Decide what the team should do next "
             "to serve its MISSION, or that there is nothing worth doing right now. You may read the "
             + ("workspace" if cfg["where"] == "workspace" else "repository") + " (Read/Grep/Glob); you do not do the work.",
             "", "MISSION:", cfg["mission"], "",
             f"Today is {time.strftime('%Y-%m-%d %H:%M', time.localtime(now))}. Money left: today "
             f"${max(0.0, cfg['budget_day'] - spend[0]):.2f}, this month ${max(0.0, cfg['budget_month'] - spend[1]):.2f}; "
             f"one run may spend up to ${cfg['budget_run']:.2f}.", "",
             "MEMORY (the team's last runs, newest first; 'dropped by you' means the human rejected that work -- "
             "don't repeat it the same way):"]
    lines += [f"- {time.strftime('%m-%d %H:%M', time.localtime(e['ts']))} {e['goal']} -> {e['outcome']} (${e['cost']:.2f})"
              + (f": {e['summary']}" if e.get("summary") else "") for e in reversed(memory)] or ["(none yet)"]
    if recent:
        lines += ["", "IN PROGRESS / WAITING:"] + [f"- {r['goal']} [{r['state']}]" for r in recent]
    if agents:
        lines += ["", "THE TEAM (what it can do -- its agents look things up, read and write files, use services):",
                  _orch_agents_text(agents)]
    lines += ["", "Rules:",
              "- Judge by what the TEAM can find out, not by what you know: you don't need the answer yourself -- if a "
              "researcher can look it up (web, files, services), that is a goal (found live: a scout idled because "
              "its own knowledge was old, with a web researcher in the team).",
              "- One goal, concrete and finishable by a small team in one run within the per-run budget. Prefer the "
              "most valuable next step for the mission; build on what was accepted.",
              "- Answer idle when the mission is served for now, when the useful work needs a human decision first, "
              "or when the money left is too little. Say why.",
              "- mode: hierarchical only for a goal with clearly separate areas; flat otherwise.",
              "", "FILES:", files or "(empty)", "", "NOTES:", notes or "(none)"]
    return "\n".join(lines)


def _orch_entity_load(conn, eid):
    r = conn.execute("SELECT * FROM entities WHERE id=?", (eid,)).fetchone()
    if not r:
        return None, None
    ent = dict(r)
    try:
        cfg = json.loads(ent["data"])
    except (ValueError, TypeError):
        return None, None
    try:
        ent["memory"] = json.loads(ent.get("memory") or "[]")
    except ValueError:
        ent["memory"] = []
    return ent, dict(cfg, id=eid)


def _orch_entity_event(conn, eid, kind, message="", cost=0.0, run_id="", now=None):
    conn.execute("INSERT INTO entity_events (entity_id, ts, kind, message, cost_usd, run_id, user) VALUES (?,?,?,?,?,?,?)",
                 (eid, now or time.time(), kind, str(message)[:2000], float(cost or 0), run_id, _orch_who()))


def _orch_entity_set(conn, eid, now=None, **fields):
    fields["updated"] = now or time.time()
    conn.execute(f"UPDATE entities SET {', '.join(k + '=?' for k in fields)} WHERE id=?", (*fields.values(), eid))


def _orch_runs_costs(conn, col, key):
    """[(epoch, $)]: the whole cost (incl. their teams) of the runs whose
    `col` (entity_id / schedule_id) is key."""
    out = []
    for (rid,) in conn.execute(f"SELECT id FROM runs WHERE {col}=?", (key,)).fetchall():
        run, tasks = _orch_load(conn, rid)
        events = [dict(e) for e in conn.execute("SELECT * FROM events WHERE run_id=?", (rid,))]
        out.append((run["created"], _orch_metrics(run, tasks, events, team_coordination=_orch_team_coordination(conn, rid))["cost_total"]))
    return out


def _orch_entity_costs(conn, eid):
    """[(epoch, $)]: its runs (whole cost, incl. their teams) and its scouts."""
    return _orch_runs_costs(conn, "entity_id", eid) + \
        [(t, c) for t, c in conn.execute("SELECT ts, cost_usd FROM entity_events WHERE entity_id=? AND cost_usd>0", (eid,))]


def _orch_entity_tick(conn, eid, now=None, exe=None, spawn=None, alive=None, read=None, kill=None, git=None, limits=None):
    """One step of one entity: poll its scout, look after its run, remember
    finished runs, and start a scout when it's due and allowed."""
    now = now or time.time()
    alive, read, kill = alive or _orch_alive, read or _orch_read_out, kill or _orch_kill
    ent, cfg = _orch_entity_load(conn, eid)
    if not ent:
        return
    runs = [dict(r) for r in conn.execute("SELECT id, state, goal, finished, updated, created FROM runs "
                                           "WHERE entity_id=? ORDER BY created DESC", (eid,))]
    # memory: every finished run once
    known = {m.get("run_id") for m in ent["memory"]}
    fresh = [r for r in reversed(runs) if r["state"] not in _ORCH_RUN_ACTIVE and r["id"] not in known]
    if fresh:
        mem = list(ent["memory"])
        for r in fresh:
            run, tasks = _orch_load(conn, r["id"])
            events = [dict(e) for e in conn.execute("SELECT * FROM events WHERE run_id=?", (r["id"],))]
            mem.append(_orch_memory_entry(run, tasks, events))
            _orch_entity_event(conn, eid, "remembered", f"{run['goal'][:200]} -> {mem[-1]['outcome']}", run_id=r["id"], now=now)
        _orch_entity_set(conn, eid, now=now, memory=json.dumps(mem[-_ORCH_ENTITY_MEMORY:], ensure_ascii=False))
        ent["memory"] = mem[-_ORCH_ENTITY_MEMORY:]
    # its scout is thinking
    if ent.get("job_pid") or ent.get("job_base"):
        if alive(ent["job_pid"]) and _orch_ours(ent["job_pid"], ent.get("job_started")) \
                and now - (ent.get("job_started") or now) <= _ORCH_LLM_TIMEOUT_S:
            return
        killed = _orch_stop(ent["job_pid"], ent.get("job_started"), alive, kill)
        res = _orch_parse(read(ent["job_base"]))
        if killed and not res["cost"]:
            res["cost"] = _ORCH_ENTITY_SCOUT_BUDGET   # killed before it reported a cost: its budget is counted
        _orch_entity_set(conn, eid, now=now, job_pid=None, job_base="", job_started=None, last_scout=now, run_now=0)
        if not res["ok"]:
            _orch_entity_event(conn, eid, "scout_failed", _orch_explain_error(res["error"]), res["cost"], now=now)
            _orch_entity_set(conn, eid, now=now, state="idle", note="the scout failed: " + _orch_explain_error(res["error"])[:300])
            return
        d = res["data"]
        if d.get("action") != "run" or len(str(d.get("goal") or "").strip()) < 10:
            _orch_entity_event(conn, eid, "idle", str(d.get("reason") or "")[:600], res["cost"], now=now)
            _orch_entity_set(conn, eid, now=now, state="idle", note="nothing to do: " + str(d.get("reason") or "")[:300])
            return
        body = {"goal": str(d["goal"]).strip()[:8000], "model": cfg["model"], "max_parallel": cfg["max_parallel"],
                "budget_usd": cfg["budget_run"], "mode": d.get("mode") if d.get("mode") in _ORCH_MODES else cfg["mode"],
                "team": cfg["team"], "entity_id": eid}
        body.update({"workspace": cfg["workspace"]} if cfg["where"] == "workspace" else {"cwd": cfg["cwd"]})
        r = _orch_create(body, now=now, exe=exe, spawn=spawn, git=git, conn=conn)
        if not r.get("ok"):
            _orch_entity_event(conn, eid, "start_failed", r.get("error", ""), res["cost"], now=now)
            _orch_entity_set(conn, eid, now=now, state="idle", note="could not start a run: " + str(r.get("error"))[:300])
            return
        _orch_entity_event(conn, eid, "run_started", f"{body['goal'][:300]} -- {str(d.get('reason') or '')[:300]}",
                           res["cost"], run_id=r["id"], now=now)
        _orch_entity_set(conn, eid, now=now, state="running", note="")
        return
    active = next((r for r in runs if r["state"] in _ORCH_RUN_ACTIVE), None)
    if active:
        if active["state"] == "plan_ready":
            run, tasks = _orch_load(conn, active["id"])
            _orch_set_run(conn, active["id"], now=now, state="running")
            _orch_event(conn, active["id"], "approved", f"plan approved by entity '{cfg['name']}' "
                        f"(within its ${cfg['budget_run']:.2f} per run): {len(tasks)} task(s)", now=now)
        state = "waiting_you" if active["state"] == "review" else "running"
        if ent.get("state") != state:
            _orch_entity_set(conn, eid, now=now, state=state, note="")
        return
    if ent.get("paused") and not ent.get("run_now"):
        if ent.get("state") != "paused":
            _orch_entity_set(conn, eid, now=now, state="paused")
        return
    last_end = max([float(r["finished"] or r["updated"] or 0) for r in runs] or [0]) or None
    due, why, next_due = _orch_entity_due(cfg, ent, last_end, now)
    if not due:
        if ent.get("next_due") != next_due or ent.get("state") not in ("idle",):
            _orch_entity_set(conn, eid, now=now, state="idle", next_due=next_due)
        return
    spend = _orch_entity_spend(_orch_entity_costs(conn, eid), now)
    block = ""
    if spend[0] + cfg["budget_run"] > cfg["budget_day"]:
        block = f"today's budget: ${spend[0]:.2f} of ${cfg['budget_day']:.2f} spent"
    elif spend[1] + cfg["budget_run"] > cfg["budget_month"]:
        block = f"this month's budget: ${spend[1]:.2f} of ${cfg['budget_month']:.2f} spent"
    else:
        block = _orch_limits_block(limits if limits is not None else _rate_limits_snapshot())
    if block:
        if ent.get("note") != "waiting: " + block:
            _orch_entity_event(conn, eid, "waiting", block, now=now)
            _orch_entity_set(conn, eid, now=now, state="waiting", note="waiting: " + block, run_now=0)
        return
    exe = exe or _find_claude_exe()
    if not exe:
        _orch_entity_set(conn, eid, now=now, state="idle", note="Claude Code (claude) not found", run_now=0)
        return
    if cfg["where"] == "workspace":
        folder, err = _orch_workspace(cfg["workspace"], git)
    else:
        folder, err = cfg["cwd"], ("" if os.path.isdir(cfg["cwd"]) else "its repository folder is gone")
    if err:
        _orch_entity_set(conn, eid, now=now, state="idle", note=err, run_now=0, last_scout=now)
        return
    listing, notes = _orch_repo_context(folder, git)
    recent = [r for r in runs if r["state"] in _ORCH_RUN_ACTIVE]
    base = os.path.join(ORCH_DIR, "entity-" + eid, f"scout-{int(now)}")
    kw, deny, ready = _orch_proc_env()
    if not ready:
        return
    pid = (spawn or _orch_spawn)(_orch_llm_argv(exe, _ORCH_SCOUT_SCHEMA, cfg["model"], _ORCH_ENTITY_SCOUT_BUDGET, True, deny),
                                 folder, _orch_scout_prompt(cfg, ent["memory"], recent, listing, notes, spend, now,
                                                            _orch_team(cfg, _orch_library(conn))), base, **kw)
    _orch_entity_event(conn, eid, "scout", why, now=now)
    _orch_entity_set(conn, eid, now=now, state="scouting", job_pid=pid, job_base=base, job_started=now, run_now=0, note="")


def _orch_entity_view(ent, cfg, runs, events, costs, now=None):
    """Pure: one entity for the dashboard."""
    now = now or time.time()
    spend = _orch_entity_spend(costs, now)
    due, why, next_due = _orch_entity_due(cfg, dict(ent, run_now=0), max([float(r.get("finished") or r.get("updated") or 0) for r in runs] or [0]) or None, now)
    return {"id": cfg["id"], "config": cfg, "state": ent.get("state") or "idle", "paused": bool(ent.get("paused")),
            "note": ent.get("note") or "", "run_now": bool(ent.get("run_now")),
            "next_due": ent.get("next_due") or next_due, "last_scout": ent.get("last_scout"),
            "spend_day": spend[0], "spend_month": spend[1], "memory": list(reversed(ent.get("memory") or [])),
            "runs": [{"id": r["id"], "goal": r["goal"][:200], "state": r["state"], "created": r["created"]} for r in runs[:20]],
            "events": [dict(e) for e in events][-60:]}


# ── actions (POST /orch/...) ──────────────────────────────────────────────
def _orch_new_id(now):
    return time.strftime("%m%d-%H%M%S", time.localtime(now))


def _orch_plan_start(conn, rid, exe, now, spawn=None, git=None):
    """The orchestrator starts writing a run's plan (the run is 'planning')."""
    run, _ = _orch_load(conn, rid)
    agents = _orch_team(run, _orch_library(conn))
    listing, notes = _orch_repo_context(run["repo"], git)
    _orch_start_llm(conn, run, "plan", _orch_plan_prompt(run["goal"], run["repo"], run["base"], listing, notes, run["mode"],
                                                        run["budget_usd"], agents, run["kind"]),
                    _orch_schema(_ORCH_PLAN_SCHEMA, run["mode"] == "hierarchical", [a["id"] for a in agents]),
                    exe, now, spawn, read_tools=True)


def _orch_start_at(value, now):
    """Pure: the start time of a run for later (epoch seconds) -> (epoch or
    None = now, error)."""
    if value in (None, ""):
        return None, None
    try:
        at = float(value)
    except (TypeError, ValueError):
        return None, "when should it start?"
    if at <= now:
        return None, "pick a start time in the future"
    if at > now + _ORCH_LATER_MAX_DAYS * 86400:
        return None, f"pick a start time within {_ORCH_LATER_MAX_DAYS} days"
    return at, None


def _orch_create(data, now=None, exe=None, spawn=None, git=None, conn=None):
    """POST /orch/create {goal, cwd, model?, max_parallel?, budget_usd?, mode?,
    approve_teams?, start_at?} -> {ok, id}. With start_at (epoch) the run
    waits as 'scheduled' and starts planning then (_orch_start_due)."""
    now = now or time.time()
    if not isinstance(data, dict):
        return {"ok": False, "error": "bad request"}
    goal = str(data.get("goal") or "").strip()
    if len(goal) < 10:
        return {"ok": False, "error": "describe the goal (a sentence or more)"}
    if len(goal) > 8000:
        return {"ok": False, "error": "goal too long (8,000 characters max)"}
    kind = "workspace" if str(data.get("workspace") or "").strip() else "repo"
    if kind == "workspace":
        folder, err = _orch_workspace(data.get("workspace"), git)
        if err:
            return {"ok": False, "error": err}
    else:
        folder = str(data.get("cwd") or "").strip().strip('"')
        if not folder or not os.path.isabs(folder) or not os.path.isdir(folder):
            return {"ok": False, "error": "pick an existing folder (full path)"}
    model = str(data.get("model") or "opus").strip().lower()
    if model not in _ORCH_MODELS:
        return {"ok": False, "error": "unknown model"}
    try:
        par = int(data.get("max_parallel") or 3)
    except (TypeError, ValueError):
        par = 3
    par = min(max(par, 1), _ORCH_MAX_PARALLEL)
    budget = _orch_clamp(data.get("budget_usd"), _ORCH_RUN_BUDGET, 5.0)
    mode = str(data.get("mode") or "flat").strip().lower()
    if mode not in _ORCH_MODES:
        return {"ok": False, "error": "unknown mode"}
    start_at, err = _orch_start_at(data.get("start_at"), now)
    if err:
        return {"ok": False, "error": err}
    exe = exe or _find_claude_exe()
    if not exe:
        return {"ok": False, "error": "Claude Code (claude) not found on this PC"}
    if start_at is None and not _orch_proc_env()[2]:
        return {"ok": False, "error": "AOC is still listing your MCP servers (needed to run on your subscription) "
                                      "-- try again in a minute"}
    facts, err = _wt_repo(folder, git)
    if err:
        return {"ok": False, "error": err}
    own = conn is None
    with _orch_lock:
        conn = conn or _orch_connect()
        try:
            # a run for later doesn't hold the repo: whether it is free is checked when it starts
            if start_at is None and conn.execute(
                    "SELECT COUNT(*) FROM runs WHERE state IN ('planning','plan_ready','running','supervising') "
                    "AND repo=? AND COALESCE(parent_id,'')=''", (facts["repo"],)).fetchone()[0]:
                return {"ok": False, "error": "this repo already has an orchestration in progress -- finish or cancel it first"}
            lib = _orch_library(conn)
            team = [str(a) for a in (data.get("team") or []) if str(a) in lib]
            if data.get("team") and not team:
                return {"ok": False, "error": "none of the chosen agents exist any more"}
            rid = _orch_new_id(now)
            while conn.execute("SELECT 1 FROM runs WHERE id=?", (rid,)).fetchone():
                rid += "x"
            conn.execute("""INSERT INTO runs (id, created, updated, goal, repo, base, base_sha, state, model,
                            max_parallel, budget_usd, mode, approve_teams, team, kind, entity_id, start_at, schedule_id)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (rid, now, now, goal, facts["repo"], facts["base"], facts["base_sha"],
                          "planning" if start_at is None else "scheduled", model, par, budget, mode,
                          1 if data.get("approve_teams") is True else 0, json.dumps(team), kind,
                          re.sub(r"[^a-z0-9-]", "", str(data.get("entity_id") or ""))[:32], start_at,
                          re.sub(r"[^a-z0-9-]", "", str(data.get("schedule_id") or ""))[:32]))
            # a scheduled or entity run is started by AOC, not by you (found live: it showed "1 of your actions")
            _orch_event(conn, rid, "created", goal[:500], human=not (data.get("schedule_id") or data.get("entity_id")),
                        now=now)
            if start_at is None:
                _orch_plan_start(conn, rid, exe, now, spawn, git)
            else:
                _orch_event(conn, rid, "scheduled", "starts " + time.strftime("%Y-%m-%d %H:%M", time.localtime(start_at)),
                            human=True, now=now)
            conn.commit()
        finally:
            if own:
                conn.close()
    return {"ok": True, "id": rid, "scheduled": start_at is not None}


def _orch_start_scheduled(conn, run, now, exe=None, spawn=None, git=None):
    """A run for later starts planning, on the repo as it is now (its branch
    and last commit). -> '' or why it can't start yet."""
    exe = exe or _find_claude_exe()
    if not exe:
        return "Claude Code (claude) not found on this PC"
    if not _orch_proc_env()[2]:
        return "AOC is still listing your MCP servers"
    if conn.execute("SELECT COUNT(*) FROM runs WHERE state IN ('planning','plan_ready','running','supervising') "
                    "AND repo=? AND COALESCE(parent_id,'')=''", (run["repo"],)).fetchone()[0]:
        return "this repo has another orchestration in progress -- it starts once that one is done"
    facts, err = _wt_repo(run["repo"], git)
    if err:
        return err
    # created = when it really started: its time and its day's spend count from here
    _orch_set_run(conn, run["id"], now=now, state="planning", created=now, base=facts["base"], base_sha=facts["base_sha"],
                  error="")
    _orch_event(conn, run["id"], "started", "the start time came" if (run.get("start_at") or 0) <= now
                else "started early by you", human=(run.get("start_at") or 0) > now, now=now)
    _orch_plan_start(conn, run["id"], exe, now, spawn, git)
    return ""


def _orch_start_due(conn, rid, now=None, exe=None, spawn=None, git=None):
    """Tick: a run whose start time has come starts; if it can't yet, it
    says why once and tries again next tick."""
    now = now or time.time()
    run, _ = _orch_load(conn, rid)
    if not run or run["state"] != "scheduled":
        return
    why = _orch_start_scheduled(conn, run, now, exe, spawn, git)
    if why and run.get("error") != "waiting: " + why:
        _orch_set_run(conn, rid, now=now, error="waiting: " + why)
        _orch_event(conn, rid, "waiting", why, now=now)


def _orch_edit_plan(conn, rid, data, now):
    run, tasks = _orch_load(conn, rid)
    if run["state"] != "plan_ready":
        return {"ok": False, "error": "the plan can only be changed before it is approved"}
    new, err = _orch_validate_tasks((data or {}).get("tasks"), allow_manager=_orch_allows_manager(run),
                                    roles=_orch_team_ids(conn, run))
    if err:
        return {"ok": False, "error": err}
    conn.execute("DELETE FROM tasks WHERE run_id=?", (rid,))
    _orch_insert_tasks(conn, rid, new, 0)
    if "budget_usd" in (data or {}):
        _orch_set_run(conn, rid, now=now, budget_usd=_orch_clamp(data["budget_usd"], _ORCH_RUN_BUDGET, run["budget_usd"]))
    if "max_parallel" in (data or {}):
        try:
            _orch_set_run(conn, rid, now=now, max_parallel=min(max(int(data["max_parallel"]), 1), _ORCH_MAX_PARALLEL))
        except (TypeError, ValueError):
            pass
    _orch_event(conn, rid, "plan_edited", f"{len(new)} task(s)", human=True, now=now)
    return {"ok": True}


def _orch_cleanup(run, tasks, git=None, keep_merged=False):
    """Kill what still runs (only if it is still ours: a pid noted before a
    reboot may be someone else's now), remove every worktree and branch of
    the run. -> {"tasks": ids of the tasks killed, "job": planner/reviewer killed?}."""
    git = git or _wt_git
    killed = {"tasks": {t["id"] for t in tasks if t.get("pid") and _orch_stop(t["pid"], t.get("started"))},
              "job": bool(run.get("job_pid")) and _orch_stop(run["job_pid"], run.get("job_started"))}
    for t in tasks:
        if t.get("path") and os.path.isdir(t["path"]):
            git(["worktree", "remove", "--force", t["path"]], run["repo"], timeout=60)
        if t.get("branch"):
            git(["branch", "-d" if keep_merged else "-D", t["branch"]], run["repo"])
    git(["worktree", "prune"], run["repo"])
    return killed


def _orch_halt(conn, run, tasks, now, git=None, why="cancelled"):
    """Stop a run's work: kill what runs, remove its worktrees and branches
    and close its open tasks -- a task at its gate too (an OK asked for on a
    stopped run could never be given). A process killed before it answered
    never reports what it spent: its budget is counted instead of $0."""
    job_budget = _orch_llm_budget(run, _orch_spent(conn, run["id"]))
    killed = _orch_cleanup(run, tasks, git)
    for t in tasks:
        if t["state"] not in ("pending", "running", "gate"):
            continue
        cost = float(t.get("cost_usd") or 0) + (float(t["budget_usd"] or 0) if t["id"] in killed["tasks"] else 0.0)
        if t["role"] == "manager" and t.get("child_id"):
            cost = _orch_spent(conn, t["child_id"])   # its team's spend, its own kills included (closed first)
        _orch_set_task(conn, run["id"], t["id"], state="skipped", pid=None, error=why, finished=now, cost_usd=cost)
    if killed["job"]:
        _orch_event(conn, run["id"], "plan_failed" if run.get("job_kind") == "plan" else "supervise",
                    f"stopped before it answered ({why}) -- its budget is counted", cost=job_budget, now=now)


def _orch_merge_pick(tasks, only):
    """Pure: the tasks you chose to keep -> (their ids in merge order, error).
    A task built on another one carries that one's work on its branch, so it
    can't be kept without it (found the hard way in design, not live)."""
    by_id = {t["id"]: t for t in tasks}
    mergeable = {t["id"] for t in tasks if t["state"] == "done" and t.get("commits") and t.get("branch")}
    chosen = set(only) & mergeable
    if not chosen:
        return None, "pick at least one task with work to keep"
    for tid in sorted(chosen):
        need, frontier = set(), set(by_id[tid]["depends_on"])
        while frontier:
            need |= frontier
            frontier = {d for f in frontier for d in by_id[f]["depends_on"]} - need
        missing = sorted((need & mergeable) - chosen)
        if missing:
            return None, f"'{tid}' is built on {', '.join(missing)} -- keep that too, or leave '{tid}' out"
    order = _orch_topo(tasks) or [t["id"] for t in tasks]
    return [i for i in order if i in chosen], None


def _orch_merge(conn, rid, now, git=None, only=None):
    """Merge the developers' branches (plan order, each after its inputs) into
    the branch the run started from, each checked with merge-tree first.
    Stops at the first conflict (nothing half-merged: that branch is skipped
    and the run stays in review with the files named)."""
    git = git or _wt_git
    run, tasks = _orch_load(conn, rid)
    if run["state"] != "review":
        return {"ok": False, "error": "nothing to merge yet"}
    repo = run["repo"]
    rc, cur, _ = git(["symbolic-ref", "--short", "-q", "HEAD"], repo)
    if rc != 0 or cur.strip() != run["base"]:
        return {"ok": False, "error": f"the repo is on '{cur.strip() or 'detached HEAD'}', not '{run['base']}' -- check out {run['base']} first"}
    rc, dirty, _ = git(["status", "--porcelain=v1", "-z", "--untracked-files=no"], repo)
    if rc != 0 or _parse_porcelain_z(dirty):
        return {"ok": False, "error": "the repo has uncommitted changes -- commit or stash them first"}
    order = _orch_topo(tasks) or [t["id"] for t in tasks]
    by_id = {t["id"]: t for t in tasks}
    if only is not None:
        ids, err = _orch_merge_pick(tasks, only)
        if err:
            return {"ok": False, "error": err}
        todo = [by_id[i] for i in ids]
    else:
        todo = [by_id[i] for i in order if by_id[i]["state"] == "done" and by_id[i].get("commits") and by_id[i].get("branch")]
    merged = []
    for t in todo:
        if git(["merge-base", "--is-ancestor", t["branch"], "HEAD"], repo)[0] == 0:
            merged.append(t["id"])
            continue
        rc, out, er = git(["merge-tree", "--write-tree", "--name-only", "--no-messages", "HEAD", t["branch"]], repo, timeout=60)
        if rc != 0:
            files = _wt_conflicts(out) if rc == 1 else []
            msg = (f"'{t['id']}' would conflict in " + ", ".join(files[:5])) if files else \
                f"conflict check failed for '{t['id']}': " + _wt_err(er, "git merge-tree needs git 2.38 or newer")
            _orch_set_run(conn, rid, now=now, error=msg)
            _orch_event(conn, rid, "merge_conflict", msg, t["id"], now=now)
            conn.commit()
            return {"ok": False, "error": msg, "merged": merged}
        rc, _, er = git(["merge", "--no-ff", "--no-edit", "-m",
                         f"Merge {t['branch']}: {t['title']} (AOC orchestration {rid})", t["branch"]], repo, timeout=120)
        if rc != 0:
            git(["merge", "--abort"], repo)
            msg = f"merging '{t['id']}' failed: " + _wt_err(er, "unknown error")
            _orch_set_run(conn, rid, now=now, error=msg)
            _orch_event(conn, rid, "merge_conflict", msg, t["id"], now=now)
            conn.commit()
            return {"ok": False, "error": msg, "merged": merged}
        merged.append(t["id"])
    rc, sha, _ = git(["rev-parse", "HEAD"], repo)
    dropped = [t["id"] for t in tasks if t.get("branch") and t.get("commits") and t["state"] == "done" and t["id"] not in merged]
    # what you kept is merged (-d is enough); every other branch goes for good (-D) -- what you left out,
    # and a failed task's commits too (it keeps its branch to be looked at; after the merge nobody would)
    _orch_cleanup(run, [t for t in tasks if t["id"] in merged or not t.get("branch")], git, keep_merged=True)
    _orch_cleanup(run, [t for t in tasks if t.get("branch") and t["id"] not in merged], git)
    _orch_set_run(conn, rid, now=now, state="merged", error="", finished=now)
    _orch_event(conn, rid, "merged", f"{len(merged)} branch(es) into {run['base']} @ {sha.strip()[:10]}"
                + (f"; left out: {', '.join(dropped)}" if dropped else ""), human=True, now=now)
    return {"ok": True, "merged": merged, "dropped": dropped, "sha": sha.strip()[:40]}


def _orch_close_teams(conn, tasks, now, git=None):
    """Cancel / clean up the team runs of a run's manager tasks that are not
    finished yet (cancelling or discarding the run above)."""
    for t in tasks:
        child, ctasks = _orch_load(conn, t.get("child_id") or "")
        if not child or child["state"] in ("merged", "discarded", "cancelled"):
            continue
        _orch_halt(conn, child, ctasks, now, git)
        _orch_set_run(conn, child["id"], now=now, state="cancelled" if child["state"] in _ORCH_ACTIVE + ("plan_ready",)
                      else "discarded", job_kind="", job_pid=None, finished=child.get("finished") or now)
        _orch_event(conn, child["id"], "cancelled", "the run above was stopped", now=now)


def _orch_team_coordination(conn, rid):
    """What a run's teams spent on their managers' planning and reviews."""
    return float(conn.execute("""SELECT COALESCE(SUM(e.cost_usd),0) FROM events e JOIN runs r ON e.run_id=r.id
                                 WHERE r.parent_id=? AND e.kind IN ('plan_ready','plan_failed','supervise')""",
                              (rid,)).fetchone()[0])


def _orch_action(action, data, now=None, git=None, exe=None, spawn=None):
    """POST /orch/<plan|approve|cancel|merge|discard|start> {id, ...}."""
    now = now or time.time()
    rid = str((data or {}).get("id") or "")
    with _orch_lock:
        conn = _orch_connect()
        try:
            run, tasks = _orch_load(conn, rid)
            if not run:
                return {"ok": False, "error": "unknown run"}
            if action == "plan":
                reply = _orch_edit_plan(conn, rid, data, now)
            elif action == "approve":
                if run["state"] != "plan_ready":
                    return {"ok": False, "error": "there is no plan waiting for approval"}
                _orch_set_run(conn, rid, now=now, state="running")
                _orch_event(conn, rid, "approved", f"plan approved: {len(tasks)} task(s)", human=True, now=now)
                reply = {"ok": True}
            elif action == "start":
                if run["state"] != "scheduled":
                    return {"ok": False, "error": "this run isn't waiting for its start time"}
                why = _orch_start_scheduled(conn, run, now, exe, spawn, git)
                if why:
                    return {"ok": False, "error": why}
                reply = {"ok": True}
            elif action == "cancel" and run["state"] == "scheduled":
                # nothing started yet: no process, no worktree
                _orch_set_run(conn, rid, now=now, state="cancelled", finished=now)
                _orch_event(conn, rid, "cancelled", "cancelled by you before it started", human=True, now=now)
                reply = {"ok": True}
            elif action == "cancel":
                if run["state"] not in ("planning", "plan_ready", "running", "supervising"):
                    return {"ok": False, "error": "the run is not in progress"}
                _orch_close_teams(conn, tasks, now, git)
                _orch_halt(conn, run, tasks, now, git)
                _orch_set_run(conn, rid, now=now, state="cancelled", job_kind="", job_pid=None, finished=now)
                _orch_event(conn, rid, "cancelled", "cancelled by you", human=True, now=now)
                reply = {"ok": True}
            elif action == "gate":
                tid = str((data or {}).get("task") or "")
                t = next((x for x in tasks if x["id"] == tid), None)
                if run["state"] not in _ORCH_ACTIVE:
                    return {"ok": False, "error": "the run is over -- its tasks won't start any more"}
                if not t or t["state"] != "gate":
                    return {"ok": False, "error": "that task isn't waiting for your OK"}
                if (data or {}).get("allow") is True:
                    _orch_set_task(conn, rid, tid, state="pending", approved=1)
                    _orch_event(conn, rid, "task_allowed", "allowed by you", tid, human=True, now=now)
                else:
                    _orch_set_task(conn, rid, tid, state="skipped", error="skipped by you", finished=now)
                    _orch_event(conn, rid, "task_skipped", "skipped by you", tid, human=True, now=now)
                reply = {"ok": True}
            elif action in ("merge", "discard") and run.get("parent_id"):
                return {"ok": False, "error": "this team's work is taken over by the run above -- merge or discard that one"}
            elif action == "merge":
                only = (data or {}).get("only")
                reply = _orch_merge(conn, rid, now, git, only=[str(x) for x in only] if isinstance(only, list) else None)
                if reply.get("ok"):
                    _orch_close_other(conn, run, now, git)
            elif action == "discard":
                if run["state"] not in ("review", "failed"):
                    return {"ok": False, "error": "only a finished run can be discarded"}
                _orch_close_teams(conn, tasks, now, git)
                _orch_cleanup(run, tasks, git)
                _orch_set_run(conn, rid, now=now, state="discarded", finished=run.get("finished") or now)
                _orch_event(conn, rid, "discarded", "work discarded by you", human=True, now=now)
                reply = {"ok": True}
            else:
                return {"ok": False, "error": "unknown action"}
            conn.commit()
            return reply
        finally:
            conn.close()


# ── The same goal with one agent (PDF ch. 9: is the team worth it?) ──────
_ORCH_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"better": {"type": "string", "enum": ["team", "solo", "tie"]},
                   "reasons": {"type": "string", "description": "why, in 2-5 short sentences"},
                   "risks": {"type": "string", "description": "what to check in the better one before merging"}},
    "required": ["better", "reasons", "risks"], "additionalProperties": False,
}


def _orch_baseline(data, now=None):
    """POST /orch/baseline {id}: run the same goal with one developer -- no
    planning, no review -- next to the team's run, to compare. Costs the
    solo agent's budget; only when you ask."""
    now = now or time.time()
    rid = str((data or {}).get("id") or "")
    with _orch_lock:
        conn = _orch_connect()
        try:
            run, tasks = _orch_load(conn, rid)
            if not run or run.get("parent_id") or run.get("baseline_of"):
                return {"ok": False, "error": "only a team's own run can be compared"}
            if run["state"] in ("planning", "plan_ready"):
                return {"ok": False, "error": "approve the plan first (the comparison runs next to the team)"}
            if run["state"] not in ("running", "supervising", "review"):
                return {"ok": False, "error": "this run is over -- only a run in progress or in review can be compared"}
            bid = rid + "-solo"
            if conn.execute("SELECT 1 FROM runs WHERE id=?", (bid,)).fetchone():
                return {"ok": False, "error": "this run already has its one-agent version"}
            budget = round(min(float(run["budget_usd"]), 20.0), 2)
            conn.execute("""INSERT INTO runs (id, created, updated, goal, repo, base, base_sha, state, model, max_parallel,
                            budget_usd, plan_summary, mode, team, kind, baseline_of)
                            VALUES (?,?,?,?,?,?,?,'running',?,1,?,?,'flat','["developer"]',?,?)""",
                         (bid, now, now, run["goal"], run["repo"], run["base"], run["base_sha"], run["model"], budget,
                          "One developer does the whole goal alone, for comparison.", run.get("kind") or "repo", rid))
            _orch_insert_tasks(conn, bid, [{"id": "solo", "role": "developer", "title": "The whole goal, alone",
                                            "instructions": run["goal"] + "\n\nDo the whole goal yourself: there is no "
                                            "team, no plan and no review after you.",
                                            "done_criteria": "the goal is met, as the human would check it",
                                            "depends_on": [], "model": "sonnet", "budget_usd": round(min(budget * 0.85, 5.0), 2)}], 0)
            _orch_event(conn, bid, "created", f"the one-agent version of run {rid}", human=True, now=now)
            _orch_event(conn, rid, "baseline", f"comparing with one agent: run {bid}", human=True, now=now)
            conn.commit()
        finally:
            conn.close()
    return {"ok": True, "id": bid}


def _orch_close_other(conn, run, now, git=None):
    """You merged one side of a team / one-agent pair: the other side is
    stopped or dropped (its worktrees and branches would only linger)."""
    if run.get("baseline_of"):
        oid = run["baseline_of"]
    else:
        row = conn.execute("SELECT id FROM runs WHERE baseline_of=?", (run["id"],)).fetchone()
        oid = row[0] if row else ""
    other, otasks = _orch_load(conn, oid) if oid else (None, [])
    if not other or other["state"] in ("merged", "discarded", "cancelled"):
        return
    active = other["state"] in _ORCH_ACTIVE + ("plan_ready",)
    _orch_close_teams(conn, otasks, now, git)
    _orch_halt(conn, other, otasks, now, git)
    _orch_set_run(conn, oid, now=now, state="cancelled" if active else "discarded", job_kind="", job_pid=None,
                  finished=other.get("finished") or now)
    _orch_event(conn, oid, "cancelled" if active else "discarded", f"the other version (run {run['id']}) was merged", now=now)


def _orch_diffstat(run, tasks, git=None):
    """{files, insertions, deletions} of a run's kept branches against its base."""
    git = git or _wt_git
    files = ins = dels = 0
    for t in tasks:
        if t["state"] != "done" or not t.get("commits") or not t.get("branch"):
            continue
        rc, out, _ = git(["diff", "--shortstat", run["base_sha"], t["branch"]], run["repo"])
        m = re.search(r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?", out if rc == 0 else "")
        if m:
            files += int(m.group(1)); ins += int(m.group(2) or 0); dels += int(m.group(3) or 0)
    return {"files": files, "insertions": ins, "deletions": dels}


def _orch_compare(rid, git=None):
    """GET /orch/compare?id= -> the team's run and its one-agent version side by side."""
    conn = _orch_connect()
    try:
        team, ttasks = _orch_load(conn, rid)
        if not team:
            return {"ok": False, "error": "unknown run"}
        b = conn.execute("SELECT id FROM runs WHERE baseline_of=?", (rid,)).fetchone()
        if not b:
            return {"ok": True, "baseline": None}
        solo, stasks = _orch_load(conn, b[0])

        def side(run, tasks):
            events = [dict(e) for e in conn.execute("SELECT * FROM events WHERE run_id=?", (run["id"],))]
            m = _orch_metrics(run, tasks, events, team_coordination=_orch_team_coordination(conn, run["id"]))
            return {"id": run["id"], "state": run["state"], "cost": m["cost_total"], "coordination": m["cost_coordination"],
                    "seconds": m["duration_s"], "tasks": m["tasks"], "success": m["success_rate"],
                    "diff": _orch_diffstat(run, tasks, git) if run["state"] in ("review", "running", "supervising") else None,
                    "report": _orch_clip(run.get("report"), 600)}
        verdict = None
        try:
            verdict = json.loads(team.get("verdict") or "null")
        except ValueError:
            pass
        return {"ok": True, "baseline": b[0], "team": side(team, ttasks), "solo": side(solo, stasks), "verdict": verdict}
    finally:
        conn.close()


def _orch_judge_prompt(goal, team_report, team_diff, solo_report, solo_diff):
    """Pure: the judge's instructions."""
    return "\n".join([
        "Two attempts at the same GOAL in the same repository: one by a TEAM of AI agents (planned, reviewed), one by a "
        "SINGLE agent alone. Judge which result is better for the goal: correctness and completeness first, then code "
        "quality and scope (no unasked-for changes). Cost is shown separately to the human; don't weigh it.",
        "", "GOAL:", goal, "", "TEAM -- report:", team_report or "(none)", "TEAM -- diff:", team_diff or "(no changes)",
        "", "SOLO -- report:", solo_report or "(none)", "SOLO -- diff:", solo_diff or "(no changes)"])


_orch_judging = set()   # runs whose two versions are being compared right now


def _orch_judge(data, run=None):
    """POST /orch/judge {id}: Claude compares the two results (costs a little;
    only when asked, when both are done, one comparison per run at a time)."""
    rid = str((data or {}).get("id") or "")
    with _orch_lock:
        if rid in _orch_judging:
            return {"ok": False, "error": "already comparing these two -- the verdict comes in a minute or two"}
        _orch_judging.add(rid)
    try:
        return _orch_judge_now(rid, run)
    finally:
        _orch_judging.discard(rid)


def _orch_judge_now(rid, run=None):
    conn = _orch_connect()
    try:
        team, ttasks = _orch_load(conn, rid)
        b = conn.execute("SELECT id FROM runs WHERE baseline_of=?", (rid,)).fetchone() if team else None
        if not b:
            return {"ok": False, "error": "no one-agent version to compare with"}
        solo, stasks = _orch_load(conn, b[0])
        if team["state"] != "review" or solo["state"] != "review":
            return {"ok": False, "error": "both versions must be done (in review) before they can be compared"}

        def diff(r, tasks):
            parts = []
            for t in tasks:
                if t["state"] == "done" and t.get("commits") and t.get("branch"):
                    rc, out, _ = _wt_git(["diff", r["base_sha"], t["branch"]], r["repo"], timeout=60)
                    if rc == 0:
                        parts.append(out)
            return _orch_clip("\n".join(parts), 20000)
        prompt = _orch_judge_prompt(team["goal"], team.get("report"), diff(team, ttasks), solo.get("report"), diff(solo, stasks))
    finally:
        conn.close()
    exe = _find_claude_exe()
    if not exe:
        return {"ok": False, "error": "Claude Code (claude) not found"}
    kw, deny, ready = _orch_proc_env()
    if not ready:
        return {"ok": False, "error": "still listing the MCP servers -- try again in a minute"}
    try:
        r = (run or subprocess.run)(_orch_llm_argv(exe, _ORCH_JUDGE_SCHEMA, "sonnet", 1.0, False, deny), input=prompt,
                                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
                                    creationflags=_NO_WINDOW, **kw)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "the judge took too long"}
    out = r.stdout or ""
    res = _orch_parse(out if "{" in out else out + " stderr: " + str(r.stderr or "").strip()[-300:])
    if not res["ok"]:
        return {"ok": False, "error": _orch_explain_error(res["error"])}
    verdict = dict(res["data"], cost=round(res["cost"], 4))
    with _orch_lock:
        conn = _orch_connect()
        try:
            _orch_set_run(conn, rid, verdict=json.dumps(verdict, ensure_ascii=False))
            _orch_event(conn, rid, "judged", f"better: {verdict['better']} -- {verdict['reasons'][:300]}", cost=res["cost"], human=True)
            conn.commit()
        finally:
            conn.close()
    return dict(verdict, ok=True)


def _orch_agents_list():
    """GET /orch/agents: the library (built-ins first, then yours)."""
    conn = _orch_connect()
    try:
        return {"ok": True, "agents": list(_orch_library(conn).values())}
    finally:
        conn.close()


def _orch_agent_save(data, now=None):
    """POST /orch/agent_save {agent fields, old_id?}: add or change one of your
    agents (runs already planned keep the copy they made)."""
    agent, err = _orch_validate_agent(data)
    if err:
        return {"ok": False, "error": err}
    old = str((data or {}).get("old_id") or "")
    with _orch_lock:
        conn = _orch_connect()
        try:
            if old and old != agent["id"]:
                if conn.execute("SELECT 1 FROM agents WHERE id=?", (agent["id"],)).fetchone():
                    return {"ok": False, "error": f"an agent called '{agent['id']}' already exists"}
                conn.execute("DELETE FROM agents WHERE id=?", (old,))
            conn.execute("INSERT OR REPLACE INTO agents (id, data, updated) VALUES (?,?,?)",
                         (agent["id"], json.dumps({k: agent[k] for k in _ORCH_AGENT_KEYS if k != "id"}, ensure_ascii=False),
                          now or time.time()))
            conn.commit()
        finally:
            conn.close()
    return {"ok": True, "agent": agent}


def _orch_agent_delete(data):
    """POST /orch/agent_delete {id}."""
    aid = str((data or {}).get("id") or "")
    if aid in _ORCH_ROLES:
        return {"ok": False, "error": "the built-in agents can't be deleted"}
    with _orch_lock:
        conn = _orch_connect()
        try:
            gone = conn.execute("DELETE FROM agents WHERE id=?", (aid,)).rowcount
            conn.commit()
        finally:
            conn.close()
    return {"ok": True} if gone else {"ok": False, "error": "unknown agent"}


def _orch_entities_list(now=None):
    """GET /orch/entities."""
    conn = _orch_connect()
    try:
        out = []
        for (eid,) in conn.execute("SELECT id FROM entities ORDER BY created").fetchall():
            ent, cfg = _orch_entity_load(conn, eid)
            if not ent:
                continue
            runs = [dict(r) for r in conn.execute("SELECT id, state, goal, finished, updated, created FROM runs "
                                                   "WHERE entity_id=? ORDER BY created DESC", (eid,))]
            events = [dict(e) for e in conn.execute("SELECT * FROM entity_events WHERE entity_id=? ORDER BY ts", (eid,))]
            out.append(_orch_entity_view(ent, cfg, runs, events, _orch_entity_costs(conn, eid), now))
        return {"ok": True, "entities": out}
    finally:
        conn.close()


def _orch_entity_save(data, now=None):
    """POST /orch/entity_save {config, old_id?}."""
    now = now or time.time()
    cfg, err = _orch_validate_entity(data)
    if err:
        return {"ok": False, "error": err}
    old = str((data or {}).get("old_id") or "")
    with _orch_lock:
        conn = _orch_connect()
        try:
            lib = _orch_library(conn)
            cfg["team"] = [a for a in cfg["team"] if a in lib] or list(_ORCH_AGENT_ROLES)
            if old and old != cfg["id"]:
                if conn.execute("SELECT 1 FROM entities WHERE id=?", (cfg["id"],)).fetchone():
                    return {"ok": False, "error": f"an entity called '{cfg['id']}' already exists"}
                conn.execute("UPDATE entities SET id=? WHERE id=?", (cfg["id"], old))
                conn.execute("UPDATE entity_events SET entity_id=? WHERE entity_id=?", (cfg["id"], old))
                conn.execute("UPDATE runs SET entity_id=? WHERE entity_id=?", (cfg["id"], old))
            data_json = json.dumps({k: v for k, v in cfg.items() if k != "id"}, ensure_ascii=False)
            if conn.execute("SELECT 1 FROM entities WHERE id=?", (cfg["id"],)).fetchone():
                conn.execute("UPDATE entities SET data=?, updated=? WHERE id=?", (data_json, now, cfg["id"]))
            else:
                conn.execute("INSERT INTO entities (id, data, created, updated, paused) VALUES (?,?,?,?,1)",
                             (cfg["id"], data_json, now, now))
                _orch_entity_event(conn, cfg["id"], "created", "made paused -- resume it or Run now", now=now)
            conn.commit()
        finally:
            conn.close()
    return {"ok": True, "entity": cfg}


def _orch_entity_action(action, data, now=None):
    """POST /orch/entity_<run|pause|forget|delete> {id, paused?}."""
    now = now or time.time()
    eid = str((data or {}).get("id") or "")
    with _orch_lock:
        conn = _orch_connect()
        try:
            ent, cfg = _orch_entity_load(conn, eid)
            if not ent:
                return {"ok": False, "error": "unknown entity"}
            if action == "run":
                if conn.execute("SELECT 1 FROM runs WHERE entity_id=? AND state IN ('planning','plan_ready','running',"
                                "'supervising','review')", (eid,)).fetchone():
                    return {"ok": False, "error": "it already has a run in progress -- finish that one first"}
                _orch_entity_set(conn, eid, now=now, run_now=1)
                _orch_entity_event(conn, eid, "run_now", "you asked it to look for work now", now=now)
            elif action == "pause":
                on = (data or {}).get("paused") is not False
                fields = {"paused": 1 if on else 0, "state": "paused" if on else "idle"}
                if on and (ent.get("job_pid") or ent.get("job_base")):
                    # found live: a scout still thinking when you paused went on to start (and approve) a run
                    killed = _orch_stop(ent.get("job_pid"), ent.get("job_started"))
                    cost = _ORCH_ENTITY_SCOUT_BUDGET if killed else \
                        (_orch_parse(_orch_read_out(ent["job_base"]))["cost"] if ent.get("job_base") else 0.0)
                    _orch_entity_event(conn, eid, "scout_stopped", "paused while its scout was thinking -- no run is started"
                                       + (" (stopped before it answered: its budget is counted)" if killed else ""),
                                       cost, now=now)
                    fields.update(job_pid=None, job_base="", job_started=None, last_scout=now, run_now=0, note="")
                _orch_entity_set(conn, eid, now=now, **fields)
                _orch_entity_event(conn, eid, "paused" if on else "resumed", "", now=now)
            elif action == "forget":
                _orch_entity_set(conn, eid, now=now, memory="[]")
                _orch_entity_event(conn, eid, "forgot", "memory cleared by you", now=now)
            elif action == "delete":
                if conn.execute("SELECT 1 FROM runs WHERE entity_id=? AND state IN ('planning','plan_ready','running',"
                                "'supervising','review')", (eid,)).fetchone():
                    return {"ok": False, "error": "finish or cancel its run first"}
                _orch_stop(ent.get("job_pid"), ent.get("job_started"))
                conn.execute("DELETE FROM entities WHERE id=?", (eid,))
                conn.execute("DELETE FROM entity_events WHERE entity_id=?", (eid,))
            else:
                return {"ok": False, "error": "unknown action"}
            conn.commit()
            return {"ok": True}
        finally:
            conn.close()


# ── Repeating runs: one goal on a clock ───────────────────────────────────
# A schedule = what a run is asked for (goal, where, team, orchestrator,
# budget ...) + when (daily or on weekdays at HH:MM, every N hours -- the
# entities' clock, _orch_entity_due) + caps per day / month. When it's due
# it starts an ordinary run (schedule_id = it). A firing is skipped, and
# logged, while its last run is still going (no overlap), when the caps
# would be crossed, or -- on your subscription -- while your plan limits are
# nearly used up; the next one comes on time. A slot missed while AOC was
# off fires once at startup, not once per slot. Its plans wait for your OK
# unless you let the schedule approve them (within its run budget).
_ORCH_SCHEDULE_TYPES = ("daily", "weekdays", "every")


def _orch_validate_repeat(data):
    """Pure: POST /orch/schedule_save body -> (config, None) or (None, error).
    The run's own fields are checked again by _orch_create at each firing."""
    if not isinstance(data, dict):
        return None, "bad request"
    goal = str(data.get("goal") or "").strip()
    if len(goal) < 10:
        return None, "describe the goal (a sentence or more)"
    if len(goal) > 8000:
        return None, "goal too long (8,000 characters max)"
    ws = _orch_workspace_slug(data.get("workspace"))
    cwd = "" if ws else str(data.get("cwd") or "").strip().strip('"')
    if not ws and not (cwd and os.path.isabs(cwd)):
        return None, "pick an existing folder (full path)"
    model = str(data.get("model") or "opus").strip().lower()
    if model not in _ORCH_MODELS:
        return None, "unknown model"
    mode = str(data.get("mode") or "flat").strip().lower()
    if mode not in _ORCH_MODES:
        return None, "unknown mode"
    sched, err = _orch_validate_schedule(data.get("schedule"), _ORCH_SCHEDULE_TYPES)
    if err:
        return None, err
    try:
        par = min(max(int(data.get("max_parallel") or 3), 1), _ORCH_MAX_PARALLEL)
    except (TypeError, ValueError):
        return None, "bad number"
    b_run = _orch_clamp(data.get("budget_usd"), _ORCH_RUN_BUDGET, 5.0)
    b_day = _orch_clamp(data.get("budget_day"), _ORCH_ENTITY_BUDGET_DAY, max(10.0, b_run))
    b_month = _orch_clamp(data.get("budget_month"), _ORCH_ENTITY_BUDGET_MONTH, max(100.0, b_day))
    if b_run > b_day or b_day > b_month:
        return None, "budgets must grow: per run <= per day <= per month"
    return {"goal": goal, "cwd": cwd, "workspace": ws, "model": model, "max_parallel": par, "budget_usd": b_run,
            "mode": mode, "approve_teams": mode == "hierarchical" and data.get("approve_teams") is True,
            "team": [str(a) for a in (data.get("team") or [])][:20], "schedule": sched,
            "budget_day": b_day, "budget_month": b_month, "auto_approve": data.get("auto_approve") is True}, None


def _orch_schedule_load(conn, sid):
    r = conn.execute("SELECT * FROM schedules WHERE id=?", (sid,)).fetchone()
    if not r:
        return None, None
    try:
        return dict(r), json.loads(r["data"])
    except (ValueError, TypeError):
        return None, None


def _orch_schedule_set(conn, sid, now=None, **fields):
    fields["updated"] = now or time.time()
    conn.execute(f"UPDATE schedules SET {', '.join(k + '=?' for k in fields)} WHERE id=?", (*fields.values(), sid))


def _orch_schedule_event(conn, sid, kind, message="", run_id="", now=None):
    conn.execute("INSERT INTO schedule_events (schedule_id, ts, kind, message, run_id, user) VALUES (?,?,?,?,?,?)",
                 (sid, now or time.time(), kind, str(message)[:2000], run_id, _orch_who()))


def _orch_schedule_next(sch, cfg, now):
    """Pure: -> (due now?, why, next firing or None). It counts from its last
    firing -- or from when it was made: a slot before that never fires."""
    if sch.get("paused"):
        return False, "", None
    return _orch_entity_due({"schedule": cfg["schedule"]}, {"last_scout": sch.get("last_fire") or sch.get("created") or 0},
                            None, now)


def _orch_schedule_block(cfg, last, spend, billing, limits):
    """Pure: why this firing is skipped ('' = it may start). last = its last
    run {id, state} or None, spend = ($ today, $ this month)."""
    if last and last["state"] in _ORCH_ACTIVE + ("plan_ready",):
        return f"its last run ({last['id']}) is still {last['state'].replace('_', ' ')}"
    if spend[0] + cfg["budget_usd"] > cfg["budget_day"]:
        return f"today's budget: ${spend[0]:.2f} of ${cfg['budget_day']:.2f} spent"
    if spend[1] + cfg["budget_usd"] > cfg["budget_month"]:
        return f"this month's budget: ${spend[1]:.2f} of ${cfg['budget_month']:.2f} spent"
    if billing == "subscription":
        return _orch_limits_block(limits)
    return ""


def _orch_schedule_tick(conn, sid, now=None, exe=None, spawn=None, git=None, limits=None):
    """One step of one schedule: approve its run's plan if you let it, and
    fire when it's due (or skip that firing and say why)."""
    now = now or time.time()
    sch, cfg = _orch_schedule_load(conn, sid)
    if not sch:
        return
    r = conn.execute("SELECT id, state FROM runs WHERE schedule_id=? ORDER BY created DESC LIMIT 1", (sid,)).fetchone()
    last = dict(r) if r else None
    if last and last["state"] == "plan_ready" and cfg.get("auto_approve"):
        n = conn.execute("SELECT COUNT(*) FROM tasks WHERE run_id=?", (last["id"],)).fetchone()[0]
        _orch_set_run(conn, last["id"], now=now, state="running")
        _orch_event(conn, last["id"], "approved", f"plan approved by its schedule (within its ${cfg['budget_usd']:.2f} "
                    f"per run): {n} task(s)", now=now)
        last["state"] = "running"
    due, why, next_due = _orch_schedule_next(sch, cfg, now)
    if not due:
        if sch.get("next_due") != next_due:
            _orch_schedule_set(conn, sid, now=now, next_due=next_due)
        return
    block = _orch_schedule_block(cfg, last, _orch_entity_spend(_orch_runs_costs(conn, "schedule_id", sid), now),
                                 _orch_settings()["billing"], limits if limits is not None else _rate_limits_snapshot())
    if block:
        _orch_schedule_event(conn, sid, "skipped", block, now=now)
        _orch_schedule_set(conn, sid, now=now, last_fire=now, last_result="skipped: " + block,
                           next_due=_orch_schedule_next(dict(sch, last_fire=now), cfg, now)[2])
        return
    if not _orch_proc_env()[2]:
        return   # the MCP server list isn't known yet (scanning): fire next tick
    body = {k: cfg[k] for k in ("goal", "model", "max_parallel", "budget_usd", "mode", "approve_teams", "team")}
    body.update({"workspace": cfg["workspace"]} if cfg.get("workspace") else {"cwd": cfg["cwd"]}, schedule_id=sid)
    res = _orch_create(body, now=now, exe=exe, spawn=spawn, git=git, conn=conn)
    nxt = _orch_schedule_next(dict(sch, last_fire=now), cfg, now)[2]
    if not res.get("ok"):
        _orch_schedule_event(conn, sid, "failed", res.get("error", ""), now=now)
        _orch_schedule_set(conn, sid, now=now, last_fire=now, last_result="could not start: " + str(res.get("error"))[:300],
                           next_due=nxt)
        return
    _orch_schedule_event(conn, sid, "fired", why, run_id=res["id"], now=now)
    _orch_schedule_set(conn, sid, now=now, last_fire=now, last_run=res["id"], last_result="started run " + res["id"],
                       next_due=nxt)


def _orch_schedule_view(sch, cfg, runs, events, costs, now=None):
    """Pure: one schedule for the dashboard."""
    now = now or time.time()
    spend = _orch_entity_spend(costs, now)
    return {"id": sch["id"], "config": cfg, "paused": bool(sch.get("paused")), "next_due": _orch_schedule_next(sch, cfg, now)[2],
            "last_fire": sch.get("last_fire"), "last_result": sch.get("last_result") or "", "created": sch.get("created"),
            "spend_day": spend[0], "spend_month": spend[1],
            "runs": [{"id": r["id"], "goal": r["goal"][:200], "state": r["state"], "created": r["created"]} for r in runs[:10]],
            "events": [dict(e) for e in events][-30:]}


def _orch_schedules_list(now=None):
    """GET /orch/schedules."""
    if not os.path.exists(ORCH_DB):
        return {"ok": True, "schedules": []}
    conn = _orch_connect()
    try:
        out = []
        for (sid,) in conn.execute("SELECT id FROM schedules ORDER BY created").fetchall():
            sch, cfg = _orch_schedule_load(conn, sid)
            if not sch:
                continue
            runs = [dict(r) for r in conn.execute("SELECT id, state, goal, created FROM runs WHERE schedule_id=? "
                                                   "ORDER BY created DESC", (sid,))]
            events = [dict(e) for e in conn.execute("SELECT * FROM schedule_events WHERE schedule_id=? ORDER BY ts", (sid,))]
            out.append(_orch_schedule_view(sch, cfg, runs, events, _orch_runs_costs(conn, "schedule_id", sid), now))
        return {"ok": True, "schedules": out}
    finally:
        conn.close()


def _orch_schedule_save(data, now=None, git=None):
    """POST /orch/schedule_save {goal, cwd | workspace, ..., schedule,
    budget_day?, budget_month?, auto_approve?} -> {ok, id}. A new one."""
    now = now or time.time()
    cfg, err = _orch_validate_repeat(data)
    if err:
        return {"ok": False, "error": err}
    if cfg["cwd"]:
        if not os.path.isdir(cfg["cwd"]):
            return {"ok": False, "error": "pick an existing folder (full path)"}
        _, err = _wt_repo(cfg["cwd"], git)
        if err:
            return {"ok": False, "error": err}
    with _orch_lock:
        conn = _orch_connect()
        try:
            lib = _orch_library(conn)
            team = [a for a in cfg["team"] if a in lib]
            if cfg["team"] and not team:
                return {"ok": False, "error": "none of the chosen agents exist any more"}
            cfg["team"] = team
            sid = "s" + _orch_new_id(now)
            while conn.execute("SELECT 1 FROM schedules WHERE id=?", (sid,)).fetchone():
                sid += "x"
            conn.execute("INSERT INTO schedules (id, data, created, updated) VALUES (?,?,?,?)",
                         (sid, json.dumps(cfg, ensure_ascii=False), now, now))
            sch, _ = _orch_schedule_load(conn, sid)
            nxt = _orch_schedule_next(sch, cfg, now)[2]
            _orch_schedule_set(conn, sid, now=now, next_due=nxt)
            _orch_schedule_event(conn, sid, "created", "first run " + time.strftime("%Y-%m-%d %H:%M", time.localtime(nxt)),
                                 now=now)
            conn.commit()
        finally:
            conn.close()
    return {"ok": True, "id": sid, "next_due": nxt}


def _orch_schedule_action(action, data, now=None):
    """POST /orch/schedule_<pause|delete> {id, paused?}. Its runs stay."""
    now = now or time.time()
    sid = str((data or {}).get("id") or "")
    with _orch_lock:
        conn = _orch_connect()
        try:
            sch, cfg = _orch_schedule_load(conn, sid)
            if not sch:
                return {"ok": False, "error": "unknown schedule"}
            if action == "pause":
                on = (data or {}).get("paused") is not False
                if on:
                    _orch_schedule_set(conn, sid, now=now, paused=1, next_due=None)
                else:
                    # resumed: slots it was paused through don't fire -- it counts from now
                    nxt = _orch_schedule_next(dict(sch, paused=0, last_fire=now), cfg, now)[2]
                    _orch_schedule_set(conn, sid, now=now, paused=0, last_fire=now, next_due=nxt)
                _orch_schedule_event(conn, sid, "paused" if on else "resumed", "", now=now)
            elif action == "delete":
                conn.execute("DELETE FROM schedules WHERE id=?", (sid,))
                conn.execute("DELETE FROM schedule_events WHERE schedule_id=?", (sid,))
            else:
                return {"ok": False, "error": "unknown action"}
            conn.commit()
            return {"ok": True}
        finally:
            conn.close()


def _orch_list(limit=30):
    """GET /orch/runs: newest first, with their metrics."""
    if not os.path.exists(ORCH_DB):
        return {"ok": True, "runs": []}
    conn = _orch_connect()
    try:
        out = []
        for (rid,) in conn.execute("SELECT id FROM runs WHERE COALESCE(parent_id,'')='' ORDER BY created DESC LIMIT ?",
                                   (limit,)).fetchall():
            run, tasks = _orch_load(conn, rid)
            events = [dict(e) for e in conn.execute("SELECT * FROM events WHERE run_id=? ORDER BY ts", (rid,))]
            teams = [c for c in (_orch_load(conn, t["child_id"])[0] for t in tasks if t.get("child_id")) if c]
            out.append({"id": rid, "goal": run["goal"][:300], "repo": run["repo"], "state": run["state"],
                        "created": run["created"], "tasks": len(tasks), "mode": run.get("mode") or "flat",
                        "entity_id": run.get("entity_id") or "", "baseline_of": run.get("baseline_of") or "",
                        "start_at": run.get("start_at"), "schedule_id": run.get("schedule_id") or "",
                        "running": sum(1 for t in tasks if t["state"] == "running"),
                        "needs_you": run["state"] in ("plan_ready", "review") or any(c["state"] == "plan_ready" for c in teams),
                        "metrics": _orch_metrics(run, tasks, events, team_coordination=_orch_team_coordination(conn, rid))})
        return {"ok": True, "runs": out}
    finally:
        conn.close()


def _orch_history_index(rows):
    """Pure: past finished tasks [(agent name, model, cost, seconds)] ->
    {(name, model): (avg $, avg s, n), model: (avg $, avg s, n)}."""
    acc = {}
    for name, model, cost, secs in rows:
        for key in ((name, model), model):
            a = acc.setdefault(key, [0.0, 0.0, 0, 0])
            a[0] += float(cost or 0)
            a[2] += 1
            if secs is not None and secs >= 0:
                a[1] += secs
                a[3] += 1
    return {k: (a[0] / a[2], (a[1] / a[3]) if a[3] else None, a[2]) for k, a in acc.items()}


def _orch_estimate(tasks, waves, index, max_parallel=3):
    """Pure: what a plan will likely cost and take, from your past tasks of
    the same agent + model (else the same model, else half the task's
    budget -- and then it says so). Time = the waves one after another, each
    as long as its slowest task (more tasks than parallel slots take turns)."""
    by_id = {t["id"]: t for t in tasks}
    cost, known, guessed, secs_total = 0.0, 0, 0, 0.0
    per = {}
    for t in tasks:
        name = "Manager" if t["role"] == "manager" else (_orch_task_agent(t).get("name") or t["role"])
        hit = index.get((name, t.get("model"))) or index.get(t.get("model"))
        if hit and hit[2] >= 2:
            c_, s_ = min(hit[0], t["budget_usd"]), hit[1]
            known += 1
        else:
            c_, s_ = t["budget_usd"] * 0.5, None
            guessed += 1
        per[t["id"]] = s_ if s_ is not None else 180.0
        cost += c_
    for w in waves or []:
        times = sorted((per[i] for i in w if i in by_id), reverse=True)
        slots = max(1, int(max_parallel or 1))
        rounds = [times[i:i + slots] for i in range(0, len(times), slots)]
        secs_total += sum(r[0] for r in rounds if r)
    return {"cost": round(cost, 2), "seconds": round(secs_total), "from_history": known, "guessed": guessed}


def _orch_project_dir(path, base=None):
    """Pure-ish: where Claude Code keeps the transcripts of sessions started
    in `path` (every character but letters, digits and '-' becomes '-')."""
    base = base or os.path.join(os.path.expanduser("~"), ".claude", "projects")
    return os.path.join(base, re.sub(r"[^A-Za-z0-9-]", "-", str(path or "")))


def _orch_live_session(path, base=None):
    """The session id of the agent working in `path` right now (its newest
    transcript there), '' if none yet -- so a running task can be watched
    before it reports its session id."""
    d = _orch_project_dir(path, base)
    try:
        files = [os.path.join(d, n) for n in os.listdir(d) if n.endswith(".jsonl")]
    except OSError:
        return ""
    return os.path.splitext(os.path.basename(max(files, key=os.path.getmtime)))[0] if files else ""


def _orch_in_history(sids, path=None):
    """Which of these Claude Code sessions History has: the transcript
    scanner saves each agent's session there under its own id."""
    sids, path = sorted({str(s) for s in sids if s}), path or DB_FILE
    if not sids or not os.path.exists(path):
        return set()
    try:
        c = sqlite3.connect(path, timeout=5)
        try:
            return {r[0] for r in c.execute(f"SELECT id FROM sessions WHERE id IN ({','.join('?' * len(sids))})", sids)}
        finally:
            c.close()
    except sqlite3.Error:
        return set()


def _orch_run_commits(run, tasks, git=None, limit=200):
    """The commits a run's agents made -> [{sha, subject, when, task}].
    Before the merge: each task's own commits on its branch (not the base's,
    not its inputs'). After it: what each merge AOC made into the base
    brought in (the branches are gone by then)."""
    git = git or _wt_git
    fmt = "--format=%H%x1f%ct%x1f%s"
    out, seen = [], set()

    def add(text, tid):
        for line in (text or "").splitlines():
            p = line.split("\x1f")
            if len(p) == 3 and p[0] not in seen:
                seen.add(p[0])
                out.append({"sha": p[0], "when": int(p[1]) if p[1].isdigit() else None, "subject": p[2], "task": tid})
    if run["state"] == "merged":
        rc, merges, _ = git(["log", "--merges", "--fixed-strings", f"--grep=(AOC orchestration {run['id']})",
                             "--format=%H%x1f%s", run["base_sha"] + ".." + run["base"]], run["repo"])
        names = re.compile(r"^Merge aoc/orch-" + re.escape(run["id"]) + r"-(.+)-\d+: ")
        for line in (merges if rc == 0 else "").splitlines():
            sha, _, subj = line.partition("\x1f")
            m = names.match(subj)
            rc, text, _ = git(["log", "--no-merges", fmt, sha + "^1.." + sha + "^2"], run["repo"])
            add(text if rc == 0 else "", m.group(1) if m else "")
    else:
        inputs = [t["branch"] for t in tasks if t.get("branch") and t.get("commits")]
        for t in tasks:
            if t.get("branch") and t.get("commits"):
                rc, text, _ = git(["log", "--no-merges", fmt, t["branch"], "^" + run["base_sha"],
                                   *["^" + b for b in inputs if b != t["branch"]]], run["repo"])
                add(text if rc == 0 else "", t["id"])
    return sorted(out, key=lambda x: x["when"] or 0)[-limit:]


def _orch_commits(rid, git=None):
    """GET /orch/commits?id= -> {ok, commits, base}."""
    if not os.path.exists(ORCH_DB):
        return {"ok": False, "error": "unknown run"}
    conn = _orch_connect()
    try:
        run, tasks = _orch_load(conn, rid)
    finally:
        conn.close()
    if not run:
        return {"ok": False, "error": "unknown run"}
    if run["state"] in ("scheduled", "discarded", "cancelled") or not run.get("base_sha"):
        return {"ok": True, "commits": [], "base": run.get("base") or ""}
    return {"ok": True, "commits": _orch_run_commits(run, tasks, git), "base": run["base"]}


def _orch_get(rid):
    """GET /orch/run?id="""
    if not os.path.exists(ORCH_DB):
        return {"ok": False, "error": "unknown run"}
    conn = _orch_connect()
    try:
        run, tasks = _orch_load(conn, rid)
        if not run:
            return {"ok": False, "error": "unknown run"}
        events = [dict(e) for e in conn.execute("SELECT * FROM events WHERE run_id=? ORDER BY ts", (rid,))]
        for t in tasks:
            if t["state"] == "running" and t.get("path") and t["role"] != "manager":
                t["live_session"] = _orch_live_session(t["path"])
        known = _orch_in_history([t.get("session_id") or t.get("live_session") for t in tasks])
        for t in tasks:
            t["in_history"] = (t.get("session_id") or t.get("live_session") or "") in known
        children = {}
        for t in tasks:
            child, ctasks = _orch_load(conn, t.get("child_id") or "")
            if child:
                children[t["id"]] = _orch_child_summary(child, ctasks)
        estimate = None
        if run["state"] == "plan_ready":
            rows = []
            for a, role, model, cost, st, fi in conn.execute(
                    "SELECT agent, role, model, cost_usd, started, finished FROM tasks WHERE state IN ('done','failed') "
                    "AND finished >= ?", (time.time() - 90 * 86400,)).fetchall():
                try:
                    name = (json.loads(a) or {}).get("name") if a else None
                except ValueError:
                    name = None
                name = name or ("Manager" if role == "manager" else _orch_task_agent({"role": role}).get("name") or role)
                rows.append((name, model, cost, (fi - st) if st and fi else None))
            estimate = _orch_estimate(tasks, _orch_waves(tasks), _orch_history_index(rows), run.get("max_parallel") or 3)
        parent = None
        if run.get("parent_id"):
            p, _ = _orch_load(conn, run["parent_id"])
            parent = {"id": run["parent_id"], "task": run.get("parent_task") or "", "goal": (p or {}).get("goal", "")[:300]}
        b = conn.execute("SELECT id FROM runs WHERE baseline_of=?", (rid,)).fetchone()
        return dict(_orch_view(run, tasks, events, children=children, parent=parent,
                               team_coordination=_orch_team_coordination(conn, rid)), ok=True, estimate=estimate,
                    baseline=b[0] if b else "")
    finally:
        conn.close()


if os.environ.get("AOC_ORCH_WORKER", "1") != "0":
    threading.Thread(target=_orch_worker, daemon=True, name="orch-worker").start()
