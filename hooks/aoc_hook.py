"""
AOC Hook — automatická registrácia agentov v Agent Operations Center.
Fires on PreToolUse / PostToolUse pre 'Agent' tool.
AOC monitor musí bežať na http://localhost:5151

Deployment template: MONITOR_SCRIPT and PYTHONW below are filled in by
setup.py when it copies this file into ~/.claude/hooks/ (they point outside
this file's own directory, so they can't be derived from __file__ the way
the log/lock paths are). Editing this copy in the repo doesn't affect an
already-deployed hook — re-run setup.py to redeploy.
"""
import json
import os
import sys
import hashlib
import urllib.request
import urllib.error
from datetime import datetime

AOC_URL = "http://127.0.0.1:5151"
MONITOR_SCRIPT = "__MONITOR_SCRIPT__"
PYTHONW = "__PYTHONW__"
AOC_TOKEN_FILE = os.path.join(os.path.dirname(MONITOR_SCRIPT), "aoc_token.txt")

# Derived from this file's own location (wherever it actually got deployed
# to, e.g. ~/.claude/hooks/) rather than hardcoded, so the same deployed
# copy works regardless of which user account/home directory it's under.
_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))

LOCK_FILE = os.path.join(_HOOKS_DIR, "aoc_monitor.lock")


def _find_claude_ancestor_pid():
    """Walk up the process tree from this hook's own PID looking for the
    nearest ancestor named claude.exe, using CreateToolhelp32Snapshot
    directly (no subprocess spawn -- tasklist/PowerShell per call would add
    real per-turn latency since this hook fires on every single turn).
    Live-tested: ~7ms per call, correctly found the real claude.exe PID
    through several intermediate shell hops. Lets AOC's dashboard offer a
    "force stop this session" action -- it currently has no way at all to
    map a session_id back to the actual OS process running it."""
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    TH32CS_SNAPPROCESS = 0x00000002
    try:
        kernel32 = ctypes.windll.kernel32
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snapshot or snapshot == -1:
            return None
        procs = {}
        try:
            entry = PROCESSENTRY32()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
            if kernel32.Process32First(snapshot, ctypes.byref(entry)):
                while True:
                    name = entry.szExeFile.decode("mbcs", "ignore").lower()
                    procs[entry.th32ProcessID] = (name, entry.th32ParentProcessID)
                    if not kernel32.Process32Next(snapshot, ctypes.byref(entry)):
                        break
        finally:
            kernel32.CloseHandle(snapshot)

        pid = os.getpid()
        seen = set()
        for _ in range(20):  # cap depth as a safety net against any cycle
            if pid in seen or pid not in procs:
                break
            seen.add(pid)
            name, parent_pid = procs[pid]
            if name == "claude.exe":
                return pid
            pid = parent_pid
        return None
    except Exception:
        return None


