# AOC server, part 13: one-click fixes, startup context, memory editor + archive, failure map, system monitor.
# monitor.py exec()s server/NN_*.py in number order into ONE shared
# namespace (its own) -- not an importable module: names from earlier
# parts are plain globals here, and __file__ is monitor.py.
# ── One-click fixes (tips in History -> CONTEXT / FAILURES / PERMISSIONS) ──
# A tip can carry a fix: a small, allowlisted change to Claude Code's own
# config that AOC previews, applies (backup first) and can undo:
#   plugin -- enabledPlugins["<name>@<marketplace>"] = false in ~/.claude/settings.json
#             (installed plugins only; plugins synced from your claude.ai account
#             are managed there, so those tips get a 'manual' fix with steps)
#   mcp    -- add the server to projects[<folder>].disabledMcpServers in
#             ~/.claude.json for every folder Claude Code knows (what /mcp's
#             toggle writes; new folders start with the server on)
#   env    -- an allowlisted env var in settings.json "env" (all of Claude's shells)
#   note   -- an allowlisted line appended to ~/.claude/CLAUDE.md
#   allow  -- add an allow rule to permissions.allow in ~/.claude/settings.json;
#             only rules the permission advisor rates safe (_perm_rule_tier)
#   unallow -- remove a rule ("<file>|<rule>") from permissions.allow in
#             ~/.claude/settings.json or ~/.claude/settings.local.json
# Every apply is logged in fixes.json with what it changed; undo reverts
# exactly that (and leaves alone a value you changed by hand since).
FIXES_FILE = os.path.join(AOC_DATA_DIR, "fixes.json")
_FIX_ENV = {"PYTHONIOENCODING": "utf-8"}
_FIX_NOTES = {
    "heredoc": "- For multi-line scripts, write the script to a file (Write tool) and run the file -- "
               "don't pass long heredocs or nested quotes to the shell.",
    "utf8_open": "- On this Windows machine, open text files in Python with encoding='utf-8' "
                 "(the default code page is not UTF-8).",
}
_FIX_LOCK = threading.Lock()


def _fix_paths():
    home = os.path.expanduser("~")
    return {"settings": os.path.join(home, ".claude", "settings.json"),
            "claude_json": os.path.join(home, ".claude.json"),
            "claude_md": os.path.join(home, ".claude", "CLAUDE.md"),
            "settings_local": os.path.join(home, ".claude", "settings.local.json"),
            "plugins": os.path.join(home, ".claude", "plugins", "installed_plugins.json")}


def _fix_key(fix) -> str:
    """Pure: one identity per fix target, e.g. 'plugin:vercel@claude-plugins-official'."""
    fix = fix if isinstance(fix, dict) else {}
    return f"{fix.get('kind', '')}:{fix.get('target', '')}"


def _fix_read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, type(default)) else default
    except FileNotFoundError:
        return default


def _fix_installed_plugins() -> dict:
    """{'vercel': 'vercel@claude-plugins-official', ...} for locally installed plugins."""
    data = _fix_read_json(_fix_paths()["plugins"], {})
    out = {}
    for pid in (data.get("plugins") or {}):
        out.setdefault(str(pid).split("@", 1)[0], str(pid))
    return out


def _fix_for_plugin(name, installed) -> dict:
    """Pure: the fix for an unused plugin's tip."""
    pid = installed.get(name)
    if pid:
        return {"kind": "plugin", "target": pid}
    return {"kind": "manual", "target": name,
            "steps": f"The {name} plugin comes from your claude.ai account, not from this PC. Turn it off in "
                     "claude.ai settings (plugins / capabilities), or ask your organization's admin."}


_FIX_FOR_REASON = {"Python UnicodeEncodeError": {"kind": "env", "target": "PYTHONIOENCODING"},
                   "Shell quoting error": {"kind": "note", "target": "heredoc"},
                   "Python UnicodeDecodeError": {"kind": "note", "target": "utf8_open"}}


def _fix_plan(fix, settings, claude_json, claude_md, settings_local=None):
    """Pure: an allowlisted fix + the current file contents -> {"ok", "title",
    "changes": [...]} or {"ok": False, "error"}. A change is
    {"file", "op": "set"|"list_add"|"list_remove"|"line_add", "path", "before",
    "after"|"item"|"text", "index"}."""
    fix = fix if isinstance(fix, dict) else {}
    kind, target = str(fix.get("kind") or ""), str(fix.get("target") or "")
    if kind == "plugin":
        if not re.match(r"^[\w.-]{1,80}@[\w.-]{1,80}$", target):
            return {"ok": False, "error": "unknown plugin"}
        cur = (settings.get("enabledPlugins") or {}).get(target)
        if cur is False:
            return {"ok": False, "error": "already off"}
        return {"ok": True, "title": f"Turn off the {target.split('@')[0]} plugin",
                "changes": [{"file": "settings", "op": "set", "path": ["enabledPlugins", target],
                             "before": cur, "after": False}]}
    if kind == "mcp":
        if not re.match(r"^[\w .:()/-]{1,80}$", target):
            return {"ok": False, "error": "unknown server"}
        changes = []
        for folder, cfg in sorted((claude_json.get("projects") or {}).items()):
            cur = cfg.get("disabledMcpServers") if isinstance(cfg, dict) else None
            if isinstance(cur, list) and target in cur:
                continue
            changes.append({"file": "claude_json", "op": "list_add", "path": ["projects", folder, "disabledMcpServers"],
                            "before": cur if isinstance(cur, list) else None, "item": target})
        if not changes:
            return {"ok": False, "error": "already off in every folder"}
        return {"ok": True, "title": f"Turn off the MCP server {target}", "changes": changes}
    if kind == "env":
        if target not in _FIX_ENV:
            return {"ok": False, "error": "unknown setting"}
        cur = (settings.get("env") or {}).get(target)
        if cur == _FIX_ENV[target]:
            return {"ok": False, "error": "already set"}
        return {"ok": True, "title": f"Set {target}={_FIX_ENV[target]} for Claude's shells",
                "changes": [{"file": "settings", "op": "set", "path": ["env", target], "before": cur,
                             "after": _FIX_ENV[target]}]}
    if kind == "note":
        text = _FIX_NOTES.get(target)
        if not text:
            return {"ok": False, "error": "unknown note"}
        if text in (claude_md or ""):
            return {"ok": False, "error": "already in CLAUDE.md"}
        return {"ok": True, "title": "Add a line to your CLAUDE.md",
                "changes": [{"file": "claude_md", "op": "line_add", "path": [], "text": text}]}
    if kind == "allow":
        tier, why, _ = _perm_rule_tier(target)
        if tier not in ("read", "project") or len(target) > 600:
            return {"ok": False, "error": f"AOC doesn't add this rule: {why or 'not on its safe list'}"}
        perms = settings.get("permissions")
        title = f"Allow {target} in every project"
        if not isinstance(perms, dict):
            return {"ok": True, "title": title, "changes": [{"file": "settings", "op": "set", "path": ["permissions"],
                                                             "before": perms, "after": {"allow": [target]}}]}
        cur = perms.get("allow")
        if isinstance(cur, list) and target in cur:
            return {"ok": False, "error": "already allowed"}
        return {"ok": True, "title": title,
                "changes": [{"file": "settings", "op": "list_add", "path": ["permissions", "allow"],
                             "before": cur if isinstance(cur, list) else None, "item": target}]}
    if kind == "unallow":
        file, _, rule = target.partition("|")
        doc = {"settings": settings, "settings_local": settings_local}.get(file)
        cur = _fix_get(doc, ["permissions", "allow"]) if isinstance(doc, dict) else None
        if not rule or not isinstance(cur, list) or rule not in cur:
            return {"ok": False, "error": "that rule isn't in the file any more"}
        return {"ok": True, "title": f"Remove the rule {rule}",
                "changes": [{"file": file, "op": "list_remove", "path": ["permissions", "allow"], "item": rule,
                             "index": cur.index(rule)}]}
    return {"ok": False, "error": "this tip has no automatic fix"}


def _fix_get(obj, path):
    for k in path:
        obj = obj.get(k) if isinstance(obj, dict) else None
    return obj


def _fix_set(obj, path, value):
    """Set (value not None) or delete (None) obj[path...]; parents created as needed."""
    for k in path[:-1]:
        if not isinstance(obj.get(k), dict):
            obj[k] = {}
        obj = obj[k]
    if value is None:
        obj.pop(path[-1], None)
    else:
        obj[path[-1]] = value


def _fix_do(changes, docs, undo=False):
    """Pure-ish: apply (or revert) changes to in-memory docs {"settings": dict,
    "claude_json": dict, "claude_md": str}. Returns the changes skipped on undo
    because the value was changed by hand since."""
    skipped = []
    for ch in changes:
        f, op, path = ch["file"], ch["op"], ch.get("path") or []
        if op == "line_add":
            text = ch["text"]
            md = docs.get(f) or ""
            if undo:
                lines = md.split("\n")
                if text in lines:
                    lines.remove(text)
                    docs[f] = "\n".join(lines)
            else:
                docs[f] = (md.rstrip("\n") + "\n\n" if md.strip() else "") + text + "\n"
            continue
        doc = docs[f]
        cur = _fix_get(doc, path)
        if op == "set":
            if undo:
                if cur == ch["after"]:
                    _fix_set(doc, path, ch["before"])
                else:
                    skipped.append(ch)
            else:
                _fix_set(doc, path, ch["after"])
        elif op == "list_add":
            lst = list(cur) if isinstance(cur, list) else []
            if undo:
                if ch["item"] in lst:
                    lst.remove(ch["item"])
                _fix_set(doc, path, None if (not lst and ch.get("before") is None) else lst)
            elif ch["item"] not in lst:
                _fix_set(doc, path, lst + [ch["item"]])
        elif op == "list_remove":
            lst = list(cur) if isinstance(cur, list) else []
            if undo:
                if ch["item"] not in lst:
                    lst.insert(min(int(ch.get("index") or 0), len(lst)), ch["item"])
                    _fix_set(doc, path, lst)
            elif ch["item"] in lst:
                lst.remove(ch["item"])
                _fix_set(doc, path, lst)
    return skipped


