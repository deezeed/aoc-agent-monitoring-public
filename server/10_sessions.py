# AOC server, part 10: session changes, change review, launch, worktrees, background agents.
# monitor.py exec()s server/NN_*.py in number order into ONE shared
# namespace (its own) -- not an importable module: names from earlier
# parts are plain globals here, and __file__ is monitor.py.
# ── Session changes: uncommitted edits of the files a session touched ─────
# Only files the session itself edited (from its transcript, see
# _activity_track's "edited") are ever looked at, so the endpoint can't be
# used to read anything else on disk. Grouped by git repo.


def _git_out(args, cwd, timeout=8):
    """git output decoded as UTF-8 (paths like C:/Users/x/Počítač survive;
    _run would decode with the ANSI code page). None on failure."""
    try:
        r = subprocess.run(["git", "-c", "core.quotepath=off"] + args, cwd=cwd, capture_output=True,
                           timeout=timeout, creationflags=_NO_WINDOW)
    except Exception:
        return None
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None


def _parse_porcelain_z(out):
    """Pure: `git status --porcelain=v1 -z` -> {repo-relative path: XY code}."""
    res, parts, i = {}, (out or "").split("\0"), 0
    while i < len(parts):
        e = parts[i]
        i += 1
        if len(e) < 4:
            continue
        code, path = e[:2], e[3:]
        if "R" in code or "C" in code:
            i += 1  # the rename source follows as its own entry
        res[path] = code
    return res


def _parse_numstat(out):
    """Pure: `git diff --numstat` -> {path: (ins, dels)}; binary -> (None, None)."""
    res = {}
    for line in (out or "").splitlines():
        p = line.split("\t", 2)
        if len(p) == 3:
            res[p[2]] = (int(p[0]) if p[0].isdigit() else None, int(p[1]) if p[1].isdigit() else None)
    return res


def _change_kind(code):
    """Pure: porcelain XY -> added | deleted | renamed | untracked | modified."""
    if code == "??":
        return "untracked"
    for ch, kind in (("D", "deleted"), ("A", "added"), ("R", "renamed")):
        if ch in code:
            return kind
    return "modified"


def _git_root_for(path, cache):
    d = os.path.dirname(path)
    while d and not os.path.isdir(d):
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    if not d:
        return None
    if d not in cache:
        out = _git_out(["rev-parse", "--show-toplevel"], d)
        # realpath: a short 8.3 temp path (C:\Users\RUNNER~1) and git's long one must compare equal
        cache[d] = os.path.realpath(out.strip()) if out and out.strip() else None
    return cache[d]


def _session_edited(sid):
    with _transcript_cursors_lock:
        cur = _transcript_cursors.get(sid) or {}
        return list(((cur.get("stats") or {}).get("activity") or {}).get("edited") or [])


def _sessions_activity(since):
    """{session_id: {"minutes": [[epoch_min, lines], ...] since `since`, oldest
    first, "edited": [paths]}} for every transcript the scanner follows --
    what the Timeline / Heat / Graph views draw when no agent ran."""
    out = {}
    with _transcript_cursors_lock:
        for sid, cur in _transcript_cursors.items():
            act = (cur.get("stats") or {}).get("activity") or {}
            mins = sorted([m, n] for m, n in (act.get("minutes") or {}).items() if m >= since)
            if mins:
                out[sid] = {"minutes": mins, "edited": list(act.get("edited") or [])}
    return out


def _session_changes(files):
    """Uncommitted changes in `files` (absolute paths) -> {"repos": [{root,
    name, branch, files: [{path, rel, kind, ins, dels}]}], "clean", "outside"}."""
    cache, by_root, outside = {}, {}, 0
    for f in files:
        root = _git_root_for(f, cache)
        if not root:
            outside += 1
            continue
        by_root.setdefault(root, []).append(f)
    repos, clean = [], 0
    for root, fs in by_root.items():
        rels = {}
        for f in fs:
            try:
                rels[os.path.relpath(os.path.realpath(f), root).replace("\\", "/")] = f
            except ValueError:  # other drive
                outside += 1
        if not rels:
            continue
        status = _parse_porcelain_z(_git_out(["status", "--porcelain=v1", "-z", "--untracked-files=all", "--"]
                                             + list(rels), root) or "")
        nums = _parse_numstat(_git_out(["diff", "HEAD", "--numstat", "--"] + list(rels), root) or "")
        key = {os.path.normcase(r): r for r in rels}
        changed = []
        for rel, code in status.items():
            own = key.get(os.path.normcase(rel))
            if not own:
                continue
            ins, dels = nums.get(rel, (None, None))
            if code == "??":
                try:
                    with open(rels[own], "rb") as fh:
                        ins, dels = fh.read(2_000_000).count(b"\n"), 0
                except OSError:
                    pass
            changed.append({"path": rels[own], "rel": rel, "kind": _change_kind(code), "ins": ins, "dels": dels})
        clean += len(rels) - len(changed)
        if changed:
            changed.sort(key=lambda x: x["rel"])
            branch = (_git_out(["rev-parse", "--abbrev-ref", "HEAD"], root) or "").strip()
            repos.append({"root": root, "name": os.path.basename(root), "branch": branch, "files": changed})
    repos.sort(key=lambda r: r["name"].lower())
    return {"repos": repos, "clean": clean, "outside": outside}


def _session_file_diff(path):
    """Unified diff of one file vs HEAD (untracked -> whole file as added)."""
    root = _git_root_for(path, {})
    if not root:
        return ""
    rel = os.path.relpath(os.path.realpath(path), root).replace("\\", "/")
    out = _git_out(["diff", "HEAD", "--", rel], root) or ""
    if not out.strip() and os.path.isfile(path):
        if (_git_out(["status", "--porcelain=v1", "-z", "--", rel], root) or "").startswith("??"):
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.read(500_000).splitlines()
            out = f"--- /dev/null\n+++ b/{rel}\n@@ -0,0 +1,{len(lines)} @@\n" + "\n".join("+" + l for l in lines)
    return out[:1_000_000]


# ── Change review: mark files seen, discard one (with undo), stage the seen
# ones. Only files the session itself edited (the caller resolves the path
# against _session_edited first), only from this PC. A discard first copies
# the file into %LOCALAPPDATA%\AOC\change_backups\<id>\ (plus meta.json),
# so it can always be put back: tracked files are restored from HEAD with
# `git restore`, new files are moved into the backup -- never deleted.
_CHANGE_BACKUP_DIR = os.path.join(AOC_DATA_DIR, "change_backups")
_CHANGE_BACKUPS_KEEP = 50
_CHANGE_REVIEW_FILE = os.path.join(AOC_DATA_DIR, "change_review.json")
_CHANGE_LOCK = threading.Lock()


def _change_own(edited, path):
    """Pure: `path` as the session recorded it, if it is one of its edited
    files (case/slash-insensitive); None otherwise -- so ../ tricks or any
    other path on disk are refused."""
    want = os.path.normcase(os.path.normpath(str(path or "")))
    if not path or not want:
        return None
    return next((f for f in edited if os.path.normcase(os.path.normpath(f)) == want), None)


def _file_sig(path):
    """'size:mtime_ns' of a file, '' when missing -- what a 'seen' mark is tied to."""
    try:
        st = os.stat(path)
        return f"{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        return ""


def _file_hash(path):
    import hashlib
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return ""


def _change_review_load(path=None):
    d = _load_json_file(path or _CHANGE_REVIEW_FILE, {})
    return d if isinstance(d, dict) else {}


def _change_seen_set(sid, file, seen, path=None, now=None):
    """Mark (or unmark) a file seen for a session. The mark holds only while
    the file is unchanged since (its size+mtime)."""
    path = path or _CHANGE_REVIEW_FILE
    d = _change_review_load(path)
    s = d.setdefault(sid, {"files": {}, "at": 0})
    if seen:
        s["files"][os.path.normcase(os.path.normpath(file))] = _file_sig(file)
    else:
        s["files"].pop(os.path.normcase(os.path.normpath(file)), None)
    s["at"] = now or time.time()
    if len(d) > 200:   # forget the oldest sessions
        for k in sorted(d, key=lambda k: d[k].get("at") or 0)[:len(d) - 200]:
            d.pop(k, None)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    os.replace(tmp, path)
    return {"ok": True, "seen": bool(seen)}


def _change_seen_marks(sid, files, path=None):
    """{path: True} for each of `files` marked seen and unchanged since."""
    marks = (_change_review_load(path).get(sid) or {}).get("files") or {}
    out = {}
    for f in files:
        sig = marks.get(os.path.normcase(os.path.normpath(f)))
        if sig is not None and sig == _file_sig(f):
            out[f] = True
    return out


