# AOC server, part 12: replay, conversation search, clients & billing, work log, model advice.
# monitor.py exec()s server/NN_*.py in number order into ONE shared
# namespace (its own) -- not an importable module: names from earlier
# parts are plain globals here, and __file__ is monitor.py.
# ── Session replay: one session's transcript, step by step ────────────────
# GET /session_replay?session_id= -> every prompt, piece of assistant text,
# tool call (with its result / error) and git commit in order, with the cost
# of each API call on the first step it produced -- to see afterwards where
# a session went wrong or where the money went.
_REPLAY_MAX_STEPS = 4000
_REPLAY_TEXT_MAX = 1200
_replay_cache = {"key": None, "value": None}
_replay_cache_lock = threading.Lock()


def _replay_epoch(ts):
    """Pure: transcript ISO timestamp -> epoch seconds (None if unparsable)."""
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _replay_result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _replay_steps(lines, max_steps=_REPLAY_MAX_STEPS) -> dict:
    """Pure: transcript lines (raw JSON strings) -> {"steps", "summary",
    "truncated"}. Main thread only; each API call's cost is counted once
    (lines repeat the same usage per content block) and put on the first
    step it produced."""
    steps, by_tool_id, seen_calls = [], {}, set()
    total = 0.0
    calls = 0
    pending = {}   # the current API call's cost, until a step from that call takes it

    def flush():
        if pending:   # a call that produced no text / tool step (only thinking)
            steps.append({"t": pending["t"], "kind": "text", "text": "(thinking)", "cost": pending["cost"],
                          "model": pending["model"], "tokens": pending["tokens"]})
            pending.clear()
    for raw in lines:
        try:
            obj = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(obj, dict) or obj.get("isSidechain"):
            continue
        t = _replay_epoch(obj.get("timestamp"))
        msg = obj.get("message") or {}
        content = msg.get("content")
        if obj.get("type") == "user" and not obj.get("isMeta"):
            flush()
            if isinstance(content, str) and content.strip():
                text = content.strip()
                kind = "command" if text.startswith(("<command-", "<local-command")) else "prompt"
                if kind == "command":
                    m = re.search(r"<command-name>([^<]*)</command-name>", text)
                    text = m.group(1).strip() if m else "(command output)"
                    if not m:
                        continue
                steps.append({"t": t, "kind": kind, "text": text[:_REPLAY_TEXT_MAX]})
            elif isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_result":
                        st = by_tool_id.pop(b.get("tool_use_id"), None)
                        if st is not None:
                            st["err"] = bool(b.get("is_error"))
                            st["result"] = _replay_result_text(b.get("content")).strip()[:400]
                            if t and st.get("t"):
                                st["secs"] = round(max(0.0, t - st["t"]), 1)
                    elif b.get("type") == "text" and (b.get("text") or "").strip() \
                            and not str(b.get("text")).lstrip().startswith("<"):
                        steps.append({"t": t, "kind": "prompt", "text": b["text"].strip()[:_REPLAY_TEXT_MAX]})
        elif obj.get("type") == "assistant" and isinstance(content, list):
            usage = msg.get("usage") or {}
            model = msg.get("model") or ""
            key = (msg.get("id") or "", obj.get("requestId") or "")
            if pending and pending["key"] != key:
                flush()
            if usage and (key == ("", "") or key not in seen_calls):
                seen_calls.add(key)
                calls += 1
                cost = _calc_cost(usage.get("input_tokens", 0) or 0, usage.get("output_tokens", 0) or 0,
                                  usage.get("cache_creation_input_tokens", 0) or 0,
                                  usage.get("cache_read_input_tokens", 0) or 0, model,
                                  ((usage.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") or 0))
                total += cost
                pending.update(key=key, cost=round(cost, 5), model=model, t=t,
                               tokens=sum((usage.get(k, 0) or 0) for k in ("input_tokens", "output_tokens",
                                                                       "cache_creation_input_tokens", "cache_read_input_tokens")))
            for b in content:
                if not isinstance(b, dict):
                    continue
                st = None
                if b.get("type") == "text" and (b.get("text") or "").strip():
                    st = {"t": t, "kind": "text", "text": b["text"].strip()[:_REPLAY_TEXT_MAX]}
                elif b.get("type") == "tool_use":
                    summary, detail = _perm_summary(b.get("name"), b.get("input"))
                    st = {"t": t, "kind": "tool", "tool": str(b.get("name") or "?"), "text": summary,
                          "detail": detail[:_REPLAY_TEXT_MAX], "err": None}
                    if _is_commit_call(b.get("name"), b.get("input")):
                        st["commit"] = True
                    by_tool_id[b.get("id")] = st
                if st is None:
                    continue
                if pending and pending["key"] == key:
                    st["cost"], st["model"], st["tokens"] = pending["cost"], pending["model"], pending["tokens"]
                    pending.clear()
                steps.append(st)
    flush()
    truncated = len(steps) > max_steps
    if truncated:
        steps = steps[-max_steps:]
    times = [s["t"] for s in steps if s.get("t")]
    for i, s in enumerate(steps):
        s["i"] = i
    costly = sorted((s for s in steps if s.get("cost")), key=lambda s: -s["cost"])[:5]
    summary = {
        "start": min(times) if times else None, "end": max(times) if times else None,
        "cost": round(total, 4), "calls": calls,
        "prompts": sum(1 for s in steps if s["kind"] == "prompt"),
        "tools": sum(1 for s in steps if s["kind"] == "tool"),
        "errors": sum(1 for s in steps if s.get("err")),
        "commits": sum(1 for s in steps if s.get("commit")),
        "top": [s["i"] for s in costly],
    }
    return {"steps": steps, "summary": summary, "truncated": truncated}


def _find_transcript(sid):
    """The main transcript file of a session, live or past ('' if none)."""
    if not re.match(r"^[A-Za-z0-9_-]{8,80}$", str(sid or "")):
        return ""
    with _transcript_cursors_lock:
        path = (_transcript_cursors.get(sid) or {}).get("path")
    if path and os.path.exists(path):
        return path
    base = os.path.join(os.path.expanduser("~"), ".claude", "projects")
    hits = glob.glob(os.path.join(base, "*", sid + ".jsonl"))
    return max(hits, key=os.path.getmtime) if hits else ""


def _session_replay(sid) -> dict:
    """GET /session_replay -> {"ok", "steps", "summary", "truncated"}."""
    path = _find_transcript(sid)
    if not path:
        return {"ok": False, "error": "no transcript for this session on this PC"}
    st = os.stat(path)
    key = (path, st.st_size, st.st_mtime)
    with _replay_cache_lock:
        if _replay_cache["key"] == key:
            return _replay_cache["value"]
    with open(path, "rb") as f:
        lines = f.read().decode("utf-8", "replace").splitlines()
    value = {"ok": True, **_replay_steps(lines)}
    with _replay_cache_lock:
        _replay_cache["key"], _replay_cache["value"] = key, value
    return value


def _usage_first_sighting(stats: dict, msg_id, request_id) -> bool:
    """True the first time a (message id, request id) pair is seen in this
    session's transcript. Lines without either id are counted (old formats)."""
    if not msg_id and not request_id:
        return True
    seen = stats.setdefault("usage_keys", set())
    key = (msg_id or "", request_id or "")
    if key in seen:
        return False
    seen.add(key)
    return True


def _accumulate_usage(stats: dict, usage: dict, model: str) -> None:
    """Add one API response's usage to a session's running totals."""
    inp = usage.get("input_tokens", 0) or 0
    out = usage.get("output_tokens", 0) or 0
    cw = usage.get("cache_creation_input_tokens", 0) or 0
    cr = usage.get("cache_read_input_tokens", 0) or 0
    cw_1h = ((usage.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") or 0)
    stats["input_tokens"] = stats.get("input_tokens", 0) + inp
    stats["output_tokens"] = stats.get("output_tokens", 0) + out
    stats["cache_write_tokens"] = stats.get("cache_write_tokens", 0) + cw
    stats["cache_write_1h_tokens"] = stats.get("cache_write_1h_tokens", 0) + cw_1h
    stats["cache_read_tokens"] = stats.get("cache_read_tokens", 0) + cr
    stats["msg_count"] = stats.get("msg_count", 0) + 1
    stats["cost_acc"] = stats.get("cost_acc", 0.0) + _calc_cost(inp, out, cw, cr, model, cw_1h)


def _transcript_scanner_worker():
    """Background thread: scan ~/.claude/projects/ for active sessions every 30s.
    Incrementally reads transcripts to extract token usage, model, cost, and ai-title.
    Fills in sessions that hooks may have missed."""
    import time as _t
    import hashlib as _hashlib  # local import: the thread starts (line ~1143,
    # below) before module-level "import hashlib as _hashlib" (line ~1321) runs,
    # so relying on that global would be a startup-order race.
    projects_base = os.path.join(os.path.expanduser("~"), ".claude", "projects")
    # _status_lock / _load_status are defined further down; the first scan
    # used to race them at startup (NameError in background_errors.log)
    _module_loaded.wait(60)

    while True:
        try:
            if not os.path.isdir(projects_base):
                _t.sleep(30)
                continue

            now = _t.time()
            active_cutoff = now - 300        # 5 min → actively running session
            presence_cutoff = now - 1800     # 30 min → recently used session

            for encoded_proj in os.listdir(projects_base):
                proj_dir = os.path.join(projects_base, encoded_proj)
                if not os.path.isdir(proj_dir):
                    continue

                for fname in os.listdir(proj_dir):
                    if not fname.endswith(".jsonl"):
                        continue
                    fpath = os.path.join(proj_dir, fname)
                    try:
                        mtime = os.path.getmtime(fpath)
                    except OSError:
                        continue

                    if mtime < presence_cutoff:
                        continue

                    session_id = fname[:-6]  # strip .jsonl

                    # Get current cursor for this session
                    with _transcript_cursors_lock:
                        cursor = _transcript_cursors.get(session_id, {"path": fpath, "offset": 0, "stats": {}})

                    # Read new bytes from current offset
                    try:
                        with open(fpath, "rb") as f:
                            f.seek(cursor["offset"])
                            new_data = f.read()
                        new_offset = cursor["offset"] + len(new_data)
                        new_lines = [l for l in new_data.decode("utf-8", errors="replace").splitlines() if l.strip()]
                    except Exception:
                        continue

                    stats = cursor["stats"].copy()
                    ai_title = stats.get("ai_title", "")
                    # Agent/Task subagent starts + completions detected directly from
                    # this transcript -- a fallback for when Claude Code's hook
                    # dispatch silently never fires PreToolUse/PostToolUse for a real
                    # Agent tool call (confirmed live, 2026-07-15: zero TRACE lines
                    # anywhere for a session with real Agent tool_use blocks in its
                    # transcript). The transcript is Claude Code's own authoritative
                    # record, independent of that fragile side-channel.
                    agent_events = []

                    # Also read ai-title from first 50 lines if we haven't found it yet
                    # and this is the first scan (offset was 0 before)
                    if not ai_title and cursor["offset"] == 0:
                        ai_title = _read_ai_title(fpath)
                        if ai_title:
                            stats["ai_title"] = ai_title

                    _limit_last = None   # this pass's last usage-limit stop / clear
                    for line in new_lines:
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue
                        t = obj.get("type")
                        _loop_track(stats, obj)
                        _activity_track(stats, obj, now)
                        _lev, _linfo = _limit_stop_event(obj)
                        if _lev:
                            _limit_last = (_lev, _linfo, obj.get("cwd") or "")
                        _ql = obj.get("quotaLimits")
                        if isinstance(_ql, dict) and _ql.get("status") == "rejected":
                            _record_rate_limit_hit(_ql.get("rateLimitType"), _ql.get("resetsAt"),
                                                   _iso_to_epoch(obj.get("timestamp")) or now)
                        if t == "ai-title" and not ai_title:
                            ai_title = obj.get("aiTitle", "")
                            stats["ai_title"] = ai_title
                        elif t == "assistant":
                            msg = obj.get("message", {})
                            usage = msg.get("usage", {})
                            # "waiting on you" fallback for sessions whose Stop/
                            # UserPromptSubmit heartbeat hook silently misses.
                            _woy = _waiting_on_you_from_line(t, msg.get("stop_reason"))
                            if _woy is not None:
                                stats["waiting_on_you"] = _woy
                                stats["woy_epoch"] = _iso_to_epoch(obj.get("timestamp")) or now
                            if not stats.get("model") and msg.get("model"):
                                stats["model"] = msg["model"]
                            if not stats.get("cc_version") and obj.get("version"):
                                stats["cc_version"] = obj["version"]
                                _check_new_cc_version(obj["version"])
                            if not stats.get("git_branch") and obj.get("gitBranch"):
                                stats["git_branch"] = obj["gitBranch"]
                            ts = obj.get("timestamp", "")
                            if ts:
                                if not stats.get("first_ts"):
                                    stats["first_ts"] = ts
                                stats["last_ts"] = ts
                            # One API response is written as one line PER CONTENT
                            # BLOCK (thinking, text, tool_use...), each repeating
                            # the same usage -- summing every line overcounted
                            # tokens and cost ~2.4x. Count each (message id,
                            # request id) once, priced with its own model.
                            if usage and _usage_first_sighting(stats, msg.get("id"), obj.get("requestId")):
                                _accumulate_usage(stats, usage, msg.get("model") or stats.get("model", ""))
                            _last_txt = _assistant_text(obj)
                            if _last_txt:
                                stats["last_message"] = _last_txt

                            # Agent/Task subagent start -- same id scheme as
                            # aoc_hook.py's agent_id_from_hook() (ag_ + md5(tool_use_id)),
                            # so a hook that *does* fire for the same call lands on the
                            # same id and the existing exact-match upsert merges them.
                            for block in msg.get("content", []) or []:
                                if not isinstance(block, dict) or block.get("type") != "tool_use":
                                    continue
                                if block.get("name") not in ("Agent", "Task"):
                                    continue
                                tu_id = block.get("id", "")
                                if not tu_id:
                                    continue
                                inp = block.get("input", {}) or {}
                                desc = inp.get("description") or inp.get("prompt") or ""
                                agent_events.append({
                                    "id": "ag_" + _hashlib.md5(tu_id.encode()).hexdigest()[:10],
                                    "name": (desc[:50] or tu_id),
                                    "icon": "AI",
                                    "description": desc,
                                    "status": "running",
                                    "started_at": _iso_utc_to_local_hms(ts) if ts else _now_ts(),
                                    # Only ever set on the *start* event -- quantifies how
                                    # often Claude Code's hook dispatch actually drops a
                                    # PreToolUse (previously invisible/unmeasurable, since
                                    # the fallback silently fixed it). Not set on completion
                                    # events, so an agent the hook DID catch never gets
                                    # mislabeled just because the transcript scanner also
                                    # (harmlessly, redundantly) saw its completion.
                                    "detected_via": "transcript",
                                })
                                # Tracked persistently (via `stats`, which survives
                                # across scan passes through the cursor) so the
                                # immediate-failure fallback below can still
                                # recognize this id's completion even if it lands
                                # in a later 30s scan pass than the start did.
                                stats.setdefault("agent_tool_use_ids", set()).add(tu_id)

                        elif t == "user":
                            stats["waiting_on_you"] = _waiting_on_you_from_line(t, None)
                            stats["woy_epoch"] = _iso_to_epoch(obj.get("timestamp")) or now
                            # Agent/Task completion via the SYNCHRONOUS path -- a
                            # foreground Agent call (run_in_background: false)
                            # resolves with a normal tool_result whose sibling
                            # toolUseResult carries full per-model usage, richer
                            # than the async task-notification's flat token total.
                            tur = obj.get("toolUseResult")
                            if isinstance(tur, dict) and "totalTokens" in tur and "resolvedModel" in tur:
                                tu_id = ""
                                for block in (obj.get("message", {}) or {}).get("content", []) or []:
                                    if isinstance(block, dict) and block.get("type") == "tool_result":
                                        tu_id = block.get("tool_use_id", "")
                                        break
                                if tu_id:
                                    usage = tur.get("usage", {}) or {}
                                    au = {
                                        "id": "ag_" + _hashlib.md5(tu_id.encode()).hexdigest()[:10],
                                        "status": "done" if tur.get("status") == "completed" else "error",
                                        "completed_at": _now_ts(),
                                        "tokens_used": tur.get("totalTokens"),
                                        "model": tur.get("resolvedModel", ""),
                                        "input_tokens": usage.get("input_tokens", 0),
                                        "output_tokens": usage.get("output_tokens", 0),
                                        "cache_write_tokens": usage.get("cache_creation_input_tokens", 0),
                                        "cache_write_1h_tokens": (usage.get("cache_creation") or {}).get("ephemeral_1h_input_tokens", 0) or 0,
                                        "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
                                        "_completion_only": True,
                                    }
                                    content = tur.get("content")
                                    if isinstance(content, list) and content and isinstance(content[0], dict):
                                        au["log"] = [content[0].get("text", "")]
                                    agent_events.append(au)
                            else:
                                # Immediate-failure fallback: an Agent/Task call that
                                # errors before ever actually running (e.g. "not in a
                                # git repository" for worktree isolation) never gets a
                                # totalTokens/resolvedModel-shaped toolUseResult -- it's
                                # either a plain error string or a dict missing those
                                # keys. Without this, such an agent stays stuck showing
                                # "running" forever (confirmed live, 2026-07-18: a
                                # worktree-creation failure's toolUseResult was just the
                                # bare string "Error: Cannot create agent worktree...").
                                # Only fires for a tool_use_id we've actually seen as an
                                # Agent/Task start (tracked above), so an unrelated
                                # failed tool call (Bash, Read, etc.) never gets
                                # mistaken for a stuck agent completion.
                                known_ids = stats.get("agent_tool_use_ids") or set()
                                for block in (obj.get("message", {}) or {}).get("content", []) or []:
                                    if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                                        continue
                                    tu_id = block.get("tool_use_id", "")
                                    if not tu_id or tu_id not in known_ids or not block.get("is_error"):
                                        continue
                                    error_text = block.get("content")
                                    if not isinstance(error_text, str):
                                        error_text = str(tur) if tur else "Agent call failed"
                                    agent_events.append({
                                        "id": "ag_" + _hashlib.md5(tu_id.encode()).hexdigest()[:10],
                                        "status": "error",
                                        "completed_at": _now_ts(),
                                        "error_message": error_text[:4000],
                                        "log": [error_text[:80]],
                                        "_completion_only": True,
                                    })

                        # Agent/Task completion -- arrives asynchronously, well after
                        # the tool_use block, as a task-notification block (seen both
                        # as a queue-operation line and mirrored into a "user" message;
                        # checked regardless of `t` for that reason). Regex over the
                        # raw line rather than the parsed structure since the block is
                        # plain text embedded in a content string, not nested JSON.
                        for notif_m in _TASK_NOTIF_RE.finditer(line):
                            notif_body = notif_m.group(1)
                            tags = dict(_TASK_TAG_RE.findall(notif_body))
                            tu_id = tags.get("tool-use-id", "")
                            if not tu_id:
                                continue
                            usage_m = _TASK_USAGE_RE.search(notif_body)
                            au = {
                                "id": "ag_" + _hashlib.md5(tu_id.encode()).hexdigest()[:10],
                                "status": "done" if tags.get("status") == "completed" else "error",
                                "completed_at": _now_ts(),
                                "log": [tags.get("result") or tags.get("summary") or ""],
                                # This same task-notification XML shape is also used for
                                # background Bash/Monitor tool completions, not just
                                # Agent/Task subagents -- only ever update an agent that
                                # a real "start" already created (see the apply loop
                                # below), never create a new card from a completion
                                # alone here (unlike the hook's own late-arrival path,
                                # which trusts its update came from a real agent).
                                "_completion_only": True,
                            }
                            if usage_m:
                                au["tokens_used"] = int(usage_m.group(1))
                            agent_events.append(au)

                    # per-message cost, summed in _accumulate_usage
                    stats["estimated_cost"] = stats.get("cost_acc", 0.0)
                    stats["loop"] = _loop_signal(stats.get("loop_ring"))
                    if _limit_last:
                        try:
                            _limit_stop_record(session_id, _limit_last[0], _limit_last[1], _limit_last[2], ai_title)
                        except Exception as e:
                            _log_bg_error("limit_stop", e)

                    # Save updated cursor
                    with _transcript_cursors_lock:
                        _transcript_cursors[session_id] = {"path": fpath, "offset": new_offset, "stats": stats}

                    project_name = _decode_project_name(encoded_proj)

                    with _status_lock:
                        status = _load_status()
                        sessions = status.setdefault("sessions", {})
                        if sessions.get(session_id, {}).get("dismissed"):
                            continue  # user dismissed this session — don't re-add
                        if session_id not in sessions:
                            sessions[session_id] = {}
                        sess = sessions[session_id]
                        # an Orchestrate agent's transcript (its projects dir is named
                        # after its aoc-orch-* worktree): a worker, even before a hook
                        # reports it -- see _orch_worker_of
                        if _orch_worker_path(encoded_proj):
                            sess["transcript_dir"] = encoded_proj
                        # Only fill base fields if hook data is stale
                        last_seen_epoch = sess.get("last_seen_epoch", 0)
                        if now - last_seen_epoch >= 120:
                            if not sess.get("last_seen_epoch") or sess["last_seen_epoch"] < mtime:
                                sess["last_seen_epoch"] = mtime
                                sess["last_seen"] = _now_ts()
                            sess.setdefault("project", project_name)
                            # Mark active if transcript was modified within last 5
                            # minutes, OR the underlying claude.exe is confirmed still
                            # running -- a transcript can legitimately go quiet for
                            # longer than 5 min mid-turn (a long tool call, thinking)
                            # without the CLI actually having closed.
                            sess["session_active"] = mtime > active_cutoff or _is_claude_pid_alive(sess.get("host_pid"))
                        # Always update stats fields regardless of hook freshness
                        for field in ("model", "cc_version", "git_branch", "input_tokens", "output_tokens",
                                      "cache_read_tokens", "cache_write_tokens", "estimated_cost",
                                      "msg_count", "first_ts", "last_ts"):
                            val = stats.get(field)
                            if val is not None and val != 0 and val != "":
                                sess[field] = val
                        # Not folded into the loop above: "val != 0" is True for a bool,
                        # since bool is an int subclass and False == 0 -- that check would
                        # silently swallow every "False" value forever.
                        if "waiting_on_you" in stats and _transcript_woy_applies(sess, stats) and not _orch_worker_of(sess):
                            _accumulate_waiting_time(sess, stats["waiting_on_you"], now)
                            if bool(stats["waiting_on_you"]) != bool(sess.get("waiting_on_you")):
                                sess["waiting_kind"] = "turn" if stats["waiting_on_you"] else ""
                                sess["waiting_message"] = ""
                            sess["waiting_on_you"] = stats["waiting_on_you"]
                        if stats.get("last_message"):
                            sess["last_message"] = stats["last_message"]
                        sess["loop"] = stats.get("loop")
                        _act = stats.get("activity") or {}
                        sess["activity_raw"] = {k: _act.get(k, 0) for k in ("since", "tools", "errors", "commits")}
                        sess["activity_raw"]["files"] = list(_act.get("files") or [])
                        sess["edited_count"] = len(_act.get("edited") or [])
                        if ai_title:
                            # Self-heals an already-corrupted stored value, not
                            # just "set if missing" -- the previous version of
                            # this code only ever filled in a MISSING
                            # display_name, so any future genuine corruption
                            # would have been permanent (the auto-un-dismiss
                            # style bug class: a one-time bad write with no
                            # path back to good). Compares against a fresh
                            # read of the transcript (always re-derivable)
                            # rather than checking whether the stored value
                            # merely "looks fixable" via _fix_mojibake, since
                            # some hypothetical corruption (a genuine
                            # unrecoverable U+FFFD from an old errors="replace"
                            # decode, as opposed to a systematic codepage
                            # mismatch) can't be reversed by re-encoding an
                            # already-lossy stored string -- only a fresh
                            # source has the real characters.
                            fixed_title = _fix_mojibake(ai_title)
                            if sess.get("display_name") != fixed_title:
                                sess["display_name"] = fixed_title
                        # B — fix mojibake cwd that may have been stored before the encoding fix
                        if sess.get("cwd"):
                            sess["cwd"] = _fix_mojibake(sess["cwd"])
                        # Apply any Agent/Task starts/completions detected above through
                        # the same upsert path the hook-driven /update handler uses, so
                        # a session whose hook never fires still gets agent cards.
                        known_ids = {a["id"] for a in status.get("agents", [])}
                        for au in agent_events:
                            if au.pop("_completion_only", False) and au["id"] not in known_ids:
                                continue  # not a real Agent/Task call -- see marker comment above
                            _apply_agent_update(status, au, session_id, sess.get("project", project_name), _now_ts())
                            known_ids.add(au["id"])
                        _save_status(status)

                    # A — persist token/cost data to SQLite so analytics are accurate
                    total_tokens = stats.get("input_tokens", 0) + stats.get("output_tokens", 0)
                    estimated_cost = stats.get("estimated_cost", 0.0)
                    if total_tokens > 0 or estimated_cost > 0:
                        # Prefer in-memory project (set by hook) over decoded path (may fail for long paths)
                        db_project = sess.get("project", "") or project_name or ""
                        try:
                            with _db_lock:
                                c = _db_conn()
                                _db_upsert_transcript_session(
                                    c, session_id, db_project,
                                    sess.get("first_ts") or stats.get("first_ts", ""),
                                    sess.get("last_ts") or stats.get("last_ts", ""),
                                    total_tokens, estimated_cost,
                                    sess.get("cc_version") or stats.get("cc_version") or "",
                                    sess.get("display_name") or ai_title or "")
                                c.commit()
                                c.close()
                        except Exception as e:
                            _log_bg_error("_transcript_scanner_worker/db", e)

        except Exception as e:
            _log_bg_error("_transcript_scanner_worker", e)

        _t.sleep(30)


_threading.Thread(target=_transcript_scanner_worker, daemon=True).start()


# ── Conversation search (full text over ~/.claude/projects transcripts) ─────
# Own SQLite file next to history.db, not inside it: history.db is copied by
# the hourly backup, and the index is rebuildable from the transcripts at any
# time. Only what people actually wrote/read is indexed -- user prompts and
# assistant text -- not tool calls/results, which are ~99 % of the bytes.
TRANSCRIPT_INDEX_DB = os.path.join(AOC_DATA_DIR, "transcript_index.db")
_TRANSCRIPT_INDEX_MAX_TEXT = 20000      # chars per message
_TRANSCRIPT_INDEX_BYTES_PER_TICK = 32 * 1024 * 1024
_transcript_cost_repair_done = [False]  # once per process, after the first full index pass
_transcript_index_progress = {"files": 0, "indexed": 0, "messages": 0, "building": False, "updated_at": 0}


# Bump when the schema or what gets extracted changes: the index is derived
# data, so an old one is simply dropped and rebuilt from the transcripts.
_TRANSCRIPT_INDEX_VERSION = 3


def _transcript_index_connect(path=None):
    conn = sqlite3.connect(path or TRANSCRIPT_INDEX_DB, timeout=10, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    if conn.execute("PRAGMA user_version").fetchone()[0] != _TRANSCRIPT_INDEX_VERSION:
        conn.executescript("""
            DROP TABLE IF EXISTS files; DROP TABLE IF EXISTS sessions_meta;
            DROP TABLE IF EXISTS msgs; DROP TABLE IF EXISTS usage;
            DROP TABLE IF EXISTS commits;
        """)
        conn.execute(f"PRAGMA user_version = {_TRANSCRIPT_INDEX_VERSION}")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, offset INTEGER NOT NULL,
                                          session_id TEXT NOT NULL, last_usage_key TEXT);
        CREATE TABLE IF NOT EXISTS sessions_meta (session_id TEXT PRIMARY KEY, project TEXT, cwd TEXT,
                                                  title TEXT, first_ts TEXT, last_ts TEXT,
                                                  last_call_epoch REAL, run_cost REAL DEFAULT 0);
        CREATE VIRTUAL TABLE IF NOT EXISTS msgs USING fts5(
            text, session_id UNINDEXED, ts UNINDEXED, role UNINDEXED,
            tokenize = 'unicode61 remove_diacritics 2');
        -- one row per session, model and local day; tokens deduplicated per
        -- API response. cold_* = re-caches: responses (not a session's first)
        -- that wrote most of their prompt to the cache again instead of
        -- reading it (see _usage_line_sample); cold_idle_n = those after
        -- >= 55 min of silence (Claude Code's cache lives 1 hour).
        CREATE TABLE IF NOT EXISTS usage (
            session_id TEXT NOT NULL, model TEXT NOT NULL, day TEXT NOT NULL,
            n INTEGER DEFAULT 0, inp INTEGER DEFAULT 0, out INTEGER DEFAULT 0,
            cw INTEGER DEFAULT 0, cw1h INTEGER DEFAULT 0, cr INTEGER DEFAULT 0, cost REAL DEFAULT 0,
            cold_n INTEGER DEFAULT 0, cold_cw INTEGER DEFAULT 0, cold_cw1h INTEGER DEFAULT 0,
            cold_idle_n INTEGER DEFAULT 0,
            PRIMARY KEY (session_id, model, day));
        -- git commits Claude made (from Bash tool calls/results). cost_at = the
        -- session's main-conversation spend when the commit landed, so a
        -- commit's own cost is the difference to the session's previous one.
        -- A Bash call running `git commit` is stored first as sha 'tu:<tool
        -- use id>', kind 'pending'; its result turns it into the real commit,
        -- or into kind 'quiet' (git commit -q prints no sha) for
        -- _resolve_quiet_commits to look up in git log, or 'none'.
        CREATE TABLE IF NOT EXISTS commits (
            session_id TEXT NOT NULL, sha TEXT NOT NULL, kind TEXT, branch TEXT, subject TEXT,
            cwd TEXT, ts TEXT, epoch REAL, end_epoch REAL, day TEXT, cost_at REAL DEFAULT 0,
            files INTEGER, ins INTEGER, dels INTEGER,
            PRIMARY KEY (session_id, sha));
    """)
    return conn


_COLD_IDLE_S = 55 * 60
_RECACHE_MIN_TOKENS = 20000


def _usage_line_sample(obj, prev_key, prev_call_epoch):
    """Pure: one transcript line -> (sample | None, key, call_epoch). A sample
    is one API response's usage: {model, day, inp, out, cw, cw1h, cr, cold,
    cold_idle}. Lines repeating the previous (message id, request id) -- one
    per content block of the same response, always adjacent -- return None."""
    if not isinstance(obj, dict) or obj.get("type") != "assistant":
        return None, prev_key, prev_call_epoch
    msg = obj.get("message") or {}
    u = msg.get("usage")
    if not isinstance(u, dict):
        return None, prev_key, prev_call_epoch
    key = f"{msg.get('id') or ''}|{obj.get('requestId') or ''}"
    if key != "|" and key == prev_key:
        return None, prev_key, prev_call_epoch
    model = msg.get("model") or ""
    if model.startswith("<"):  # "<synthetic>" harness messages, no API call
        return None, key, prev_call_epoch
    epoch = _iso_to_epoch(obj.get("timestamp"))
    cw = u.get("cache_creation_input_tokens", 0) or 0
    cr = u.get("cache_read_input_tokens", 0) or 0
    inp = u.get("input_tokens", 0) or 0
    # a re-cache: at least half the prompt (and >= 20k tokens) written again
    # -- the cache had expired, the model changed, or the context was
    # compacted/rewritten. Normal turns write only the newest messages.
    cold = (prev_call_epoch is not None and cw >= _RECACHE_MIN_TOKENS
            and cw * 2 >= cw + cr + inp)
    sample = {
        "model": model,
        "day": time.strftime("%Y-%m-%d", time.localtime(epoch)) if epoch else "",
        "inp": inp, "out": u.get("output_tokens", 0) or 0,
        "cw": cw, "cw1h": (u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens", 0) or 0,
        "cr": cr, "cold": cold,
        "cold_idle": bool(cold and epoch and prev_call_epoch and epoch - prev_call_epoch >= _COLD_IDLE_S),
    }
    return sample, key, (epoch or prev_call_epoch)


def _usage_add_samples(conn, session_id, samples):
    """Fold samples into the usage table (one UPSERT per session/model/day)."""
    agg = {}
    for sm in samples:
        k = (sm["model"], sm["day"])
        a = agg.setdefault(k, [0] * 11)
        a[0] += 1
        a[1] += sm["inp"]; a[2] += sm["out"]; a[3] += sm["cw"]; a[4] += sm["cw1h"]; a[5] += sm["cr"]
        a[6] += _calc_cost(sm["inp"], sm["out"], sm["cw"], sm["cr"], sm["model"], sm["cw1h"])
        if sm["cold"]:
            a[7] += 1; a[8] += sm["cw"]; a[9] += sm["cw1h"]
            a[10] += 1 if sm["cold_idle"] else 0
    for (model, day), a in agg.items():
        conn.execute("""
            INSERT INTO usage (session_id, model, day, n, inp, out, cw, cw1h, cr, cost,
                               cold_n, cold_cw, cold_cw1h, cold_idle_n)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (session_id, model, day) DO UPDATE SET
                n = n + excluded.n, inp = inp + excluded.inp, out = out + excluded.out,
                cw = cw + excluded.cw, cw1h = cw1h + excluded.cw1h, cr = cr + excluded.cr,
                cost = cost + excluded.cost, cold_n = cold_n + excluded.cold_n,
                cold_cw = cold_cw + excluded.cold_cw, cold_cw1h = cold_cw1h + excluded.cold_cw1h,
                cold_idle_n = cold_idle_n + excluded.cold_idle_n""",
                     (session_id, model, day, *a))


_GIT_COMMIT_LINE_RE = re.compile(r"^\[([^\s\]]+)(?: \(root-commit\))? ([0-9a-f]{7,40})\] (.+)$", re.M)
_GIT_SHORTSTAT_RE = re.compile(r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?")


_GIT_COMMIT_CMD_RE = re.compile(r"\bgit(?:\s+-C\s+(\"[^\"]+\"|'[^']+'|\S+))?(?:\s+-c\s+\S+)*\s+commit(?![\w-])")


def _git_commit_calls(obj):
    """Pure: an assistant transcript line -> [(tool_use_id, repo dir or '')]
    for each Bash call whose command runs `git commit` (the dir is the
    `git -C <dir>` argument when there is one)."""
    if not isinstance(obj, dict) or obj.get("type") != "assistant":
        return []
    content = (obj.get("message") or {}).get("content")
    out = []
    for b in content if isinstance(content, list) else []:
        if not (isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Bash" and b.get("id")):
            continue
        cmd = (b.get("input") or {}).get("command")
        m = _GIT_COMMIT_CMD_RE.search(cmd) if isinstance(cmd, str) else None
        if m:
            out.append((str(b["id"])[:100], (m.group(1) or "").strip("\"'")))
    return out


def _tool_result_ids(obj):
    """Pure: tool_use ids answered by a user transcript line."""
    if not isinstance(obj, dict) or obj.get("type") != "user":
        return []
    content = (obj.get("message") or {}).get("content")
    return [str(b["tool_use_id"])[:100] for b in (content if isinstance(content, list) else [])
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id")]


def _commit_from_line(obj):
    """Pure: one transcript line -> the git commit its Bash tool result
    reports, or None. Claude Code tags such results with
    toolUseResult.gitOperation.commit {sha, kind}; older transcripts don't,
    so a '[branch sha] subject' first line in the stdout counts too.
    -> {sha, kind, branch, subject, files, ins, dels, cwd, ts}"""
    if not isinstance(obj, dict) or obj.get("type") != "user":
        return None
    tr = obj.get("toolUseResult")
    if not isinstance(tr, dict):
        return None
    stdout = tr.get("stdout")
    stdout = stdout if isinstance(stdout, str) else ""
    op = tr.get("gitOperation")
    op = op.get("commit") if isinstance(op, dict) else None
    sha = kind = None
    if isinstance(op, dict) and isinstance(op.get("sha"), str) and re.fullmatch(r"[0-9a-f]{7,40}", op["sha"]):
        sha, kind = op["sha"], str(op.get("kind") or "committed")[:20]
    m = None
    for cand in _GIT_COMMIT_LINE_RE.finditer(stdout):
        if sha is None or cand.group(2).startswith(sha) or sha.startswith(cand.group(2)):
            m = cand
            break
    if sha is None:
        if not m:
            return None
        sha, kind = m.group(2), "committed"
    st = _GIT_SHORTSTAT_RE.search(stdout)
    return {
        "sha": sha, "kind": kind,
        "branch": m.group(1)[:200] if m else "",
        "subject": m.group(3).strip()[:300] if m else "",
        "files": int(st.group(1)) if st else None,
        "ins": int(st.group(2) or 0) if st else None,
        "dels": int(st.group(3) or 0) if st else None,
        "cwd": obj.get("cwd") or "",
        "ts": obj.get("timestamp") or "",
    }


def _transcript_line_messages(obj):
    """Pure: one parsed transcript line -> [(role, text)] worth indexing.
    User prompts (plain strings and text blocks) and assistant text blocks;
    skips tool calls/results, thinking, meta/caveat lines, subagent
    (sidechain) traffic and harness-injected '<task-notification>'-style
    strings."""
    if not isinstance(obj, dict) or obj.get("isSidechain") or obj.get("isMeta"):
        return []
    t = obj.get("type")
    if t not in ("user", "assistant"):
        return []
    content = (obj.get("message") or {}).get("content")
    out = []
    if isinstance(content, str):
        if t == "user" and content.strip() and not content.lstrip().startswith("<"):
            out.append((t, content))
    elif isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text" and (b.get("text") or "").strip():
                if t == "user" and b["text"].lstrip().startswith("<"):
                    continue
                out.append((t, b["text"]))
    return [(r, txt[:_TRANSCRIPT_INDEX_MAX_TEXT]) for r, txt in out]


def _index_commit_line(conn, session_id, obj, run_cost, pending):
    """Record what one transcript line says about git commits: a Bash call
    running `git commit` -> a 'pending' row; its result -> the real commit
    (sha in the output) or 'quiet'/'none'. A result with a sha but no
    recorded call (e.g. a commit made by a script) is stored directly.
    `pending` = this session's open tool_use ids, kept current here."""
    ts = obj.get("timestamp") or ""
    ep = _iso_to_epoch(ts) if ts else None
    day = time.strftime("%Y-%m-%d", time.localtime(ep)) if ep else ""
    for tid, repo in _git_commit_calls(obj):
        conn.execute("INSERT OR IGNORE INTO commits (session_id, sha, kind, cwd, ts, epoch, day, cost_at) "
                     "VALUES (?, ?, 'pending', ?, ?, ?, ?, ?)",
                     (session_id, "tu:" + tid, repo, ts, ep, day, round(run_cost, 6)))
        pending.add(tid)
    ids = [t for t in _tool_result_ids(obj) if t in pending]
    cm = _commit_from_line(obj)
    if not ids and not cm:
        return
    if cm and conn.execute("SELECT 1 FROM commits WHERE session_id = ? AND sha = ?", (session_id, cm["sha"])).fetchone():
        cm = None  # already recorded (same result seen twice)
    for tid in ids:
        pending.discard(tid)
        key = "tu:" + tid
        if cm:
            conn.execute("""UPDATE commits SET sha = ?, kind = ?, branch = ?, subject = ?, cwd = COALESCE(NULLIF(cwd, ''), ?),
                                end_epoch = ?, files = ?, ins = ?, dels = ? WHERE session_id = ? AND sha = ?""",
                         (cm["sha"], cm["kind"], cm["branch"], cm["subject"], cm["cwd"], ep,
                          cm["files"], cm["ins"], cm["dels"], session_id, key))
            cm = None
        else:
            # git commit -q (or a failed commit): no sha in the output
            conn.execute("UPDATE commits SET kind = 'quiet', cwd = COALESCE(NULLIF(cwd, ''), ?), end_epoch = ? "
                         "WHERE session_id = ? AND sha = ?", (obj.get("cwd") or "", ep, session_id, key))
    if cm:
        conn.execute("""INSERT OR IGNORE INTO commits (session_id, sha, kind, branch, subject, cwd, ts, epoch, end_epoch,
                                                       day, cost_at, files, ins, dels)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (session_id, cm["sha"], cm["kind"], cm["branch"], cm["subject"], cm["cwd"], ts, ep, ep, day,
                      round(run_cost, 6), cm["files"], cm["ins"], cm["dels"]))


def _parse_git_log_shortstat(out):
    """Pure: `git log --format=%H%x1f%ct%x1f%D%x1f%s --shortstat` output ->
    [{sha, ct, refs, subject, files, ins, dels}]."""
    res = []
    for line in (out or "").splitlines():
        if "\x1f" in line:
            p = line.split("\x1f")
            if len(p) >= 4 and re.fullmatch(r"[0-9a-f]{40}", p[0]):
                try:
                    ct = int(p[1])
                except ValueError:
                    continue
                res.append({"sha": p[0], "ct": ct, "refs": p[2], "subject": "\x1f".join(p[3:]).strip(),
                            "files": None, "ins": None, "dels": None})
        elif res and res[-1]["files"] is None:
            st = _GIT_SHORTSTAT_RE.search(line)
            if st:
                res[-1].update(files=int(st.group(1)), ins=int(st.group(2) or 0), dels=int(st.group(3) or 0))
    return res


def _resolve_quiet_commits(conn, run_git, now, limit=40):
    """Look up the sha of 'quiet' commit calls (no sha in the output) in git
    log: commits in that repo whose committer time falls between the call
    and its result (+-10 s), not yet recorded. None found -> kind 'none' (the
    commit failed, nothing to commit, or the dir isn't a repo). Several -> the
    first takes the row, the rest are added with the same cost_at.
    run_git(cwd, args) -> stdout or None. Returns rows resolved."""
    rows = conn.execute("""SELECT session_id, sha, cwd, epoch, end_epoch, day, ts, cost_at FROM commits
                            WHERE kind = 'quiet' AND COALESCE(end_epoch, epoch, 0) < ? LIMIT ?""",
                        (now - 30, limit)).fetchall()
    done = 0
    for sid, key, cwd, ep, end_ep, day, ts, cost_at in rows:
        start, end = (ep or end_ep or 0) - 10, (end_ep or ep or 0) + 10
        out = run_git(cwd, ["log", "--all", "--format=%H%x1f%ct%x1f%D%x1f%s", "--shortstat",
                            "--since=" + time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(start - 120)), "-n", "200"]) \
            if cwd and start > 0 else None
        hits = [c for c in _parse_git_log_shortstat(out) if start <= c["ct"] <= end]
        hits = [c for c in sorted(hits, key=lambda c: c["ct"])
                if not conn.execute("SELECT 1 FROM commits WHERE sha = ? OR sha = ?", (c["sha"][:7], c["sha"])).fetchone()]
        if not hits:
            conn.execute("UPDATE commits SET kind = 'none' WHERE session_id = ? AND sha = ?", (sid, key))
        for i, c in enumerate(hits):
            m = re.search(r"HEAD -> ([^,]+)", c["refs"] or "")
            vals = (c["sha"][:7], m.group(1).strip() if m else "", c["subject"][:300], c["files"], c["ins"], c["dels"])
            if i == 0:
                conn.execute("UPDATE commits SET sha = ?, kind = 'committed', branch = ?, subject = ?, files = ?, ins = ?, "
                             "dels = ? WHERE session_id = ? AND sha = ?", vals + (sid, key))
            else:
                conn.execute("""INSERT OR IGNORE INTO commits (session_id, sha, kind, branch, subject, files, ins, dels,
                                                               cwd, ts, epoch, end_epoch, day, cost_at)
                                VALUES (?, ?, 'committed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                             (sid,) + vals + (cwd, ts, ep, end_ep, day, cost_at))
        done += 1
    return done


def _run_git_quiet(cwd, args):
    try:
        r = subprocess.run(["git", "-C", cwd] + args, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def _transcript_index_file(conn, path, session_id, project, offset, max_bytes):
    """Index the complete lines of one transcript from `offset` on (at most
    ~max_bytes). A file that shrank (rewritten) is re-indexed from 0.
    Returns (new_offset, bytes_read, messages_added). Caller commits."""
    size = os.path.getsize(path)
    if size < offset:
        conn.execute("DELETE FROM msgs WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM usage WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM commits WHERE session_id = ?", (session_id,))
        conn.execute("UPDATE sessions_meta SET last_call_epoch = NULL, run_cost = 0 WHERE session_id = ?", (session_id,))
        conn.execute("UPDATE files SET last_usage_key = NULL WHERE path = ?", (path,))
        offset = 0
    if size == offset:
        return offset, 0, 0
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read(max_bytes)
    end = data.rfind(b"\n")
    if end < 0:
        # a single line longer than max_bytes: take it whole
        if len(data) < max_bytes:
            return offset, 0, 0  # partial last line, still being written
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.readline()
        end = len(data) - 1
        if not data.endswith(b"\n"):
            return offset, 0, 0
    chunk = data[:end + 1]
    rows, meta, samples = [], {}, []
    pending = {sha[3:] for (sha,) in conn.execute(
        "SELECT sha FROM commits WHERE session_id = ? AND kind = 'pending'", (session_id,))}
    r = conn.execute("SELECT last_usage_key FROM files WHERE path = ?", (path,)).fetchone()
    prev_key = r[0] if r else None
    r = conn.execute("SELECT last_call_epoch, run_cost FROM sessions_meta WHERE session_id = ?", (session_id,)).fetchone()
    prev_call = r[0] if r else None
    run_cost = (r[1] or 0.0) if r else 0.0
    for raw in chunk.splitlines():
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") == "ai-title" and obj.get("aiTitle"):
            meta["title"] = obj["aiTitle"]
        ts = obj.get("timestamp") or ""
        if ts and not obj.get("isSidechain"):
            meta.setdefault("first_ts", ts)
            meta["last_ts"] = ts
        if obj.get("cwd") and "cwd" not in meta:
            meta["cwd"] = obj["cwd"]
        for role, text in _transcript_line_messages(obj):
            rows.append((text, session_id, ts, role))
        sample, prev_key, prev_call = _usage_line_sample(obj, prev_key, prev_call)
        if sample:
            samples.append(sample)
            run_cost += _calc_cost(sample["inp"], sample["out"], sample["cw"], sample["cr"], sample["model"], sample["cw1h"])
        _index_commit_line(conn, session_id, obj, run_cost, pending)
    _usage_add_samples(conn, session_id, samples)
    if rows:
        conn.executemany("INSERT INTO msgs (text, session_id, ts, role) VALUES (?, ?, ?, ?)", rows)
    conn.execute("INSERT OR IGNORE INTO sessions_meta (session_id, project) VALUES (?, ?)", (session_id, project))
    conn.execute("UPDATE sessions_meta SET last_call_epoch = ?, run_cost = ? WHERE session_id = ?",
                 (prev_call, run_cost, session_id))
    if meta:
        conn.execute("""UPDATE sessions_meta SET
                            title = COALESCE(?, title), cwd = COALESCE(cwd, ?),
                            first_ts = COALESCE(first_ts, ?), last_ts = COALESCE(?, last_ts)
                        WHERE session_id = ?""",
                     (meta.get("title"), meta.get("cwd"), meta.get("first_ts"), meta.get("last_ts"), session_id))
    new_offset = offset + len(chunk)
    conn.execute("INSERT OR REPLACE INTO files (path, offset, session_id, last_usage_key) VALUES (?, ?, ?, ?)",
                 (path, new_offset, session_id, prev_key))
    return new_offset, len(chunk), len(rows)


def _transcript_index_tick(conn, projects_base, budget_bytes=_TRANSCRIPT_INDEX_BYTES_PER_TICK):
    """One pass: index new bytes of every top-level transcript, newest files
    first, until budget_bytes is spent. Returns (files_seen, files_current,
    bytes_read) -- files_current < files_seen means more work is pending."""
    known = dict(conn.execute("SELECT path, offset FROM files"))
    entries = []
    for proj in os.listdir(projects_base) if os.path.isdir(projects_base) else []:
        pdir = os.path.join(projects_base, proj)
        if not os.path.isdir(pdir):
            continue
        for fname in os.listdir(pdir):
            if fname.endswith(".jsonl"):
                fp = os.path.join(pdir, fname)
                try:
                    st = os.stat(fp)
                except OSError:
                    continue
                entries.append((st.st_mtime, st.st_size, fp, proj, fname[:-6]))
    entries.sort(reverse=True)
    spent, current = 0, 0
    for _mtime, size, fp, proj, sid in entries:
        off = known.get(fp, 0)
        if size == off:
            current += 1
            continue
        if spent >= budget_bytes:
            continue
        try:
            new_off, n, _added = _transcript_index_file(conn, fp, sid, _decode_project_name(proj), off,
                                                       budget_bytes - spent if budget_bytes > spent else 1)
        except OSError:
            continue
        conn.commit()
        spent += n
        if new_off == size:
            current += 1
    return len(entries), current, spent


def _fts_query(q):
    """User text -> an FTS5 MATCH expression: every word must appear (AND),
    each quoted so FTS syntax in the input (AND, NEAR, -, :, *) is literal;
    the last word also matches as a prefix, for search-as-you-type."""
    words = [w for w in re.findall(r"[\w][\w'.\-]*", q or "", re.UNICODE) if w.strip("'.-")]
    if not words:
        return ""
    parts = ['"' + w.replace('"', '""') + '"' for w in words[:12]]
    parts[-1] += "*"
    return " ".join(parts)


def _search_transcripts(conn, q, limit_sessions=30, hits_per_session=3):
    """Matches grouped by session, best match first: [{session_id, title,
    project, cwd, first_ts, last_ts, hits: [{role, ts, snippet}], n_hits}].
    Snippets mark matches with \\x02...\\x03 (the UI escapes, then swaps in
    <mark>), so no HTML ever comes out of the index."""
    expr = _fts_query(q)
    if not expr:
        return []
    rows = conn.execute(
        "SELECT session_id, role, ts, snippet(msgs, 0, char(2), char(3), '…', 14) "
        "FROM msgs WHERE msgs MATCH ? ORDER BY rank LIMIT 2000", (expr,)).fetchall()
    sessions, order = {}, []
    for sid, role, ts, snip in rows:
        s = sessions.get(sid)
        if s is None:
            if len(order) >= limit_sessions:
                continue
            s = sessions[sid] = {"session_id": sid, "hits": [], "n_hits": 0}
            order.append(sid)
        s["n_hits"] += 1
        if len(s["hits"]) < hits_per_session:
            s["hits"].append({"role": role, "ts": ts, "snippet": snip})
    if order:
        marks = ",".join("?" * len(order))
        for sid, project, cwd, title, first_ts, last_ts in conn.execute(
                f"SELECT session_id, project, cwd, title, first_ts, last_ts FROM sessions_meta "
                f"WHERE session_id IN ({marks})", order):
            sessions[sid].update(project=project or "", cwd=cwd or "", title=title or "",
                                 first_ts=first_ts or "", last_ts=last_ts or "")
    return [sessions[sid] for sid in order]


def _cache_stats(conn, since_day, top=12):
    """Prompt-cache report over usage rows with day >= since_day:
    totals, by_day, by_model and the sessions where the cache did worst.
      hit_rate   = cache reads / all prompt tokens (reads + writes + uncached)
      saved      = what the cache reads would have cost as plain input, minus
                   what they did cost
      cold_extra = what the cold re-writes cost beyond reading the same
                   tokens from a warm cache (5-min writes 1.25x, 1-hour 2x)"""
    def money(model, cr, cold_cw, cold_cw1h):
        pi, _po, pw, pr = _model_pricing(model or "")
        saved = cr * (pi - pr) / 1e6
        c1h = min(cold_cw1h, cold_cw)
        extra = ((cold_cw - c1h) * (pw - pr) + c1h * (pi * _CACHE_WRITE_1H_MULT - pr)) / 1e6
        return saved, extra

    rows = conn.execute("""
        SELECT u.session_id, u.model, u.day, u.n, u.inp, u.out, u.cw, u.cw1h, u.cr, u.cost,
               u.cold_n, u.cold_cw, u.cold_cw1h, u.cold_idle_n
        FROM usage u WHERE u.day >= ?""", (since_day,)).fetchall()
    keys = ("n", "inp", "out", "cw", "cr", "cost", "cold_n", "cold_cw", "cold_idle_n", "saved", "cold_extra",
            "cost_read", "cost_write", "cost_out", "cost_in")

    def blank():
        return dict.fromkeys(keys, 0)

    def add(d, r, saved, extra, split):
        d["n"] += r[3]; d["inp"] += r[4]; d["out"] += r[5]; d["cw"] += r[6]; d["cr"] += r[8]
        d["cost"] += r[9]; d["cold_n"] += r[10]; d["cold_cw"] += r[11]; d["cold_idle_n"] += r[13]
        d["saved"] += saved; d["cold_extra"] += extra
        for k, v in split.items():
            d[k] += v

    total, by_day, by_model, by_sess = blank(), {}, {}, {}
    for r in rows:
        saved, extra = money(r[1], r[8], r[11], r[12])
        pi, po, pw, pr = _model_pricing(r[1] or "")
        c1h = min(r[7], r[6])
        split = {"cost_read": r[8] * pr / 1e6, "cost_out": r[5] * po / 1e6, "cost_in": r[4] * pi / 1e6,
                 "cost_write": ((r[6] - c1h) * pw + c1h * pi * _CACHE_WRITE_1H_MULT) / 1e6}
        for d in (total, by_day.setdefault(r[2], blank()), by_model.setdefault(r[1], blank()),
                  by_sess.setdefault(r[0], blank())):
            add(d, r, saved, extra, split)

    def finish(d):
        prompt = d["inp"] + d["cw"] + d["cr"]
        d["hit_rate"] = round(d["cr"] / prompt, 4) if prompt else None
        d["avg_context"] = int(prompt / d["n"]) if d["n"] else 0
        for k in ("cost", "saved", "cold_extra", "cost_read", "cost_write", "cost_out", "cost_in"):
            d[k] = round(d[k], 4)
        return d

    sessions = []
    if by_sess:
        ids = list(by_sess)
        meta = {}
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            for sid, project, cwd, title, last_ts in conn.execute(
                    f"SELECT session_id, project, cwd, title, last_ts FROM sessions_meta "
                    f"WHERE session_id IN ({','.join('?' * len(part))})", part):
                meta[sid] = {"project": project or "", "cwd": cwd or "", "title": title or "", "last_ts": last_ts or ""}
        for sid, d in by_sess.items():
            sessions.append(dict(finish(d), session_id=sid, **meta.get(sid, {"project": "", "cwd": "", "title": "", "last_ts": ""})))
        # most expensive first -- the report explains where each one's money went
        sessions.sort(key=lambda x: x["cost"], reverse=True)
    return {
        "since": since_day,
        "total": finish(total),
        "by_day": [dict(finish(d), day=k) for k, d in sorted(by_day.items())],
        "by_model": sorted((dict(finish(d), model=k) for k, d in by_model.items()), key=lambda x: -x["cost"]),
        "sessions": sessions[:top],
    }


def _commit_stats(conn, since_day, limit=300):
    """Cost per commit (History -> COMMITS) over commits with day >= since_day.
    A commit's cost is the session's main-conversation spend since its
    previous commit (or since the session started): the work that went into
    it. Earlier commits outside the period still count as the starting point.
      commits  newest first: sha, kind, branch, subject, repo, cwd, ts,
               session_id, title, cost, files, ins, dels
      by_repo  most spend first: repo, commits, cost, per_commit, ins, dels, sessions
      total    commits, cost, median, ins, dels, sessions, spend (all usage
               in the period), no_commit_spend (sessions that never committed)"""
    rows = conn.execute("""
        SELECT session_id, sha, kind, branch, subject, cwd, ts, epoch, day, cost_at, files, ins, dels
        FROM commits WHERE sha NOT LIKE 'tu:%' AND session_id IN (
            SELECT DISTINCT session_id FROM commits WHERE day >= ? AND sha NOT LIKE 'tu:%')
        ORDER BY session_id, COALESCE(epoch, 0), rowid""", (since_day,)).fetchall()
    meta = {}
    sids = sorted({r[0] for r in rows})
    for i in range(0, len(sids), 500):
        part = sids[i:i + 500]
        for sid, project, title in conn.execute(
                f"SELECT session_id, project, title FROM sessions_meta WHERE session_id IN ({','.join('?' * len(part))})", part):
            meta[sid] = (project or "", title or "")
    commits, prev_sid, prev_at = [], None, 0.0
    for sid, sha, kind, branch, subject, cwd, ts, epoch, day, cost_at, files, ins, dels in rows:
        if sid != prev_sid:
            prev_sid, prev_at = sid, 0.0
        cost = max(0.0, (cost_at or 0.0) - prev_at)
        prev_at = max(prev_at, cost_at or 0.0)
        if (day or "") < since_day:
            continue
        repo = (cwd or "").replace("\\", "/").rstrip("/").split("/")[-1] or meta.get(sid, ("", ""))[0]
        commits.append({"sha": sha, "kind": kind or "committed", "branch": branch or "", "subject": subject or "",
                        "repo": repo, "cwd": cwd or "", "ts": ts or "", "epoch": epoch, "session_id": sid,
                        "title": meta.get(sid, ("", ""))[1], "cost": round(cost, 4),
                        "files": files, "ins": ins, "dels": dels})
    commits.sort(key=lambda x: x["epoch"] or 0, reverse=True)
    by_repo = {}
    for cm in commits:
        b = by_repo.setdefault(cm["repo"], {"repo": cm["repo"], "commits": 0, "cost": 0.0, "ins": 0, "dels": 0, "_s": set()})
        b["commits"] += 1
        b["cost"] += cm["cost"]
        b["ins"] += cm["ins"] or 0
        b["dels"] += cm["dels"] or 0
        b["_s"].add(cm["session_id"])
    repos = []
    for b in by_repo.values():
        b["sessions"] = len(b.pop("_s"))
        b["cost"] = round(b["cost"], 4)
        b["per_commit"] = round(b["cost"] / b["commits"], 4) if b["commits"] else 0
        repos.append(b)
    repos.sort(key=lambda x: x["cost"], reverse=True)
    costs = sorted(cm["cost"] for cm in commits)
    n = len(costs)
    median = (costs[n // 2] if n % 2 else (costs[n // 2 - 1] + costs[n // 2]) / 2) if n else 0
    spend = conn.execute("SELECT COALESCE(SUM(cost), 0) FROM usage WHERE day >= ?", (since_day,)).fetchone()[0]
    no_commit = conn.execute("""SELECT COALESCE(SUM(cost), 0) FROM usage WHERE day >= ?
                                AND session_id NOT IN (SELECT session_id FROM commits WHERE sha NOT LIKE 'tu:%')""",
                             (since_day,)).fetchone()[0]
    return {
        "since": since_day,
        "total": {"commits": n, "cost": round(sum(costs), 4), "median": round(median, 4),
                  "ins": sum(cm["ins"] or 0 for cm in commits), "dels": sum(cm["dels"] or 0 for cm in commits),
                  "sessions": len({cm["session_id"] for cm in commits}),
                  "spend": round(spend, 4), "no_commit_spend": round(no_commit, 4)},
        "by_repo": repos,
        "commits": commits[:limit],
    }


# ── Clients & billing (History -> CLIENTS) ────────────────────────────────
# Each client is a name, keywords and an optional markup %. A session
# belongs to the first client whose keyword appears in its title, project,
# working directory or the repos it committed to -- the title matters most
# for anyone who starts Claude Code from their home folder, where project
# is the same for everything. A session can also be assigned by hand.
# Costs come from transcript_index.db's per-day usage, so a session that
# runs past midnight on the last day of a month is split between months.
CLIENTS_FILE = os.path.join(AOC_DATA_DIR, "clients.json")
_CLIENT_NONE = "-"  # override: "no client" on purpose
_CLIENT_CURRENCY = "EUR"  # the hourly rate's currency when none is set (costs are always USD)


def _sanitize_clients(data) -> dict:
    data = data if isinstance(data, dict) else {}
    clients, seen = [], set()
    for c in (data.get("clients") or [])[:100]:
        if not isinstance(c, dict):
            continue
        name = " ".join(str(c.get("name") or "").split())[:80]
        if not name or name == _CLIENT_NONE or name.lower() in seen:
            continue
        seen.add(name.lower())
        match = []
        for m in (c.get("match") or [])[:30]:
            m = " ".join(str(m).split()).lower()[:80]
            if m and m not in match:
                match.append(m)
        try:
            markup = max(0.0, min(float(c.get("markup") or 0), 500.0))
        except (TypeError, ValueError):
            markup = 0.0
        try:
            rate = max(0.0, min(float(c.get("rate") or 0), 100000.0))
        except (TypeError, ValueError):
            rate = 0.0
        if rate != rate:  # NaN
            rate = 0.0
        currency = str(c.get("currency") or "").strip().upper()
        # a note printed on the client's monthly report (line breaks kept)
        note = "\n".join(" ".join(l.split()) for l in str(c.get("note") or "").replace("\r", "").split("\n"))
        clients.append({"name": name, "match": match, "markup": round(markup, 2), "rate": round(rate, 2),
                        "currency": currency if re.fullmatch(r"[A-Z]{3}", currency) else _CLIENT_CURRENCY,
                        "note": note.strip()[:2000]})
    names = {c["name"] for c in clients} | {_CLIENT_NONE}
    overrides = {}
    raw = data.get("overrides") if isinstance(data.get("overrides"), dict) else {}
    for sid, v in list(raw.items())[:5000]:
        if isinstance(v, str) and v in names:
            overrides[str(sid)[:80]] = v
    return {"clients": clients, "overrides": overrides}


def _load_clients() -> dict:
    return _sanitize_clients(_load_json_file(CLIENTS_FILE, {}))


def _save_clients(data) -> dict:
    cfg = _sanitize_clients(data)
    os.makedirs(AOC_DATA_DIR, exist_ok=True)
    tmp = CLIENTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CLIENTS_FILE)
    return cfg


def _client_for(sid, haystack, cfg):
    """Pure: -> (client name or "", how: "manual" | "keyword: x" | "")."""
    ov = cfg["overrides"].get(sid)
    if ov is not None:
        return ("" if ov == _CLIENT_NONE else ov), "manual"
    hay = [str(h).lower() for h in haystack if h]
    for c in cfg["clients"]:
        for m in c["match"]:
            if any(m in h for h in hay):
                return c["name"], f"keyword: {m}"
    return "", ""


def _client_report(conn, month, cfg, home=""):
    """Month (YYYY-MM) of Claude Code spend per client, from the transcript
    index: {"month", "months", "total", "clients": [{name, markup, rate,
    currency, cost, billed, tokens, commits, active_s, hours, time_amount,
    projects: [{name, sessions, active_s, cost}], sessions: [...]}, ... ,
    unassigned last]}. Time is the work log's active time (gaps over
    _WORKLOG_GAP_S are breaks) counting only messages / commits whose LOCAL
    date is in the month, so a session past midnight on the 31st is split
    like its cost is."""
    like = month + "-%"
    rows = conn.execute("""SELECT session_id, SUM(cost), SUM(inp + out + cw + cr), MIN(day), MAX(day)
                           FROM usage WHERE day LIKE ? GROUP BY session_id""", (like,)).fetchall()
    sids = [r[0] for r in rows]
    meta, commits, beats = {}, {}, {}
    in_month = lambda ep: time.strftime("%Y-%m", time.localtime(ep)) == month
    for i in range(0, len(sids), 500):
        part = sids[i:i + 500]
        qs = ",".join("?" * len(part))
        for sid, project, cwd, title in conn.execute(
                f"SELECT session_id, project, cwd, title FROM sessions_meta WHERE session_id IN ({qs})", part):
            meta[sid] = (project or "", cwd or "", title or "")
        for sid, cwd, epoch in conn.execute(
                f"""SELECT session_id, cwd, epoch FROM commits WHERE sha NOT LIKE 'tu:%' AND day LIKE ?
                    AND session_id IN ({qs})""", [like] + part):
            c = commits.setdefault(sid, {"n": 0, "repos": [], "all": []})
            c["n"] += 1
            repo = (cwd or "").replace("\\", "/").rstrip("/").split("/")[-1]
            if repo:
                c["all"].append(repo)
                if repo not in c["repos"]:
                    c["repos"].append(repo)
            if epoch and in_month(epoch):
                beats.setdefault(sid, []).append(epoch)
        for sid, ts in conn.execute(f"SELECT session_id, ts FROM msgs WHERE session_id IN ({qs})", part):
            ep = _iso_to_epoch(ts)
            if ep and in_month(ep):
                beats.setdefault(sid, []).append(ep)
    blank = lambda name, c: {"name": name, "markup": c.get("markup", 0.0), "rate": c.get("rate", 0.0),
                             "currency": c.get("currency", _CLIENT_CURRENCY), "cost": 0.0, "tokens": 0, "commits": 0,
                             "active_s": 0, "projects": {}, "sessions": []}
    groups = {c["name"]: blank(c["name"], c) for c in cfg["clients"]}
    unassigned = blank("", {})
    for sid, cost, tokens, d0, d1 in rows:
        project, cwd, title = meta.get(sid, ("", "", ""))
        cm = commits.get(sid, {"n": 0, "repos": [], "all": []})
        name, how = _client_for(sid, [title, project, cwd] + cm["repos"], cfg)
        g = groups.get(name, unassigned)
        active = _worklog_active_s(beats.get(sid, []))
        proj = _worklog_project(cwd, cm["all"], home)
        g["sessions"].append({"id": sid, "title": title or project or sid[:8], "project": project,
                              "cost": round(cost or 0, 4), "tokens": int(tokens or 0), "first_day": d0, "last_day": d1,
                              "commits": cm["n"], "repos": cm["repos"], "how": how,
                              "work_project": proj, "active_s": active})
        g["cost"] += cost or 0
        g["tokens"] += int(tokens or 0)
        g["commits"] += cm["n"]
        g["active_s"] += active
        p = g["projects"].setdefault(proj, {"name": proj, "sessions": 0, "active_s": 0, "cost": 0.0})
        p["sessions"] += 1
        p["active_s"] += active
        p["cost"] += cost or 0
    out = []
    for g in list(groups.values()) + [unassigned]:
        g["sessions"].sort(key=lambda s: s["cost"], reverse=True)
        g["cost"] = round(g["cost"], 4)
        g["billed"] = round(g["cost"] * (1 + g["markup"] / 100), 2)
        g["hours"] = _billable_hours(g["active_s"])
        g["time_amount"] = round(g["hours"] * g["rate"], 2)
        g["projects"] = sorted(({**p, "cost": round(p["cost"], 4)} for p in g["projects"].values()),
                               key=lambda p: (p["name"] == _WORKLOG_HOME_GROUP, -p["cost"]))
        out.append(g)
    months = [r[0] for r in conn.execute(
        "SELECT DISTINCT substr(day, 1, 7) FROM usage WHERE day >= '2000' ORDER BY 1 DESC LIMIT 36")]
    return {"month": month, "months": months, "clients": out,
            "total": round(sum(g["cost"] for g in out), 4)}


_BILL_STEP_H = 0.25  # invoiced time: a client's month total, rounded UP to a quarter hour


def _billable_hours(active_s):
    """Pure: a client's active seconds -> hours to invoice, rounded up to
    _BILL_STEP_H (a started quarter hour counts; 0 stays 0)."""
    q = round(max(0, active_s or 0) / 3600 / _BILL_STEP_H, 6)
    steps = int(q) + (q > int(q))
    return round(steps * _BILL_STEP_H, 2)


def _csv_dec(x, fmt=".2f"):
    """Pure: a number for the invoice CSV in Slovak / Czech Excel: decimal
    comma (the list separator there is ';')."""
    return format(x, fmt).replace(".", ",")


def _client_file_slug(client):
    """Pure: a client name as an ASCII file-name part -- "Ľubo Čaj s.r.o."
    -> "Lubo-Caj-s.r.o" (diacritics dropped, not turned into dashes)."""
    import unicodedata
    ascii_ = unicodedata.normalize("NFKD", client or "all").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9._-]+", "-", ascii_).strip("-.") or "client"


def _client_report_csv(report, client=None, bom=False):
    """CSV for invoicing: one line per session, then a total per client.
    client=None -> every client (and the unassigned sessions). The time
    columns come after billed_usd so older sheets keep their columns; bom
    = a UTF-8 BOM first, so Excel reads diacritics in names right. ';' and
    a decimal comma, so Slovak Excel opens it in columns, numbers as
    numbers. Session rows have the exact hours; the TOTAL row has the
    billed hours (_billable_hours) that time_amount is computed from."""
    import csv, io
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(["month", "client", "session", "session_id", "first_day", "last_day", "commits", "repos",
                "tokens", "cost_usd", "markup_pct", "billed_usd", "project", "hours", "rate", "currency", "time_amount"])
    for g in report["clients"]:
        if client is not None and g["name"] != client:
            continue
        if not g["sessions"]:
            continue
        name = g["name"] or "(unassigned)"
        rate, cur = g.get("rate", 0.0), g.get("currency", _CLIENT_CURRENCY)
        for s in g["sessions"]:
            a = s.get("active_s", 0)
            w.writerow([report["month"], name, s["title"], s["id"], s["first_day"], s["last_day"], s["commits"],
                        " ".join(s["repos"]), s["tokens"], _csv_dec(s["cost"]), _csv_dec(g["markup"], "g"),
                        _csv_dec(s["cost"] * (1 + g["markup"] / 100)), s.get("work_project", ""),
                        _csv_dec(a / 3600), _csv_dec(rate, "g"), cur, _csv_dec(a / 3600 * rate)])
        w.writerow([report["month"], name, "TOTAL", "", "", "", g["commits"], "", g["tokens"],
                    _csv_dec(g["cost"]), _csv_dec(g["markup"], "g"), _csv_dec(g["billed"]), "",
                    _csv_dec(g.get("hours", 0.0)), _csv_dec(rate, "g"), cur, _csv_dec(g.get("time_amount", 0.0))])
    return ("﻿" if bom else "") + buf.getvalue()


# ── Monthly client report (History -> CLIENTS -> Client report) ───────────
# A page for the CLIENT, not for you: per project the worked and billable
# time (and the amount, if the client has an hourly rate), then what was
# done -- that month's commit subjects plus session names, per project. No
# AI costs, tokens or model names. Slovak number/date formatting (decimal
# comma, d. m. yyyy); labels in Slovak or English.
_CR_MONTHS = {
    "sk": ["január", "február", "marec", "apríl", "máj", "jún", "júl", "august", "september", "október", "november", "december"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
}
_CR_LABELS = {
    "sk": {"title": "Výkaz práce", "summary": "Súhrn", "project": "Projekt", "worked": "Odpracovaný čas",
           "billable": "Fakturované hodiny", "rate": "Sadzba", "amount": "Suma", "total": "Spolu",
           "done": "Čo sa urobilo", "nothing": "V tomto mesiaci nie je zaznamenaná žiadna práca.",
           "no_items": "Bez podrobností.", "period": "Obdobie", "generated": "Vygenerované",
           "rounding": "Fakturovaný čas je za mesiac zaokrúhlený nahor na {step} h; prestávka dlhšia ako {gap} min sa nepočíta.",
           "print": "Tlačiť / Uložiť ako PDF", "other": "Ostatné"},
    "en": {"title": "Work report", "summary": "Summary", "project": "Project", "worked": "Worked time",
           "billable": "Billable hours", "rate": "Rate", "amount": "Amount", "total": "Total",
           "done": "What was done", "nothing": "No work recorded this month.",
           "no_items": "No details.", "period": "Period", "generated": "Generated",
           "rounding": "Billable time is rounded up to {step} h for the month; a pause longer than {gap} min is not counted.",
           "print": "Print / Save as PDF", "other": "Other"},
}
# commit subjects that say nothing to a client
_CR_NOISE_RE = re.compile(r"^(merge\b|wip\b|\[wip\]|fixup!|squash!|amend!|tests?[:(])", re.I)


def _cr_lang(lang):
    """Pure: "sk" for Slovak/Czech page languages, else "en"."""
    return "sk" if str(lang or "").lower()[:2] in ("sk", "cs") else "en"


def _cr_num(x, dec=2):
    """Pure: 1234.5 -> "1 234,50" (non-breaking space thousands, decimal comma)."""
    s = f"{float(x or 0):,.{dec}f}"
    return s.replace(",", " ").replace(".", ",")


def _cr_date(day):
    """Pure: "2026-11-06" -> "6. 11. 2026" (the input back if it isn't a date)."""
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(day or ""))
    return f"{int(m[3])}. {int(m[2])}. {m[1]}" if m else str(day or "")


def _cr_alloc_hours(actives, total_hours, step=_BILL_STEP_H):
    """Pure: split the client's billable hours (already rounded up for the
    month) over projects by their active seconds, in whole `step`s, largest
    remainder first -- so the rows add up to exactly the invoiced total."""
    steps = int(round((total_hours or 0) / step))
    tot = sum(max(0, a) for a in actives)
    if not steps or not tot:
        return [0.0] * len(actives)
    raw = [max(0, a) / tot * steps for a in actives]
    got = [int(r) for r in raw]
    order = sorted(range(len(raw)), key=lambda i: (-(raw[i] - got[i]), -actives[i]))
    for i in order[:steps - sum(got)]:
        got[i] += 1
    return [round(g * step, 2) for g in got]


def _cr_is_noise(subject):
    """Pure: a commit subject a client doesn't need (merge, WIP, fixup, tests)."""
    s = " ".join(str(subject or "").split())
    return not s or bool(_CR_NOISE_RE.match(s))


def _cr_clean_text(text, limit=140):
    """Pure: one line of a session name / first prompt, or "" if it is a
    slash command, tool/system markup or empty."""
    t = " ".join(str(text or "").split())
    if not t or t[0] in "</" or t.lower().startswith(("caveat:", "[request interrupted")):
        return ""
    return t if len(t) <= limit else t[:limit - 1].rstrip() + "…"


def _cr_skipped_shas(cwd, shas, run=None):
    """SHAs among `shas` whose commit carries "Release-note: skip" (test-only
    / internal commits), read from the repo with one git call. A repo that's
    gone or git failing -> nothing skipped; SHAs the repo no longer has
    (rebased, another clone) are just ignored."""
    shas = [s for s in shas or [] if re.fullmatch(r"[0-9a-f]{7,40}", str(s))]
    if not shas or not cwd or not os.path.isdir(cwd):
        return set()
    run = run or subprocess.run
    try:
        # the whole message, not %(trailers): a "Release-note: skip" line
        # followed by a blank line and Co-Authored-By isn't a git trailer
        r = run(["git", "-C", cwd, "log", "--no-walk=unsorted", "--ignore-missing", "--format=%H%x1f%B%x1e", *shas],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return set()
    if r.returncode != 0:
        return set()
    full = set()
    for rec in (r.stdout or "").split("\x1e"):
        sha, _, body = rec.strip().partition("\x1f")
        if sha and re.search(r"(?im)^release-note:\s*skip\s*$", body):
            full.add(sha)
    # the index stores short SHAs, git answers with full ones -> the input's
    return {s for s in shas if any(f.startswith(s) for f in full)}


def _cr_items(sessions, commits, first_prompts, skipped=()):
    """Pure: "what was done" per project. sessions = the client's report
    sessions (id, title, work_project, first_day), commits = [{session_id,
    sha, subject, day, epoch}], first_prompts = {session id: text} for
    sessions without a name. -> {project: [{"day", "text", "kind"}]}: the
    month's commits (oldest first, noise and Release-note: skip dropped),
    then session names that add something; duplicates (case and spacing
    ignored) only once, per project."""
    proj = {s["id"]: s.get("work_project") or "" for s in sessions}
    out, seen = {}, {}
    key = lambda t: " ".join(t.lower().split()).rstrip(".")

    def add(p, day, text, kind):
        k = key(text)
        if not k or k in seen.setdefault(p, set()):
            return
        seen[p].add(k)
        out.setdefault(p, []).append({"day": day, "text": text, "kind": kind})

    skipped = set(skipped)
    for c in sorted(commits, key=lambda c: (c.get("epoch") or 0, c.get("sha") or "")):
        if c["session_id"] not in proj or c.get("sha") in skipped or _cr_is_noise(c.get("subject")):
            continue
        add(proj[c["session_id"]], c.get("day") or "", _cr_clean_text(c.get("subject"), 200), "commit")
    for s in sorted(sessions, key=lambda s: (s.get("first_day") or "", s["id"])):
        p = proj[s["id"]]
        title = s.get("title") or ""
        # the report falls back to the project / id prefix for unnamed sessions
        if not title or title == s["id"][:8] or key(title) in (key(p), key(s.get("project") or "")):
            title = first_prompts.get(s["id"], "")
        add(p, s.get("first_day") or "", _cr_clean_text(title), "session")
    return out


def _client_month_report(conn, report, client, note="", git_run=None):
    """The data behind the client report page, from a _client_report month:
    {"client", "month", "note", "rate", "currency", "projects": [{name,
    active_s, hours, amount, items}], "total": {active_s, hours, amount}},
    or None if there's no such client. Billable hours are split over
    projects so they add up to the invoice's (_cr_alloc_hours)."""
    g = next((x for x in report["clients"] if x["name"] == client), None)
    if g is None:
        return None
    month = report["month"]
    sids = [s["id"] for s in g["sessions"]]
    commits, first = [], {}
    for i in range(0, len(sids), 500):
        part = sids[i:i + 500]
        qs = ",".join("?" * len(part))
        for sid, sha, subject, cwd, day, epoch in conn.execute(
                f"""SELECT session_id, sha, subject, cwd, day, epoch FROM commits
                    WHERE sha NOT LIKE 'tu:%' AND day LIKE ? AND session_id IN ({qs})""", [month + "-%"] + part):
            commits.append({"session_id": sid, "sha": sha, "subject": subject, "cwd": cwd, "day": day, "epoch": epoch})
        for sid, text in conn.execute(
                f"""SELECT session_id, text FROM msgs WHERE role = 'user' AND session_id IN ({qs})
                    ORDER BY ts""", part):
            if sid not in first and _cr_clean_text(text):
                first[sid] = text
    by_repo = {}
    for c in commits:
        by_repo.setdefault(c["cwd"] or "", set()).add(c["sha"])
    skipped = set()
    for cwd, shas in by_repo.items():
        skipped |= _cr_skipped_shas(cwd, sorted(shas), git_run)
    items = _cr_items(g["sessions"], commits, first, skipped)
    projs = [p for p in g.get("projects") or [] if p["active_s"] or items.get(p["name"])]
    hours = _cr_alloc_hours([p["active_s"] for p in projs], g.get("hours", 0.0))
    rate = g.get("rate", 0.0)
    rows = [{"name": p["name"], "active_s": p["active_s"], "hours": h, "amount": round(h * rate, 2),
             "items": items.get(p["name"], [])} for p, h in zip(projs, hours)]
    return {"client": g["name"], "month": month, "note": note, "rate": rate,
            "currency": g.get("currency", _CLIENT_CURRENCY), "projects": rows,
            "total": {"active_s": sum(r["active_s"] for r in rows), "hours": round(sum(hours), 2),
                      "amount": round(sum(r["amount"] for r in rows), 2)}}


def _client_month_report_html(data, lang="en", today=None):
    """Pure: the client report page (browser -> Save as PDF)."""
    import html as _html
    esc = lambda v: _html.escape(str(v), quote=True)
    lang = _cr_lang(lang)
    L = _CR_LABELS[lang]
    y, m = (int(x) for x in data["month"].split("-"))
    month_name = f"{_CR_MONTHS[lang][m - 1]} {y}"
    last = calendar.monthrange(y, m)[1]
    period = f"{_cr_date(f'{y}-{m:02d}-01')} – {_cr_date(f'{y}-{m:02d}-{last:02d}')}"
    hm = lambda s: f"{int(s) // 3600}:{int(s) % 3600 // 60:02d}"
    rate, cur = data["rate"], esc(data["currency"])
    pname = lambda n: L["other"] if n == _WORKLOG_HOME_GROUP else n
    head = (f"<th>{L['project']}</th><th class=n>{L['worked']}</th><th class=n>{L['billable']}</th>"
            + (f"<th class=n>{L['amount']}</th>" if rate else ""))
    amount = lambda v: f"<td class=n>{_cr_num(v)} {cur}</td>" if rate else ""
    rows = "".join(f"<tr><td>{esc(pname(p['name']))}</td><td class=n>{hm(p['active_s'])}</td>"
                   f"<td class=n>{_cr_num(p['hours'])} h</td>{amount(p['amount'])}</tr>" for p in data["projects"])
    t = data["total"]
    total = (f"<tr class=t><td>{L['total']}</td><td class=n>{hm(t['active_s'])}</td><td class=n>{_cr_num(t['hours'])} h</td>"
             f"{amount(t['amount'])}</tr>")
    rate_line = f"<p class=d>{L['rate']}: {_cr_num(rate)} {cur}/h</p>" if rate else ""
    done = []
    for p in data["projects"]:
        li = "".join(f"<li>{esc(i['text'])}<span class=d> · {esc(_cr_date(i['day']))}</span></li>" for i in p["items"])
        done.append(f"<h3>{esc(pname(p['name']))}</h3>" + (f"<ul>{li}</ul>" if li else f"<p class=d>{L['no_items']}</p>"))
    note = f"<div class=note>{esc(data['note'])}</div>" if data.get("note") else ""
    body = (f"<h2>{L['summary']}</h2><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody><tfoot>{total}</tfoot></table>"
            f"{rate_line}<p class=d>{L['rounding'].format(step=_cr_num(_BILL_STEP_H), gap=_WORKLOG_GAP_S // 60)}</p>"
            f"<h2>{L['done']}</h2>{''.join(done)}") if data["projects"] else f"<p>{L['nothing']}</p>"
    gen = _cr_date(today or datetime.now().strftime("%Y-%m-%d"))
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><title>{esc(data['client'])} - {esc(L['title'])} {esc(month_name)}</title>
<style>body{{font:14px/1.5 system-ui,Segoe UI,sans-serif;color:#111;max-width:860px;margin:32px auto;padding:0 20px}}
h1{{font-size:24px;margin:0 0 2px}}h2{{font-size:16px;margin:28px 0 8px}}h3{{font-size:14px;margin:16px 0 4px}}.sub{{color:#555;margin:0 0 18px}}
table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid #ddd;vertical-align:top}}
th{{font-size:12px;color:#555;text-transform:uppercase;letter-spacing:.04em}}.n{{text-align:right;white-space:nowrap}}
tr.t td{{font-weight:700;border-top:2px solid #111;border-bottom:none}}ul{{margin:0;padding-left:20px}}li{{margin:2px 0}}
.d{{color:#777;font-size:12px}}.note{{white-space:pre-wrap;background:#f6f6f2;border-left:3px solid #888;padding:10px 14px;margin:0 0 8px}}
.foot{{margin-top:28px;color:#777;font-size:12px}}button{{float:right;padding:6px 14px;cursor:pointer}}
@media print{{button{{display:none}}body{{margin:0}}h3{{break-after:avoid}}li{{break-inside:avoid}}}}</style></head>
<body><button onclick="print()">{L['print']}</button>
<h1>{esc(data['client'])}</h1><p class="sub">{L['title']} · {esc(month_name)}</p>
{note}{body}
<p class="foot">{L['period']}: {period} · {L['generated']}: {gen}</p>
</body></html>"""


# ── Work log / standup (History -> WORK LOG) ──────────────────────────────
# What got done in a period, per project (the repo a session committed to,
# else its folder) or per client (_client_for): sessions, commits, time
# active, cost -- all from transcript_index.db. "Summarize" hands a compact
# digest (your asks, Claude's last word, commit subjects) to Haiku for a
# standup or a client-facing report; answers are cached by digest, so the
# same period costs nothing the second time.
WORKLOG_CACHE_FILE = os.path.join(AOC_DATA_DIR, "worklogs.json")
_WORKLOG_MAX_DAYS = 92
_WORKLOG_GAP_S = 10 * 60        # a longer silence is a break, not work
_WORKLOG_DIGEST_MAX = 14000
_WORKLOG_MODES = ("standup", "client")
_WORKLOG_HOME_GROUP = "Other (home folder)"
_worklog_lock = threading.Lock()
_worklog_busy = set()           # digest keys being summarized right now


def _worklog_range(d0, d1, today):
    """Pure: validated (from, to) YYYY-MM-DD; bad input -> today, swapped
    if reversed, at most _WORKLOG_MAX_DAYS long (the start moves)."""
    def ok(d):
        try:
            datetime.strptime(str(d), "%Y-%m-%d")
            return True
        except ValueError:
            return False
    d0 = d0 if ok(d0) else today
    d1 = d1 if ok(d1) else (d0 if ok(d0) and d0 != today else today)
    if d1 < d0:
        d0, d1 = d1, d0
    a, b = datetime.strptime(d0, "%Y-%m-%d"), datetime.strptime(d1, "%Y-%m-%d")
    if (b - a).days >= _WORKLOG_MAX_DAYS:
        d0 = (b - timedelta(days=_WORKLOG_MAX_DAYS - 1)).strftime("%Y-%m-%d")
    return d0, d1


def _worklog_active_s(epochs, gap=_WORKLOG_GAP_S):
    """Pure: seconds of work in these activity timestamps -- each gap counts
    up to `gap`, longer ones are breaks."""
    e = sorted(x for x in epochs if x)
    return int(sum(min(b - a, gap) for a, b in zip(e, e[1:])))


def _worklog_project(cwd, repos, home):
    """Pure: the project a session belongs to -- the repo it committed to
    most, else its folder's name; sessions in the home folder that never
    committed share one group."""
    if repos:
        return max(repos, key=repos.count)
    c = (cwd or "").replace("\\", "/").rstrip("/")
    h = (home or "").replace("\\", "/").rstrip("/")
    if not c or (h and c.lower() == h.lower()):
        return _WORKLOG_HOME_GROUP
    return c.split("/")[-1] or _WORKLOG_HOME_GROUP


def _worklog_report(conn, d0, d1, group, cfg, home=""):
    """{from, to, group, total: {cost, tokens, sessions, commits, active_s},
    groups: [{name, cost, tokens, active_s, sessions: [{id, title, cost,
    tokens, active_s, commits, first_day, last_day, asks, last_reply}],
    commits: [{sha, subject, repo, day, epoch, session_id, ins, dels}]}]}
    most spend first; group = "project" | "client"."""
    rows = conn.execute("""SELECT session_id, SUM(cost), SUM(inp + out + cw + cr), MIN(day), MAX(day)
                           FROM usage WHERE day >= ? AND day <= ? GROUP BY session_id""", (d0, d1)).fetchall()
    sids = [r[0] for r in rows]
    meta, commits, msgs = {}, {}, {}
    for i in range(0, len(sids), 500):
        part = sids[i:i + 500]
        qs = ",".join("?" * len(part))
        for sid, project, cwd, title in conn.execute(
                f"SELECT session_id, project, cwd, title FROM sessions_meta WHERE session_id IN ({qs})", part):
            meta[sid] = (project or "", cwd or "", title or "")
        for sid, sha, subject, cwd, epoch, day, ins, dels in conn.execute(
                f"""SELECT session_id, sha, subject, cwd, epoch, day, ins, dels FROM commits
                    WHERE sha NOT LIKE 'tu:%' AND day >= ? AND day <= ? AND session_id IN ({qs})
                    ORDER BY COALESCE(epoch, 0)""", [d0, d1] + part):
            repo = (cwd or "").replace("\\", "/").rstrip("/").split("/")[-1]
            commits.setdefault(sid, []).append({"sha": sha, "subject": subject or "", "repo": repo, "day": day or "",
                                                "epoch": epoch, "session_id": sid, "ins": ins, "dels": dels})
        for sid, ts, role, text in conn.execute(
                f"SELECT session_id, ts, role, substr(text, 1, 600) FROM msgs WHERE session_id IN ({qs})", part):
            ep = _iso_to_epoch(ts)
            if ep and d0 <= time.strftime("%Y-%m-%d", time.localtime(ep)) <= d1:
                msgs.setdefault(sid, []).append((ep, role, text or ""))
    groups = {}
    for sid, cost, tokens, f_day, l_day in rows:
        project, cwd, title = meta.get(sid, ("", "", ""))
        cms = commits.get(sid, [])
        repos = [c["repo"] for c in cms if c["repo"]]
        if group == "client":
            name = _client_for(sid, [title, project, cwd] + repos, cfg)[0]
        else:
            name = _worklog_project(cwd, repos, home)
        ms = sorted(msgs.get(sid, []), key=lambda m: m[0])
        asks = [" ".join(t.split())[:300] for _, r, t in ms if r == "user" and t.strip()]
        reply = next((" ".join(t.split())[:500] for _, r, t in reversed(ms) if r == "assistant" and t.strip()), "")
        active = _worklog_active_s([m[0] for m in ms] + [c["epoch"] for c in cms])
        g = groups.setdefault(name, {"name": name, "cost": 0.0, "tokens": 0, "active_s": 0, "sessions": [], "commits": []})
        g["sessions"].append({"id": sid, "title": title or (asks[0][:80] if asks else sid[:8]), "cost": round(cost or 0, 4),
                              "tokens": int(tokens or 0), "active_s": active, "commits": len(cms),
                              "first_day": f_day, "last_day": l_day, "asks": asks[:6], "last_reply": reply})
        g["commits"] += cms
        g["cost"] += cost or 0
        g["tokens"] += int(tokens or 0)
        g["active_s"] += active
    out = sorted(groups.values(), key=lambda g: (g["name"] in ("", _WORKLOG_HOME_GROUP), -g["cost"]))
    for g in out:
        g["cost"] = round(g["cost"], 4)
        g["sessions"].sort(key=lambda s: s["cost"], reverse=True)
        g["commits"].sort(key=lambda c: c["epoch"] or 0)
    return {"from": d0, "to": d1, "group": group, "groups": out,
            "total": {"cost": round(sum(g["cost"] for g in out), 4), "tokens": sum(g["tokens"] for g in out),
                      "sessions": len(rows), "commits": sum(len(g["commits"]) for g in out),
                      "active_s": sum(g["active_s"] for g in out)}}


def _worklog_digest(report, max_chars=_WORKLOG_DIGEST_MAX):
    """Pure: the period as compact text for the summarizer: per group its
    commits, then each session's asks (YOU) and Claude's last word. No
    costs -- a client report must not leak them by accident."""
    out = [f"PERIOD: {report['from']}" + ("" if report["to"] == report["from"] else f" to {report['to']}")]
    for g in report["groups"]:
        out.append(f"\n== {g['name'] or 'Unassigned'} ==")
        for c in g["commits"]:
            out.append(f"COMMIT {c['repo']}: {c['subject']}")
        for s in g["sessions"]:
            out.append(f"SESSION: {s['title']}")
            out += ["  YOU: " + a for a in s["asks"]]
            if s["last_reply"]:
                out.append("  CLAUDE (last): " + s["last_reply"])
    text = "\n".join(out)
    if len(text) > max_chars:
        head = max_chars // 2
        text = text[:head] + "\n[... cut ...]\n" + text[-(max_chars - head):]
    return text


_WORKLOG_INSTRUCTIONS = {
    "standup": (
        "Below is a log of one developer's Claude Code work in a period, grouped by project: commits, "
        "what they asked (YOU) and Claude's last word per session. Write their standup in three short "
        "sections titled Done, Next and Blockers (translate the three titles into the language of the YOU "
        "lines). Done: 2-6 bullets of concrete results, most important first, with the project name. "
        "Next: 1-4 bullets of what is unfinished or was said to come next. Blockers: bullets, or a single "
        "'none' in that language. Bullets start with '- '. Plain text only: no markdown, no bold, no "
        "backticks, no preamble. Write in the same language as the YOU lines."),
    "client": (
        "Below is a log of a developer's work in a period, grouped by project: commits, what they asked "
        "(YOU) and the AI assistant's last word per session. Write a short work report they can send to "
        "their client: for each project a heading line with its name, then 2-5 bullets ('- ') of the "
        "results in plain business language -- what was built, fixed or improved and why it matters. "
        "No internal jargon, no file names, no mention of AI, Claude, prompts, costs or tokens. Plain "
        "text only: no markdown, no bold, no preamble. Write in the same language as the YOU lines."),
}


def _worklog_key(mode, digest):
    import hashlib
    return hashlib.sha1((mode + "\n" + digest).encode("utf-8")).hexdigest()


def _worklog_cached(key):
    d = _load_json_file(WORKLOG_CACHE_FILE, {})
    return (d if isinstance(d, dict) else {}).get(key)


def _worklog_store(key, entry):
    with _worklog_lock:
        d = _load_json_file(WORKLOG_CACHE_FILE, {})
        d = d if isinstance(d, dict) else {}
        d[key] = entry
        d = dict(sorted(d.items(), key=lambda kv: kv[1].get("generated_at", 0))[-100:])
        os.makedirs(AOC_DATA_DIR, exist_ok=True)
        tmp = WORKLOG_CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, WORKLOG_CACHE_FILE)


def _worklog_cached_summaries(report):
    """{mode: cached entry or None} for this exact report."""
    digest = _worklog_digest(report)
    return {m: _worklog_cached(_worklog_key(m, digest)) for m in _WORKLOG_MODES}


def _worklog_summary(report, mode, force=False, run=None, now=None):
    """POST /worklog/summary: {"ok", "text", "cost", "generated_at", "mode",
    "fresh"} -- from the cache unless forced; one summary at a time."""
    if mode not in _WORKLOG_MODES:
        return {"ok": False, "error": "unknown mode"}
    if not report["groups"]:
        return {"ok": False, "error": "no work in this period"}
    now = now or time.time()
    digest = _worklog_digest(report)
    key = _worklog_key(mode, digest)
    cached = _worklog_cached(key)
    if cached and not force:
        return {"ok": True, **cached, "fresh": False}
    with _worklog_lock:
        if key in _worklog_busy:
            return {"ok": False, "busy": True, "error": "already summarizing"}
        _worklog_busy.add(key)
    try:
        text, cost = (run or _run_haiku)(_WORKLOG_INSTRUCTIONS[mode], digest)
        entry = {"text": text[:6000], "cost": round(cost, 4), "generated_at": now, "mode": mode,
                 "from": report["from"], "to": report["to"], "group": report["group"]}
        _worklog_store(key, entry)
        return {"ok": True, **entry, "fresh": True}
    except Exception as e:
        _log_bg_error("worklog", e)
        return {"ok": False, "error": str(e)[:200]}
    finally:
        with _worklog_lock:
            _worklog_busy.discard(key)


def _worklog_print_html(report, summary=None):
    """A plain, printable work log (browser -> Save as PDF): the summary if
    there is one, then per group its commits and sessions. No costs, so it
    can go to a client as is."""
    import html as _html
    esc = lambda v: _html.escape(str(v), quote=True)
    period = report["from"] + ("" if report["to"] == report["from"] else " – " + report["to"])
    hrs = lambda s: f"{s / 3600:.1f} h" if s >= 3600 else f"{max(1, round(s / 60))} min"
    parts = []
    if summary and summary.get("text"):
        parts.append(f"<div class=sum>{esc(summary['text'])}</div>")
    for g in report["groups"]:
        cm = "".join(f"<li><code>{esc((c['sha'] or '')[:7])}</code> {esc(c['subject'] or '(no message)')}"
                     f"<span class=d> · {esc(c['repo'])} · {esc(c['day'])}</span></li>" for c in g["commits"])
        ss = "".join(f"<li>{esc(s['title'])}<span class=d> · {esc(s['first_day'])} · {hrs(s['active_s'])}</span></li>"
                     for s in g["sessions"])
        parts.append(f"<h2>{esc(g['name'] or 'Unassigned')} <span class=d>{hrs(g['active_s'])} active</span></h2>"
                     + (f"<h3>Commits</h3><ul>{cm}</ul>" if cm else "") + f"<h3>Sessions</h3><ul>{ss}</ul>")
    body = "".join(parts) or "<p>No work recorded in this period.</p>"
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Work log {esc(period)}</title>
<style>body{{font:14px/1.5 system-ui,Segoe UI,sans-serif;color:#111;max-width:860px;margin:32px auto;padding:0 20px}}
h1{{font-size:22px;margin:0 0 4px}}h2{{font-size:17px;margin:26px 0 4px;border-bottom:1px solid #ddd;padding-bottom:3px}}
h3{{font-size:12px;color:#555;text-transform:uppercase;letter-spacing:.04em;margin:10px 0 2px}}ul{{margin:0;padding-left:20px}}
.d{{color:#777;font-size:12px;font-weight:400}}.sub{{color:#555;margin:0 0 18px}}.sum{{white-space:pre-wrap;background:#f6f6f2;border-left:3px solid #888;padding:10px 14px;margin:0 0 10px}}
.foot{{margin-top:26px;color:#777;font-size:12px}}button{{float:right;padding:6px 14px;cursor:pointer}}@media print{{button{{display:none}}body{{margin:0}}}}</style></head>
<body><button onclick="print()">Print / Save as PDF</button>
<h1>Work log</h1><p class="sub">{esc(period)} · {report['total']['sessions']} sessions · {report['total']['commits']} commits · {hrs(report['total']['active_s'])} active</p>
{body}<p class="foot">From Claude Code's session records on this computer. Generated by AOC on {esc(datetime.now().strftime('%Y-%m-%d'))}.</p>
</body></html>"""


def _worklog_csv(report):
    """CSV of the period: one line per session, then a total per group."""
    import csv, io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["from", "to", report["group"], "session", "session_id", "first_day", "last_day",
                "active_min", "commits", "tokens", "cost_usd"])
    for g in report["groups"]:
        name = g["name"] or "(unassigned)"
        for s in g["sessions"]:
            w.writerow([report["from"], report["to"], name, s["title"], s["id"], s["first_day"], s["last_day"],
                        round(s["active_s"] / 60), s["commits"], s["tokens"], f'{s["cost"]:.2f}'])
        w.writerow([report["from"], report["to"], name, "TOTAL", "", "", "", round(g["active_s"] / 60),
                    len(g["commits"]), g["tokens"], f'{g["cost"]:.2f}'])
    return buf.getvalue()


# ── Model advice (History -> MODELS) ──────────────────────────────────────
# Every API call in the transcripts is sorted by the work it did (the tools
# it called: only reading/searching, editing, running commands, delegating
# to a subagent, or just writing text) and re-priced on Sonnet and Haiku
# with the same tokens. That's an estimate -- another model would use a
# different number of tokens -- but it shows where a cheaper model would
# matter and how much, on your own usage. Cache reads dominate long
# sessions and cost the same on Opus 5.5 and Sonnet 5, so switching saves
# much less than the list prices suggest; the report says so.
_ADVICE_READ_ONLY = {"Read", "Grep", "Glob", "LS", "WebSearch", "WebFetch", "TodoWrite", "ToolSearch",
                     "TaskOutput", "NotebookRead"}
_ADVICE_EDIT = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
_ADVICE_SHELL = {"Bash", "PowerShell", "BashOutput", "KillShell", "Monitor"}
_ADVICE_CHEAP = {"sonnet": "claude-sonnet-5", "haiku": "claude-haiku-4-5"}
_advice_cache = {}  # days -> (computed_at, report)
_ADVICE_CACHE_S = 15 * 60


def _advice_kind(tools):
    """Pure: tool names one API call used -> explore | edit | shell | delegate | text | other."""
    tools = set(tools or ())
    if not tools:
        return "text"
    if tools & _ADVICE_EDIT:
        return "edit"
    if tools & {"Agent", "Task"}:
        return "delegate"
    if tools & _ADVICE_SHELL:
        return "shell"
    if tools <= _ADVICE_READ_ONLY:
        return "explore"
    return "other"


def _advice_scan_file(path, since_iso, agg):
    """Add one transcript's API calls (each counted once, like the usage
    index) to agg[(where, model, kind)] = [calls, cost, on_sonnet, on_haiku]."""
    where = "subagent" if "subagents" in path.replace("\\", "/").split("/") else "main"
    msgs = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"assistant"' not in line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if not isinstance(o, dict) or o.get("type") != "assistant" or str(o.get("timestamp") or "") < since_iso:
                continue
            m = o.get("message") or {}
            key = (m.get("id") or "") + "|" + (o.get("requestId") or "")
            e = msgs.setdefault(key, [m.get("model") or "", m.get("usage") or {}, set()])
            for b in m.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    e[2].add(b.get("name"))
    for model, u, tools in msgs.values():
        if not u or not model or model.startswith("<"):
            continue
        tok = (u.get("input_tokens") or 0, u.get("output_tokens") or 0,
               u.get("cache_creation_input_tokens") or 0, u.get("cache_read_input_tokens") or 0)
        cw1 = (u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") or 0
        a = agg.setdefault((where, model, _advice_kind(tools)), [0, 0.0, 0.0, 0.0])
        a[0] += 1
        a[1] += _calc_cost(*tok, model, cw1)
        a[2] += _calc_cost(*tok, _ADVICE_CHEAP["sonnet"], cw1)
        a[3] += _calc_cost(*tok, _ADVICE_CHEAP["haiku"], cw1)


def _model_label(model):
    """Pure: claude-opus-5-5 -> Opus 5.5."""
    m = re.match(r"claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-|$)", model or "")
    if not m:
        return model or "?"
    return m.group(1).capitalize() + " " + m.group(2) + ("." + m.group(3) if m.group(3) else "")


def _advice_report(agg, days):
    """Pure: agg -> {"total", "monthly_factor", "rows", "by_model", "tips"}."""
    rows = [{"where": w, "model": m, "label": _model_label(m), "kind": k, "calls": v[0], "cost": round(v[1], 4),
             "on_sonnet": round(v[2], 4), "on_haiku": round(v[3], 4)} for (w, m, k), v in agg.items()]
    rows.sort(key=lambda r: r["cost"], reverse=True)
    total = sum(r["cost"] for r in rows)
    month = 30.0 / max(days, 1)
    by_model = {}
    for r in rows:
        b = by_model.setdefault(r["label"], {"label": r["label"], "cost": 0.0, "calls": 0})
        b["cost"] += r["cost"]
        b["calls"] += r["calls"]
    by_model = sorted(by_model.values(), key=lambda b: b["cost"], reverse=True)
    for b in by_model:
        b["share"] = round(b["cost"] / total * 100, 1) if total else 0
    pricey = lambda r: r["label"].startswith("Opus")
    tips = []
    explore = [r for r in rows if r["where"] == "main" and pricey(r) and r["kind"] == "explore"]
    e_cost = sum(r["cost"] for r in explore)
    if e_cost * month >= 5:
        save = e_cost - sum(r["on_haiku"] for r in explore)
        tips.append({"id": "explore", "saving_month": round(save * month, 2),
                     "title": "Hand searching and reading to a cheaper subagent",
                     "detail": f"Opus spent ${e_cost:.2f} on calls that only read or searched files "
                               f"({sum(r['calls'] for r in explore):,} calls). Asking Claude to use the Explore "
                               "subagent (or a custom agent with `model: haiku`) for that part would cost about "
                               f"${e_cost - save:.2f} instead."})
    subs = [r for r in rows if r["where"] == "subagent" and pricey(r)]
    s_cost = sum(r["cost"] for r in subs)
    if s_cost * month >= 2:
        save = s_cost - sum(r["on_sonnet"] for r in subs)
        tips.append({"id": "subagents", "saving_month": round(save * month, 2),
                     "title": "Run subagents on Sonnet",
                     "detail": f"Subagents ran on Opus for ${s_cost:.2f}. Set `model: sonnet` in your agent "
                               f"definitions (or CLAUDE_CODE_SUBAGENT_MODEL) to pay about ${s_cost - save:.2f}."})
    # without the reading/searching calls the tip above already covers, so the
    # savings of the tips add up
    main_opus = [r for r in rows if r["where"] == "main" and pricey(r) and r["kind"] != "explore"]
    m_cost = sum(r["cost"] for r in main_opus)
    if m_cost * month >= 5:
        sonnet = sum(r["on_sonnet"] for r in main_opus)
        pct = round((1 - sonnet / m_cost) * 100) if m_cost else 0
        tips.append({"id": "main", "saving_month": round((m_cost - sonnet) * month, 2),
                     "title": f"Sonnet for routine sessions saves {pct}% here, not half",
                     "detail": f"The rest of your Opus work cost ${m_cost:.2f}; the same tokens on Sonnet would be ${sonnet:.2f}. "
                               "Most of it is re-reading the cached conversation, which costs the same on both, so "
                               "keeping conversations short (/clear between tasks, /compact) saves more than switching. "
                               "Use Sonnet (/model sonnet) for routine runs and keep Opus for hard problems."})
    if total and not tips:
        tips.append({"id": "fine", "saving_month": 0, "title": "Your model mix already looks lean",
                     "detail": "No big spend on an expensive model for cheap work in this period."})
    tips.sort(key=lambda t: t["saving_month"], reverse=True)
    return {"total": round(total, 4), "days": days, "monthly_factor": round(month, 3),
            "rows": rows, "by_model": by_model, "tips": tips}


def _model_advice(days, now=None):
    """GET /model_advice: scan the last `days` of transcripts (cached 15 min)."""
    now = now or time.time()
    hit = _advice_cache.get(days)
    if hit and now - hit[0] < _ADVICE_CACHE_S:
        return hit[1]
    base = os.path.join(os.path.expanduser("~"), ".claude", "projects")
    since = now - days * 86400
    since_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(since))
    agg = {}
    for pattern in ("*/*.jsonl", "*/*/subagents/*.jsonl"):
        for f in glob.glob(os.path.join(base, pattern)):
            try:
                if os.path.getmtime(f) < since:
                    continue
                _advice_scan_file(f, since_iso, agg)
            except OSError:
                continue
    report = _advice_report(agg, days)
    report["computed_at"] = now
    _advice_cache[days] = (now, report)
    return report