def _fix_load_docs(paths):
    md = ""
    try:
        with open(paths["claude_md"], encoding="utf-8") as f:
            md = f.read()
    except FileNotFoundError:
        pass
    return {"settings": _fix_read_json(paths["settings"], {}), "claude_json": _fix_read_json(paths["claude_json"], {}),
            "claude_md": md, "settings_local": _fix_read_json(paths["settings_local"], {})}


def _fix_write(paths, docs, files, stamp):
    """Back up then atomically replace each touched file. ~/.claude.json is
    rewritten by running sessions too, so it's re-read after a moment and the
    write retried if a session put back its own copy."""
    import shutil
    for f in files:
        p = paths[f]
        eol, final, ascii_only = "\n", True, False   # keep the file's own line endings, final newline, \u escapes
        if os.path.exists(p):
            with open(p, "rb") as fh:
                raw = fh.read()
            eol = "\r\n" if b"\r\n" in raw else "\n"
            final = not raw or raw.endswith(b"\n")
            ascii_only = bool(raw) and max(raw) < 128 and b"\\u" in raw
            shutil.copy2(p, f"{p}.bak-aoc-fix-{stamp}")
            olds = sorted(glob.glob(glob.escape(p) + ".bak-aoc-fix-*"))
            for old in olds[:-5]:
                try:
                    os.remove(old)
                except OSError:
                    pass
        os.makedirs(os.path.dirname(p), exist_ok=True)
        text = docs[f] if f == "claude_md" else json.dumps(docs[f], indent=2, ensure_ascii=ascii_only)
        text = text.rstrip("\n") + ("\n" if final or f == "claude_md" else "")
        tmp = p + ".aoc-tmp"
        with open(tmp, "w", encoding="utf-8", newline=eol) as fh:
            fh.write(text)
        os.replace(tmp, p)


def _fix_load_log():
    data = _fix_read_json(FIXES_FILE, {})
    return data.get("applied") if isinstance(data.get("applied"), list) else []


def _fix_save_log(applied):
    os.makedirs(AOC_DATA_DIR, exist_ok=True)
    tmp = FIXES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"applied": applied[-200:]}, f, indent=2, ensure_ascii=False)
    os.replace(tmp, FIXES_FILE)


def _fix_settle(paths, changes, retries=3, wait=2.0):
    """Re-read ~/.claude.json after `wait` s; re-apply when a running session
    overwrote it. True when the change stuck."""
    for _ in range(retries):
        time.sleep(wait)
        docs = _fix_load_docs(paths)
        probe = {"claude_json": json.loads(json.dumps(docs["claude_json"]))}
        _fix_do([c for c in changes if c["file"] == "claude_json"], probe)
        if probe["claude_json"] == docs["claude_json"]:
            return True
        _fix_write(paths, probe | {k: v for k, v in docs.items() if k != "claude_json"}, ["claude_json"],
                   time.strftime("%Y%m%d-%H%M%S"))
    return False


def _fix_preview(fix):
    paths = _fix_paths()
    docs = _fix_load_docs(paths)
    plan = _fix_plan(fix, docs["settings"], docs["claude_json"], docs["claude_md"], docs["settings_local"])
    if plan.get("ok"):
        plan["files"] = sorted({paths[c["file"]] for c in plan["changes"]})
    return plan


def _fix_apply(fix, settle=True):
    with _FIX_LOCK:
        paths = _fix_paths()
        docs = _fix_load_docs(paths)
        plan = _fix_plan(fix, docs["settings"], docs["claude_json"], docs["claude_md"], docs["settings_local"])
        if not plan.get("ok"):
            return plan
        _fix_do(plan["changes"], docs)
        files = sorted({c["file"] for c in plan["changes"]})
        stamp = time.strftime("%Y%m%d-%H%M%S")
        _fix_write(paths, docs, files, stamp)
        rec = {"id": f"fx{int(time.time() * 1000)}", "key": _fix_key(fix), "kind": fix["kind"],
               "target": fix["target"], "title": plan["title"], "changes": plan["changes"], "at": time.time()}
        stuck = True
        if settle and "claude_json" in files:
            stuck = _fix_settle(paths, plan["changes"])
        applied = [r for r in _fix_load_log() if r.get("key") != rec["key"]]
        _fix_save_log(applied + [rec])
        return {"ok": True, "record": rec, "stuck": stuck}


def _fix_undo(fix_id, settle=True):
    with _FIX_LOCK:
        applied = _fix_load_log()
        rec = next((r for r in applied if r.get("id") == fix_id), None)
        if rec is None:
            return {"ok": False, "error": "not found"}
        paths = _fix_paths()
        docs = _fix_load_docs(paths)
        skipped = _fix_do(rec["changes"], docs, undo=True)
        files = sorted({c["file"] for c in rec["changes"]})
        _fix_write(paths, docs, files, time.strftime("%Y%m%d-%H%M%S"))
        if settle and "claude_json" in files:
            for _ in range(3):
                time.sleep(2.0)
                again = _fix_load_docs(paths)
                left = [c for c in rec["changes"] if c["file"] == "claude_json"
                        and rec["target"] in (_fix_get(again["claude_json"], c["path"]) or [])]
                if not left:
                    break
                _fix_do(left, again, undo=True)
                _fix_write(paths, again, ["claude_json"], time.strftime("%Y%m%d-%H%M%S"))
        _fix_save_log([r for r in applied if r.get("id") != fix_id])
        return {"ok": True, "skipped": len(skipped)}


# ── Startup context (History -> CONTEXT) ──────────────────────────────────
# What every session carries before your first word. The prompt of a
# session's first API call is everything Claude Code sends up front; the
# transcript records most of its parts as attachments (skills listing per
# plugin, deferred tool names, MCP server instructions, agent types,
# CLAUDE.md / memory files, system prompt) -- the rest is Claude Code's
# built-in tool definitions. That prompt is written to the cache once and
# re-read on every later API call, so a few thousand tokens of unused
# skills or MCP tools add up over hundreds of calls. MCP servers that
# connect after the first call still add their tools from then on, so those
# deltas are counted from the whole session.
_CTX_CHARS_PER_TOKEN = 3.7
_CTX_CACHE_S = 15 * 60
_CTX_LATE_MARKERS = (b'"Skill"', b"command-name", b"mcp__", b"_delta")   # lines worth parsing after the first call
_CTX_MSG_ID = re.compile(rb'"id":"(msg_[^"]+)"')
_CTX_REQ_ID = re.compile(rb'"requestId":"([^"]+)"')
_ctx_cache = {}


def _ctx_tokens(chars) -> int:
    return int(round((chars or 0) / _CTX_CHARS_PER_TOKEN))


def _ctx_mcp_key(name) -> str:
    """'claude.ai Vercel' (instructions) and 'claude_ai_Vercel' (tool names) -> one key."""
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def _ctx_mcp_label(seg) -> str:
    """Tool-name segment -> a readable server name: 'claude_ai_Google_Drive'
    -> 'claude.ai Google Drive', 'plugin_design_figma' -> 'figma (design plugin)'."""
    if seg.startswith("claude_ai_"):
        return "claude.ai " + seg[10:].replace("_", " ")
    bits = seg.split("_", 2)
    if bits[0] == "plugin" and len(bits) == 3:
        return f"{bits[2]} ({bits[1]} plugin)"
    return seg


def _ctx_plugin(skill) -> str:
    """'vercel:deploy' -> 'vercel'; a skill without a plugin prefix -> '(no plugin)'."""
    skill = str(skill or "").lstrip("/")
    return skill.split(":", 1)[0] if ":" in skill else "(no plugin)"