def _change_status(path):
    """(root, rel, porcelain code) of a file -- code '' when unchanged."""
    root = _git_root_for(path, {})
    if not root:
        return None, None, ""
    rel = os.path.relpath(os.path.realpath(path), root).replace("\\", "/")
    st = _parse_porcelain_z(_git_out(["status", "--porcelain=v1", "-z", "--untracked-files=all", "--", rel], root) or "")
    code = next((v for k, v in st.items() if os.path.normcase(k) == os.path.normcase(rel)), "")
    return root, rel, code


def _change_backups(sid=None, bdir=None):
    """Discards (newest first), optionally of one session, not yet undone."""
    bdir = bdir or _CHANGE_BACKUP_DIR
    out = []
    try:
        names = os.listdir(bdir)
    except OSError:
        return out
    for n in names:
        meta = _load_json_file(os.path.join(bdir, n, "meta.json"), None)
        if isinstance(meta, dict) and not meta.get("undone") and (sid is None or meta.get("sid") == sid):
            out.append(meta)
    out.sort(key=lambda m: -(m.get("at") or 0))
    return out


def _change_prune(bdir, keep=None):
    import shutil
    keep = keep or _CHANGE_BACKUPS_KEEP
    try:
        names = os.listdir(bdir)
    except OSError:
        return
    metas = []
    for n in names:
        meta = _load_json_file(os.path.join(bdir, n, "meta.json"), None)
        metas.append(((meta or {}).get("at") or 0, n))
    for _, n in sorted(metas, reverse=True)[keep:]:
        shutil.rmtree(os.path.join(bdir, n), ignore_errors=True)


def _change_discard(path, sid, bdir=None, now=None, expect_sig=None):
    """Throw away the uncommitted change of one file (a backup first).
    `expect_sig` = the file's _file_sig when the panel was shown: if it
    changed since, refuse (conflict) -- you'd discard work you never saw."""
    import shutil, uuid
    bdir = bdir or _CHANGE_BACKUP_DIR
    if expect_sig is not None and _file_sig(path) != expect_sig:
        return {"ok": False, "error": "the file changed since you opened the panel -- look at it again", "conflict": True}
    root, rel, code = _change_status(path)
    if not root:
        return {"ok": False, "error": "not in a git repo"}
    if not code:
        return {"ok": False, "error": "no uncommitted change in this file"}
    kind = _change_kind(code)
    if kind == "renamed":
        return {"ok": False, "error": "renamed files: undo the rename in git yourself"}
    existed = os.path.isfile(path)
    if os.path.exists(path) and not existed:
        return {"ok": False, "error": "not a file"}
    bid = time.strftime("%Y%m%d-%H%M%S", time.localtime(now or time.time())) + "-" + uuid.uuid4().hex[:6]
    d = os.path.join(bdir, bid)
    os.makedirs(d, exist_ok=True)
    if existed:
        shutil.copy2(path, os.path.join(d, "file"))
    meta = {"id": bid, "sid": sid, "path": path, "root": root, "rel": rel, "kind": kind, "code": code,
            "existed": existed, "staged": code[0] not in " ?", "at": now or time.time(), "undone": False}
    if kind in ("untracked", "added"):
        if kind == "added" and _git_out(["rm", "--cached", "-q", "--", rel], root) is None:
            shutil.rmtree(d, ignore_errors=True)
            return {"ok": False, "error": "git rm --cached failed"}
        os.remove(path)   # the copy is in the backup
    elif _git_out(["restore", "--source=HEAD", "--staged", "--worktree", "--", rel], root) is None:
        shutil.rmtree(d, ignore_errors=True)
        return {"ok": False, "error": "git restore failed"}
    meta["after"] = _file_hash(path)   # what undo expects to find
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False)
    _change_prune(bdir)
    return {"ok": True, "backup": meta}


def _change_undo(bid, bdir=None, force=False):
    """Put a discarded file back as it was. Refuses when the file changed
    again since the discard (unless force) -- that would lose newer work."""
    import shutil
    bdir = bdir or _CHANGE_BACKUP_DIR
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", str(bid or "")):
        return {"ok": False, "error": "bad id"}
    d = os.path.join(bdir, bid)
    meta = _load_json_file(os.path.join(d, "meta.json"), None)
    if not isinstance(meta, dict):
        return {"ok": False, "error": "backup not found"}
    if meta.get("undone"):
        return {"ok": False, "error": "already undone"}
    path = meta["path"]
    if not force and _file_hash(path) != meta.get("after", ""):
        return {"ok": False, "error": "the file changed since it was discarded", "conflict": True}
    if meta.get("existed"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        shutil.copy2(os.path.join(d, "file"), path)
    elif os.path.isfile(path):
        os.remove(path)   # it was deleted in the working tree; the discard brought it back
    if meta.get("kind") == "added":
        _git_out(["add", "--", meta["rel"]], meta["root"])
    meta["undone"] = True
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False)
    return {"ok": True, "path": path}


def _change_stage(paths):
    """git add each file (all already checked to be the session's own)."""
    staged, failed = [], []
    for p in paths:
        root = _git_root_for(p, {})
        rel = os.path.relpath(os.path.realpath(p), root).replace("\\", "/") if root else None
        if root and _git_out(["add", "-A", "--", rel], root) is not None:
            staged.append(p)
        else:
            failed.append(p)
    return {"ok": not failed, "staged": staged, "failed": failed}


# ── Launch: start a new Claude Code session from the dashboard ────────────
# claude.exe is started directly in a new console window (no shell, so a
# prompt with quotes, & or ; reaches Claude exactly as typed), in a folder
# you picked, with your prompt as its first message. Its hooks then report
# it like any session you started yourself. Through the tunnel this works
# from the phone too -- the same token-protected power as the TERM tab.
_LAUNCH_MODELS = ("", "opus", "sonnet", "haiku")
_LAUNCH_MIN_GAP_S = 5
_launch_last = [0.0]


def _launch_validate(data, isdir=os.path.isdir):
    """Pure-ish: request body -> (args dict, None) or (None, error text)."""
    if not isinstance(data, dict):
        return None, "bad request"
    folder = os.path.normpath(os.path.expanduser(str(data.get("cwd") or "").strip().strip('"'))) \
        if str(data.get("cwd") or "").strip() else ""
    if not folder or not os.path.isabs(folder) or not isdir(folder):
        return None, "pick an existing folder (full path)"
    prompt = str(data.get("prompt") or "").strip()
    if len(prompt) > 20000:
        return None, "prompt too long (20,000 characters max)"
    model = str(data.get("model") or "").strip().lower()
    if model not in _LAUNCH_MODELS:
        return None, "unknown model"
    resume = str(data.get("resume") or "").strip()
    if resume and not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", resume):
        return None, "bad session id"
    out = {"cwd": folder, "prompt": prompt, "model": model}
    if resume:
        out["resume"] = resume
        if data.get("keep_prompt") is True and prompt:
            out["keep_prompt"] = True   # --resume <id> "<prompt>": reopen AND send it
    return out, None


def _launch_argv(exe, args):
    """Pure: the claude command line (a list -- never through a shell)."""
    argv = [exe]
    if args.get("resume"):
        argv += ["--resume", args["resume"]]
    if args.get("model"):
        argv += ["--model", args["model"]]
    if args.get("prompt"):
        # a prompt starting with "-" would be read as a flag (--settings=... with
        # hooks runs commands); a leading space keeps it a prompt
        p = args["prompt"]
        argv.append(" " + p if p.startswith("-") else p)
    return argv


def _launch_dirs(rows, isdir=os.path.isdir, limit=30):
    """Pure-ish: [(folder, last_epoch)] -> folders that still exist, newest
    first, each once (case-insensitive), with a display name."""
    seen, out = set(), []
    for folder, epoch in sorted((r for r in rows if r[0]), key=lambda r: -(r[1] or 0)):
        key = os.path.normcase(os.path.normpath(folder))
        if key in seen or not isdir(folder):
            continue
        seen.add(key)
        out.append({"path": folder, "name": os.path.basename(folder.rstrip("\\/")) or folder, "last": epoch})
        if len(out) >= limit:
            break
    return out


def _launch_known_dirs():
    """Folders Claude Code worked in lately: repos it committed to, session
    folders, and the AOC dashboard's current project dir."""
    rows = []
    if os.path.exists(TRANSCRIPT_INDEX_DB):
        conn = None
        try:
            conn = _transcript_index_connect()
            rows += conn.execute("""SELECT cwd, MAX(epoch) FROM commits WHERE sha NOT LIKE 'tu:%'
                                    GROUP BY cwd ORDER BY 2 DESC LIMIT 60""").fetchall()
            rows += conn.execute("SELECT cwd, MAX(last_call_epoch) FROM sessions_meta GROUP BY cwd ORDER BY 2 DESC LIMIT 60").fetchall()
        except Exception as e:
            _log_bg_error("launch dirs", e)
        finally:
            if conn is not None:
                conn.close()
    return _launch_dirs(rows)