def ensure_monitor() -> bool:
    """Start AOC monitor if not running. Returns (is_running, just_started)."""
    import os
    try:
        urllib.request.urlopen(f"{AOC_URL}/status", timeout=1)
        return True
    except Exception:
        pass

    import subprocess, time

    # Check lock file — another hook may already be starting the monitor
    lock_exists = os.path.exists(LOCK_FILE)
    if lock_exists:
        try:
            if time.time() - os.path.getmtime(LOCK_FILE) > 30:
                os.remove(LOCK_FILE)
                lock_exists = False
        except Exception:
            lock_exists = False
    if not lock_exists:
        try:
            with open(LOCK_FILE, "w") as f:
                f.write(str(os.getpid()))
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0  # SW_HIDE
            subprocess.Popen(
                [PYTHONW, MONITOR_SCRIPT, "--headless"],
                startupinfo=si,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
        except Exception:
            pass

    # Poll until monitor is ready (max 6s)
    for _ in range(12):
        time.sleep(0.5)
        try:
            urllib.request.urlopen(f"{AOC_URL}/status", timeout=1)
            try:
                os.remove(LOCK_FILE)
            except Exception:
                pass
            # Open browser on first start per session (flag reset on reboot via TEMP)
            _open_browser_once()
            return True
        except Exception:
            pass

    try:
        os.remove(LOCK_FILE)
    except Exception:
        pass
    return False


def _open_browser_once():
    """Open AOC in default browser — only once per boot (flag in TEMP)."""
    import os, subprocess
    flag = os.path.join(os.environ.get("TEMP", "C:/Windows/Temp"), "aoc_browser_opened.flag")
    if os.path.exists(flag):
        return
    try:
        with open(flag, "w") as f:
            f.write("1")
        # Use Windows 'start' to open default browser — non-blocking
        subprocess.Popen(
            ["cmd", "/c", "start", "", f"{AOC_URL}"],
            creationflags=0x08000000,
        )
    except Exception:
        pass


_LOG_PATH = os.path.join(_HOOKS_DIR, "aoc_hook_debug.log")
_LOG_MAX = 200 * 1024  # 200 KB

def _log_ts() -> str:
    """Timestamp prefix for debug log lines. The log previously had none at
    all -- every line looked identical regardless of when it happened, which
    made correlating a live bug report against the log (e.g. "did the hook
    fire in the last minute?") impossible without cross-checking file mtime."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _rotate_log():
    import os
    try:
        if os.path.getsize(_LOG_PATH) > _LOG_MAX:
            with open(_LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            with open(_LOG_PATH, "w", encoding="utf-8") as f:
                f.writelines(lines[len(lines) // 2:])
    except Exception:
        pass


def _read_aoc_token():
    """monitor.py's _check_auth() disables the localhost bypass while a
    remote tunnel is active (cloudflared/ngrok forward traffic that also
    looks like it came from 127.0.0.1, so the bypass can't tell a real
    local request from a tunneled one) -- meaning our own /update POSTs,
    which run on the same machine, would get 401'd during that window
    unless authenticated the same way a browser tab is. A tunnel is only
    ever started after monitor.py has provisioned a token (it refuses to
    expose a tunnel with none), so this file exists exactly when it's
    needed and is harmlessly absent otherwise."""
    try:
        with open(AOC_TOKEN_FILE, "r", encoding="utf-8") as f:
            t = f.read().strip()
            return t if len(t) >= 16 else None
    except Exception:
        return None


# ── Optional cloud backend forwarding (AOC SaaS) ────────────────────────
# Both files absent by default -- local-only mode, today's exact behavior,
# zero overhead/latency change for anyone who hasn't opted into the cloud
# product. Populated by setup.py's --cloud-url/--cloud-key flags. Kept
# entirely separate from AOC_URL/AOC_TOKEN_FILE above: this machine's own
# monitor.py dashboard keeps working byte-for-byte identically regardless
# of whether cloud forwarding is configured -- the cloud POST is an
# additional, independent forward, never a replacement for the local one.
AOC_CLOUD_URL_FILE = os.path.join(_HOOKS_DIR, "aoc_cloud_url.txt")
AOC_API_KEY_FILE = os.path.join(_HOOKS_DIR, "aoc_api_key.txt")
# Cloud updates that couldn't be delivered wait here, one JSON job per line,
# until monitor.py's _cloud_command_worker drains them in order (see
# _post_cloud_with_retry). Capped so a machine that stays offline, or has
# monitor.py stopped, can't grow it without bound.
AOC_CLOUD_SPOOL_FILE = os.path.join(_HOOKS_DIR, "aoc_cloud_spool.jsonl")
_CLOUD_SPOOL_MAX_BYTES = 5 * 1024 * 1024


def _read_cloud_config():
    try:
        with open(AOC_CLOUD_URL_FILE, "r", encoding="utf-8") as f:
            url = f.read().strip()
        with open(AOC_API_KEY_FILE, "r", encoding="utf-8") as f:
            key = f.read().strip()
        if url and key:
            return url.rstrip("/"), key
    except Exception:
        pass
    return None, None


def _post_cloud_with_retry(url: str, key: str, path: str, data: dict) -> None:
    """Runs ONLY inside the detached process _dispatch_cloud_post spawns,
    never in the original hook invocation -- see that function's docstring
    for why. A real internet round trip needs more tolerance than the
    local-only post_aoc() above: a longer timeout and a couple of
    short-backoff retries for transient failures, neither of which
    localhost calls have ever needed. Final failure is silently swallowed,
    matching post_aoc()'s own "best-effort, monitoring must never disrupt
    the user's actual CLI session" philosophy.

    Undeliverable updates are spooled rather than lost (a laptop offline
    for a whole session used to lose all of it), and monitor.py drains the
    spool. Order matters because the cloud upserts field by field: an old
    "running" replayed after a newer "done" would move the agent backwards.
    So while anything is still spooled, a new update goes to the back of
    the spool instead of jumping ahead of it."""
    if _cloud_spool_pending():
        _cloud_spool_append(path, data)
        return
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8", "Authorization": f"Bearer {key}"}
    for delay in (0, 0.3, 1.0):
        if delay:
            import time as _time
            _time.sleep(delay)
        try:
            req = urllib.request.Request(f"{url}{path}", data=body, method="POST", headers=headers)
            urllib.request.urlopen(req, timeout=5)
            return
        except Exception:
            continue
    _cloud_spool_append(path, data)


def _cloud_spool_pending(spool_path: str = None) -> bool:
    spool_path = spool_path or AOC_CLOUD_SPOOL_FILE
    for p in (spool_path, spool_path + ".draining"):
        try:
            if os.path.getsize(p) > 0:
                return True
        except OSError:
            pass
    return False


def _cloud_spool_append(path: str, data: dict, spool_path: str = None) -> None:
    spool_path = spool_path or AOC_CLOUD_SPOOL_FILE
    try:
        if os.path.exists(spool_path) and os.path.getsize(spool_path) > _CLOUD_SPOOL_MAX_BYTES:
            return  # full -- dropping new data beats unbounded growth
        line = json.dumps({"path": path, "data": data}, ensure_ascii=False)
        with open(spool_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _dispatch_cloud_post(path: str, data: dict) -> None:
    """Fires the cloud forward (if configured) in a fully separate,
    detached OS process -- NOT a background thread. run_hook.pyw already
    does p.wait(timeout=25) on this script's own process, and Python only
    fully exits once every non-daemon thread finishes, so a thread here
    would still extend that wait by however long the retry/backoff above
    takes. A detached child process has no such coupling: this process
    calls Popen and moves on immediately, regardless of how long the
    child takes. Mirrors ensure_monitor()'s own detached-Popen pattern
    (same problem, same fix, already established in this file)."""
    url, key = _read_cloud_config()
    if not url or not key:
        return
    import subprocess
    import tempfile
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    try:
        json.dump({"url": url, "key": key, "path": path, "data": data}, tmp)
        tmp.close()
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        subprocess.Popen(
            [PYTHONW, os.path.abspath(__file__), "--cloud-post", tmp.name],
            startupinfo=si,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except Exception:
        try:
            os.remove(tmp.name)
        except Exception:
            pass


def post_aoc(path: str, data: dict) -> bool:
    _rotate_log()
    ok = False
    try:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}
        token = _read_aoc_token()
        if token:
            headers["X-AOC-Token"] = token
        req = urllib.request.Request(
            f"{AOC_URL}{path}",
            data=body,
            method="POST",
            headers=headers,
        )
        urllib.request.urlopen(req, timeout=2)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{_log_ts()} POST_OK {path} sid={data.get('session_id','?')[:8]} project={data.get('project','?')} display_name={data.get('display_name','?')} active={data.get('session_active','?')} agent={data.get('agents',[{}])[0].get('name','?')[:30]}\n---\n")
        ok = True
    except Exception as e:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{_log_ts()} POST_FAIL {path} err={e}\n---\n")
    # Cloud forwarding is independent of the local POST's outcome above (a
    # customer's cloud dashboard shouldn't miss data just because their
    # local monitor.py happened to be briefly down) -- always attempted,
    # a no-op if no cloud config is present.
    _dispatch_cloud_post(path, data)
    return ok


def agent_id_from_hook(hook: dict, desc: str) -> str:
    """Prefer tool_use_id -- the ID Anthropic assigns per tool invocation,
    confirmed (2026-07-15 probe) present and byte-identical between a call's
    PreToolUse and PostToolUse payloads -- over hashing the description text
    alone. Two different subagent calls that happen to share identical wording
    would otherwise collide on the description-hash and get merged into one
    card by /update's upsert; tool_use_id is unique per invocation by
    construction, so this closes that gap. Falls back to the description hash
    if tool_use_id is ever missing. Keeps the same "ag_" + 10 hex chars shape
    either way, since other code (frontend .slice(-4) unit labels, the SQLite
    agents table) assumes that shape."""
    key = hook.get("tool_use_id") or desc
    return "ag_" + hashlib.md5(key.encode()).hexdigest()[:10]


def now_ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _parse_subagent_tool_uses(transcript_path: str, agent_id: str) -> list:
    """Shared single-pass reader for a subagent's own transcript file, used
    by both _extract_files_changed and _extract_child_agent_ids below so
    that a single PostToolUse invocation needing both (main() does, for
    every agent that completes) opens and json.loads's the transcript file
    ONCE instead of twice. This hook runs synchronously on the calling
    agent's blocking critical path, so for a subagent that made hundreds of
    tool calls, a second full read+parse pass over the same file is exactly
    the kind of avoidable latency that the pre-filter below already exists
    to prevent -- doubling it here would have undone half the point.

    Claude Code writes each subagent's full transcript separately at
    <parent_transcript_dir>/subagents/agent-<agentId>.jsonl (confirmed
    live, 2026-07-27) -- same JSONL shape as the main session transcript.

    Returns a flat list of every tool_use content block (as dicts) across
    all "assistant" lines, in file order. Empty list if transcript_path/
    agent_id are missing or invalid, the subagent file doesn't exist, or
    anything goes wrong while reading it -- never fatal, callers all treat
    "no blocks found" identically to "nothing to extract"."""
    if not transcript_path or not transcript_path.endswith(".jsonl") or not agent_id:
        return []
    sub_path = os.path.join(transcript_path[:-len(".jsonl")], "subagents", f"agent-{agent_id}.jsonl")
    if not os.path.isfile(sub_path):
        return []
    blocks = []
    try:
        with open(sub_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                # Cheap pre-filter before paying for a full json.loads: the
                # lines we actually want ("assistant", carrying tool_use)
                # are typically the *small* ones -- the "user" lines (tool
                # results fed back in) carry the actual output of every
                # Read/Grep/Bash call and can be large. Safe: any real
                # `"type":"assistant"` line necessarily contains this exact
                # substring, so this can only skip the json.loads on lines
                # that are already going to be discarded -- never a false
                # skip of a real assistant line.
                if not line or '"assistant"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get("type") != "assistant":
                    continue
                for block in (obj.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        blocks.append(block)
    except Exception:
        return []
    return blocks


def _extract_files_changed(transcript_path: str, agent_id: str, _blocks: list = None) -> list:
    """Extract Write/Edit/MultiEdit tool_use blocks from a subagent's own
    transcript, since the Agent tool's own PostToolUse payload only carries
    aggregate stats (tool_response.toolStats.editFileCount/linesAdded/
    linesRemoved) -- never per-file paths, confirmed against a live
    captured payload (2026-07-27). Returns [{path, type, lines}, ...]
    matching the shape AOC's UI/DB have expected all along (agent card,
    detail panel, Compare views, GRAPH/TREE shared-file edges, right-panel
    FILES tab -- ~15 call sites, previously always empty since nothing
    ever populated this).

    `_blocks`, if given, is a pre-parsed block list from
    _parse_subagent_tool_uses (main() below passes one shared list to both
    this and _extract_child_agent_ids so the transcript file is only read
    once) -- when omitted, this reads and parses the file itself, same as
    before this parameter existed."""
    blocks = _parse_subagent_tool_uses(transcript_path, agent_id) if _blocks is None else _blocks
    files = {}
    try:
        for block in blocks:
            name = block.get("name")
            inp = block.get("input") or {}
            path = inp.get("file_path")
            if not path:
                continue
            # AOC's UI extracts a filename for display via
            # path.split('/').pop() in several places (confirmed
            # via grep) -- a raw Windows path never splits on that,
            # showing the full path instead of just the basename.
            # Same forward-slash convention this file already uses
            # for MONITOR_SCRIPT/PYTHONW above.
            path = path.replace("\\", "/")
            if name == "Write":
                content = inp.get("content", "") or ""
                files[path] = {"path": path, "type": "new",
                                "lines": content.count("\n") + (1 if content else 0)}
            elif name == "Edit":
                old, new = inp.get("old_string", "") or "", inp.get("new_string", "") or ""
                files[path] = {"path": path, "type": "changed",
                                "lines": max(old.count("\n"), new.count("\n")) + 1}
            elif name == "MultiEdit":
                edits = inp.get("edits") or []
                lines = sum(
                    max((e.get("old_string", "") or "").count("\n"),
                        (e.get("new_string", "") or "").count("\n")) + 1
                    for e in edits if isinstance(e, dict)
                )
                if lines:
                    files[path] = {"path": path, "type": "changed", "lines": lines}
    except Exception:
        return []
    return list(files.values())


def _extract_child_agent_ids(transcript_path: str, agent_id: str, _blocks: list = None) -> list:
    """Extract nested Task/Agent tool_use blocks from a subagent's own
    transcript (same file _extract_files_changed reads above, via the
    shared _parse_subagent_tool_uses) -- i.e. cases where this subagent
    itself spawned a child subagent. TREE view has always needed a
    parent_id field to draw any hierarchy at all, but nothing in this file
    ever populated it (AOC's own TREE view has been showing a flat list
    with an "add parent_id to agent updates" hint on every real session,
    for every install, since the feature was added).

    Live-verified 2026-07-29: a nested tool_use's own "id" is exactly what
    agent_id_from_hook() hashes to compute that child's AOC agent id.
    Hashing a real nested tool_use id pulled from a live subagent
    transcript reproduced an id already sitting in history.db under the
    exact same description text -- confirming Claude Code fires that
    child's own independent PreToolUse/PostToolUse hook chain the same way
    it does for a top-level session's direct Agent calls (it's a
    "sidechain" per the transcript's own isSidechain field, but hooked
    identically). No protocol change needed on Claude Code's side, just
    this reverse lookup from the parent's own transcript.

    `_blocks` -- see _extract_files_changed's own docstring, same shared-
    parse parameter.

    Returns a list of ag_<hash> child ids (empty if none found, the
    transcript is missing, or agent_id_from_hook isn't reachable for any
    reason -- never fatal, mirrors _extract_files_changed's own
    fail-quiet contract)."""
    blocks = _parse_subagent_tool_uses(transcript_path, agent_id) if _blocks is None else _blocks
    child_ids = []
    try:
        for block in blocks:
            if block.get("name") not in ("Task", "Agent"):
                continue
            child_tool_use_id = block.get("id")
            if child_tool_use_id:
                child_ids.append(agent_id_from_hook({"tool_use_id": child_tool_use_id}, ""))
    except Exception:
        return []
    return child_ids


def main():
    args = sys.argv[1:]

    # Internal self-invocation: a detached process dispatched by
    # _dispatch_cloud_post to run the cloud retry logic without extending
    # the original hook invocation's lifetime (see that function's
    # docstring). Not a real Claude Code hook event -- handled and
    # returned before any of the normal hook-parsing logic below.
    if "--cloud-post" in args:
        idx = args.index("--cloud-post")
        tmp_path = args[idx + 1] if idx + 1 < len(args) else None
        if tmp_path:
            try:
                with open(tmp_path, encoding="utf-8") as f:
                    job = json.load(f)
                _post_cloud_with_retry(job["url"], job["key"], job["path"], job["data"])
            except Exception:
                pass
            finally:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
        return

    # Support --file <path> from PowerShell wrapper (avoids pythonw stdin issues)
    file_arg = None
    for i, a in enumerate(args):
        if a == "--file" and i + 1 < len(args):
            file_arg = args[i + 1]
            break

    try:
        if file_arg:
            with open(file_arg, encoding="utf-8-sig") as f:
                raw = f.read()
        else:
            raw = sys.stdin.read()
        hook = json.loads(raw)
    except Exception:
        sys.exit(0)

    event_name = hook.get("hook_event_name", "")
    session_id = hook.get("session_id", "default")
    cwd = hook.get("cwd", "")

    # Every other log line in this file only gets written once an event passes
    # every filter and reaches post_aoc() — an event that gets quietly
    # filtered out (wrong tool_name, unrecognized event_name) leaves zero
    # trace, indistinguishable from "the hook never fired at all". This one
    # always fires (right after JSON parsing succeeds) so the next time an
    # agent goes missing, grepping TRACE tells us which case it was.
    try:
        _rotate_log()
        with open(_LOG_PATH, "a", encoding="utf-8") as _f:
            _f.write(f"{_log_ts()} TRACE event={event_name} tool={hook.get('tool_name','')} sid={session_id[:8]}\n---\n")
    except Exception:
        pass

    import os as _os, re as _re

    def _decode_project_name(encoded: str) -> str:
        """Decode Claude Code's encoded project path (C--Users-marek-ai-antivirus)
        to the real project directory name, using filesystem to resolve hyphens."""
        m = _re.match(r'^([A-Za-z])--(.+)$', encoded)
        if not m:
            return encoded
        path = m.group(1) + ":\\"
        parts = m.group(2).split('-')
        i = 0
        while i < len(parts):
            # try longest match first (handles hyphenated dir names like ai-antivirus)
            matched = False
            for j in range(len(parts), i, -1):
                candidate = _os.path.join(path, '-'.join(parts[i:j]))
                if _os.path.isdir(candidate):
                    path = candidate
                    i = j
                    matched = True
                    break
            if not matched:
                break
        name = _os.path.basename(path)
        # skip generic home/root names
        home_name = _os.path.basename(_os.path.expanduser("~"))
        return "" if name in (home_name, "Users", "home", "") else name

    def _ai_title_from_transcript(transcript_path: str) -> str:
        """Read the ai-title entry from transcript JSONL — this is the session title
        Claude Code generates and shows at startup (e.g. 'Continue Ai Antivirus')."""
        if not transcript_path or not _os.path.isfile(transcript_path):
            return ""
        try:
            with open(transcript_path, encoding="utf-8", errors="replace") as f:
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

    def _project_from_session_scan(sid: str) -> str:
        """Scan ~/.claude/projects/ dirs for a file starting with session_id."""
        base = _os.path.join(_os.path.expanduser("~"), ".claude", "projects")
        if not _os.path.isdir(base):
            return ""
        try:
            for encoded in _os.listdir(base):
                proj_dir = _os.path.join(base, encoded)
                if not _os.path.isdir(proj_dir):
                    continue
                if any(f.startswith(sid) for f in _os.listdir(proj_dir)):
                    return _decode_project_name(encoded)
        except Exception:
            pass
        return ""

    transcript_path = hook.get("transcript_path", "")

    # ai-title is the real session name Claude shows at startup — use as display_name
    ai_title = _ai_title_from_transcript(transcript_path)

    # project: decode from transcript path or scan; fallback to cwd basename
    def _project_from_transcript(tp: str) -> str:
        tp = tp.replace("\\", "/")
        m = _re.search(r'\.claude/projects/([^/]+)/', tp)
        if not m:
            return ""
        return _decode_project_name(m.group(1))

    project = (_project_from_transcript(transcript_path) if transcript_path else ""
               or _project_from_session_scan(session_id)
               or (_os.path.basename(cwd) if cwd else ""))

    # Heartbeat — keep CLI session alive in AOC
    # UserPromptSubmit = user is actively chatting → mark active
    # Stop = Claude finished a turn, but session is still open → just refresh last_seen
    if event_name in ("UserPromptSubmit", "Stop"):
        ensure_monitor()
        payload = {"session_id": session_id, "cwd": cwd, "project": project}
        if ai_title:
            payload["display_name"] = ai_title
        payload["session_active"] = True  # both UserPromptSubmit and Stop keep session alive
        # Stop = Claude just finished a turn, control is back with the human ("waiting on
        # you"); UserPromptSubmit = the human just replied, Claude is about to work again.
        payload["waiting_on_you"] = (event_name == "Stop")
        host_pid = _find_claude_ancestor_pid()
        if host_pid:
            payload["host_pid"] = host_pid
        post_aoc("/update", payload)
        sys.exit(0)

    tool_name = hook.get("tool_name", "")
    if tool_name != "Agent":
        sys.exit(0)

    is_pre = event_name == "PreToolUse"
    is_post = event_name == "PostToolUse"
    if not (is_pre or is_post):
        sys.exit(0)

    ensure_monitor()

    tool_input = hook.get("tool_input", {})
    description = tool_input.get("description", "Agent")
    # subagent_type (e.g. "general-purpose", "Explore", "Plan") is sitting
    # right there in tool_input on both PreToolUse and PostToolUse -- read
    # once here instead of separately in each branch below.
    subagent_type = tool_input.get("subagent_type", "")
    agent_id = agent_id_from_hook(hook, description)
    ts = now_ts()

    def parse_tasks(desc: str) -> list:
        """Extract task items from agent description."""
        import re
        lines = desc.splitlines()
        tasks = []
        for line in lines:
            line = line.strip()
            # numbered list: "1. Task" or "1) Task"
            m = re.match(r'^(\d+)[.)]\s+(.+)', line)
            if m:
                tasks.append({"label": m.group(2)[:60], "done": False})
                continue
            # bullet list: "- Task" or "• Task" or "* Task"
            m = re.match(r'^[-•*]\s+(.+)', line)
            if m:
                tasks.append({"label": m.group(1)[:60], "done": False})
                continue
        # fallback: comma-separated items after colon (e.g. "Round 3: A, B, C")
        if not tasks and ':' in desc:
            after = desc.split(':', 1)[1].strip()
            parts = [p.strip() for p in after.split(',') if p.strip()]
            if 1 < len(parts) <= 10:
                tasks = [{"label": p[:60], "done": False} for p in parts]
        return tasks or [{"label": "Running...", "done": False}]

    if is_pre:
        # ── PreToolUse: Register agent as running ──
        tasks = parse_tasks(description)
        agent = {
            "id": agent_id,
            "name": description[:50],
            "icon": "AI",
            "description": description,
            "status": "running",
            "tasks": tasks,
            "log": [f"Started {ts}"],
            "started_at": ts,
        }
        if subagent_type:
            agent["subagent_type"] = subagent_type
        post_aoc("/update", {
            "session_id": session_id,
            "cwd": cwd,
            "project": project,
            "session_active": True,
            "agents": [agent]
        })
    else:
        # ── PostToolUse: Mark agent done ──
        tool_output = hook.get("tool_response") or hook.get("tool_result") or hook.get("tool_output", {})
        tool_error = hook.get("tool_error")
        success = not tool_error

        # A completed Agent tool's real response shape is
        # {status, content:[{type:"text", text:...}], usage:{...}, totalTokens,
        # resolvedModel, ...} — there's no "result" key (confirmed against a
        # live captured payload), so this previously always read empty.
        result_summary = ""
        if isinstance(tool_output, dict):
            content = tool_output.get("content")
            if isinstance(content, list) and content and isinstance(content[0], dict):
                result_summary = str(content[0].get("text", ""))[:80]
            elif tool_output.get("result"):
                result_summary = str(tool_output["result"])[:80]
        elif isinstance(tool_output, str):
            result_summary = tool_output[:80]

        # Real per-invocation token usage — confirmed present on a live
        # payload (tool_response.totalTokens / .usage), unlike the session-level
        # transcript scanner's running totals which lag by up to 30s and can't
        # be attributed to one specific agent. AOC's UI already has ~15 display
        # sites wired to agent.tokens_used, they just never received data.
        tokens_used = 0
        tool_use_count = None
        usage_fields = {}
        if isinstance(tool_output, dict):
            tokens_used = tool_output.get("totalTokens") or 0
            # How many tool calls the subagent itself made -- a distinct
            # signal from tokens_used (a high token count with few tool
            # calls behaves very differently from a low count with many).
            # Also sitting right there in tool_response, never captured.
            tool_use_count = tool_output.get("totalToolUseCount")
            usage = tool_output.get("usage")
            model = tool_output.get("resolvedModel")
            if isinstance(usage, dict) and model:
                # field names match _calc_cost's params / the session-level
                # transcript scanner's stats dict in monitor.py, so monitor.py
                # can price this the exact same way it already prices sessions.
                usage_fields = {
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "cache_write_tokens": usage.get("cache_creation_input_tokens", 0),
                    "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
                    "model": model,
                }

        # On failure, capture the fullest error text actually available — AOC's
        # UI (agent card, detail panel, SQLite agents.error_msg) already has
        # display code wired to this field, it just never received real data.
        error_message = ""
        if not success:
            if isinstance(tool_error, str) and tool_error.strip():
                error_message = tool_error
            elif isinstance(tool_output, dict) and tool_output.get("error"):
                error_message = str(tool_output["error"])
            elif isinstance(tool_output, dict) and tool_output.get("result"):
                error_message = str(tool_output["result"])
            elif isinstance(tool_output, str) and tool_output.strip():
                error_message = tool_output
            else:
                error_message = "Unknown error"
            error_message = error_message[:4000]

        # Mark all tasks as done on completion
        tasks_done = parse_tasks(description)
        for t in tasks_done:
            t["done"] = True

        agent_update = {
            "id": agent_id,
            # Normally redundant with PreToolUse (which already set these), but if
            # that earlier event was lost (e.g. AOC restarted mid-task), this is
            # the only payload monitor.py has to work with when deciding whether
            # to still surface the agent instead of silently dropping it.
            "name": description[:50],
            "icon": "AI",
            "description": description,
            "status": "done" if success else "error",
            "tasks": tasks_done,
            "log": [result_summary or ("Completed" if success else "Error")],
            "completed_at": ts,
        }
        if error_message:
            agent_update["error_message"] = error_message
        if tokens_used:
            agent_update["tokens_used"] = tokens_used
        if tool_use_count is not None:
            agent_update["tool_use_count"] = tool_use_count
        if usage_fields:
            agent_update.update(usage_fields)
        if subagent_type:
            agent_update["subagent_type"] = subagent_type
        # Claude Code's own subagent transcript file is named after
        # tool_response.agentId, NOT AOC's internal ag_<hash> id (confirmed
        # live -- the first attempt using agent_id here always missed).
        real_agent_id = tool_output.get("agentId") if isinstance(tool_output, dict) else None
        # Parsed once and shared below -- _extract_files_changed and
        # _extract_child_agent_ids both need every tool_use block from this
        # same subagent transcript file, and this hook is on the calling
        # agent's blocking critical path, so reading + json.loads'ing the
        # file twice back-to-back here would be a needless doubling.
        _subagent_blocks = _parse_subagent_tool_uses(hook.get("transcript_path", ""), real_agent_id)
        files_changed = _extract_files_changed(hook.get("transcript_path", ""), real_agent_id, _subagent_blocks)
        if files_changed:
            agent_update["files_changed"] = files_changed
        child_ids = _extract_child_agent_ids(hook.get("transcript_path", ""), real_agent_id, _subagent_blocks)
        if child_ids:
            agent_update["child_ids"] = child_ids

        post_aoc("/update", {
            "session_id": session_id,
            "cwd": cwd,
            "agents": [agent_update]
        })


if __name__ == "__main__":
    try:
        main()
    except Exception as _e:
        # main()'s own try/except only guards JSON parsing (sys.exit(0) is the
        # intentional early-out for malformed input) — anything that throws
        # after that point (e.g. in transcript scanning) previously died fully
        # silent, since post_aoc() is the only thing that writes to this log.
        # A live agent that never appeared in AOC with zero trace anywhere
        # pointed straight at this blind spot.
        try:
            import traceback
            with open(_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"{_log_ts()} UNHANDLED_EXCEPTION: {_e!r}\n{traceback.format_exc()}---\n")
        except Exception:
            pass