def _ctx_scan_lines(lines):
    """Pure: one main transcript's lines -> its startup context, or None
    when it never reached an API call.
    parts {name: chars} from the attachments before the first call;
    skills {plugin: [count, chars]}; files {path: [kind, chars]};
    mcp {key: [label, chars]} over the whole session; skill_uses /
    mcp_uses {plugin|key: n} (Skill tool, /plugin:skill commands, MCP tools)."""
    parts, skills, files, mcp, uses, mcp_uses = {}, {}, {}, {}, {}, {}
    first = None
    model, cwd, prompt_chars = "", "", 0
    calls = set()

    def add_mcp(key, label, n, named=False):
        m = mcp.setdefault(key, [label, 0])
        if named:   # the name the server gives itself in its instructions wins
            m[0] = label
        m[1] += n

    for raw in lines:
        if first is not None and not any(k in raw for k in _CTX_LATE_MARKERS):
            if b'"usage"' in raw and b'"isSidechain":true' not in raw:   # just count the call, no JSON parse
                mi, ri = _CTX_MSG_ID.search(raw), _CTX_REQ_ID.search(raw)
                calls.add((mi.group(1).decode() if mi else "", ri.group(1).decode() if ri else ""))
            continue
        try:
            o = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(o, dict) or o.get("isSidechain"):
            continue
        cwd = cwd or str(o.get("cwd") or "")
        msg = o.get("message") if isinstance(o.get("message"), dict) else {}
        if o.get("type") == "assistant":
            if msg.get("usage"):
                calls.add((msg.get("id") or "", o.get("requestId") or ""))
                if first is None:
                    u = msg["usage"]
                    first = sum((u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                         "cache_read_input_tokens"))
                    model = msg.get("model") or ""
            for b in msg.get("content") or []:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                name = str(b.get("name") or "")
                if name == "Skill":
                    plug = _ctx_plugin((b.get("input") or {}).get("skill"))
                    uses[plug] = uses.get(plug, 0) + 1
                elif name.startswith("mcp__") and name.count("__") >= 2:
                    key = _ctx_mcp_key(name.split("__")[1])
                    mcp_uses[key] = mcp_uses.get(key, 0) + 1
            continue
        if o.get("type") == "user" and isinstance(msg.get("content"), str):
            text = msg["content"]
            for cmd in re.findall(r"<command-name>/?([^<\s]+)</command-name>", text):
                if ":" in cmd:
                    plug = _ctx_plugin(cmd)
                    uses[plug] = uses.get(plug, 0) + 1
            if first is None and not o.get("isMeta"):
                prompt_chars += len(text)
        a = o.get("attachment") if isinstance(o.get("attachment"), dict) else None
        if not a:
            continue
        t = a.get("type")
        # MCP tools and instructions: any time in the session
        if t == "deferred_tools_delta":
            for line in a.get("addedLines") or []:
                line = str(line)
                name = line.split(None, 1)[0] if line.strip() else ""
                if name.startswith("mcp__") and name.count("__") >= 2:
                    seg = name.split("__")[1]
                    add_mcp(_ctx_mcp_key(seg), _ctx_mcp_label(seg), len(line) + 1)
            if first is None:
                parts["deferred tool names"] = parts.get("deferred tool names", 0) + \
                    sum(len(str(x)) + 1 for x in a.get("addedLines") or [])
            continue
        if t == "mcp_instructions_delta":
            names = a.get("addedNames") or []
            blocks = a.get("addedBlocks") or []
            for i, blk in enumerate(blocks):
                label = str(names[i]) if i < len(names) else ""
                if not label:
                    mm = re.match(r"\s*##\s*(.+)", str(blk))
                    label = mm.group(1).strip() if mm else "?"
                add_mcp(_ctx_mcp_key(label), label, len(str(blk)), named=True)
            if first is None:
                parts["MCP server instructions"] = parts.get("MCP server instructions", 0) + \
                    sum(len(str(x)) for x in blocks)
            continue
        if first is not None:
            continue
        if t == "skill_listing":
            content = str(a.get("content") or "")
            cur = None
            for line in content.splitlines():
                if line.startswith("- "):
                    head = line[2:].split(": ", 1)[0]
                    cur = skills.setdefault(_ctx_plugin(head), [0, 0])
                    cur[0] += 1
                if cur is not None:   # a description's continuation lines belong to its skill
                    cur[1] += len(line) + 1
            parts["skills"] = parts.get("skills", 0) + len(content)
        elif t == "agent_listing_delta":
            parts["agent types"] = parts.get("agent types", 0) + sum(len(str(x)) + 1 for x in a.get("addedLines") or [])
        elif t == "instructions":
            for fi in a.get("files") or []:
                if isinstance(fi, dict):
                    n = len(str(fi.get("content") or ""))
                    files[str(fi.get("path") or "?")] = [str(fi.get("type") or ""), n]
                    parts["CLAUDE.md & memory"] = parts.get("CLAUDE.md & memory", 0) + n
        elif t == "prompt_snapshot":
            parts["system prompt"] = sum(len(x) for x in a.get("systemPrompt") or [] if isinstance(x, str))
        elif t == "environment":
            parts["environment"] = len(json.dumps(a.get("snapshot") or {}))
    if not first:
        return None
    return {"first": first, "model": model, "cwd": cwd, "calls": len(calls), "prompt_chars": prompt_chars,
            "parts": parts, "skills": skills, "files": files, "mcp": mcp,
            "skill_uses": uses, "mcp_uses": mcp_uses}


def _ctx_report(sessions, days) -> dict:
    """Pure: scanned sessions -> the CONTEXT tab. A session's startup costs
    one cache write plus a cache read on every later call (_calc_cost on its
    model); a part's cost is its share of the startup tokens actually paid."""
    if not sessions:
        return {"days": days, "sessions": 0}
    month = 30 / max(1, days)
    tot_cost = 0.0
    parts_tok, skills_agg, files_agg, mcp_agg, uses, mcp_uses, projects = {}, {}, {}, {}, {}, {}, {}
    starts = []
    for s in sessions:
        start = max(0, s["first"] - _ctx_tokens(s["prompt_chars"]))
        reads = start * max(0, s["calls"] - 1)
        cost = _calc_cost(0, 0, start, 0, s["model"]) + _calc_cost(0, 0, 0, reads, s["model"])
        tot_cost += cost
        starts.append(start)
        known = 0
        for k, ch in s["parts"].items():
            t = _ctx_tokens(ch)
            known += t
            parts_tok[k] = parts_tok.get(k, 0) + t
        parts_tok["built-in tools & rest"] = parts_tok.get("built-in tools & rest", 0) + max(0, start - known)
        for plug, (n, ch) in s["skills"].items():
            g = skills_agg.setdefault(plug, {"plugin": plug, "skills": 0, "total": 0, "sessions": 0})
            g["skills"] = max(g["skills"], n)
            g["total"] += _ctx_tokens(ch)
            g["sessions"] += 1
        for path, (kind, ch) in s["files"].items():
            g = files_agg.setdefault(path, {"path": path, "kind": kind, "tokens": 0, "total": 0, "sessions": 0})
            g["tokens"] = max(g["tokens"], _ctx_tokens(ch))
            g["total"] += _ctx_tokens(ch)
            g["sessions"] += 1
        for key, (label, ch) in s["mcp"].items():
            g = mcp_agg.setdefault(key, {"server": label, "total": 0, "sessions": 0})
            g["total"] += _ctx_tokens(ch)
            g["sessions"] += 1
        for plug, n in s["skill_uses"].items():
            uses[plug] = uses.get(plug, 0) + n
        for key, n in s["mcp_uses"].items():
            mcp_uses[key] = mcp_uses.get(key, 0) + n
        name = os.path.basename(s["cwd"].rstrip("\\/")) or "?"
        p = projects.setdefault(name, {"project": name, "sessions": 0, "tokens": 0, "cost": 0.0, "calls": 0})
        p["sessions"] += 1
        p["tokens"] += start
        p["calls"] += s["calls"]
        p["cost"] += cost
    n = len(sessions)
    all_start = max(1, sum(starts))
    per_tok = tot_cost / all_start   # $ per startup token, as actually paid (writes + re-reads)

    breakdown = sorted(({"part": k, "tokens": int(v / n), "share": round(v / all_start * 100, 1)}
                        for k, v in parts_tok.items() if v), key=lambda r: -r["tokens"])
    skills_rows = sorted(({"plugin": g["plugin"], "skills": g["skills"], "sessions": g["sessions"],
                           "tokens": int(g["total"] / g["sessions"]), "uses": uses.get(g["plugin"], 0),
                           "cost": round(per_tok * g["total"], 2)} for g in skills_agg.values()),
                         key=lambda g: -g["tokens"])
    mcp_rows = sorted(({"server": g["server"], "sessions": g["sessions"], "tokens": int(g["total"] / g["sessions"]),
                        "uses": mcp_uses.get(k, 0), "cost": round(per_tok * g["total"], 2)}
                       for k, g in mcp_agg.items()), key=lambda g: -g["tokens"])
    files_rows = sorted(({"path": f["path"], "kind": f["kind"], "tokens": f["tokens"], "sessions": f["sessions"],
                          "cost": round(per_tok * f["total"], 2)} for f in files_agg.values()),
                        key=lambda f: -f["tokens"])

    tips = []
    for g in skills_rows:
        if g["plugin"] != "(no plugin)" and not g["uses"] and g["tokens"] >= 300:
            tips.append({"kind": "plugin", "plugin": g["plugin"], "saving_month": round(g["cost"] * month, 2),
                         "title": f"Unused plugin: {g['plugin']}",
                         "detail": f"It lists {g['skills']} skill{'s' if g['skills'] != 1 else ''} (~{g['tokens']:,} tokens) "
                                   f"in every session and none was used in {days} days. Disable it with `/plugin`."})
    for g in mcp_rows:
        if not g["uses"] and g["tokens"] >= 300:
            tips.append({"kind": "mcp", "server": g["server"], "saving_month": round(g["cost"] * month, 2),
                         "title": f"Unused MCP server: {g['server']}",
                         "detail": f"Its tool names and instructions add ~{g['tokens']:,} tokens to every session and no "
                                   f"tool of it was called in {days} days. Disconnect it (`/mcp`, or the connector in claude.ai settings)."})
    for f in files_rows:
        if f["tokens"] >= 1500:
            tips.append({"kind": "file", "saving_month": round(f["cost"] * 0.5 * month, 2),
                         "title": f"Big instructions file: {os.path.basename(f['path'])}",
                         "detail": f"~{f['tokens']:,} tokens loaded into every session it applies to. Keep it to short pointers "
                                   "and move the detail into files Claude reads when needed; halving it saves the amount shown."})
    tips.sort(key=lambda t: -t["saving_month"])
    tips = [t for t in tips if t["saving_month"] >= 0.01][:8]
    if not tips:
        tips.append({"kind": "fine", "saving_month": 0, "title": "Your startup context already looks lean",
                     "detail": "No unused plugin or MCP server and no oversized instructions file in this period."})
    return {
        "days": days, "sessions": n, "monthly_factor": round(month, 3),
        "avg_tokens": int(sum(starts) / n), "max_tokens": max(starts),
        "avg_calls": round(sum(s["calls"] for s in sessions) / n, 1), "cost": round(tot_cost, 2),
        "breakdown": breakdown, "skills": skills_rows, "mcp": mcp_rows[:20], "files": files_rows[:20],
        "projects": sorted(({**p, "avg_tokens": int(p["tokens"] / p["sessions"]), "cost": round(p["cost"], 2)}
                            for p in projects.values()), key=lambda p: -p["cost"])[:15],
        "tips": tips,
    }