def _folder_trusted(folder, config=None):
    """Did you already accept Claude Code's "trust this folder" dialog for
    this folder (or a parent)? Read from ~/.claude.json; unknown -> False."""
    if config is None:
        config = _load_json_file(os.path.join(os.path.expanduser("~"), ".claude.json"), {})
    trusted = {k.replace("\\", "/").rstrip("/").casefold()
               for k, v in ((config or {}).get("projects") or {}).items()
               if isinstance(v, dict) and v.get("hasTrustDialogAccepted")}
    f = os.path.normpath(folder).replace("\\", "/").rstrip("/").casefold()
    while f:
        if f in trusted:
            return True
        parent = f.rsplit("/", 1)[0] if "/" in f else ""
        if parent == f or not parent or parent.endswith(":"):
            return False
        f = parent
    return False


def _launch_session(data, now=None, popen=None):
    """POST /launch -> {"ok", "pid"} or {"ok": False, "error"}."""
    now = now or time.time()
    args, err = _launch_validate(data)
    if err:
        return {"ok": False, "error": err}
    if now - _launch_last[0] < _LAUNCH_MIN_GAP_S:
        return {"ok": False, "error": "one launch every few seconds, please"}
    exe = _find_claude_exe()
    if not exe:
        return {"ok": False, "error": "Claude Code (claude) not found on this PC"}
    _launch_last[0] = now
    if args.get("resume") and args.get("prompt") and not args.get("keep_prompt"):
        args = {**args, "prompt": ""}   # --resume reopens the conversation; nothing to type
    p = (popen or subprocess.Popen)(_launch_argv(exe, args), cwd=args["cwd"],
                                    creationflags=0x00000010 if os.name == "nt" else 0)  # CREATE_NEW_CONSOLE
    return {"ok": True, "pid": p.pid, "cwd": args["cwd"], "trusted": _folder_trusted(args["cwd"])}


# ── Parallel sessions in git worktrees ────────────────────────────────────
# "+ New session" can start 1-4 sessions side by side, each in its own git
# worktree on its own branch (aoc/<when>-<n>) off the repo's current HEAD,
# so they never edit the same files. %LOCALAPPDATA%\AOC\worktrees.json lists
# the worktrees AOC made; only those can be merged (into the branch they
# started from, after a dry-run conflict check) or discarded (worktree and
# branch removed). They live in <repo>\.claude\worktrees\aoc-* like Claude
# Code's own, so a repo folder you already trusted stays trusted.
WORKTREES_FILE = os.path.join(AOC_DATA_DIR, "worktrees.json")
_WT_MAX_PARALLEL = 4
_WT_MAX = 40
_WT_STATUS_CACHE_S = 10
_wt_lock = threading.Lock()
_wt_status_cache = {}


def _wt_git(args, cwd, timeout=30):
    """(returncode, stdout, stderr) of a git call, decoded as UTF-8;
    (-1, "", error) when git can't run at all."""
    try:
        r = subprocess.run(["git", "-c", "core.quotepath=off"] + list(args), cwd=cwd, capture_output=True,
                           timeout=timeout, creationflags=_NO_WINDOW)
    except Exception as e:
        return -1, "", str(e)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def _wt_err(text, fallback):
    """Pure: the last meaningful line of git's stderr, for an error message."""
    lines = [l.strip() for l in str(text or "").splitlines() if l.strip() and not l.strip().startswith("hint:")]
    return (lines[-1] if lines else fallback)[:240]


def _wt_load(path=None):
    data = _load_json_file(path or WORKTREES_FILE, [])
    return [w for w in data if isinstance(w, dict) and w.get("id") and w.get("path")] if isinstance(data, list) else []


def _wt_save(items, path=None):
    path = path or WORKTREES_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _wt_validate(data, isdir=os.path.isdir):
    """Pure-ish: POST /worktrees/launch body -> (args + count, None) or (None, error)."""
    args, err = _launch_validate(data, isdir=isdir)
    if err:
        return None, err
    if args.get("resume"):
        return None, "a worktree session starts fresh (no resume)"
    try:
        n = int(data.get("count", 1))
    except (TypeError, ValueError):
        return None, "bad count"
    if not 1 <= n <= _WT_MAX_PARALLEL:
        return None, f"1 to {_WT_MAX_PARALLEL} sessions at once"
    return dict(args, count=n), None


def _wt_names(repo, slug, n):
    """Pure: [(id, branch, worktree path)] for n parallel sessions."""
    return [(f"{slug}-{i}", f"aoc/{slug}-{i}", os.path.join(repo, ".claude", "worktrees", f"aoc-{slug}-{i}"))
            for i in range(1, n + 1)]


def _wt_repo(cwd, git=None):
    """(repo facts, None) or (None, error): top folder, current branch, HEAD sha."""
    git = git or _wt_git
    rc, top, er = git(["rev-parse", "--show-toplevel"], cwd)
    if rc != 0:
        return None, "that folder is not inside a git repo"
    repo = os.path.normpath(os.path.realpath(top.strip()))
    rc, branch, _ = git(["symbolic-ref", "--short", "-q", "HEAD"], repo)
    if rc != 0 or not branch.strip():
        return None, "the repo has no branch checked out (detached HEAD) -- check one out first"
    rc, sha, _ = git(["rev-parse", "--verify", "-q", "HEAD"], repo)
    if rc != 0 or not sha.strip():
        return None, "the repo has no commits yet"
    return {"repo": repo, "base": branch.strip(), "base_sha": sha.strip()}, None


def _wt_remove(repo, path, branch, git=None, force_branch=True):
    """Remove a worktree and its branch. Returns an error text or ''."""
    git = git or _wt_git
    err = ""
    if os.path.isdir(path):
        rc, _, er = git(["worktree", "remove", "--force", path], repo, timeout=60)
        if rc != 0:
            err = _wt_err(er, "could not remove the worktree folder (is a program still open in it?)")
    git(["worktree", "prune"], repo)
    if not err and branch:
        rc, _, er = git(["branch", "-D" if force_branch else "-d", branch], repo)
        if rc != 0 and "not found" not in er:
            err = _wt_err(er, "could not delete the branch")
    return err


def _wt_create(facts, slug, n, git=None):
    """Create n worktrees off facts['base_sha']; all or nothing.
    -> ([{id, branch, path}], None) or (None, error)."""
    git = git or _wt_git
    made = []
    for wid, branch, path in _wt_names(facts["repo"], slug, n):
        rc, _, er = git(["worktree", "add", "-b", branch, path, facts["base_sha"]], facts["repo"], timeout=120)
        if rc != 0:
            for m in made:
                _wt_remove(facts["repo"], m["path"], m["branch"], git)
            return None, "git worktree add failed: " + _wt_err(er, "unknown error")
        made.append({"id": wid, "branch": branch, "path": path})
    return made, None


def _wt_launch(data, now=None, popen=None, git=None, path=None):
    """POST /worktrees/launch {cwd, prompt, model, count} -> {"ok", "items"}."""
    now = now or time.time()
    args, err = _wt_validate(data)
    if err:
        return {"ok": False, "error": err}
    if now - _launch_last[0] < _LAUNCH_MIN_GAP_S:
        return {"ok": False, "error": "one launch every few seconds, please"}
    exe = _find_claude_exe()
    if not exe:
        return {"ok": False, "error": "Claude Code (claude) not found on this PC"}
    facts, err = _wt_repo(args["cwd"], git)
    if err:
        return {"ok": False, "error": err}
    # started in a subfolder of the repo -> each session starts in the same subfolder of its worktree
    rel = os.path.relpath(os.path.realpath(args["cwd"]), facts["repo"])
    rel = "" if rel == "." or rel.startswith("..") else rel
    with _wt_lock:
        items = _wt_load(path)
        if len(items) + args["count"] > _WT_MAX:
            return {"ok": False, "error": f"AOC already keeps {len(items)} worktrees -- merge or discard some first"}
        slug = time.strftime("%m%d-%H%M%S", time.localtime(now))
        made, err = _wt_create(facts, slug, args["count"], git)
        if err:
            return {"ok": False, "error": err}
        _launch_last[0] = now
        for i, w in enumerate(made):
            cwd = os.path.join(w["path"], rel) if rel and os.path.isdir(os.path.join(w["path"], rel)) else w["path"]
            w.update(group=slug, n=i + 1, of=len(made), repo=facts["repo"], base=facts["base"],
                     base_sha=facts["base_sha"], cwd=cwd, prompt=args["prompt"][:500], model=args["model"],
                     created=now, state="open")
            try:
                p = (popen or subprocess.Popen)(_launch_argv(exe, {"prompt": args["prompt"], "model": args["model"]}),
                                                cwd=cwd, creationflags=0x00000010 if os.name == "nt" else 0)
                w["pid"] = p.pid
            except OSError as e:
                w["pid"] = None
                w["error"] = f"could not start claude: {e}"[:200]
        _wt_save(items + made, path)
    return {"ok": True, "items": made, "trusted": _folder_trusted(facts["repo"])}


