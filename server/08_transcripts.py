# AOC server, part 08: process counter, encoding, transcript scanner, loop detection, away recap.
# monitor.py exec()s server/NN_*.py in number order into ONE shared
# namespace (its own) -- not an importable module: names from earlier
# parts are plain globals here, and __file__ is monitor.py.
# ── Claude process counter ─────────────────────────────────────────────────────

_claude_proc_count = [0]  # cached count of running claude.exe processes

_own_claude_pids = set()  # `claude -p` recaps AOC runs itself (_run_haiku): not CLI sessions


def _claude_proc_worker():
    """Background thread: count running claude.exe processes every 2 seconds
    (via _snapshot_processes; tasklist only as a fallback).
    Was 10s, which — stacked with the /events WS heartbeat — meant a closed CLI
    could take up to ~12s to disappear from the dashboard's total-CLI count.
    Also wakes any connected /events WebSocket immediately when the count
    actually changes, instead of waiting for the next heartbeat tick."""
    import subprocess as _sp, time as _t
    _cflags = _sp.CREATE_NO_WINDOW if os.name == "nt" else 0
    # This thread starts ~900 lines before _status_cond/_status_version are
    # defined. With tasklist the first count took long enough that they always
    # existed by then; the ~30ms snapshot gets there first and the change
    # notification raised NameError on every start.
    while "_status_cond" not in globals() or "_status_version" not in globals():
        _t.sleep(0.05)
    while True:
        try:
            # In-process snapshot first: spawning tasklist every 2s (~43k
            # processes a day) regularly blew its 5s timeout under load,
            # freezing the CLI count. tasklist stays as the fallback for a
            # failed snapshot only.
            procs = _snapshot_processes()
            if procs is not None:
                # minus the `claude -p` recaps AOC itself is running
                new_count = sum(1 for pid, exe in procs if exe == "claude.exe" and pid not in _own_claude_pids)
            else:
                r = _sp.run(
                    ["tasklist", "/FI", "IMAGENAME eq claude.exe", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, timeout=5, creationflags=_cflags
                )
                new_count = r.stdout.count("claude.exe")
            if new_count != _claude_proc_count[0]:
                _claude_proc_count[0] = new_count
                with _status_cond:
                    _status_version[0] += 1
                    _status_cond.notify_all()
        except Exception as e:
            _log_bg_error("_claude_proc_worker", e)
        _t.sleep(2)

_threading.Thread(target=_claude_proc_worker, daemon=True).start()


# ── Encoding utilities ─────────────────────────────────────────────────────────

def _set_session_note(status: dict, session_id: str, note: str) -> bool:
    """Write a per-session note (distinct from the single global
    status["session_note"] scratchpad) onto sessions[session_id]["note"],
    capped at 2000 chars like the global note. Returns False without
    mutating `status` if session_id doesn't exist -- a note can't attach
    to a session AOC has never seen."""
    sessions = status.setdefault("sessions", {})
    if session_id not in sessions:
        return False
    sessions[session_id]["note"] = str(note)[:2000]
    return True

def _fix_mojibake(s: str) -> str:
    """Fix strings where UTF-8 bytes were decoded as a single-byte Windows
    codepage instead of UTF-8 -- a recurring Windows pipe/subprocess issue.
    Tries latin-1 first (the original case this was written for), then
    cp1250/Central European (this machine's system codepage -- a Slovak
    display_name that took that specific wrong turn would read as
    "PokraÄŤovĂˇnĂ..." instead of "Pokračování...";
    not confirmed to have actually happened to live data, added purely
    as defensive coverage for the same class of issue)."""
    for codec in ("latin-1", "cp1250"):
        try:
            fixed = s.encode(codec).decode("utf-8")
            if fixed != s:
                return fixed
        except Exception:
            continue
    return s


def _decode_project_name(encoded: str) -> str:
    """Decode Claude Code's encoded project path (C--Users-marek-ai-antivirus)
    to a readable project name by walking the filesystem to resolve hyphens."""
    import re as _re2
    m = _re2.match(r'^([A-Za-z])--(.+)$', encoded)
    if not m:
        return encoded
    path = m.group(1) + ":\\"
    parts = m.group(2).split('-')
    i = 0
    while i < len(parts):
        matched = False
        for j in range(len(parts), i, -1):
            candidate = os.path.join(path, '-'.join(parts[i:j]))
            if os.path.isdir(candidate):
                path = candidate
                i = j
                matched = True
                break
        if not matched:
            break
    name = os.path.basename(path)
    home_name = os.path.basename(os.path.expanduser("~"))
    return "" if name in (home_name, "Users", "home", "") else name


# ── Transcript scanner ─────────────────────────────────────────────────────────

def _waiting_on_you_from_line(t, stop_reason):
    """Given a transcript line's `type` and (for assistant lines) its
    `stop_reason`, decide whether this line marks a turn boundary that
    should update the session's waiting_on_you flag -- and to what value.
    Returns None for anything that isn't a real conversation turn (the
    side-channel metadata lines like ai-title/agent-name/mode/permission-
    mode), so those never reset the flag. An assistant line whose
    stop_reason is "tool_use" means Claude is still mid-turn (about to run
    a tool and keep going); any other stop_reason (end_turn, max_tokens,
    stop_sequence) means the turn is over and control is back with the
    human. Any "user" line -- a real reply the human typed, or a
    tool_result fed back to Claude -- means the turn is no longer sitting
    with the human either way."""
    if t == "assistant":
        if stop_reason is None:
            return None
        return stop_reason != "tool_use"
    if t == "user":
        return False
    return None

def _transcript_woy_applies(sess: dict, stats: dict) -> bool:
    """Whether the transcript scanner's waiting_on_you (from the last turn
    line it read) may override the session's flag: only when that line is
    newer than the hook's last word. The Notification hook marks a session
    'waiting for permission' while the transcript's last line is still the
    tool_use that asked -- read up to 30 s later, it must not clear it."""
    return (stats.get("woy_epoch") or 0) > (sess.get("waiting_hook_epoch") or 0)

def _compute_waiting_secs(sess: dict, now_epoch: float) -> int:
    """How long a session has been sitting in waiting_on_you state, in
    seconds. Works with no new tracking field: the Stop heartbeat that
    flips waiting_on_you to True is the same event that last refreshed
    last_seen_epoch, and nothing updates last_seen_epoch again until the
    user's next prompt flips waiting_on_you back to False -- so
    now - last_seen_epoch already *is* the waiting duration."""
    if not sess.get("waiting_on_you"):
        return 0
    return int(now_epoch - sess.get("last_seen_epoch", now_epoch))

def _accumulate_waiting_time(sess: dict, new_waiting, now_epoch: float) -> None:
    """Mutates `sess` in place to build a running total of how long this
    CLI session has spent with waiting_on_you=True across its whole
    lifetime (waiting_on_you_accum_s), for the WAITING ON YOU history trend.
    _compute_waiting_secs above only ever answers "how long has it been
    waiting *right now*" -- fine for a live badge, useless for a lifetime
    total, since nothing else records when each waiting period started or
    ended. Called every time either code path that sets waiting_on_you (the
    /update heartbeat, and the transcript-scanner fallback) is about to
    change it -- only an actual True<->False transition does anything; a
    same-value update (the common case) is a no-op."""
    was_waiting = bool(sess.get("waiting_on_you"))
    new_waiting = bool(new_waiting)
    if new_waiting and not was_waiting:
        sess["waiting_on_you_since"] = now_epoch
    elif not new_waiting and was_waiting:
        since = sess.get("waiting_on_you_since")
        if since:
            sess["waiting_on_you_accum_s"] = sess.get("waiting_on_you_accum_s", 0) + max(0, now_epoch - since)
        sess["waiting_on_you_since"] = None

def _read_ai_title(jsonl_path: str) -> str:
    """Read the ai-title entry from a Claude Code transcript JSONL file."""
    try:
        with open(jsonl_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get("type") == "ai-title":
                    return obj.get("aiTitle", "")
    except Exception:
        pass
    return ""


# Model pricing: (input_$/1M, output_$/1M, cache_write_$/1M, cache_read_$/1M)
# Standard context window across every Claude model tier currently priced
# above (does not account for the opt-in 1M-context beta some models
# offer) -- the agent card's token progress bar (see renderAgents' tokensHtml)
# has been reading a.token_limit to compute this all along, but nothing
# ever sent it, so that bar never rendered.
_CLAUDE_CONTEXT_WINDOW = 200_000

# First matching prefix wins, so specific ids come before their family
# ("claude-opus-4-5" before "claude-opus-4"). cache_write is the 5-minute
# TTL rate (1.25x input); 1-hour writes cost 2x input -- see
# _CACHE_WRITE_1H_MULT. Rates from Anthropic's model table (2026-06).
_MODEL_PRICING = {
    "claude-fable-5-1":  (10.0, 50.0, 12.50, 0.25),
    "claude-mythos-5-1": (10.0, 50.0, 12.50, 0.25),
    "claude-fable-5":    (10.0, 50.0, 12.50, 1.00),
    "claude-mythos-5":   (10.0, 50.0, 12.50, 1.00),
    "claude-opus-5-5":   (4.0,  20.0,  5.00, 0.20),
    "claude-opus-5":     (5.0,  25.0,  6.25, 0.50),
    "claude-opus-4-8":   (5.0,  25.0,  6.25, 0.50),
    "claude-opus-4-7":   (5.0,  25.0,  6.25, 0.50),
    "claude-opus-4-6":   (5.0,  25.0,  6.25, 0.50),
    "claude-opus-4-5":   (5.0,  25.0,  6.25, 0.50),
    "claude-opus-4":     (15.0, 75.0, 18.75, 1.50),
    "claude-sonnet-5":   (2.0,  10.0,  2.50, 0.20),
    "claude-sonnet-4":   (3.0,  15.0,  3.75, 0.30),
    "claude-haiku-4":    (1.0,   5.0,  1.25, 0.10),
    "claude-opus-3":     (15.0, 75.0, 18.75, 1.50),
    "claude-sonnet-3-5": (3.0,  15.0,  3.75, 0.30),
    "claude-3-5-haiku":  (0.80,  4.0,  1.00, 0.08),
    "claude-haiku-3":    (0.25,  1.25, 0.30, 0.03),
}
_CACHE_WRITE_1H_MULT = 2.0

_transcript_cursors: dict = {}   # session_id → {"path": str, "offset": int, "stats": dict}
_transcript_cursors_lock = _threading.Lock()

def _model_pricing(model: str):
    for prefix, pricing in _MODEL_PRICING.items():
        if model.startswith(prefix):
            return pricing
    return (3.0, 15.0, 3.75, 0.30)  # default: sonnet-4

def _calc_cost(inp: int, out: int, cache_write: int, cache_read: int, model: str,
               cache_write_1h: int = 0) -> float:
    """cache_write is all cache-creation tokens; cache_write_1h is the part
    written with the 1-hour TTL (usage.cache_creation.ephemeral_1h_input_tokens),
    which costs 2x input instead of 1.25x. Claude Code writes 1-hour entries."""
    pi, po, pw, pr = _model_pricing(model)
    cw_1h = min(max(cache_write_1h or 0, 0), cache_write or 0)
    return (inp * pi + out * po + ((cache_write or 0) - cw_1h) * pw
            + cw_1h * pi * _CACHE_WRITE_1H_MULT + cache_read * pr) / 1_000_000


def _recache_cost(tokens, ttl, model):
    """(cold $, warm $) for the next message of a session whose context is
    `tokens` long: cold = writing it all to cache again (1.25x input for a
    5-minute entry, 2x for a 1-hour one), warm = reading it from cache."""
    pi, _po, pw, pr = _model_pricing(model or "")
    tokens = max(int(tokens or 0), 0)
    write = pi * _CACHE_WRITE_1H_MULT if ttl == "1h" else pw
    return tokens * write / 1_000_000, tokens * pr / 1_000_000


# How long before expiry the alert fires, per cache TTL, and the smallest
# extra cost worth interrupting you for.
_CACHE_ALERT_LEAD_S = {"1h": 600, "5m": 120}
_CACHE_ALERT_MIN_USD = 0.25


def _cache_expiry_alerts(sessions, notified, now):
    """Pure: [(key, title, body)] for open sessions waiting on you whose
    prompt cache expires within _CACHE_ALERT_LEAD_S and would cost at least
    _CACHE_ALERT_MIN_USD more to re-cache than to read. key = (sid, expiry),
    so a session alerts once per wait; a new reply moves the expiry and
    re-arms it. Keys already in `notified` are skipped."""
    out = []
    for s in sessions or ():
        c = s.get("context") or {}
        sid, exp, ttl = s.get("id"), c.get("cache_expires_at"), c.get("cache_ttl")
        if not sid or not s.get("session_active") or not s.get("waiting_on_you") or not exp or ttl not in _CACHE_ALERT_LEAD_S:
            continue
        left = exp - now
        if left <= 0 or left > _CACHE_ALERT_LEAD_S[ttl] or (sid, exp) in notified:
            continue
        tokens = c.get("recache_tokens") or c.get("tokens") or 0
        cold, warm = _recache_cost(tokens, ttl, s.get("model"))
        if cold - warm < _CACHE_ALERT_MIN_USD:
            continue
        mins = max(1, int(left // 60))
        name = s.get("display_name") or s.get("project") or "Session"
        tok = f"{tokens / 1000:.0f}k" if tokens >= 1000 else str(tokens)
        out.append(((sid, exp), f"🧊 {name[:40]}: cache expires in {mins} min",
                    f"Reply within {mins} min to keep it warm (~${warm:.2f}). "
                    f"After that the next message re-caches {tok} tokens: ~${cold:.2f}."))
    return out


# child_id -> parent_id, for a parent's child_ids arriving before the
# child agent itself has been created yet (see _apply_agent_update below).
# In practice the child's own PreToolUse/PostToolUse almost always fire
# before the parent's own PostToolUse gets around to reporting child_ids
# (the parent's tool call blocks on the child completing first), so this
# is a rare-ordering safety net, not the common path. Transient/in-memory
# only, same as _pr_link_cache and friends -- doesn't need to survive a
# restart.
_pending_parent_links: dict = {}

def _apply_agent_update(status: dict, au: dict, session_id: str, session_project: str, now: str) -> None:
    """Create or update one agent entry in status["agents"], keyed by au["id"].
    Extracted from the /update HTTP handler so the transcript-based fallback
    scanner (which detects Agent/Task tool_use blocks directly from a
    session's transcript, for when Claude Code's hook dispatch silently never
    fires PreToolUse/PostToolUse) can create/update the exact same schema
    through the exact same upsert path -- one place the agent schema can
    drift, not two."""
    aid = au.get("id")
    if not aid:
        return
    au.setdefault("session_id", session_id)
    au.setdefault("session_project", session_project)
    # Real per-model cost when granular usage is present (aoc_hook.py's
    # PostToolUse) -- reuses the exact _calc_cost the session-level KPI gauge
    # already trusts, instead of the flat _costRate guess the frontend falls
    # back to otherwise.
    if au.get("model") and "input_tokens" in au:
        au["estimated_cost"] = _calc_cost(
            au.get("input_tokens", 0), au.get("output_tokens", 0),
            au.get("cache_write_tokens", 0), au.get("cache_read_tokens", 0),
            au["model"], au.get("cache_write_1h_tokens", 0))
    existing_ids = {a["id"]: a for a in status.get("agents", [])}
    # child_ids (this agent itself spawned these subagents, per aoc_hook.py's
    # _extract_child_agent_ids) sets TREE view's parent_id on each child --
    # either directly if the child already exists, or via the pending-links
    # map above if it hasn't been created yet.
    child_ids = au.pop("child_ids", None)
    if child_ids:
        for cid in child_ids:
            if cid in existing_ids:
                existing_ids[cid]["parent_id"] = aid
            else:
                _pending_parent_links[cid] = aid
    if aid in existing_ids and au.get("detected_via") == "transcript":
        # The scanner reads a start line up to 30 s late; by then the hook has
        # usually registered the agent (and for a finished one, marked it
        # done). Re-applying that start used to flip it back to "running"
        # and flag a hook miss that never happened -- only fill the gaps.
        a = existing_ids[aid]
        for k, v in au.items():
            if k not in ("status", "detected_via") and not a.get(k):
                a[k] = v
        return
    if aid in existing_ids:
        a = existing_ids[aid]
        old_status = a.get("status", "")
        new_status = au.get("status", old_status)
        if old_status != new_status:
            try:
                if new_status == "running":
                    _log.agent_start({**a, **au})
                elif new_status == "done":
                    _log.agent_done({**a, **au})
            except Exception as e:
                _log_bg_error("_apply_agent_update:status_transition", e)
        if "tasks" in au and "tasks" in a:
            for i, t_new in enumerate(au["tasks"]):
                if i < len(a["tasks"]):
                    was_done = a["tasks"][i].get("done", False)
                    is_done  = t_new.get("done", False)
                    if not was_done and is_done:
                        t_new["completed_at"] = now
                        try:
                            _log.task_complete(a, t_new)
                        except Exception as e:
                            _log_bg_error("_apply_agent_update:task_complete", e)
                    elif a["tasks"][i].get("completed_at"):
                        t_new.setdefault("completed_at", a["tasks"][i]["completed_at"])
        # accumulate log entries instead of replacing
        if "log" in au:
            new_entries = au.pop("log")
            combined = a.get("log", []) + new_entries
            a["log"] = combined[-300:]  # keep last 300 entries
        a.update(au)
        if au.get("status") == "done" and not a.get("completed_at"):
            a["completed_at"] = now
    else:
        # ── CREATE new agent ──
        # A done/error update for an agent we never saw start means its
        # PreToolUse registration was lost (e.g. AOC restarted mid-task, or
        # the hook dispatch itself silently never fired) -- still surface it
        # rather than silently dropping it: a late, incomplete card beats an
        # agent that ran, failed, and left no trace anywhere (not even the
        # error tracking panel, since that reads from this same agents list).
        # started_at is unknown in this case, so fall back to completed_at
        # (reads as a ~0s duration rather than a nonsense value) and note it.
        late_arrival = au.get("status") in ("done", "error") and not au.get("started_at")
        new_agent = {
            "id": aid,
            "name": au.get("name", aid),
            "icon": au.get("icon", "??"),
            "description": au.get("description", ""),
            "status": au.get("status", "waiting"),
            "tasks": au.get("tasks", []),
            "log": (["[registration missed — surfaced late]"] + au.get("log", []))
                   if late_arrival else au.get("log", []),
            "started_at": au.get("started_at") or au.get("completed_at") or now,
            "completed_at": None,
            "token_limit": _CLAUDE_CONTEXT_WINDOW,
            "session_id": au.get("session_id", session_id),
            "session_project": au.get("session_project", session_project),
            # How many *other* CLI sessions were active at the moment this
            # agent was first registered -- captured once, at creation, for
            # both hook- and transcript-created agents (both paths run
            # through this one function). Lets us test the hypothesis that
            # Claude Code's hook dispatch drops PreToolUse more often under
            # concurrent hook activity from multiple sessions, instead of
            # just guessing (see project notes, 2026-07-15/16).
            "concurrent_sessions": sum(
                1 for s in status.get("sessions", {}).values()
                if s.get("session_active") and not s.get("dismissed")
            ),
        }
        if au.get("completed_at"):
            new_agent["completed_at"] = au["completed_at"]
        if au.get("error_message"):
            new_agent["error_message"] = au["error_message"]
        if au.get("files_changed"):
            new_agent["files_changed"] = au["files_changed"]
        if au.get("tokens_used"):
            new_agent["tokens_used"] = au["tokens_used"]
        if au.get("tool_use_count") is not None:
            new_agent["tool_use_count"] = au["tool_use_count"]
        if au.get("estimated_cost") is not None:
            new_agent["estimated_cost"] = au["estimated_cost"]
        # Carried over so a late-arrival/transcript-created agent gets the
        # same accurate per-model cost data the update path already gets via
        # its blind a.update(au) merge -- previously only estimated_cost (the
        # derived number) made it into a brand-new card, not the raw
        # breakdown a future recompute or SQLite export would need.
        if au.get("model"):
            new_agent["model"] = au["model"]
            new_agent["input_tokens"] = au.get("input_tokens", 0)
            new_agent["output_tokens"] = au.get("output_tokens", 0)
            new_agent["cache_write_tokens"] = au.get("cache_write_tokens", 0)
            new_agent["cache_read_tokens"] = au.get("cache_read_tokens", 0)
        if au.get("detected_via"):
            new_agent["detected_via"] = au["detected_via"]
        if au.get("subagent_type"):
            new_agent["subagent_type"] = au["subagent_type"]
        pending_parent = _pending_parent_links.pop(aid, None)
        if pending_parent:
            new_agent["parent_id"] = pending_parent
        if not status.get("session_active"):
            status["session_active"] = True
            status["started_at"] = now
        status.setdefault("agents", []).append(new_agent)
        try:
            _log.agent_start(new_agent)
        except Exception as e:
            _log_bg_error("_apply_agent_update:agent_start", e)


def _assistant_text(obj, limit=1500):
    """Pure: the visible text of one main-thread assistant transcript line
    (text blocks joined), or "" -- what Claude last said, shown on a session
    that's waiting on you. Subagent (sidechain) lines don't count."""
    if not isinstance(obj, dict) or obj.get("type") != "assistant" or obj.get("isSidechain"):
        return ""
    content = (obj.get("message") or {}).get("content")
    if not isinstance(content, list):
        return ""
    parts = [b.get("text", "") for b in content
             if isinstance(b, dict) and b.get("type") == "text" and (b.get("text") or "").strip()]
    text = "\n\n".join(p.strip() for p in parts)
    # keep the END: a question to the user comes last
    return text if len(text) <= limit else "…" + text[-limit:]


# ── Loop detection: a CLI session going round in circles ──────────────────
# The transcript scanner keeps the main thread's last _LOOP_RING_MAX tool
# calls (since your last message or Claude's last successful git commit,
# both of which count as progress) and _loop_signal looks for the two
# patterns that rarely happen in healthy work: a run of failures, and the
# fix -> run -> fail cycle on one file.
_LOOP_RING_MAX = 40
_LOOP_ERROR_STREAK = 5    # this many failed tool calls in a row
_LOOP_SAME_FAIL = 3       # the same call failing this many times
_LOOP_FILE_EDITS = 6      # one file edited this many times...
_LOOP_FILE_ERRORS = 4     # ...with at least this many failures in the window
_LOOP_EDIT_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")


def _loop_tool_key(name, inp):
    """Pure: (key identifying 'the same call', file path or '')."""
    inp = inp if isinstance(inp, dict) else {}
    name = str(name or "?")
    if name == "Bash":
        return "Bash: " + " ".join(str(inp.get("command") or "").split())[:200], ""
    path = str(inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or "")
    if name in _LOOP_EDIT_TOOLS or name == "Read":
        return f"{name}: {path}"[:220], path if name in _LOOP_EDIT_TOOLS else ""
    try:
        args = json.dumps(inp, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        args = str(inp)
    return f"{name}: {args}"[:220], ""


_GIT_COMMIT_RE = re.compile(r"\bgit\b[^\n|;&]*?\scommit\b")


def _is_commit_call(name, inp):
    """Pure: a shell tool call that runs `git commit` (anywhere in a long
    command line, e.g. `cd x && git add -A && git commit -m ...`)."""
    if name not in ("Bash", "PowerShell") or not isinstance(inp, dict):
        return False
    return bool(_GIT_COMMIT_RE.search(str(inp.get("command") or "")))


def _loop_track(stats, obj):
    """Feed one transcript line into stats["loop_ring"] (main thread only)."""
    if not isinstance(obj, dict) or obj.get("isSidechain"):
        return
    t = obj.get("type")
    content = (obj.get("message") or {}).get("content")
    ring = stats.setdefault("loop_ring", [])
    if t == "assistant" and isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") not in ("Agent", "Task"):
                key, path = _loop_tool_key(b.get("name"), b.get("input"))
                ring.append({"id": b.get("id", ""), "key": key, "file": path, "err": None,
                             "commit": _is_commit_call(b.get("name"), b.get("input"))})
        del ring[:-_LOOP_RING_MAX]
    elif t == "user":
        results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"] \
            if isinstance(content, list) else []
        if not results:
            if not obj.get("isMeta"):
                ring.clear()  # you wrote something: a fresh start
            return
        for r in results:
            for e in reversed(ring):
                if e["id"] and e["id"] == r.get("tool_use_id"):
                    e["err"] = bool(r.get("is_error"))
                    if not e["err"] and e.get("commit"):
                        ring.clear()  # committed: progress
                    break


def _loop_signal(ring):
    """Pure: {"kind", "text", "count"} if the recent tool calls look like a
    loop, else None."""
    done = [e for e in ring or [] if e.get("err") is not None]
    if not done:
        return None
    streak = 0
    for e in reversed(done):
        if not e["err"]:
            break
        streak += 1
    if streak >= _LOOP_ERROR_STREAK:
        return {"kind": "errors", "count": streak,
                "text": f"{streak} tool calls failed in a row (last: {done[-1]['key'][:80]})"}
    fails = {}
    for e in done:
        if e["err"]:
            fails[e["key"]] = fails.get(e["key"], 0) + 1
    key, n = max(fails.items(), key=lambda kv: kv[1], default=("", 0))
    if n >= _LOOP_SAME_FAIL:
        return {"kind": "same_fail", "count": n, "text": f"The same call failed {n}×: {key[:80]}"}
    edits = {}
    for e in ring:
        if e.get("file"):
            edits[e["file"]] = edits.get(e["file"], 0) + 1
    path, n = max(edits.items(), key=lambda kv: kv[1], default=("", 0))
    errors = sum(1 for e in done if e["err"])
    if n >= _LOOP_FILE_EDITS and errors >= _LOOP_FILE_ERRORS:
        name = path.replace("\\", "/").rstrip("/").split("/")[-1]
        return {"kind": "edit_loop", "count": n,
                "text": f"{name} edited {n}× with {errors} failures in between, no commit"}
    return None


# ── "While you were away": what a session did since your last message ─────
# The scanner counts tool calls / failures / commits / edited files since
# your last message to each session (_activity_track, cheap, every scan).
# On Today, sessions with real activity since then get a short recap written
# by Claude Haiku through your own Claude Code login (`claude -p`, no tools,
# hooks off, nothing saved -- about 1 cent), cached per session until it
# has done more (_recap_get).
RECAP_SETTINGS_FILE = os.path.join(AOC_DATA_DIR, "recap.json")
RECAP_CACHE_FILE = os.path.join(AOC_DATA_DIR, "recaps.json")
_RECAP_MODES = ("auto", "click", "off")
_RECAP_MIN_AWAY_S = 15 * 60     # your last message at least this long ago...
_RECAP_MIN_TOOLS = 3            # ...and at least this many tool calls since
_RECAP_REFRESH_S = 10 * 60      # don't re-summarize one session more often
_RECAP_DIGEST_MAX = 12000
_recap_lock = threading.Lock()
_recap_busy = set()             # session ids being summarized right now
_recap_run_lock = threading.Lock()  # one `claude -p` at a time, however many dashboards ask


def _human_text(obj):
    """Pure: the text of a line YOU wrote to the main thread, else None
    (tool results, meta/system lines and subagent lines are not you)."""
    if not isinstance(obj, dict) or obj.get("type") != "user" or obj.get("isMeta") or obj.get("isSidechain"):
        return None
    content = (obj.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return None


_SESSION_EDITED_MAX = 300  # files remembered per session for its Changes view
_ACTIVITY_MINUTES_MAX = 1440  # active minutes remembered per session (a day's worth)


def _activity_track(stats, obj, now):
    """Feed one transcript line into stats["activity"]: counters since your
    last message to this session."""
    act = stats.setdefault("activity", {"since": 0, "tools": 0, "errors": 0, "commits": 0, "files": [], "pending": {}})
    # transcript lines per minute (last 24 h) -- the Heat / Timeline session views
    if isinstance(obj, dict) and obj.get("type") in ("user", "assistant") and not obj.get("isSidechain"):
        ts = _iso_to_epoch(obj.get("timestamp"))
        if ts:
            mins = act.setdefault("minutes", {})
            m = int(ts // 60) * 60
            mins[m] = mins.get(m, 0) + 1
            if len(mins) > _ACTIVITY_MINUTES_MAX:
                for k in sorted(mins)[:len(mins) - _ACTIVITY_MINUTES_MAX]:
                    del mins[k]
    if _human_text(obj) is not None:
        act.update({"since": _iso_to_epoch(obj.get("timestamp")) or now, "tools": 0, "errors": 0,
                    "commits": 0, "files": [], "pending": {}})
        return
    if not isinstance(obj, dict) or obj.get("isSidechain"):
        return
    content = (obj.get("message") or {}).get("content")
    if not isinstance(content, list):
        return
    for b in content:
        if not isinstance(b, dict):
            continue
        if obj.get("type") == "assistant" and b.get("type") == "tool_use":
            act["tools"] += 1
            key, path = _loop_tool_key(b.get("name"), b.get("input"))
            if path and path not in act["files"]:
                act["files"] = (act["files"] + [path])[-50:]
            if path:  # every file the session ever edited (not reset by your messages)
                edited = [f for f in act.get("edited", []) if f != path] + [path]
                act["edited"] = edited[-_SESSION_EDITED_MAX:]
            if _is_commit_call(b.get("name"), b.get("input")):
                act["pending"][b.get("id", "")] = "commit"
        elif obj.get("type") == "user" and b.get("type") == "tool_result":
            if b.get("is_error"):
                act["errors"] += 1
            elif act["pending"].pop(b.get("tool_use_id", ""), None) == "commit":
                act["commits"] += 1
            act["pending"].pop(b.get("tool_use_id", ""), None)


def _activity_view(act, now):
    """Pure: the public "since your last message" summary, or None if the
    session hasn't done enough on its own to be worth a recap."""
    if not act or not act.get("since") or now - act["since"] < _RECAP_MIN_AWAY_S \
            or act.get("tools", 0) < _RECAP_MIN_TOOLS:
        return None
    return {"since": act["since"], "tools": act["tools"], "errors": act["errors"],
            "commits": act["commits"], "files": len(act.get("files") or [])}


def _recap_digest(lines, max_chars=_RECAP_DIGEST_MAX):
    """Pure: compact text of what happened after your last message in these
    transcript lines (your message, Claude's words, each tool call with its
    failures), middle cut if too long. "" if you never wrote anything."""
    objs = []
    for line in lines:
        try:
            o = json.loads(line)
        except Exception:
            continue
        if isinstance(o, dict) and not o.get("isSidechain"):
            objs.append(o)
    start = next((i for i in range(len(objs) - 1, -1, -1) if _human_text(objs[i]) is not None), None)
    if start is None:
        return ""
    out = ["YOU: " + _human_text(objs[start]).strip()[:800]]
    names = {}
    for o in objs[start + 1:]:
        content = (o.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if o.get("type") == "assistant" and b.get("type") == "text" and (b.get("text") or "").strip():
                out.append("CLAUDE: " + " ".join(b["text"].split())[:400])
            elif o.get("type") == "assistant" and b.get("type") == "tool_use":
                key = _loop_tool_key(b.get("name"), b.get("input"))[0]
                names[b.get("id", "")] = key
                out.append("-> " + key[:160])
            elif o.get("type") == "user" and b.get("type") == "tool_result" and b.get("is_error"):
                c = b.get("content")
                if isinstance(c, list):
                    c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
                out.append("   FAILED: " + " ".join(str(c or "").split())[:200])
    last = _assistant_text(next((o for o in reversed(objs) if _assistant_text(o)), None), limit=1500)
    if last:
        out.append("CLAUDE'S LAST MESSAGE: " + last)
    text = "\n".join(out)
    if len(text) > max_chars:
        head = max_chars // 4
        text = text[:head] + "\n[... middle cut ...]\n" + text[-(max_chars - head):]
    return text


_RECAP_INSTRUCTION = (
    "Below is a log of a Claude Code session since its owner's last message (YOU), who has been "
    "away. Write a recap for them: 2-4 short bullet points (start each with '- '). Say what got done, "
    "the state now (tests passing? committed? pushed? finished or still going?), and anything that "
    "needs them (errors, a question, a decision). Later lines override earlier ones: describe the "
    "final state, never both versions. Plain text only: no markdown, no bold, no backticks. Plain words, no preamble, no headings. Write in the "
    "same language as the YOU message.")


def _find_claude_exe():
    """claude.exe for `claude -p`: the npm wrapper's real binary (a .cmd
    would mangle the empty --tools argument), the native installer's, or
    whatever is on PATH."""
    import shutil
    found = shutil.which("claude")
    if found and found.lower().endswith((".cmd", ".bat", ".ps1")) or (found and not os.path.splitext(found)[1]):
        exe = os.path.join(os.path.dirname(found), "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe")
        if os.path.exists(exe):
            return exe
    native = os.path.join(os.path.expanduser("~"), ".local", "bin", "claude.exe")
    if os.path.exists(native):
        return native
    return found


def _run_haiku(prompt, stdin_text, timeout=120, model="haiku"):
    """-> (text, cost_usd). `claude -p` with Haiku (or `model`), no tools,
    hooks off (our own hooks would otherwise register it as a session), not saved."""
    exe = _find_claude_exe()
    if not exe:
        raise RuntimeError("Claude Code (claude) not found")
    with _recap_run_lock:
        p = subprocess.Popen(
            [exe, "-p", "--model", model, "--no-session-persistence", "--tools", "",
             "--settings", '{"disableAllHooks": true}', "--output-format", "json", prompt],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=AOC_DATA_DIR if os.path.isdir(AOC_DATA_DIR) else None,
            creationflags=0x08000000 if os.name == "nt" else 0)  # CREATE_NO_WINDOW
        _own_claude_pids.add(p.pid)
        try:
            out, err = p.communicate(stdin_text.encode("utf-8"), timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
            raise RuntimeError("Claude Code took too long to answer")
        finally:
            _own_claude_pids.discard(p.pid)
    try:
        d = json.loads(out.decode("utf-8", "replace"))
    except ValueError:
        raise RuntimeError((err.decode("utf-8", "replace") or "no output").strip()[:200])
    if d.get("is_error") or not (d.get("result") or "").strip():
        raise RuntimeError(str(d.get("result") or "empty answer")[:200])
    return d["result"].strip(), float(d.get("total_cost_usd") or 0)


def _load_recap_settings():
    d = _load_json_file(RECAP_SETTINGS_FILE, {})
    mode = d.get("mode") if isinstance(d, dict) and d.get("mode") in _RECAP_MODES else "auto"
    return {"mode": mode}


def _save_recap_settings(data):
    mode = data.get("mode") if isinstance(data, dict) and data.get("mode") in _RECAP_MODES else "auto"
    os.makedirs(AOC_DATA_DIR, exist_ok=True)
    with open(RECAP_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump({"mode": mode}, f)
    return {"mode": mode}


def _recap_cached(sid):
    d = _load_json_file(RECAP_CACHE_FILE, {})
    return (d if isinstance(d, dict) else {}).get(sid)


def _recap_store(sid, entry):
    with _recap_lock:
        d = _load_json_file(RECAP_CACHE_FILE, {})
        d = d if isinstance(d, dict) else {}
        d[sid] = entry
        # keep the 200 newest
        d = dict(sorted(d.items(), key=lambda kv: kv[1].get("generated_at", 0))[-200:])
        os.makedirs(AOC_DATA_DIR, exist_ok=True)
        tmp = RECAP_CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, RECAP_CACHE_FILE)


def _recap_needs_refresh(cached, since, size, now, force):
    """Pure: summarize again? Not while nothing new happened; not more often
    than _RECAP_REFRESH_S unless asked (force) -- and never when nothing new."""
    if not cached or cached.get("since") != since:
        return True
    if cached.get("size") == size:
        return False
    return force or now - cached.get("generated_at", 0) >= _RECAP_REFRESH_S


def _recap_get(sid, force=False, run=None, now=None):
    """GET /recap: {"ok", "text", "generated_at", "cost", "fresh"} for a
    session's activity since your last message; summarizes (blocking,
    a few seconds) when needed. run = the summarizer (tests swap it)."""
    now = now or time.time()
    with _transcript_cursors_lock:
        cur = dict(_transcript_cursors.get(sid) or {})
    path = cur.get("path")
    act = (cur.get("stats") or {}).get("activity") or {}
    if not path or not os.path.exists(path):
        return {"ok": False, "error": "no transcript for this session"}
    size = os.path.getsize(path)
    cached = _recap_cached(sid)
    if not _recap_needs_refresh(cached, act.get("since"), size, now, force):
        return {"ok": True, **cached, "fresh": False}
    with _recap_lock:
        if sid in _recap_busy:
            return {"ok": False, "busy": True, "error": "already summarizing"}
        _recap_busy.add(sid)
    try:
        with open(path, "rb") as f:
            f.seek(max(0, size - 4 * 1024 * 1024))  # the last 4 MB is plenty
            lines = f.read().decode("utf-8", "replace").splitlines()
        digest = _recap_digest(lines)
        if not digest:
            return {"ok": False, "error": "nothing to summarize yet"}
        text, cost = (run or _run_haiku)(_RECAP_INSTRUCTION, digest)
        entry = {"text": text[:2000], "cost": round(cost, 4), "generated_at": now,
                 "since": act.get("since"), "size": size}
        _recap_store(sid, entry)
        return {"ok": True, **entry, "fresh": True}
    except Exception as e:
        _log_bg_error("recap", e)
        return {"ok": False, "error": str(e)[:200]}
    finally:
        with _recap_lock:
            _recap_busy.discard(sid)