def _context_report(days, now=None):
    """GET /context_report: scan the main transcripts of the last `days` (cached 15 min)."""
    now = now or time.time()
    hit = _ctx_cache.get(days)
    if hit and now - hit[0] < _CTX_CACHE_S:
        return hit[1]
    base = os.path.join(os.path.expanduser("~"), ".claude", "projects")
    since = now - days * 86400
    sessions = []
    for f in glob.glob(os.path.join(base, "*", "*.jsonl")):
        try:
            if os.path.getmtime(f) < since:
                continue
            with open(f, "rb") as fh:
                s = _ctx_scan_lines(fh)
        except OSError:
            continue
        if s:
            sessions.append(s)
    report = _ctx_report(sessions, days)
    installed = _fix_installed_plugins()
    for t in report.get("tips") or []:
        if t.get("kind") == "plugin":
            t["fix"] = _fix_for_plugin(t["plugin"], installed)
        elif t.get("kind") == "mcp":
            m = re.match(r"^(.+) \(([\w-]+) plugin\)$", t["server"])   # 'figma (design plugin)' -> plugin:design:figma
            t["fix"] = {"kind": "mcp", "target": f"plugin:{m.group(2)}:{m.group(1)}" if m else t["server"]}
    report["computed_at"] = now
    _ctx_cache[days] = (now, report)
    return report


# ── Memory & CLAUDE.md editor (History -> MEMORY) ─────────────────────────
# The instruction files Claude Code loads at session start (~/.claude/
# CLAUDE.md, a project's CLAUDE.md / .claude/CLAUDE.md / CLAUDE.local.md,
# each auto-memory MEMORY.md index) and the memory topic files the indexes
# point to. _mem_lint flags what costs tokens or misleads Claude: oversized
# files, index lines that should be one-line hooks, links and paths that
# lead nowhere, topic files no index mentions, repeated lines. Saving is
# local-only, refused when the file changed since it was opened, and keeps
# backups in AOC's data folder (not next to the memory files).
_MEM_MAX_FILES = 200
_MEM_MAX_BYTES = 512 * 1024
_MEM_BACKUP_DIR = os.path.join(AOC_DATA_DIR, "memory_backups")
_MEM_BACKUPS_KEEP = 10
_MEM_LOADED = ("user", "project", "local", "index")   # read into every session start (where they apply)
_MEM_BIG_TOKENS = 1500
_MEM_BIG_TOPIC_TOKENS = 8000   # ARCHIVE OLD is offered from here on
_MEM_INDEX_MAX_LINES = 200   # Claude Code loads only the start of a long MEMORY.md
_MEM_INDEX_LINE_CHARS = 300
_MEM_DUPE_MIN_CHARS = 40
_MEM_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_MEM_WIKI = re.compile(r"\[\[([^\]|#]+)")
_MEM_ABS = re.compile(r"(?<![\w/\\])(?:[A-Za-z]:[\\/]|~[\\/])[^\s`'\"<>|*?()\[\],;]*")
_MEM_SEV = {"error": 0, "warn": 1, "info": 2}


def _mem_discover(home, dirs, isfile=os.path.isfile, glob_fn=glob.glob):
    """[{path, kind}] -- kind: user / project / local / index / memory.
    `dirs` = folders Claude Code worked in (_launch_known_dirs)."""
    out, seen = [], set()

    def add(p, kind):
        p = os.path.normpath(p)
        k = os.path.normcase(p)
        if k not in seen and isfile(p):
            seen.add(k)
            out.append({"path": p, "kind": kind})

    j = os.path.join
    add(j(home, ".claude", "CLAUDE.md"), "user")
    for d in dirs:
        add(j(d, "CLAUDE.md"), "project")
        add(j(d, ".claude", "CLAUDE.md"), "project")
        add(j(d, "CLAUDE.local.md"), "local")
    mds = glob_fn(j(glob.escape(home), ".claude", "projects", "*", "memory", "*.md"))
    for p in sorted(mds, key=lambda p: (os.path.dirname(p).lower(), os.path.basename(p) != "MEMORY.md", os.path.basename(p).lower())):
        add(p, "index" if os.path.basename(p) == "MEMORY.md" else "memory")
    return out[:_MEM_MAX_FILES]


def _mem_frontmatter(text):
    """{key: value} of a leading '---' block, or None when there is none."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    meta = {}
    for ln in lines[1:]:
        if ln.strip() == "---":
            return meta
        m = re.match(r"^([\w-]+):\s*(.*)$", ln)
        if m:
            meta[m.group(1)] = m.group(2).strip().strip("'\"")
    return None


def _mem_find_path(line, m, exists):
    """The path an absolute-path match starts, or None when it exists. A
    path with spaces ('...\\AOC-Agent Monitoring\\x') is cut at the first
    space by the regex, so up to 4 following words are tried too."""
    cand = m.group(0).rstrip(".:!")
    if any(x in cand for x in ("<", "...", "…", "%", "{", "$")) or len(cand) <= 3:
        return None
    rest = line[m.end():]
    tries = [cand]
    if rest.startswith(" ") and m.group(0) == cand:   # the path itself may go on after a space
        for w in rest[1:].split(" ")[:4]:
            w = re.split(r"[`'\"<>|*?()\[\],;]", w)[0]
            if not w:
                break
            tries.append(tries[-1] + " " + w.rstrip(".:!"))
            if w != w.rstrip(".:!"):
                break
    if m.start() > 0 and line[m.start() - 1] == "`" and "`" in rest:   # `C:\a b\c` -- the whole quoted path
        tries.append(line[m.start():m.end() + rest.index("`")].rstrip(".:!"))
    for t in tries:
        if exists(os.path.expanduser(t)):
            return None
    # only when its folder (or the one above) is there: a moved / renamed /
    # deleted file, not an example path like C:\Users\x\.ssh\id_rsa
    full = tries[-1]
    parent = os.path.dirname(os.path.expanduser(full).rstrip("\\/"))
    grand = os.path.dirname(parent)
    if exists(parent) or (grand and len(re.split(r"[\\/]+", grand.strip("\\/"))) >= 3 and exists(grand)):
        return full
    return None


def _mem_lint(files, exists=os.path.exists):
    """Pure (file system only through `exists`): files = [{path, kind, text}]
    -> [{path, kind, tokens, lines, issues: [{sev, line, rule, msg}]}],
    worst first. Line numbers are 1-based; 0 = the whole file."""
    by_dir = {}
    for f in files:
        if f["kind"] in ("index", "memory"):
            by_dir.setdefault(os.path.normcase(os.path.dirname(f["path"])), []).append(f)
    names, linked = {}, {}
    for d, group in by_dir.items():
        ns = set()
        for f in group:
            ns.add(os.path.splitext(os.path.basename(f["path"]))[0].lower())
            meta = _mem_frontmatter(f["text"]) or {}
            if meta.get("name"):
                ns.add(meta["name"].lower())
        names[d] = ns
        linked[d] = {os.path.normcase(os.path.basename(t)) for f in group if f["kind"] == "index"
                     for t in _MEM_LINK.findall(f["text"])}

    seen_lines, out = {}, []
    for f in files:
        path, kind, text = f["path"], f["kind"], f["text"]
        d = os.path.normcase(os.path.dirname(path))
        lines = text.splitlines()
        issues = []
        add = lambda sev, ln, rule, msg: issues.append({"sev": sev, "line": ln, "rule": rule, "msg": msg})
        tokens = _ctx_tokens(len(text))
        if kind in _MEM_LOADED and tokens >= _MEM_BIG_TOKENS:
            add("warn", 0, "size", f"~{tokens:,} tokens read at every session start. Keep short pointers here "
                                   "and move detail into files Claude opens when needed.")
        archive = kind == "memory" and os.path.splitext(os.path.basename(path))[0].lower().endswith("_archive")
        if kind == "memory" and not archive and tokens >= _MEM_BIG_TOPIC_TOKENS:
            add("warn", 0, "size", f"~{tokens:,} tokens: each time Claude opens this memory it reads all of it. "
                                   "ARCHIVE OLD moves the older dated entries into a separate archive file.")
        if kind == "index" and len(lines) > _MEM_INDEX_MAX_LINES:
            add("warn", _MEM_INDEX_MAX_LINES + 1, "index_length",
                f"{len(lines)} lines: Claude Code loads only the first {_MEM_INDEX_MAX_LINES}, the rest is never seen.")
        # an archive is meant to be reached from its topic file, not the index
        own = ((_mem_frontmatter(text) or {}).get("name") or "").strip().lower() if archive else ""
        reached = bool(own) and own in {w.strip().lower() for g in by_dir.get(d, []) if g is not f
                                        for w in _MEM_WIKI.findall(g["text"])}
        if kind == "memory" and not reached and d in linked and os.path.normcase(os.path.basename(path)) not in linked[d]:
            add("warn", 0, "orphan", "No link to this file in its MEMORY.md, so Claude never finds it.")
        if kind == "memory":
            meta = _mem_frontmatter(text)
            missing = [k for k in ("name", "description") if not (meta or {}).get(k)]
            if missing:
                add("info", 1, "frontmatter", "Frontmatter is missing " + " and ".join(missing)
                    + " (used to decide when the memory is relevant).")
        in_front = bool(lines) and lines[0].strip() == "---"
        reported_paths = set()
        for i, ln in enumerate(lines, 1):
            if in_front:
                if i > 1 and ln.strip() == "---":
                    in_front = False
                continue
            if kind == "index" and len(ln) > _MEM_INDEX_LINE_CHARS:
                add("warn", i, "long_line", f"{len(ln):,}-character index line. An index entry should be a "
                                            "one-line hook; put the detail in the topic file it links to.")
            for target in _MEM_LINK.findall(ln):
                if re.match(r"^[a-z][\w+.-]*:", target, re.I) or target.startswith("#"):
                    continue   # URLs, mailto:, anchors
                t = target.split("#")[0]
                full = os.path.expanduser(t) if os.path.isabs(os.path.expanduser(t)) else os.path.join(os.path.dirname(path), t)
                if t and not exists(full):
                    add("error", i, "dead_link", f"Link to {t}, which doesn't exist.")
            if kind in ("index", "memory") and d in names:
                for w in _MEM_WIKI.findall(ln):
                    if w.strip().lower() not in names[d]:
                        add("info", i, "wikilink", f"[[{w.strip()}]]: no memory with that name yet.")
            for m in _MEM_ABS.finditer(ln):
                bad = _mem_find_path(ln, m, exists)
                if bad and bad not in reported_paths:
                    reported_paths.add(bad)
                    add("warn", i, "dead_path", f"{bad} doesn't exist (moved or renamed?).")
            # repeated lines: within one file, or across the files every session
            # loads together -- topic files are opened one at a time, so the
            # same line in two of them isn't read twice
            norm = re.sub(r"\s+", " ", re.sub(r"^[\s>#*\-+\d.]+", "", ln)).strip().lower()
            if len(norm) >= _MEM_DUPE_MIN_CHARS:
                scope = seen_lines if kind in _MEM_LOADED else seen_lines.setdefault(("file", path), {})
                first = scope.get(norm)
                if first:
                    where = f"line {first[1]}" if first[0] == path else f"{os.path.basename(first[0])}:{first[1]}"
                    add("warn", i, "duplicate", f"Same as {where} -- Claude reads it twice.")
                else:
                    scope[norm] = (path, i)
        issues.sort(key=lambda x: (_MEM_SEV[x["sev"]], x["line"]))
        out.append({"path": path, "kind": kind, "tokens": tokens, "lines": len(lines), "issues": issues})
    return out


def _mem_read(path):
    with open(path, "rb") as fh:
        raw = fh.read(_MEM_MAX_BYTES + 1)
    if len(raw) > _MEM_MAX_BYTES:
        raise ValueError("file too large")
    return raw.decode("utf-8", errors="replace").replace("\r\n", "\n")


def _mem_files():
    """Discovered files, each with its text and mtime (unreadable ones skipped)."""
    out = []
    for f in _mem_discover(os.path.expanduser("~"), [d["path"] for d in _launch_known_dirs()]):
        try:
            out.append({**f, "text": _mem_read(f["path"]), "mtime": os.stat(f["path"]).st_mtime})
        except (OSError, ValueError):
            continue
    return out


def _memory_lint():
    """GET /memory_lint."""
    files = _mem_files()
    rows = _mem_lint(files)
    mt = {f["path"]: f["mtime"] for f in files}
    counts = {s: sum(1 for r in rows for x in r["issues"] if x["sev"] == s) for s in _MEM_SEV}
    texts = {f["path"]: f["text"] for f in files}
    for r in rows:
        r["mtime"] = mt[r["path"]]
        r["backups"] = len(_mem_backups(r["path"]))
        r["archive"] = None   # what ARCHIVE OLD would move, when it's offered
        if r["kind"] == "memory" and r["tokens"] >= _MEM_BIG_TOPIC_TOKENS:
            sp = _mem_archive_split(texts[r["path"]], r["path"])
            if sp:
                r["archive"] = {"entries": len(sp["moved"]), "tokens": sp["moved_tokens"],
                                "file": os.path.basename(sp["archive_path"])}
    return {"files": rows, "counts": counts,
            "start_tokens": sum(r["tokens"] for r in rows if r["kind"] in ("user", "index"))}


def _mem_known(path):
    """The discovered entry for `path` (case-insensitive), or None -- only
    these files can be read or written through the editor."""
    k = os.path.normcase(os.path.normpath(str(path or "")))
    for f in _mem_files():
        if os.path.normcase(f["path"]) == k:
            return f
    return None


def _mem_backup_prefix(path):
    import hashlib
    return hashlib.sha1(os.path.normcase(os.path.normpath(path)).encode("utf-8")).hexdigest()[:12] + "-"


def _mem_backups(path):
    """This file's backups, oldest first."""
    try:
        pre = _mem_backup_prefix(path)
        return sorted(os.path.join(_MEM_BACKUP_DIR, n) for n in os.listdir(_MEM_BACKUP_DIR) if n.startswith(pre))
    except OSError:
        return []