def _wt_shortstat(text):
    """Pure: `git diff --shortstat` -> {files, add, del}."""
    out = {"files": 0, "add": 0, "del": 0}
    for num, word in re.findall(r"(\d+) (file|insertion|deletion)", str(text or "")):
        out[{"file": "files", "insertion": "add", "deletion": "del"}[word]] = int(num)
    return out


def _wt_status(w, git=None, now=None, fresh=False):
    """Git facts for one worktree (cached 10 s): exists, dirty (uncommitted
    files), ahead/behind its base branch, committed diff stat, last subject."""
    git = git or _wt_git
    now = now or time.time()
    hit = _wt_status_cache.get(w["id"])
    if hit and not fresh and now - hit[0] < _WT_STATUS_CACHE_S:
        return hit[1]
    out = {"exists": os.path.isdir(w["path"])}
    if out["exists"]:
        rc, st, _ = git(["status", "--porcelain=v1", "-z"], w["path"])
        out["dirty"] = len(_parse_porcelain_z(st)) if rc == 0 else None
        rng = f"refs/heads/{w['base']}...refs/heads/{w['branch']}"
        rc, lr, _ = git(["rev-list", "--left-right", "--count", rng], w["repo"])
        parts = lr.split() if rc == 0 else []
        out["behind"], out["ahead"] = (int(parts[0]), int(parts[1])) if len(parts) == 2 else (None, None)
        rc, ss, _ = git(["diff", "--shortstat", rng], w["repo"])
        out.update(_wt_shortstat(ss if rc == 0 else ""))
        if out["ahead"]:
            rc, subj, _ = git(["log", "-1", "--format=%s", f"refs/heads/{w['branch']}"], w["repo"])
            out["last_subject"] = subj.strip()[:200] if rc == 0 else ""
    _wt_status_cache[w["id"]] = (now, out)
    return out


def _wt_sessions_in(w, sessions):
    """Pure: the dashboard sessions whose folder is this worktree (or inside it)."""
    root = os.path.normcase(os.path.normpath(w.get("path") or ""))
    out = []
    for s in sessions or []:
        cwd = os.path.normcase(os.path.normpath(s.get("cwd") or "")) if s.get("cwd") else ""
        if root and cwd and (cwd == root or cwd.startswith(root + os.sep)):
            out.append({"id": s.get("id"), "name": s.get("display_name") or s.get("project") or "",
                        "active": s.get("session_active") is not False,
                        "waiting": bool(s.get("waiting_on_you")), "cost": s.get("estimated_cost") or 0})
    return out


def _wt_list(sessions=(), git=None, path=None, now=None):
    """GET /worktrees -> {"items": [...]} (newest group first)."""
    keys = ("id", "group", "n", "of", "repo", "base", "branch", "path", "cwd", "prompt", "model",
            "created", "pid", "state", "error")
    out = []
    for w in _wt_load(path):
        v = {k: w.get(k) for k in keys}
        v["repo_name"] = os.path.basename((w.get("repo") or "").rstrip("\\/"))
        v.update(_wt_status(w, git, now))
        v["sessions"] = _wt_sessions_in(w, sessions)
        out.append(v)
    out.sort(key=lambda v: (-(v.get("created") or 0), v.get("n") or 0))
    return {"items": out, "max_parallel": _WT_MAX_PARALLEL}


def _wt_find(items, wid):
    return next((w for w in items if w.get("id") == wid), None)


def _wt_conflicts(out):
    """Pure: `git merge-tree --write-tree --name-only --no-messages` stdout
    on a conflict -> the conflicting paths (first line is the tree id)."""
    lines = str(out or "").split("\n")[1:]
    files = []
    for l in lines:
        if not l.strip():
            break
        if l.strip() not in files:
            files.append(l.strip())
    return files


def _wt_merge(wid, sessions=(), git=None, path=None, now=None):
    """POST /worktrees/merge {id}: merge the worktree's branch into the branch
    it started from (no-ff), after checking it would merge cleanly; then
    remove the worktree and its (now merged) branch."""
    git = git or _wt_git
    now = now or time.time()
    with _wt_lock:
        items = _wt_load(path)
        w = _wt_find(items, wid)
        if not w:
            return {"ok": False, "error": "unknown worktree"}
        if any(s["active"] for s in _wt_sessions_in(w, sessions)):
            return {"ok": False, "error": "a session is still open in this worktree -- close it first"}
        st = _wt_status(w, git, now, fresh=True)
        if not st["exists"]:
            return {"ok": False, "error": "the worktree folder is gone -- discard it instead"}
        if st.get("dirty"):
            return {"ok": False, "error": f"{st['dirty']} uncommitted file(s) in the worktree -- commit or discard them first"}
        if not st.get("ahead"):
            return {"ok": False, "error": "nothing to merge -- the branch has no new commits"}
        repo = w["repo"]
        rc, cur, _ = git(["symbolic-ref", "--short", "-q", "HEAD"], repo)
        if rc != 0 or cur.strip() != w["base"]:
            return {"ok": False, "error": f"the repo is on '{cur.strip() or 'detached HEAD'}', not '{w['base']}' -- check out {w['base']} first"}
        rc, dirty, _ = git(["status", "--porcelain=v1", "-z", "--untracked-files=no"], repo)
        if rc != 0 or _parse_porcelain_z(dirty):
            return {"ok": False, "error": "the repo has uncommitted changes -- commit or stash them first"}
        rc, out, er = git(["merge-tree", "--write-tree", "--name-only", "--no-messages", w["base"], w["branch"]], repo, timeout=60)
        if rc == 1:
            files = _wt_conflicts(out)
            return {"ok": False, "conflicts": files,
                    "error": "would conflict in " + (", ".join(files[:5]) + (f" and {len(files) - 5} more" if len(files) > 5 else "") if files else "some files")}
        if rc != 0:
            return {"ok": False, "error": "conflict check failed: " + _wt_err(er, "git merge-tree needs git 2.38 or newer")}
        rc, _, er = git(["merge", "--no-ff", "--no-edit", "-m",
                         f"Merge {w['branch']} (AOC parallel session {w.get('n', 1)}/{w.get('of', 1)})", w["branch"]],
                        repo, timeout=120)
        if rc != 0:
            git(["merge", "--abort"], repo)
            return {"ok": False, "error": "merge failed: " + _wt_err(er, "unknown error")}
        rc, sha, _ = git(["rev-parse", "HEAD"], repo)
        cleanup = _wt_remove(repo, w["path"], w["branch"], git, force_branch=False)
        _wt_status_cache.pop(wid, None)
        if cleanup:
            w.update(state="merged", merged_at=now)
        else:
            items = [x for x in items if x is not w]
        _wt_save(items, path)
    return {"ok": True, "sha": sha.strip()[:40], "cleanup_error": cleanup}


def _wt_discard(wid, sessions=(), force=False, git=None, path=None):
    """POST /worktrees/discard {id, force?}: delete the worktree and its
    branch, with whatever was done in it."""
    with _wt_lock:
        items = _wt_load(path)
        w = _wt_find(items, wid)
        if not w:
            return {"ok": False, "error": "unknown worktree"}
        if not force and any(s["active"] for s in _wt_sessions_in(w, sessions)):
            return {"ok": False, "error": "a session is still open in this worktree -- close it first"}
        err = _wt_remove(w["repo"], w["path"], w["branch"], git)
        if err:
            return {"ok": False, "error": err}
        _wt_status_cache.pop(wid, None)
        _wt_save([x for x in items if x is not w], path)
    return {"ok": True}


def _wt_diff(wid, git=None, path=None):
    """GET /worktrees/diff?id= -> committed changes vs the base branch, plus
    anything not committed yet in the worktree."""
    git = git or _wt_git
    w = _wt_find(_wt_load(path), wid)
    if not w:
        return {"ok": False, "error": "unknown worktree"}
    rc, committed, _ = git(["diff", f"refs/heads/{w['base']}...refs/heads/{w['branch']}"], w["repo"], timeout=60)
    text = committed if rc == 0 else ""
    if os.path.isdir(w["path"]):
        rc, wip, _ = git(["diff", "HEAD"], w["path"], timeout=60)
        if rc == 0 and wip.strip():
            text += ("\n" if text else "") + "# --- not committed yet ---\n" + wip
    return {"ok": True, "diff": text[:1_000_000], "branch": w["branch"], "base": w["base"]}