def _mem_write_text(path, text):
    """Replace `path` with `text`, keeping the file's own line endings."""
    eol = "\n"
    try:
        with open(path, "rb") as fh:
            if b"\r\n" in fh.read():
                eol = "\r\n"
    except OSError:
        pass
    tmp = path + ".aoc-tmp"
    with open(tmp, "w", encoding="utf-8", newline=eol) as fh:
        fh.write(text)
    os.replace(tmp, path)


def _memory_save(path, text, mtime):
    """POST /memory_file: back up then write. Refused when the file changed
    on disk after the editor loaded it (Claude may have just written it)."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > _MEM_MAX_BYTES:
        return {"ok": False, "error": "Text missing or too large."}
    f = _mem_known(path)
    if not f:
        return {"ok": False, "error": "Not a CLAUDE.md or memory file AOC knows about."}
    if not isinstance(mtime, (int, float)) or abs(f["mtime"] - mtime) > 0.001:
        return {"ok": False, "error": "changed", "mtime": f["mtime"],
                "detail": "The file changed on disk since you opened it (Claude may have just written it). Copy your edit, press REVERT to load the new version, then apply it again."}
    text = text.replace("\r\n", "\n")
    if text == f["text"]:
        return {"ok": True, "unchanged": True, "mtime": f["mtime"]}
    _mem_backup(f["path"])
    _mem_write_text(f["path"], text)
    return {"ok": True, "mtime": os.stat(f["path"]).st_mtime}


def _mem_backup(path):
    """Copy `path` into the backup folder (keeping the newest N); returns
    the backup's file name."""
    import shutil
    os.makedirs(_MEM_BACKUP_DIR, exist_ok=True)
    name = _mem_backup_prefix(path) + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + os.path.basename(path)
    shutil.copy2(path, os.path.join(_MEM_BACKUP_DIR, name))
    for old in _mem_backups(path)[:-_MEM_BACKUPS_KEEP]:
        try:
            os.remove(old)
        except OSError:
            pass
    return name


def _memory_undo(path):
    """POST /memory_file/undo: put back the newest backup (and drop it).
    When that save was an ARCHIVE OLD, the archive file goes back too (or
    away, when the archive created it)."""
    f = _mem_known(path)
    if not f:
        return {"ok": False, "error": "Not a CLAUDE.md or memory file AOC knows about."}
    baks = _mem_backups(f["path"])
    if not baks:
        return {"ok": False, "error": "No earlier version saved by AOC."}
    rec_path, rec = _mem_archive_record(f["path"]), None
    try:
        with open(rec_path, encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        pass
    if isinstance(rec, dict) and rec.get("topic_backup") == os.path.basename(baks[-1]):
        apath = rec.get("archive_path") or ""
        expected = os.path.normcase(_mem_archive_target(f["path"], f["text"])[0])
        if os.path.normcase(apath) == expected:      # never touch anything else
            abak = rec.get("archive_backup")
            if abak and os.path.isfile(os.path.join(_MEM_BACKUP_DIR, abak)):
                _mem_write_text(apath, _mem_read(os.path.join(_MEM_BACKUP_DIR, abak)))
                os.remove(os.path.join(_MEM_BACKUP_DIR, abak))
            elif not abak and os.path.isfile(apath):
                os.remove(apath)
        try:
            os.remove(rec_path)
        except OSError:
            pass
    _mem_write_text(f["path"], _mem_read(baks[-1]))
    os.remove(baks[-1])
    return {"ok": True, "mtime": os.stat(f["path"]).st_mtime}


# ── Memory archive (History -> MEMORY -> ARCHIVE OLD) ─────────────────────
# A project memory that grows with every round of work ends up tens of
# thousands of tokens, and Claude reads all of it each time it opens it.
# _mem_archive_split moves the older dated entries into <name>_archive.md
# (linked from the topic file) and keeps the frontmatter, the intro, every
# heading, every "RESUME HERE" entry and the newest history in place. A
# file counts as chronological from its first dated entry on; one with no
# dates at all (a profile, a how-to) is never touched.
_MEM_ARCHIVE_KEEP_TOKENS = 3000   # newest history that stays in place
_MEM_ARCHIVE_MIN_KEEP = 2         # ... and at least this many dated entries
_MEM_ARCHIVE_MIN_MOVE = 500       # less than this isn't worth a second file
_MEM_DATE = re.compile(r"\b20\d\d-[01]\d-[0-3]\d\b|(?<![\w.-])(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])(?![\w-])")
_MEM_RESUME = re.compile(r"resume\s+here", re.I)
_MEM_LIST_ITEM = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s")


def _mem_is_archive(path):
    return os.path.splitext(os.path.basename(path))[0].lower().endswith("_archive")


def _mem_archive_target(path, text):
    """(archive path, its memory name, the topic's own name)."""
    stem = os.path.splitext(os.path.basename(path))[0]
    name = ((_mem_frontmatter(text) or {}).get("name") or stem.replace("_", "-")).strip()
    return os.path.join(os.path.dirname(path), stem + "_archive.md"), name + "-archive", name


def _mem_intro_end(lines):
    """Index of the first line after the frontmatter and the first
    paragraph (plus any headings above it) -- that part never moves."""
    i = 0
    if lines and lines[0].strip() == "---":
        for j in range(1, len(lines)):
            if lines[j].strip() == "---":
                i = j + 1
                break
    while i < len(lines) and (not lines[i].strip() or lines[i].startswith("#")):
        i += 1
    while i < len(lines) and lines[i].strip():
        i += 1
    return i


def _mem_pieces(lines, start):
    """Pure: lines[start:] cut into pieces, in order -- {kind: heading /
    head / child, unit, a, b (line range, b exclusive), indent}. A unit is
    one top-level entry (a list item or paragraph at column 0); its
    first-level nested list items are child pieces of their own, so the
    older ones of a long-running entry can move without it."""
    pieces, unit, cur, child_indent = [], -1, None, None
    for i in range(start, len(lines)):
        ln = lines[i]
        if not ln.strip():
            if cur is not None:
                cur["b"] = i + 1
            continue
        indent = len(ln) - len(ln.lstrip(" \t"))
        item = _MEM_LIST_ITEM.match(ln)
        if indent == 0 and ln.startswith("#"):
            unit += 1
            pieces.append({"kind": "heading", "unit": unit, "a": i, "b": i + 1, "indent": 0})
            cur, child_indent = None, None
            continue
        if indent == 0:
            prev = lines[i - 1]
            if (cur is not None and cur["kind"] == "head" and cur["b"] == i and prev.strip()
                    and not item and not _MEM_LIST_ITEM.match(prev)):
                cur["b"] = i + 1            # the same paragraph goes on
                continue
            unit += 1
            cur = {"kind": "head", "unit": unit, "a": i, "b": i + 1, "indent": 0}
            pieces.append(cur)
            child_indent = None
            continue
        if cur is None:                     # indented text before any entry
            unit += 1
            cur = {"kind": "head", "unit": unit, "a": i, "b": i + 1, "indent": indent}
            pieces.append(cur)
            continue
        if item and (child_indent is None or indent <= child_indent):
            child_indent = indent if child_indent is None else child_indent
            cur = {"kind": "child", "unit": cur["unit"], "a": i, "b": i + 1, "indent": indent}
            pieces.append(cur)
            continue
        cur["b"] = i + 1                    # deeper lines belong to the piece above
    return pieces


def _mem_archive_split(text, path, existing_archive=None, today=""):
    """Pure: what ARCHIVE OLD would do to the topic file `text` at `path`.
    None when nothing (worth it) can move; else {kept, archive_text,
    archive_path, archive_name, moved: [{line, title, tokens}],
    moved_tokens, link_added}. existing_archive = that file's text when it
    is already there (the new batch goes at its end: it is newer)."""
    if _mem_is_archive(path):
        return None
    text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    pieces = _mem_pieces(lines, _mem_intro_end(lines))
    for p in pieces:
        t = "\n".join(lines[p["a"]:p["b"]])
        p["tokens"], p["dated"], p["resume"] = _ctx_tokens(len(t)), bool(_MEM_DATE.search(t)), bool(_MEM_RESUME.search(t))
    first_dated = next((k for k, p in enumerate(pieces) if p["dated"]), None)
    if first_dated is None:
        return None
    # the newest history stays: walk back from the end until enough is kept
    cut, kept_tok, kept_dated = 0, 0, 0
    for k in range(len(pieces) - 1, -1, -1):
        kept_tok += pieces[k]["tokens"]
        kept_dated += pieces[k]["dated"]
        if kept_tok >= _MEM_ARCHIVE_KEEP_TOKENS and kept_dated >= _MEM_ARCHIVE_MIN_KEEP:
            cut = k
            break
    move = [first_dated <= k < cut and p["kind"] != "heading" and not p["resume"] for k, p in enumerate(pieces)]
    # an entry's own lines stay while any of its nested items stays
    for k, p in enumerate(pieces):
        if p["kind"] == "head" and move[k] and any(
                not move[j] for j, q in enumerate(pieces) if q["kind"] == "child" and q["unit"] == p["unit"]):
            move[k] = False
    moved = [p for k, p in enumerate(pieces) if move[k]]
    moved_tokens = sum(p["tokens"] for p in moved)
    if not moved or moved_tokens < _MEM_ARCHIVE_MIN_MOVE:
        return None

    archive_path, archive_name, own_name = _mem_archive_target(path, text)
    gone = {i for p in moved for i in range(p["a"], p["b"])}
    kept_lines = [ln for i, ln in enumerate(lines) if i not in gone]
    link_added = f"[[{archive_name}]]".lower() not in text.lower()
    if link_added:
        ins = _mem_intro_end(kept_lines)
        kept_lines[ins:ins] = ["", f"Older entries: [[{archive_name}]] ({os.path.basename(archive_path)}, moved there by AOC)."]
    kept = re.sub(r"\n{3,}", "\n\n", "\n".join(kept_lines))

    # the batch, oldest first, under the heading each entry sat under; a
    # nested item whose entry stays behind is lifted to the top level
    heads_moved = {p["unit"] for p in moved if p["kind"] == "head"}
    out = [f"## Moved from {os.path.basename(path)}" + (f" on {today}" if today else ""), ""]
    last_heading, prev = None, None
    for p in moved:
        heading = next((q for q in reversed(pieces) if q["kind"] == "heading" and q["a"] < p["a"]), None)
        if heading is not None and heading is not last_heading:
            h = lines[heading["a"]]
            out += ["", h if h.startswith("######") else "#" + h, ""]
            last_heading = heading
        elif prev is not None and (prev["b"] != p["a"] or not lines[prev["b"] - 1].strip()) and out[-1] != "":
            out.append("")   # a gap in the source (or a blank line) stays a blank line
        chunk = lines[p["a"]:p["b"]]
        while chunk and not chunk[-1].strip():
            chunk.pop()
        if p["kind"] == "child" and p["unit"] not in heads_moved:
            chunk = [ln[min(p["indent"], len(ln) - len(ln.lstrip(" \t"))):] for ln in chunk]
        out += chunk
        prev = p
    batch = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip("\n") + "\n"
    if existing_archive and existing_archive.strip():
        archive_text = existing_archive.replace("\r\n", "\n").rstrip("\n") + "\n\n" + batch
    else:
        mtype = "project"
        fm = re.match(r"^---\n(.*?)\n---", text, re.S)
        m = fm and re.search(r"^\s*type:\s*([\w-]+)", fm.group(1), re.M)
        if m:
            mtype = m.group(1)
        archive_text = (f"---\nname: {archive_name}\ndescription: Older history moved out of {os.path.basename(path)} "
                        f"by AOC -- open only when you need past detail\nmetadata:\n  type: {mtype}\n---\n\n"
                        f"Older entries of [[{own_name}]], oldest first. The current state is in "
                        f"{os.path.basename(path)}.\n\n" + batch)
    return {"kept": kept, "archive_text": archive_text, "archive_path": archive_path, "archive_name": archive_name,
            "moved": [{"line": p["a"] + 1, "title": lines[p["a"]].strip()[:140], "tokens": p["tokens"]} for p in moved],
            "moved_tokens": moved_tokens, "link_added": link_added}


def _mem_archive_record(path):
    """Where the last ARCHIVE OLD of `path` notes what UNDO SAVE must put
    back as well (not named with the backup prefix: it isn't a backup)."""
    return os.path.join(_MEM_BACKUP_DIR, "archive-" + _mem_backup_prefix(path) + "last.json")


def _mem_archive_current(f):
    """(archive path, its text or None, its mtime or None) for a topic file."""
    apath = _mem_archive_target(f["path"], f["text"])[0]
    if not os.path.isfile(apath):
        return apath, None, None
    return apath, _mem_read(apath), os.stat(apath).st_mtime


def _memory_archive_preview(path):
    """GET /memory_archive: what ARCHIVE OLD would move, and the diff."""
    import difflib
    f = _mem_known(path)
    if not f or f["kind"] != "memory":
        return {"ok": False, "error": "Only memory topic files can be archived."}
    apath, atext, amtime = _mem_archive_current(f)
    sp = _mem_archive_split(f["text"], f["path"], atext, datetime.now().strftime("%Y-%m-%d"))
    if not sp:
        return {"ok": False, "error": "Nothing old enough to move: no dated history beyond the newest entries."}
    name = os.path.basename(f["path"])
    diff = "".join(difflib.unified_diff(f["text"].splitlines(True), sp["kept"].splitlines(True),
                                        fromfile=name, tofile=name + " (after)", n=1))
    return {"ok": True, "path": f["path"], "mtime": f["mtime"], "archive_path": apath,
            "archive_exists": atext is not None, "archive_mtime": amtime, "moved": sp["moved"],
            "moved_tokens": sp["moved_tokens"], "before_tokens": _ctx_tokens(len(f["text"])),
            "after_tokens": _ctx_tokens(len(sp["kept"])), "link_added": sp["link_added"],
            "diff": diff[:400000], "diff_cut": len(diff) > 400000}


def _memory_archive_apply(path, mtime, archive_mtime):
    """POST /memory_archive: back both files up, write the archive, then the
    trimmed topic file. Refused when either changed since the preview."""
    f = _mem_known(path)
    if not f or f["kind"] != "memory":
        return {"ok": False, "error": "Only memory topic files can be archived."}
    apath, atext, amtime = _mem_archive_current(f)
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    if (not num(mtime) or abs(f["mtime"] - mtime) > 0.001 or (amtime is None) != (archive_mtime is None)
            or (amtime is not None and (not num(archive_mtime) or abs(amtime - archive_mtime) > 0.001))):
        return {"ok": False, "error": "changed", "detail": "The file or its archive changed on disk since the "
                "preview (Claude may have just written it). Open the preview again."}
    sp = _mem_archive_split(f["text"], f["path"], atext, datetime.now().strftime("%Y-%m-%d"))
    if not sp:
        return {"ok": False, "error": "Nothing old enough to move any more."}
    topic_bak = _mem_backup(f["path"])
    archive_bak = _mem_backup(apath) if atext is not None else None
    _mem_write_text(apath, sp["archive_text"])
    _mem_write_text(f["path"], sp["kept"])
    with open(_mem_archive_record(f["path"]), "w", encoding="utf-8") as fh:
        json.dump({"topic_backup": topic_bak, "archive_path": apath, "archive_backup": archive_bak}, fh)
    return {"ok": True, "mtime": os.stat(f["path"]).st_mtime, "moved": len(sp["moved"]),
            "moved_tokens": sp["moved_tokens"], "archive_path": apath, "archive_name": sp["archive_name"]}


# ── Failure map (History -> FAILURES) ─────────────────────────────────────
# Every tool call that came back as an error, across the transcripts of the
# last N days (main sessions and their subagents), grouped by tool, what it
# ran (the program for shell commands) and a readable reason. What a failure
# lost: the failed call's own run time plus Claude's next turn reacting to
# it (time and that API call's cost, shared among the results it answered).
# Permission denials (you, a hook or the auto-mode classifier said no) are
# counted apart -- they're decisions, not things that broke.
_FAIL_CACHE_S = 15 * 60
_FAIL_GAP_CAP_S = 600          # an idle gap (you walked away) isn't time lost to the failure
_FAIL_TOOL_ID = re.compile(rb'"id":\s*"(toolu_[A-Za-z0-9_-]+)"')
_FAIL_RESULT_ID = re.compile(rb'"tool_use_id":\s*"(toolu_[A-Za-z0-9_-]+)"')
_FAIL_IS_ERROR = re.compile(rb'"is_error":\s*true')
_FAIL_ASSISTANT = re.compile(rb'"type":\s*"assistant"')
_FAIL_SHELLS = ("Bash", "PowerShell")
_FAIL_TRIVIAL = {"cd", "set-location", "pushd", "popd", "export", "set", "echo", "sleep", "start-sleep", "cat",
                 "ls", "pwd", "true", "mkdir", "write-output", "write-host", "$env"}
_FAIL_SUBCMD = {"git", "npm", "npx", "pnpm", "yarn", "gh", "pip", "pip3", "docker", "cargo", "dotnet", "go",
                "vercel", "supabase", "uv", "poetry", "kubectl", "winget", "choco", "claude"}
# (substring, reason, kind) -- first match wins; kind 'denied' = a no, not a breakage
_FAIL_REASONS = (
    ("doesn't want to proceed", "You rejected the call", "denied"),
    ("user rejected", "You rejected the call", "denied"),
    ("Permission for this action was denied", "Denied by auto mode", "denied"),
    ("auto mode classifier gave no verdict", "Auto-mode check unavailable", "denied"),
    ("so auto mode cannot de", "Auto-mode check unavailable", "denied"),
    ("hook error", "Blocked by a hook", "denied"),
    ("blocked by hook", "Blocked by a hook", "denied"),
    ("File has not been read yet", "Edited before reading the file", "failed"),
    ("String to replace not found", "Text to replace not found", "failed"),
    ("File has been modified since read", "File changed since it was read", "failed"),
    ("No changes to make", "Edit with no change", "failed"),
    ("uv_spawn", "Program not found", "failed"),
    ("unexpected EOF while looking for matching", "Shell quoting error", "failed"),
    ("syntax error near unexpected token", "Shell quoting error", "failed"),
    ("File does not exist", "File does not exist", "failed"),
    ("Path does not exist", "File does not exist", "failed"),
    ("no such file or directory", "File does not exist", "failed"),
    ("InputValidationError", "Invalid tool input", "failed"),
    ("Insufficient scope", "Missing OAuth scope", "failed"),
    ("Executable not found", "Program not found", "failed"),
    ("command not found", "Program not found", "failed"),
    ("is not recognized as", "Program not found", "failed"),
    ("ripgrep rejected the pattern", "Bad search pattern", "failed"),
    ("<tool_use_error>Blocked:", "Blocked by Claude Code", "failed"),
    ("Command timed out", "Timed out", "failed"),
    ("timed out after", "Timed out", "failed"),
)


def _fail_reason(tool, text):
    """Pure: (tool name, error result text) -> (reason, kind, first useful line)."""
    text = str(text or "")
    body = re.sub(r"</?tool_use_error>", "", text).strip()
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    m = re.match(r"Exit code (\d+)", lines[0]) if lines else None
    # a failed command's cause is usually its last line (a traceback's exception)
    detail = lines[-1] if m and len(lines) > 1 else (lines[0] if lines else "")
    low = text.lower()
    for needle, reason, kind in _FAIL_REASONS:
        if needle.lower() in low:
            return reason, kind, detail[:300]
    exc = None
    if "Traceback (most recent call last)" in text:
        exc = next((re.match(r"([A-Za-z_][\w.]*(?:Error|Exception|Interrupt|Exit))\b", ln)
                    for ln in reversed(lines) if re.match(r"[A-Za-z_][\w.]*(?:Error|Exception|Interrupt|Exit)\b", ln)), None)
    if exc:
        return f"Python {exc.group(1).split('.')[-1]}", "failed", detail[:300]
    if m:
        return f"Exit code {m.group(1)}", "failed", detail[:300]
    if not lines:
        return "Error with no message", "failed", ""
    first = re.sub(r"[A-Za-z]:\\[^\s'\"]*|/[\w.-]+(?:/[\w.-]+)+", "<path>", lines[0])
    first = re.sub(r"\d+", "N", first)
    return first[:80], "failed", lines[0][:300]


def _fail_target(tool, inp):
    """Pure: what a call ran -- 'git push', 'python build.py', 'npm test' for
    shell commands: the last real command of a chain (a chain stops at the
    one that failed; `cd`, `cat > f`, `echo` and pipe filters skipped,
    heredoc bodies ignored), else ''."""
    if tool not in _FAIL_SHELLS or not isinstance(inp, dict):
        return ""
    cmd = str(inp.get("command") or "").strip()
    cmd = re.sub(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2\s*(?=\n|$)",
                 lambda mm: mm.group(0).split("\n", 1)[0], cmd, flags=re.S)   # keep the line, drop the body
    for seg in reversed(re.split(r"&&|\|\||;|\n", cmd)):
        words = re.findall(r'"[^"]*"|\'[^\']*\'|[^\s"\']+', seg.split("|", 1)[0].strip())   # "C:\Program Files\x.exe" stays one word
        while words and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]) or words[0] == "&"):   # FOO=1 cmd, & "x.exe"
            words.pop(0)
        if not words or words[0].lower() in _FAIL_TRIVIAL or words[0].startswith(("#", "$", "(", "{", "}")):
            continue
        head = re.split(r"[\\/]", words[0].strip("\"'&"))[-1].lower()
        head = re.sub(r"\.(exe|cmd|bat)$", "", head)
        if not re.match(r"^[\w.+-]+$", head):
            continue
        rest = [w for w in words[1:] if not w.startswith("-")]
        if head in _FAIL_SUBCMD and rest:
            return f"{head} {rest[0].lower()}"[:60]
        if head.startswith("python") or head in ("node", "py", "powershell", "pwsh", "bash", "sh"):
            script = next((w for w in rest if re.search(r"\.(py|js|mjs|ts|ps1|sh)$", w, re.I)), "")
            name = re.split(r"[\\/]", script.strip("\"'"))[-1] if script else ""
            return (f"{head} {name}" if name else head)[:60]
        return head[:60]
    return ""