# ── Stranded worktrees: a git worktree under <repo>\.claude\worktrees\ (an
# agent's from Claude Code, or one of AOC's parallel sessions) that still
# holds work -- uncommitted files or commits its repo's branch doesn't have
# -- while nothing works in it any more: no open session in the folder and
# no file or commit touched for _WT_STRANDED_IDLE_S. That's what a crashed or
# killed agent leaves behind. Inbox and Today list them with Commit WIP
# (everything committed on its branch), Relaunch (a new session in the
# folder, told to pick the work up) and Discard (worktree + branch removed,
# after you've seen the diff stat). Repos come from the folders Claude Code
# worked in lately, the open sessions and worktrees.json.
_WT_STRANDED_IDLE_S = 30 * 60
_WT_STRANDED_CACHE_S = 60
_WT_STRANDED_MAX_DIRS = 40
_WT_WIP_MESSAGE = "WIP: rescued by AOC"
_wt_stranded_cache = [0.0, None]
_wt_repo_root_cache = {}          # normcase(cwd) -> (when, main worktree path or "")


def _wt_porcelain(text):
    """Pure: `git worktree list --porcelain` -> [{path, head, branch, detached,
    locked, prunable}] (main worktree first, as git prints it)."""
    out = []
    for block in re.split(r"\n\s*\n", str(text or "").replace("\r\n", "\n")):
        w = {}
        for line in block.split("\n"):
            key, _, val = line.partition(" ")
            if key == "worktree":
                w["path"] = os.path.normpath(val.strip())
            elif key == "HEAD":
                w["head"] = val.strip()
            elif key == "branch":
                w["branch"] = val.strip().replace("refs/heads/", "", 1)
            elif key in ("detached", "locked", "prunable", "bare"):
                w[key] = True
                if key == "locked" and val.strip():
                    w["lock_reason"] = val.strip()
        if w.get("path"):
            out.append(w)
    return out


def _wt_stranded_id(path):
    """Pure: a stable short id for a worktree folder."""
    import hashlib
    return hashlib.sha1(os.path.normcase(os.path.normpath(path)).encode("utf-8")).hexdigest()[:12]


def _wt_in_claude_dir(main, path):
    """Pure: is `path` a worktree folder under <main>\\.claude\\worktrees\\?"""
    root = os.path.normcase(os.path.normpath(os.path.join(main, ".claude", "worktrees")))
    p = os.path.normcase(os.path.normpath(path))
    return p.startswith(root + os.sep) and os.sep not in p[len(root) + 1:]


def _wt_stranded_reason(st, active_sessions, now, idle_s=_WT_STRANDED_IDLE_S):
    """Pure: why this worktree counts as stranded ('' = it doesn't).
    st: {exists, dirty, ahead, last_activity}."""
    if not st.get("exists") or active_sessions:
        return ""
    if not (st.get("dirty") or st.get("ahead")):
        return ""
    idle = now - (st.get("last_activity") or 0)
    if idle < idle_s:
        return ""
    bits = []
    if st.get("dirty"):
        bits.append(f"{st['dirty']} uncommitted file{'' if st['dirty'] == 1 else 's'}")
    if st.get("ahead"):
        bits.append(f"{st['ahead']} unmerged commit{'' if st['ahead'] == 1 else 's'}")
    return " + ".join(bits) + ", untouched for " + _fmt_idle(idle)


def _fmt_idle(secs):
    """Pure: 95 min -> '1 h 35 min', 3 days -> '3 d'."""
    m = int(secs // 60)
    if m < 60:
        return f"{m} min"
    if m < 24 * 60:
        return f"{m // 60} h" + (f" {m % 60} min" if m % 60 else "")
    return f"{m // (24 * 60)} d"


def _wt_main_of(cwd, git=None, now=None):
    """The main worktree folder of the repo `cwd` is in ('' if none), cached 10 min."""
    git = git or _wt_git
    now = now or time.time()
    key = os.path.normcase(os.path.normpath(cwd))
    hit = _wt_repo_root_cache.get(key)
    if hit and now - hit[0] < 600:
        return hit[1]
    main = ""
    if os.path.isdir(cwd):
        rc, out, _ = git(["worktree", "list", "--porcelain"], cwd, timeout=15)
        items = _wt_porcelain(out) if rc == 0 else []
        if items and not items[0].get("bare"):
            main = items[0]["path"]
    _wt_repo_root_cache[key] = (now, main)
    return main


def _wt_stranded_facts(main, w, base_ref, git=None):
    """Git facts for one candidate worktree: dirty, ahead (commits its HEAD
    has that `base_ref` doesn't), diff stat, last activity (newest of the
    uncommitted files' mtimes and the last commit time)."""
    git = git or _wt_git
    path = w["path"]
    out = {"exists": os.path.isdir(path), "dirty": 0, "ahead": 0, "files": 0, "add": 0, "del": 0, "last_activity": 0}
    if not out["exists"]:
        return out
    rc, st, _ = git(["status", "--porcelain=v1", "-z"], path)
    changed = _parse_porcelain_z(st) if rc == 0 else {}
    out["dirty"] = len(changed)
    last = 0.0
    for rel in list(changed)[:300]:
        try:
            last = max(last, os.path.getmtime(os.path.join(path, rel.rstrip("/"))))
        except OSError:
            pass
    rc, ct, _ = git(["log", "-1", "--format=%ct", "HEAD"], path)
    if rc == 0 and ct.strip().isdigit():
        last = max(last, float(ct.strip()))
    head = w.get("head") or "HEAD"
    if base_ref:
        rc, n, _ = git(["rev-list", "--count", f"{base_ref}..{head}"], main)
        out["ahead"] = int(n.strip()) if rc == 0 and n.strip().isdigit() else 0
        if out["ahead"]:
            rc, ss, _ = git(["diff", "--shortstat", f"{base_ref}...{head}"], main)
            out.update(_wt_shortstat(ss if rc == 0 else ""))
            rc, subj, _ = git(["log", "-1", "--format=%s", head], main)
            out["last_subject"] = subj.strip()[:200] if rc == 0 else ""
    if out["dirty"]:
        rc, ss, _ = git(["diff", "--shortstat", "HEAD"], path)
        wip = _wt_shortstat(ss if rc == 0 else "")
        out["wip_add"], out["wip_del"] = wip["add"], wip["del"]
    out["last_activity"] = last
    return out


def _wt_stranded_scan(dirs, sessions=(), managed=(), git=None, now=None, idle_s=_WT_STRANDED_IDLE_S):
    """[{id, repo, repo_name, path, branch, kind, base, reason, ...}] for the
    stranded worktrees of the repos the `dirs` are in."""
    git = git or _wt_git
    now = now or time.time()
    by_path = {os.path.normcase(os.path.normpath(m.get("path") or "")): m for m in managed or []}
    mains, out = [], []
    for d in dirs:
        main = _wt_main_of(d, git, now)
        if main and os.path.normcase(main) not in {os.path.normcase(m) for m in mains}:
            mains.append(main)
    for main in mains:
        rc, txt, _ = git(["worktree", "list", "--porcelain"], main, timeout=15)
        items = _wt_porcelain(txt) if rc == 0 else []
        if not items:
            continue
        base_ref = items[0].get("branch") or items[0].get("head") or ""
        for w in items[1:]:
            if not _wt_in_claude_dir(main, w["path"]) or w.get("prunable"):
                continue
            m = by_path.get(os.path.normcase(w["path"]))
            base = (m or {}).get("base") or base_ref
            st = _wt_stranded_facts(main, w, base, git)
            active = [s for s in _wt_sessions_in(w, sessions) if s["active"]]
            reason = _wt_stranded_reason(st, active, now, idle_s)
            if not reason:
                continue
            name = os.path.basename(w["path"])
            out.append({**st, "id": _wt_stranded_id(w["path"]), "repo": main,
                        "repo_name": os.path.basename(main.rstrip("\\/")), "path": w["path"], "name": name,
                        "branch": w.get("branch") or "", "head": (w.get("head") or "")[:40],
                        "detached": bool(w.get("detached")), "locked": bool(w.get("locked")),
                        "kind": "aoc" if m else ("agent" if name.startswith("agent-") else "other"),
                        "aoc_id": (m or {}).get("id") or "", "base": base, "reason": reason})
    out.sort(key=lambda x: -(x.get("last_activity") or 0))
    return out


def _wt_stranded_dirs(sessions=()):
    """The folders to look for repos in: open/recent sessions, worktrees.json, recent project dirs."""
    dirs, seen = [], set()
    cands = [s.get("cwd") for s in sessions or []] + [w.get("repo") for w in _wt_load()]
    try:
        cands += [d["path"] for d in _launch_known_dirs()]
    except Exception as e:
        _log_bg_error("stranded worktrees", e)
    for d in cands:
        if not d or not os.path.isdir(d):
            continue
        k = os.path.normcase(os.path.normpath(d))
        if k in seen:
            continue
        seen.add(k)
        dirs.append(d)
        if len(dirs) >= _WT_STRANDED_MAX_DIRS:
            break
    return dirs


def _wt_stranded_list(sessions=(), now=None, fresh=False):
    """GET /worktrees/stranded -> {"items": [...]} (cached 60 s)."""
    now = now or time.time()
    if not fresh and _wt_stranded_cache[1] is not None and now - _wt_stranded_cache[0] < _WT_STRANDED_CACHE_S:
        return {"items": _wt_stranded_cache[1]}
    items = _wt_stranded_scan(_wt_stranded_dirs(sessions), sessions, _wt_load(), now=now)
    _wt_stranded_cache[0], _wt_stranded_cache[1] = now, items
    return {"items": items}


def _wt_stranded_find(wid, sessions=(), now=None):
    """The stranded worktree with this id, re-checked right now (never act on a stale or unknown id)."""
    return next((w for w in _wt_stranded_list(sessions, now, fresh=True)["items"] if w["id"] == wid), None)


def _wt_resume_prompt(w):
    """Pure: what a relaunched session is told."""
    where = f"branch {w['branch']}" if w.get("branch") else "a detached HEAD"
    return (f"An earlier agent was working in this git worktree ({where}) and stopped before finishing "
            f"({w.get('reason') or 'work left over'}). Look at `git status`, `git diff` and `git log {w.get('base') or 'HEAD'}..HEAD` "
            f"to see what it did, work out what the task was, finish it, run the tests and commit on this branch. "
            f"Don't merge, push or delete the worktree.")


def _wt_stranded_act(kind, wid, sessions=(), git=None, launch=None, now=None):
    """POST /worktrees/stranded/{commit,relaunch,discard} {id}."""
    git = git or _wt_git
    w = _wt_stranded_find(wid, sessions, now)
    if not w:
        return {"ok": False, "error": "that worktree is no longer stranded (or is gone) -- refresh"}
    if kind == "commit":
        if not w["dirty"]:
            return {"ok": False, "error": "nothing uncommitted to commit"}
        if w["detached"] or not w["branch"]:
            return {"ok": False, "error": "the worktree is on a detached HEAD -- a commit there would get lost; relaunch it instead"}
        rc, _, er = git(["add", "-A"], w["path"], timeout=60)
        if rc != 0:
            return {"ok": False, "error": "git add failed: " + _wt_err(er, "unknown error")}
        rc, _, er = git(["commit", "-m", _WT_WIP_MESSAGE], w["path"], timeout=120)
        if rc != 0:
            return {"ok": False, "error": "git commit failed: " + _wt_err(er, "unknown error")}
        rc, sha, _ = git(["rev-parse", "HEAD"], w["path"])
        _wt_stranded_cache[1] = None
        return {"ok": True, "sha": sha.strip()[:40], "branch": w["branch"]}
    if kind == "relaunch":
        r = (launch or _launch_session)({"cwd": w["path"], "prompt": _wt_resume_prompt(w)})
        if r.get("ok"):
            _wt_stranded_cache[1] = None
        return r
    if kind == "discard":
        if w["locked"]:
            return {"ok": False, "error": "git has this worktree locked (an agent may still own it) -- unlock it first"}
        err = _wt_remove(w["repo"], w["path"], w["branch"], git)
        if err:
            return {"ok": False, "error": err}
        if w["aoc_id"]:
            with _wt_lock:
                _wt_save([x for x in _wt_load() if x.get("id") != w["aoc_id"]])
                _wt_status_cache.pop(w["aoc_id"], None)
        _wt_stranded_cache[1] = None
        return {"ok": True}
    return {"ok": False, "error": "unknown action"}


# ── Background agents: the Agent-tool runs Claude Code started in the
# background (forks, run_in_background subagents), with or without a git
# worktree of their own. Claude Code writes each one's transcript to
# ~/.claude/projects/<proj>/<session>/subagents/agent-<id>.jsonl next to an
# agent-<id>.meta.json {description, agentType, toolUseId, worktreePath,
# worktreeBranch, requestShape}; toolUseId is what the hook's ag_<hash> card
# id is made of, so hook card, transcript and worktree line up. Per agent:
# working / quiet / dead / finished / failed from the transcript (its mtime is
# the agent's heartbeat, even while it only reads or thinks; its last entry
# says whether it ended its turn or died on an API error such as the plan's
# session limit), the parent session and the claude.exe that holds the
# worktree's git lock. Collected in the background and cached: a git call or
# a slow OneDrive read must never hold up a request (see _infra_health_snapshot).
_BG_AGENT_QUIET_S = 10 * 60          # no transcript/file/commit activity -> "quiet"
_BG_AGENT_DEAD_S = _WT_STRANDED_IDLE_S
_BG_AGENT_KEEP_S = 6 * 3600          # a finished/dead agent with no worktree left stays this long
_BG_AGENT_WINDOW_S = 3 * 86400       # transcripts older than this aren't looked at (unless a worktree is left)
_BG_AGENT_MAX = 30
_BG_AGENT_REFRESH_S = 20
_BG_TAIL_BYTES = 64 * 1024
_bg_agents_cache = [None, 0.0]       # [items, at]
_bg_agents_refresh_lock = threading.Lock()
_bg_task_cache = {}                  # transcript path -> task text (the first prompt never changes)


def _bg_agent_aoc_id(tool_use_id):
    """Pure: the hook's card id for an Agent tool call (aoc_hook.agent_id_from_hook)."""
    import hashlib
    return "ag_" + hashlib.md5(str(tool_use_id).encode()).hexdigest()[:10] if tool_use_id else ""


def _bg_lock_pid(reason):
    """Pure: 'claude agent agent-x (pid 12096)' -> 12096 (None if absent)."""
    m = re.search(r"\(pid (\d+)\)", str(reason or ""))
    return int(m.group(1)) if m else None


def _bg_parse_jsonl(text, drop_first=False):
    """Pure: JSONL text -> parsed dicts; a partial first line (a tail read
    starting mid-line) and unparseable lines are skipped."""
    lines = str(text or "").split("\n")
    if drop_first:
        lines = lines[1:]
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def _bg_entry_text(d):
    """Pure: the plain text of a transcript entry's message."""
    c = (d.get("message") or {}).get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for x in c:
            if not isinstance(x, dict):
                continue
            if isinstance(x.get("text"), str):
                parts.append(x["text"])
            elif x.get("type") == "tool_result":
                inner = x.get("content")
                if isinstance(inner, str):
                    parts.append(inner)
                elif isinstance(inner, list):
                    parts += [y.get("text", "") for y in inner if isinstance(y, dict) and isinstance(y.get("text"), str)]
        return "\n".join(parts)
    return ""


def _bg_tail_state(entries):
    """Pure: how a subagent transcript ends -> {"finished", "api_error",
    "error_kind"}. Only the last user/assistant entry counts: an assistant
    turn that ended (stop_reason end_turn) = the agent finished; an API
    error message (isApiErrorMessage, e.g. error "rate_limit" with "You've
    hit your session limit · resets 12:20pm") = it died on that."""
    for d in reversed(entries or []):
        if d.get("type") not in ("assistant", "user"):
            continue
        if d.get("type") == "assistant" and d.get("isApiErrorMessage"):
            return {"finished": False, "api_error": _bg_entry_text(d).strip()[:300] or "API error",
                    "error_kind": str(d.get("error") or "")}
        msg = d.get("message") or {}
        return {"finished": d.get("type") == "assistant" and msg.get("stop_reason") == "end_turn",
                "api_error": "", "error_kind": ""}
    return {"finished": False, "api_error": "", "error_kind": ""}


def _bg_task_text(entries):
    """Pure: the task the agent was given -- its first user message (a
    fork's arrives as the 'Your directive:' part of its boilerplate)."""
    for d in (entries or [])[:12]:
        if d.get("type") != "user":
            continue
        t = _bg_entry_text(d).strip()
        if not t:
            continue
        if "Your directive:" in t:
            t = t.split("Your directive:", 1)[1]
            t = re.split(r"\n\s*You've inherited the conversation context", t)[0]
        return t.strip()[:4000]
    return ""


def _bg_agent_state(it, now):
    """Pure: (state, reason). state is one of working / quiet / dead /
    finished / failed. `it`: {hook_status, finished, api_error, error_kind,
    session_active (True/False/None = unknown), lock_pid, lock_pid_alive,
    last_activity, worktree: {dirty, ahead} | None}."""
    wt = it.get("worktree") or {}
    left = []
    if wt.get("dirty"):
        left.append(f"{wt['dirty']} uncommitted file{'' if wt['dirty'] == 1 else 's'} left")
    if wt.get("ahead"):
        left.append(f"{wt['ahead']} commit{'' if wt['ahead'] == 1 else 's'} waiting to be merged")
    if it.get("hook_status") == "error":
        return "failed", "; ".join(["the Agent call failed"] + left)
    if it.get("finished") or it.get("hook_status") == "done":
        return "finished", "; ".join(left) or "nothing left in a worktree"
    idle = max(0.0, now - (it.get("last_activity") or 0))
    if it.get("api_error"):
        what = "hit a plan limit" if it.get("error_kind") == "rate_limit" else "stopped on an API error"
        return "dead", f"{what}: {it['api_error']}"
    if it.get("lock_pid") and it.get("lock_pid_alive") is False:
        return "dead", "the Claude Code process that ran it is gone"
    if it.get("session_active") is False and idle >= _BG_AGENT_QUIET_S:
        return "dead", "its Claude Code session is closed, no activity for " + _fmt_idle(idle)
    if idle >= _BG_AGENT_DEAD_S:
        return "dead", "no activity for " + _fmt_idle(idle)
    if idle >= _BG_AGENT_QUIET_S:
        return "quiet", "no output for " + _fmt_idle(idle)
    return "working", ""


_BG_STATE_ORDER = {"dead": 0, "failed": 1, "quiet": 2, "working": 3, "finished": 4}


def _bg_read_tail(path, n=_BG_TAIL_BYTES):
    """The parsed last entries of a transcript (64 KB is plenty for the last turn)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - n))
            raw = f.read()
    except OSError:
        return []
    return _bg_parse_jsonl(raw.decode("utf-8", "replace"), drop_first=size > n)


def _bg_read_task(path):
    """The agent's task (cached per transcript: the first prompt never changes)."""
    if path in _bg_task_cache:
        return _bg_task_cache[path]
    try:
        with open(path, "rb") as f:
            head = f.read(256 * 1024).decode("utf-8", "replace")
    except OSError:
        return ""
    task = _bg_task_text(_bg_parse_jsonl(head.rsplit("\n", 1)[0] if len(head) >= 256 * 1024 else head))
    if len(_bg_task_cache) > 500:
        _bg_task_cache.clear()
    _bg_task_cache[path] = task
    return task


def _bg_worktree_facts(path, git=None, porcelain_cache=None):
    """Git facts for an agent's worktree folder (None when it's gone)."""
    git = git or _wt_git
    if not path or not os.path.isdir(path):
        return None
    main = _wt_main_of(path, git)
    if not main:
        return None
    pc = porcelain_cache if porcelain_cache is not None else {}
    key = os.path.normcase(main)
    if key not in pc:
        rc, txt, _ = git(["worktree", "list", "--porcelain"], main, timeout=15)
        pc[key] = _wt_porcelain(txt) if rc == 0 else []
    items = pc[key]
    w = next((x for x in items[1:] if os.path.normcase(x["path"]) == os.path.normcase(os.path.normpath(path))), None)
    if not w:
        return None
    base_ref = (items[0].get("branch") or items[0].get("head") or "") if items else ""
    st = _wt_stranded_facts(main, w, base_ref, git)
    rc, out, _ = git(["log", "-1", "--format=%ct%x00%s", "HEAD"], path)
    ct, _, subj = (out.strip().partition("\x00") if rc == 0 else ("", "", ""))
    pid = _bg_lock_pid(w.get("lock_reason"))
    return {"path": w["path"], "repo": main, "repo_name": os.path.basename(main.rstrip("\\/")),
            "branch": w.get("branch") or "", "detached": bool(w.get("detached")),
            "stranded_id": _wt_stranded_id(w["path"]), "base": base_ref,
            "dirty": st.get("dirty", 0), "ahead": st.get("ahead", 0), "files": st.get("files", 0),
            "add": st.get("add", 0), "del": st.get("del", 0),
            "wip_add": st.get("wip_add", 0), "wip_del": st.get("wip_del", 0),
            "last_activity": st.get("last_activity", 0),
            "last_commit_at": float(ct) if ct.isdigit() else 0, "last_commit_subject": subj[:200],
            "locked": bool(w.get("locked")), "lock_pid": pid,
            "lock_pid_alive": _is_claude_pid_alive(pid) if pid else None}


def _bg_agents_collect(projects_base=None, sessions=(), agents=(), now=None, git=None, glob_fn=None):
    """[{...}] for the background agents of the last _BG_AGENT_WINDOW_S
    (plus older ones whose worktree is still there), most urgent first."""
    import glob as _glob
    now = now or time.time()
    projects_base = projects_base or os.path.join(os.path.expanduser("~"), ".claude", "projects")
    glob_fn = glob_fn or _glob.glob
    sess_by_id = {str(s.get("id")): s for s in sessions or [] if s.get("id")}
    hook_by_id = {a.get("id"): a for a in agents or [] if a.get("id")}
    porcelain = {}
    out = []
    for meta_path in glob_fn(os.path.join(_glob.escape(projects_base), "*", "*", "subagents", "agent-*.meta.json")):
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        background = meta.get("requestShape") == "background" or meta.get("isFork") or meta.get("worktreePath")
        if not background:
            continue
        jsonl = meta_path[:-len(".meta.json")] + ".jsonl"
        try:
            t_at = os.path.getmtime(jsonl)
        except OSError:
            t_at = 0.0
        wt_path = meta.get("worktreePath") or ""
        wt_exists = bool(wt_path) and os.path.isdir(wt_path)
        if now - t_at > _BG_AGENT_WINDOW_S and not wt_exists:
            continue
        agent_id = os.path.basename(meta_path)[len("agent-"):-len(".meta.json")]
        session_id = os.path.basename(os.path.dirname(os.path.dirname(meta_path)))
        tail = _bg_read_tail(jsonl) if t_at else []
        ts = _bg_tail_state(tail)
        cwd = next((d.get("cwd") for d in reversed(tail) if d.get("cwd")), "")
        aoc_id = _bg_agent_aoc_id(meta.get("toolUseId"))
        hook = hook_by_id.get(aoc_id) or {}
        sess = sess_by_id.get(session_id)
        wt = _bg_worktree_facts(wt_path, git, porcelain) if wt_exists else None
        it = {"id": agent_id, "aoc_id": aoc_id if hook else "",
              "name": meta.get("description") or hook.get("name") or "agent-" + agent_id,
              "agent_type": meta.get("agentType") or hook.get("subagent_type") or "",
              "session_id": session_id,
              "session_name": (sess or {}).get("display_name") or (sess or {}).get("project") or "",
              "session_active": (bool(sess.get("session_active")) if sess else None),
              "cwd": cwd or (sess or {}).get("cwd") or "",
              "transcript_at": t_at, "hook_status": hook.get("status") or "",
              "finished": ts["finished"], "api_error": ts["api_error"], "error_kind": ts["error_kind"],
              "worktree_path": wt_path, "worktree": wt,
              "lock_pid": (wt or {}).get("lock_pid"), "lock_pid_alive": (wt or {}).get("lock_pid_alive"),
              "tokens_used": hook.get("tokens_used") or 0}
        it["last_activity"] = max(t_at, (wt or {}).get("last_activity") or 0)
        it["state"], it["reason"] = _bg_agent_state(it, now)
        if it["state"] in ("finished", "failed", "dead") and not wt and now - it["last_activity"] > _BG_AGENT_KEEP_S:
            continue
        out.append(it)
    out.sort(key=lambda x: (_BG_STATE_ORDER.get(x["state"], 9), -(x.get("last_activity") or 0)))
    return out[:_BG_AGENT_MAX]


def _bg_agents_refresh_bg():
    try:
        payload = _own_status_payload() or {}
        items = _bg_agents_collect(sessions=payload.get("sessions_list") or [], agents=payload.get("agents") or [])
        _bg_agents_cache[0], _bg_agents_cache[1] = items, time.time()
    except Exception as e:
        _log_bg_error("background agents", e)
        _bg_agents_cache[1] = time.time()
    finally:
        _bg_agents_refresh_lock.release()


def _bg_agents_snapshot(fresh=False):
    """GET /agents/background -> {"items": [...] | None, "at"}: the cached
    list (None until the first collection lands) and, when it's over
    _BG_AGENT_REFRESH_S old (or fresh=1), one background refresh."""
    if ((fresh or time.time() - _bg_agents_cache[1] >= _BG_AGENT_REFRESH_S)
            and _bg_agents_refresh_lock.acquire(blocking=False)):
        threading.Thread(target=_bg_agents_refresh_bg, daemon=True, name="bg-agents-refresh").start()
    return {"items": _bg_agents_cache[0], "at": _bg_agents_cache[1]}


def _bg_relaunch_prompt(it, task):
    """Pure: what a relaunched session is told."""
    where = (f"in this git worktree (branch {it['worktree']['branch']})" if (it.get("worktree") or {}).get("branch")
             else "in this folder")
    look = (" Start with `git status`, `git diff` and `git log --oneline -10` to see how far it got."
            if it.get("worktree") else "")
    return (f"An earlier background agent (\"{it.get('name') or 'agent'}\") was working {where} and stopped "
            f"before finishing ({it.get('reason') or 'it died'}).{look} Finish its task, run the tests and commit"
            f"{' on this branch' if it.get('worktree') else ''}. Don't merge, push or delete anything.\n\n"
            f"Its task was:\n\n{task or '(the original task text could not be read -- work it out from the changes)'}")


def _bg_agent_relaunch(aid, launch=None, collect=None):
    """POST /agents/background/relaunch {id}: a new Claude Code session in
    the agent's worktree (or the folder it worked in), told to finish its
    task. Re-checked now, and only for an agent that is dead."""
    payload = _own_status_payload() or {}
    items = (collect or _bg_agents_collect)(sessions=payload.get("sessions_list") or [], agents=payload.get("agents") or [])
    it = next((x for x in items if x["id"] == aid), None)
    if not it:
        return {"ok": False, "error": "that agent is gone from the list -- refresh"}
    if it["state"] not in ("dead", "failed"):
        return {"ok": False, "error": f"that agent is {it['state']}, not dead"}
    cwd = (it.get("worktree") or {}).get("path") or it.get("cwd") or ""
    if not cwd or not os.path.isdir(cwd):
        return {"ok": False, "error": "the folder it worked in is gone"}
    task = ""
    try:
        jsonl = _bg_agent_jsonl(it["session_id"], aid)
        task = _bg_read_task(jsonl) if jsonl else ""
    except Exception as e:
        _log_bg_error("background agents", e)
    r = (launch or _launch_session)({"cwd": cwd, "prompt": _bg_relaunch_prompt(it, task)})
    if r.get("ok"):
        _bg_agents_cache[1] = 0.0
    return r


def _bg_agent_jsonl(session_id, aid, base=None):
    """The agent's transcript path ('' if not found)."""
    import glob as _glob
    base = base or os.path.join(os.path.expanduser("~"), ".claude", "projects")
    hits = _glob.glob(os.path.join(_glob.escape(base), "*", _glob.escape(str(session_id)), "subagents",
                                   "agent-" + _glob.escape(str(aid)) + ".jsonl"))
    return hits[0] if hits else ""


# ── Checkpoint a dead agent: its uncommitted worktree changes become a WIP
# commit on the agent's own branch (never the main one, never pushed), so a
# crash, a plan limit or a closed session can't lose them -- and optionally a
# new session continues from that commit, told what the task was, which files
# the WIP holds and what the agent said last.
_BG_PROTECTED_BRANCHES = ("master", "main", "trunk", "develop")


def _bg_last_assistant_text(entries, limit=2000):
    """Pure: the agent's last real assistant text (API error entries and
    tool-only turns skipped), trimmed to `limit` from the end."""
    for d in reversed(entries or []):
        if d.get("type") != "assistant" or d.get("isApiErrorMessage"):
            continue
        t = _bg_entry_text(d).strip()
        if t:
            return t if len(t) <= limit else "…" + t[-limit:]
    return ""


def _bg_checkpoint_message(it):
    """Pure: the WIP commit's subject (one line, git-friendly length)."""
    name = re.sub(r"\s+", " ", str(it.get("name") or "agent " + str(it.get("id") or ""))).strip()
    return "WIP checkpoint: " + (name if len(name) <= 80 else name[:79] + "…")


def _bg_checkpoint_blocker(it):
    """Pure: why this agent's worktree can't be checkpointed ('' = it can)."""
    if it.get("state") not in ("dead", "failed"):
        return f"that agent is {it.get('state') or 'unknown'}, not dead -- it may still be writing those files"
    wt = it.get("worktree") or {}
    if not wt:
        return "it has no worktree left to checkpoint"
    if wt.get("detached") or not wt.get("branch"):
        return "its worktree is on a detached HEAD -- a commit there would get lost; relaunch it instead"
    if wt.get("branch") in _BG_PROTECTED_BRANCHES or wt.get("branch") == wt.get("base"):
        return f"its worktree is on {wt['branch']} -- a checkpoint only ever commits on the agent's own branch"
    if not wt.get("dirty"):
        return "nothing uncommitted to checkpoint"
    return ""


def _bg_checkpoint_prompt(it, task, files, last_text, sha):
    """Pure: what the session continuing from a checkpoint is told."""
    wt = it.get("worktree") or {}
    shown = list(files or [])[:40]
    more = len(files or []) - len(shown)
    flist = "\n".join("- " + f for f in shown) + (f"\n- … and {more} more" if more > 0 else "")
    return (f"An earlier background agent (\"{it.get('name') or 'agent'}\") was working in this git worktree "
            f"(branch {wt.get('branch')}) and stopped before finishing ({it.get('reason') or 'it died'}).\n\n"
            f"AOC saved its uncommitted work as a WIP commit {str(sha)[:10]} (\"{_bg_checkpoint_message(it)}\") "
            f"on this branch. Files in that commit:\n{flist or '- (none listed)'}\n\n"
            f"Its last message was:\n\n{last_text or '(no text message -- see `git show HEAD`)'}\n\n"
            f"Continue from there: check `git show HEAD` and `git log --oneline -10`, finish the task, run the tests, "
            f"then amend or squash the WIP commit into proper commit(s) on this branch. Don't merge, push or delete "
            f"anything.\n\nIts task was:\n\n"
            f"{task or '(the original task text could not be read -- work it out from the changes)'}")


def _bg_agent_checkpoint(aid, relaunch=False, launch=None, collect=None, git=None,
                         find_jsonl=None, read_tail=None, read_task=None):
    """POST /agents/background/checkpoint {id, relaunch}: WIP-commit a dead
    agent's uncommitted worktree changes on its branch, then (relaunch)
    start a session that continues from it. Re-checked now, and the
    worktree's checked-out branch is verified right before the commit."""
    git = git or _wt_git
    payload = _own_status_payload() or {}
    items = (collect or _bg_agents_collect)(sessions=payload.get("sessions_list") or [], agents=payload.get("agents") or [])
    it = next((x for x in items if x["id"] == aid), None)
    if not it:
        return {"ok": False, "error": "that agent is gone from the list -- refresh"}
    why = _bg_checkpoint_blocker(it)
    if why:
        return {"ok": False, "error": why}
    wt = it["worktree"]
    path = wt.get("path") or ""
    if not os.path.isdir(path):
        return {"ok": False, "error": "its worktree folder is gone"}
    rc, head, _ = git(["rev-parse", "--abbrev-ref", "HEAD"], path)
    if rc != 0 or head.strip() != wt["branch"]:
        return {"ok": False, "error": f"the worktree is no longer on {wt['branch']} -- refresh"}
    rc, st, er = git(["status", "--porcelain=v1", "-z"], path)
    if rc != 0:
        return {"ok": False, "error": "git status failed: " + _wt_err(er, "unknown error")}
    files = sorted(_parse_porcelain_z(st))
    if not files:
        return {"ok": False, "error": "nothing uncommitted to checkpoint"}
    rc, _, er = git(["add", "-A"], path, timeout=60)
    if rc != 0:
        return {"ok": False, "error": "git add failed: " + _wt_err(er, "unknown error")}
    rc, _, er = git(["commit", "-m", _bg_checkpoint_message(it)], path, timeout=120)
    if rc != 0:
        return {"ok": False, "error": "git commit failed: " + _wt_err(er, "unknown error")}
    rc, sha, _ = git(["rev-parse", "HEAD"], path)
    sha = sha.strip()[:40] if rc == 0 else ""
    _bg_agents_cache[1] = 0.0
    _wt_stranded_cache[1] = None
    out = {"ok": True, "sha": sha, "branch": wt["branch"], "files": len(files), "launched": False}
    if not relaunch:
        return out
    task, last = "", ""
    try:
        jsonl = (find_jsonl or _bg_agent_jsonl)(it["session_id"], aid)
        if jsonl:
            task = (read_task or _bg_read_task)(jsonl)
            last = _bg_last_assistant_text((read_tail or _bg_read_tail)(jsonl))
    except Exception as e:
        _log_bg_error("background agents", e)
    r = (launch or _launch_session)({"cwd": path, "prompt": _bg_checkpoint_prompt(it, task, files, last, sha)})
    out["launched"] = bool(r.get("ok"))
    if not r.get("ok"):
        out["launch_error"] = r.get("error") or "the session didn't start"
    return out