def _fail_scan_lines(lines, sid, label=""):
    """Pure: one transcript's raw lines -> {"calls": tool calls, "fails": [...]}.
    A tool_use line is kept as raw bytes until its result arrives, and only
    parsed when that result is an error -- most lines are never JSON-parsed."""
    pending = {}            # toolu id -> raw tool_use line
    calls = 0
    fails = []
    waiting = []            # failures from the last results line, until Claude's next call
    for raw in lines:
        if b'"tool_use_id"' in raw:
            ids = _FAIL_RESULT_ID.findall(raw)
            if not _FAIL_IS_ERROR.search(raw):
                for i in ids:
                    pending.pop(i, None)
                continue
            try:
                o = json.loads(raw)
            except (TypeError, ValueError):
                continue
            t = _replay_epoch(o.get("timestamp"))
            content = (o.get("message") or {}).get("content")
            results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"] \
                if isinstance(content, list) else []
            batch = []
            for r in results:
                use_raw = pending.pop(str(r.get("tool_use_id") or "").encode(), None)
                if not r.get("is_error"):
                    continue
                tool, inp, t0 = "?", {}, None
                if use_raw is not None:
                    try:
                        u = json.loads(use_raw)
                        t0 = _replay_epoch(u.get("timestamp"))
                        for b in (u.get("message") or {}).get("content") or []:
                            if isinstance(b, dict) and b.get("type") == "tool_use" and \
                                    str(b.get("id")) == str(r.get("tool_use_id")):
                                tool, inp = str(b.get("name") or "?"), b.get("input") or {}
                    except (TypeError, ValueError):
                        pass
                reason, kind, detail = _fail_reason(tool, _replay_result_text(r.get("content")))
                f = {"sid": sid, "label": label, "t": t, "tool": tool, "target": _fail_target(tool, inp),
                     "reason": reason, "kind": kind, "detail": detail, "secs": 0.0, "cost": 0.0, "share": 0}
                if kind == "denied":   # the permission advisor needs what was denied
                    f["input"] = inp if isinstance(inp, dict) else {}
                    f["use_id"] = str(r.get("tool_use_id") or "")
                if t and t0:
                    f["secs"] = min(_FAIL_GAP_CAP_S, max(0.0, t - t0))
                batch.append(f)
            for f in batch:
                f["share"] = 1 / max(1, len(results))
            fails.extend(batch)
            waiting = batch
            continue
        if not _FAIL_ASSISTANT.search(raw):
            continue
        ids = _FAIL_TOOL_ID.findall(raw) if b'"tool_use"' in raw else []
        for i in ids:
            pending[i] = raw
        calls += len(ids)
        if waiting and b'"usage"' in raw:   # Claude's next call, reacting to the failures
            try:
                o = json.loads(raw)
            except (TypeError, ValueError):
                continue
            msg = o.get("message") or {}
            u = msg.get("usage") or {}
            cost = _calc_cost(u.get("input_tokens", 0) or 0, u.get("output_tokens", 0) or 0,
                              u.get("cache_creation_input_tokens", 0) or 0, u.get("cache_read_input_tokens", 0) or 0,
                              msg.get("model") or "", ((u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") or 0))
            t = _replay_epoch(o.get("timestamp"))
            for f in waiting:
                f["cost"] = cost * f["share"]
                if t and f["t"]:
                    f["secs"] += min(_FAIL_GAP_CAP_S, max(0.0, t - f["t"]))
            waiting = []
    return {"calls": calls, "fails": fails}


def _fail_report(scans, days) -> dict:
    """Pure: per-transcript scans -> the FAILURES tab."""
    calls = sum(s["calls"] for s in scans)
    fails = [f for s in scans for f in s["fails"]]
    broke = [f for f in fails if f["kind"] == "failed"]
    denied = [f for f in fails if f["kind"] == "denied"]
    month = 30 / max(1, days)

    def group(items, keyf):
        out = {}
        for f in items:
            k = keyf(f)
            g = out.get(k)
            if g is None:
                g = out[k] = {"tool": f["tool"], "target": f["target"], "reason": f["reason"], "count": 0,
                              "secs": 0.0, "cost": 0.0, "sessions": set(), "last": None}
            g["count"] += 1
            g["secs"] += f["secs"]
            g["cost"] += f["cost"]
            g["sessions"].add(f["sid"])
            if g["last"] is None or (f["t"] or 0) >= (g["last"]["t"] or 0):
                g["last"] = f
        rows = []
        for g in out.values():
            last = g.pop("last")
            g.update(sessions=len(g["sessions"]), secs=round(g["secs"]), cost=round(g["cost"], 2),
                     last_sid=last["sid"], last_label=last["label"], last_t=last["t"], example=last["detail"])
            rows.append(g)
        rows.sort(key=lambda g: (-g["count"], -g["cost"]))
        return rows

    top = group(broke, lambda f: (f["tool"], f["target"], f["reason"]))
    by_tool = group(broke, lambda f: f["tool"])
    by_reason = group(broke, lambda f: f["reason"])
    den = group(denied, lambda f: f["reason"])
    tips = []
    for g in top[:12]:
        hint = _FAIL_HINTS.get(g["reason"])
        if hint and g["count"] >= 3:
            tips.append({"reason": g["reason"], "tool": g["tool"], "target": g["target"], "count": g["count"],
                         "detail": hint, "cost_month": round(g["cost"] * month, 2),
                         **({"fix": _FIX_FOR_REASON[g["reason"]]} if g["reason"] in _FIX_FOR_REASON else {})})
    seen = set()
    tips = [t for t in tips if not (t["detail"] in seen or seen.add(t["detail"]))][:5]
    return {
        "days": days, "calls": calls, "failed": len(broke), "denied": len(denied),
        "rate": round(len(broke) / calls * 100, 1) if calls else 0,
        "secs": round(sum(f["secs"] for f in broke)), "cost": round(sum(f["cost"] for f in broke), 2),
        "sessions": len({f["sid"] for f in broke}),
        "top": top[:25], "by_tool": by_tool[:12], "by_reason": by_reason[:12], "denied_rows": den[:8],
        "tips": tips,
    }


_FAIL_HINTS = {
    "Edited before reading the file": "Claude tried to edit files it hadn't read in this session. Usually after a "
                                      "compaction or a subagent's edit -- nothing to fix on your side, but frequent "
                                      "cases mean long sessions; /clear between tasks helps.",
    "Text to replace not found": "Edits whose old text didn't match the file (line endings, a file changed by "
                                 "something else). On Windows, CRLF files are a common cause.",
    "File does not exist": "Calls on paths that weren't there -- often the wrong working folder. Starting Claude "
                           "in the project folder instead of your home folder avoids most of them.",
    "Program not found": "A command needs a program that isn't on PATH for Claude's shell. Install it or note "
                         "the full path in CLAUDE.md.",
    "Missing OAuth scope": "A connector lacks a permission. Reconnect it in claude.ai settings and grant the "
                           "scope it asks for.",
    "Invalid tool input": "Claude called a tool with the wrong arguments -- mostly harmless retries.",
    "Bad search pattern": "Searches with a regex ripgrep rejected; Claude retries with a fixed pattern.",
    "Timed out": "Commands that hit their time limit. Long builds or tests are better run in the background.",
    "Shell quoting error": "Long commands with heredocs or nested quotes that the shell couldn't parse. Writing the "
                           "script to a file first (and running the file) avoids them.",
    "Python UnicodeEncodeError": "Python printed text the Windows console code page can't show. Setting "
                                 "PYTHONIOENCODING=utf-8 (user environment variable) fixes it for every session.",
    "Python UnicodeDecodeError": "Python read a file with the Windows default code page. Open files with "
                                 "encoding='utf-8' -- worth a line in CLAUDE.md.",
    "Python ModuleNotFoundError": "Scripts import packages that aren't installed for the Python Claude runs. Note "
                                  "the right interpreter / venv in CLAUDE.md.",
}


def _fail_files(days, now):
    base = os.path.join(os.path.expanduser("~"), ".claude", "projects")
    since = now - days * 86400
    for pattern in ("*/*.jsonl", "*/*/subagents/*.jsonl"):
        for f in glob.glob(os.path.join(base, pattern)):
            try:
                if os.path.getmtime(f) < since:
                    continue
            except OSError:
                continue
            parts = os.path.normpath(f).split(os.sep)
            sid = parts[-3] if parts[-2] == "subagents" else os.path.splitext(parts[-1])[0]
            yield f, sid


def _failure_map(days, now=None):
    """GET /failure_map: scan the last `days` of transcripts (cached 15 min)."""
    now = now or time.time()
    hit = _fail_cache.get(days)
    if hit and now - hit[0] < _FAIL_CACHE_S:
        return hit[1]
    report = _fail_report(_fail_scans(days, now), days)
    report["computed_at"] = now
    _fail_cache[days] = (now, report)
    return report


def _session_titles() -> dict:
    """{session_id: title or project} from the transcript index ({} if none)."""
    if not os.path.exists(TRANSCRIPT_INDEX_DB):
        return {}
    conn = None
    try:
        conn = _transcript_index_connect()
        return dict(conn.execute("SELECT session_id, COALESCE(NULLIF(title, ''), project, '') FROM sessions_meta").fetchall())
    except Exception as e:
        _log_bg_error("session titles", e)
        return {}
    finally:
        if conn is not None:
            conn.close()


def _fail_scans(days, now):
    """Per-transcript scans of the last `days` (cached 15 min; FAILURES and
    PERMISSIONS share them)."""
    hit = _fail_scan_cache.get(days)
    if hit and now - hit[0] < _FAIL_CACHE_S:
        return hit[1]
    titles = _session_titles()
    scans = []
    for f, sid in _fail_files(days, now):
        try:
            with open(f, "rb") as fh:
                scans.append(_fail_scan_lines(fh, sid, titles.get(sid, "")))
        except OSError:
            continue
    since = now - days * 86400
    for s in scans:
        s["fails"] = [x for x in s["fails"] if not x["t"] or x["t"] >= since]
    _fail_scan_cache[days] = (now, scans)
    return scans


_fail_cache = {}
_fail_scan_cache = {}


# ── System monitor (COMMAND view) ─────────────────────────────────────────
# CPU / RAM / system-drive use for the Command Center's rings, from the OS
# directly (no psutil needed): CPU is the busy share of the time between
# two samples, so the first call takes a short second sample.
_sysinfo_prev = {"cpu": None}
_sysinfo_lock = threading.Lock()


def _cpu_times():
    """(idle, total) CPU time since boot in OS ticks, None if unknown."""
    import sys
    if sys.platform == "win32":
        import ctypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("lo", ctypes.c_uint32), ("hi", ctypes.c_uint32)]
        idle, kern, user = FILETIME(), FILETIME(), FILETIME()
        if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
            return None
        val = lambda t: (t.hi << 32) | t.lo
        return val(idle), val(kern) + val(user)   # kernel time includes idle time
    try:
        with open("/proc/stat", encoding="ascii") as fh:
            parts = [int(x) for x in fh.readline().split()[1:]]
        return parts[3] + (parts[4] if len(parts) > 4 else 0), sum(parts)
    except (OSError, ValueError, IndexError):
        return None


def _cpu_pct(prev, cur):
    """Pure: two (idle, total) samples -> busy % in between, None if unusable."""
    if not prev or not cur or cur[1] <= prev[1]:
        return None
    return round(max(0.0, min(100.0, 100.0 * (1 - (cur[0] - prev[0]) / (cur[1] - prev[1])))), 1)


def _mem_pct():
    """(used %, total bytes) of physical memory, (None, None) if unknown."""
    import sys
    if sys.platform == "win32":
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
                        ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                        ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                        ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                        ("ullAvailExtendedVirtual", ctypes.c_uint64)]
        m = MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(m)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)) or not m.ullTotalPhys:
            return None, None
        return round(100.0 * (1 - m.ullAvailPhys / m.ullTotalPhys), 1), m.ullTotalPhys
    try:
        info = {}
        with open("/proc/meminfo", encoding="ascii") as fh:
            for ln in fh:
                k, _, v = ln.partition(":")
                info[k] = int(v.split()[0]) * 1024
        return round(100.0 * (1 - info["MemAvailable"] / info["MemTotal"]), 1), info["MemTotal"]
    except (OSError, ValueError, KeyError, ZeroDivisionError):
        return None, None


def _sysinfo():
    """GET /sysinfo -> {"cpu", "ram", "ram_total", "disk", "disk_total", "disk_path"} (% values, None if unknown)."""
    import shutil
    with _sysinfo_lock:
        cur = _cpu_times()
        prev = _sysinfo_prev["cpu"]
        if cur and (not prev or time.monotonic() - prev[2] > 30):   # no usable earlier sample: take one now
            time.sleep(0.25)
            prev, cur = (cur[0], cur[1], 0), _cpu_times()
        cpu = _cpu_pct(prev[:2] if prev else None, cur)
        if cur:
            _sysinfo_prev["cpu"] = (cur[0], cur[1], time.monotonic())
    ram, ram_total = _mem_pct()
    drive = (os.environ.get("SystemDrive", "C:") + "\\") if os.name == "nt" else "/"
    try:
        du = shutil.disk_usage(drive)
        disk, disk_total = round(100.0 * du.used / du.total, 1), du.total
    except OSError:
        disk = disk_total = None
    try:
        host = _socket.gethostname()
    except OSError:
        host = ""
    return {"cpu": cpu, "ram": ram, "ram_total": ram_total, "disk": disk, "disk_total": disk_total, "disk_path": drive,
            "host": host}
