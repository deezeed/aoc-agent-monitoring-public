# AOC server, part 14: permission advisor.
# monitor.py exec()s server/NN_*.py in number order into ONE shared
# namespace (its own) -- not an importable module: names from earlier
# parts are plain globals here, and __file__ is monitor.py.
# ── Permission advisor (History -> PERMISSIONS) ───────────────────────────
# Which permission prompts hold you up, and allow rules that would stop
# them -- never dangerous ones. Evidence: every prompt Claude Code shows
# (its PermissionRequest hook asks POST /permission/request first, so each
# one is logged in permission_log.jsonl; its answer and wait come from the
# transcript), auto-mode denials and rejected calls from the transcripts,
# and the one-off rules "Yes, don't ask again" saved in settings files
# (each was a prompt you answered yes). A suggested rule is only ever a
# read-only command, a project's own test/build/lint script, a web domain,
# a reading MCP tool, a skill or a folder to read. Deleting, moving and
# writing files, installing, network transfers, git changes, running any
# code (python, node, powershell, npx ...), system changes and deploys are
# never suggested -- and broad rules like that which you already have are
# listed with a Remove button. Rules go to ~/.claude/settings.json (every
# project) through the one-click fixes, so each one has a preview and Undo.
PERM_LOG_FILE = os.path.join(AOC_DATA_DIR, "permission_log.jsonl")
_PERM_LOG_KEEP_S = 90 * 86400
_PERM_LOG_MAX_BYTES = 4 * 1024 * 1024
_PERM_WAIT_CAP_S = 1800
_PERM_INPUT_FIELDS = ("command", "file_path", "notebook_path", "path", "url", "skill")
_PERM_SHELLS = ("Bash", "PowerShell")
_PERM_NOT_PROMPTS = {"AskUserQuestion", "ExitPlanMode", "EnterPlanMode"}   # questions to you, not permissions
_PERM_DENIAL_REASONS = {"Denied by auto mode": "denied", "Auto-mode check unavailable": "denied",
                        "You rejected the call": "rejected"}
_PERM_SKIP = {"cd", "set-location", "sl", "chdir", "pushd", "popd", "echo", "write-output", "write-host", "true",
              "false", "sleep", "start-sleep", "exit", "clear", "cls", ":"}
# PowerShell cmdlets that take script blocks (Where-Object { ... }, Select-Object
# @{e={...}}, Format-Table, Sort-Object ...) run any code, so they're not here.
_PERM_READ = {"ls", "dir", "pwd", "cat", "head", "tail", "wc", "which", "type", "file", "stat", "du", "df",
              "tree", "whoami", "uname", "grep", "egrep", "fgrep", "rg", "findstr", "diff", "cmp",
              "jq", "basename", "dirname", "realpath", "readlink", "nslookup", "ping", "netstat", "tasklist",
              "systeminfo", "ver", "gci", "gc", "gi", "gps", "ps", "gl", "gcm", "sls", "measure", "out-string",
              "out-null", "select-string", "nproc", "free", "uptime", "id", "groups", "column", "nl", "strings", "sha256sum", "md5sum",
              "help", "man"}
_PERM_PS_READ_VERBS = {"get", "test", "measure", "resolve", "convertfrom", "convertto", "receive", "show"}
_PERM_PS_VERBS = _PERM_PS_READ_VERBS | {
    "add", "clear", "close", "copy", "enter", "exit", "find", "format", "hide", "join", "lock", "move", "new", "open",
    "optimize", "pop", "push", "redo", "remove", "rename", "reset", "resize", "search", "select", "set", "skip",
    "split", "step", "switch", "undo", "unlock", "watch", "connect", "disconnect", "read", "send", "write", "compare",
    "compress", "convert", "dismount", "edit", "expand", "export", "group", "import", "initialize", "limit", "merge",
    "mount", "out", "publish", "restore", "save", "sync", "unpublish", "update", "approve", "assert", "build",
    "complete", "confirm", "deny", "deploy", "disable", "enable", "install", "invoke", "register", "request",
    "restart", "resume", "start", "stop", "submit", "suspend", "uninstall", "unregister", "wait", "debug", "repair",
    "trace", "block", "grant", "protect", "revoke", "unblock", "unprotect", "use", "where", "foreach", "sort",
    "tee", "backup", "checkpoint", "mock", "ping"}
_PERM_PS_BLOCK = {"where-object", "foreach-object", "select-object", "sort-object", "group-object", "format-table",
                  "format-list", "format-wide", "format-custom", "compare-object", "measure-command", "trace-command",
                  "get-job", "get-random"}
_PERM_SUB_READ = {
    "git": {"status", "rev-parse", "ls-files", "ls-tree", "blame", "describe", "shortlog", "cat-file",
            "merge-base", "count-objects"},
    "npm": {"ls", "list", "view", "info", "outdated", "why", "explain"},
    "pnpm": {"ls", "list", "why", "outdated"},
    "yarn": {"list", "why", "outdated", "info"},
    "pip": {"list", "show", "freeze"},
    "pip3": {"list", "show", "freeze"},
    "docker": {"ps", "images", "logs", "inspect", "version", "info", "stats"},
}
_PERM_SUB_PROJECT = {"cargo": {"test", "build", "check", "clippy"}, "go": {"test", "build", "vet"},
                     "dotnet": {"build", "test"}, "ruff": {"check"}}
_PERM_GH_READ = {("run", "list"), ("run", "view"), ("run", "watch"), ("pr", "list"), ("pr", "view"), ("pr", "diff"),
                 ("pr", "checks"), ("pr", "status"), ("issue", "list"), ("issue", "view"), ("issue", "status"),
                 ("release", "list"), ("release", "view"), ("repo", "view"), ("workflow", "list"),
                 ("workflow", "view"), ("auth", "status")}
_PERM_PROJECT_BIN = {"pytest", "vitest", "jest", "eslint", "tsc", "mypy", "mocha"}
_PERM_NPX_OK = {"tsc", "vitest", "jest", "eslint", "prettier"}
_PERM_PY_MODULES = {"pytest", "mypy", "unittest"}
_PERM_SCRIPT_OK = re.compile(r"^(test|tests|build|lint|typecheck|type-check|check|tsc|format:check)(:[\w:-]+)?$")
_PERM_NO = (
    ({"rm", "del", "erase", "rmdir", "rd", "remove-item", "ri", "mv", "move", "move-item", "mi", "ren", "rename",
      "rename-item", "rni", "shred", "unlink", "clear-content", "clc", "sdelete"}, "deletes or moves files"),
    ({"cp", "copy", "copy-item", "cpi", "xcopy", "robocopy", "new-item", "ni", "set-content", "add-content", "ac",
      "out-file", "tee", "tee-object", "touch", "mkdir", "md", "ln", "mklink", "truncate", "dd", "sed", "awk",
      "patch"}, "writes files (Claude's Edit and Write tools already cover file changes)"),
    ({"python", "python3", "py", "pythonw", "node", "deno", "bun", "ruby", "perl", "php", "java", "bash", "sh", "zsh",
      "fish", "cmd", "powershell", "pwsh", "wsl", "iex", "invoke-expression", "invoke-command", "icm", "start-job",
      "eval", "exec", "source", ".", "xargs", "env", "sudo", "runas", "start", "start-process", "saps",
      "foreach-object", "%", "&", "call", "nohup", "timeout", "watch", "npx", "uvx", "pipx", "make", "find",
      "forfiles", "wscript", "cscript", "mshta", "rundll32", "regsvr32", "certutil", "bitsadmin", "at",
      "osascript"}, "runs any code or program"),
    ({"curl", "wget", "invoke-webrequest", "iwr", "invoke-restmethod", "irm", "scp", "sftp", "ssh", "rsync", "ftp",
      "nc", "ncat", "telnet", "start-bitstransfer"}, "network transfer: can upload or download anything"),
    ({"winget", "choco", "scoop", "apt", "apt-get", "brew", "gem", "composer", "conda", "uv", "poetry", "pipenv"},
     "installs software"),
    ({"kill", "taskkill", "stop-process", "spps", "pkill", "killall", "shutdown", "restart-computer", "stop-computer",
      "sc", "net", "reg", "regedit", "schtasks", "set-executionpolicy", "icacls", "takeown", "attrib", "chmod",
      "chown", "format", "diskpart", "bcdedit", "setx", "netsh", "set-acl"}, "changes the system"),
    ({"vercel", "railway", "supabase", "firebase", "netlify", "terraform", "kubectl", "helm", "aws", "az", "gcloud",
      "heroku", "flyctl", "fly", "wrangler"}, "deploys or changes cloud resources"),
)
_PERM_SUB_NO = {"git": "changes your repository or talks to a remote", "npm": "installs packages or runs package scripts",
                "pnpm": "installs packages or runs package scripts", "yarn": "installs packages or runs package scripts",
                "pip": "installs software", "pip3": "installs software", "gh": "acts on GitHub",
                "docker": "starts, stops or deletes containers", "cargo": "runs any code or installs",
                "go": "runs any code or installs", "dotnet": "runs any code or installs", "ruff": "changes files"}
_PERM_SENSITIVE = re.compile(r"(^|/)(\.ssh|\.aws|\.gnupg|\.azure|\.kube|\.docker|\.claude|\.config/gcloud)(/|$)|\.env"
                             r"|credential|secret|password|token|wallet|keychain"
                             r"|/appdata/(local|roaming)/(google|microsoft/edge|bravesoftware|mozilla|opera)", re.I)
_PERM_MCP_READ = re.compile(r"^(get|list|search|read|query|fetch|view|find|describe|show|count|check|aggregate|"
                            r"lookup|retrieve)(_|-|$)", re.I)
_PERM_WORD_OK = re.compile(r"^[^\s*()\\\"'`$<>|&;{}\[\]]+$")
_PERM_TIER_WHY = {"read": "only reads", "project": "runs your project's own test/build/lint scripts"}
_perm_log_lock = threading.Lock()


def _perm_input_key(tool, inp) -> str:
    """Pure: the one input field that identifies a call (command, path, url...)."""
    if not isinstance(inp, dict) or str(tool or "").startswith("mcp__"):
        return ""
    for k in _PERM_INPUT_FIELDS:
        if inp.get(k) not in (None, ""):
            return str(inp[k])[:4000]
    return ""


def _perm_head(word) -> str:
    """Pure: '"C:\\x\\Git.EXE"' -> 'git'."""
    head = re.split(r"[\\/]", str(word).strip("\"'"))[-1].lower()
    return re.sub(r"\.(exe|cmd|bat|ps1)$", "", head)


_PERM_SCRIPT_WORDS = {"for", "foreach", "while", "until", "if", "elif", "elseif", "else", "case", "select", "function",
                      "do", "done", "then", "fi", "esac", "try", "catch", "finally", "switch", "param", "break",
                      "continue", "return", "throw", "begin", "process", "end", "trap"}


def _perm_simple_commands(cmd, shell="Bash"):
    """Pure: a shell command line -> (word lists of its simple commands,
    is_script). Splits on && || ; | and newlines outside quotes (heredoc
    bodies dropped); is_script = it has blocks or loops ({ }, if/for/while,
    try ...), which no single prefix rule can describe."""
    cmd = re.sub(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2\s*(?=\n|$)",
                 lambda mm: mm.group(0).split("\n", 1)[0], str(cmd or ""), flags=re.S)
    esc = "`" if shell == "PowerShell" else "\\"
    segs, buf, q, block, i = [], "", "", False, 0
    while i < len(cmd):
        ch = cmd[i]
        if q:
            buf += ch
            if ch == q:
                q = ""
            elif ch == esc and q == '"' and i + 1 < len(cmd):
                buf += cmd[i + 1]
                i += 1
        elif shell == "PowerShell" and cmd.startswith(("@'", '@"'), i):   # here-string, to '@ / "@ on its own line
            end = re.search(r"\n" + cmd[i + 1] + "@", cmd[i + 2:])
            j = len(cmd) if end is None else i + 2 + end.end()
            buf += cmd[i:j]
            i = j
            continue
        elif ch in "'\"":
            q = ch
            buf += ch
        elif ch == esc and i + 1 < len(cmd):
            buf += ch + cmd[i + 1]
            i += 1
        elif cmd.startswith(("&&", "||"), i):
            segs.append(buf)
            buf = ""
            i += 1
        elif ch in ";|\n":
            segs.append(buf)
            buf = ""
        elif cmd.startswith("${", i):              # ${VAR} is a variable, not a block
            j = cmd.find("}", i)
            j = len(cmd) - 1 if j < 0 else j
            buf += cmd[i:j + 1]
            i = j
        elif cmd.startswith("{}", i):              # find -exec {} placeholder
            buf += "{}"
            i += 1
        else:
            if ch in "{}":
                block = True
            buf += ch
        i += 1
    segs.append(buf)
    out = []
    for seg in segs:
        words = re.findall(r'"[^"]*"|\'[^\']*\'|[^\s"\']+', seg.strip())
        if words and not words[0].startswith("#"):
            out.append(words)
    script = block or any(w[0].lower() in _PERM_SCRIPT_WORDS for w in out)
    return out, script


def _perm_classify(words) -> dict:
    """Pure: a simple command's words -> {"tier": read|project|no|unknown,
    "why", "n": leading words a prefix rule keeps, "exact": rule is the whole
    command, "family": a short display name}."""
    if not words:
        return {"tier": "unknown", "why": "", "n": 0, "exact": False, "family": ""}
    head = _perm_head(words[0])
    low = [str(w).lower() for w in words]

    def res(tier, why, n, family=None, exact=False):
        if tier == "read" and re.search(r"[\\/]", str(words[0])):   # ./x/grep could be any program
            tier, why = "unknown", f"runs {head} from a path, not the standard program"
        return {"tier": tier, "why": why, "n": n, "exact": exact, "family": family or " ".join([head] + low[1:n])}
    if len(words) == 2 and low[1] == "--version":
        return res("read", "prints a version", 2, exact=True)
    if head in _PERM_SUB_READ or head in _PERM_SUB_PROJECT or head == "gh":
        i = 1
        while i < len(words) and words[i].startswith("-"):
            if head == "git" and words[i] == "-C" and i + 1 < len(words):
                i += 2
            elif head == "git" and low[i] == "--no-pager":
                i += 1
            else:
                return res("no", "unusual options before the subcommand", i + 1, head)
        if i >= len(words):
            return res("no", f"covers every {head} command", i, head)
        sub, n = low[i], i + 1
        if head == "gh":
            if sub == "search":
                return res("read", _PERM_TIER_WHY["read"], n)
            if n < len(words) and (sub, low[n]) in _PERM_GH_READ:
                return res("read", _PERM_TIER_WHY["read"], n + 1)
            return res("no", _PERM_SUB_NO["gh"], n)
        if head == "docker" and sub == "compose" and n < len(words) and low[n] in ("ps", "logs", "config", "ls"):
            return res("read", _PERM_TIER_WHY["read"], n + 1)
        if sub in _PERM_SUB_READ.get(head, ()):
            return res("read", _PERM_TIER_WHY["read"], n)
        if sub in _PERM_SUB_PROJECT.get(head, ()):
            return res("project", _PERM_TIER_WHY["project"], n)
        if head == "git" and sub in ("log", "diff", "show", "grep"):
            return res("no", "can write files (--output) or start other programs; Claude Code already runs "
                             "the plain form without asking", n)
        if head in ("npm", "pnpm", "yarn"):
            if sub in ("test", "t"):
                return res("project", _PERM_TIER_WHY["project"], n)
            if sub in ("run", "run-script"):
                if n >= len(words):
                    return res("no", "covers every package script", n)
                if _PERM_SCRIPT_OK.match(low[n]):
                    return res("project", _PERM_TIER_WHY["project"], n + 1)
                return res("no", f"runs the package script '{words[n]}' (not a test/build/lint script)", n + 1)
            if head != "npm" and _PERM_SCRIPT_OK.match(sub):
                return res("project", _PERM_TIER_WHY["project"], n)
        return res("no", _PERM_SUB_NO[head], n)
    if head in ("python", "python3", "py") and len(words) >= 3 and words[1] == "-m":
        mod = low[2]
        if mod in _PERM_PY_MODULES:
            return res("project", _PERM_TIER_WHY["project"], 3)
        if mod == "pip" and len(words) >= 4 and low[3] in _PERM_SUB_READ["pip"]:
            return res("read", _PERM_TIER_WHY["read"], 4)
        return res("no", "runs any code or program", 3)
    if head == "npx":
        if len(words) >= 2 and low[1] in _PERM_NPX_OK:
            return res("project", _PERM_TIER_WHY["project"], 2)
        if len(words) >= 3 and low[1] == "playwright" and low[2] == "test":
            return res("project", _PERM_TIER_WHY["project"], 3)
        return res("no", "downloads and runs any package", 1)
    if head in _PERM_PROJECT_BIN:
        return res("project", _PERM_TIER_WHY["project"], 1)
    for heads, why in _PERM_NO:
        if head in heads:
            return res("no", why, 1)
    if head in _PERM_READ:
        return res("read", _PERM_TIER_WHY["read"], 1)
    if head in _PERM_PS_BLOCK or head in ("where", "select", "sort", "ft", "fl", "?", "%"):
        return res("no", "takes script blocks, so it can run any code", 1)
    m = re.match(r"^([a-z]+)-[a-z0-9]+$", head)
    if m and m.group(1) in _PERM_PS_VERBS:
        if m.group(1) in _PERM_PS_READ_VERBS:
            return res("read", _PERM_TIER_WHY["read"], 1)
        return res("no", f"changes something (a PowerShell {m.group(1).capitalize()}- command)", 1)
    return res("unknown", "not on AOC's list of safe commands -- decide yourself", 1)


def _perm_read_rule(folder):
    """Pure: a folder -> (rule, tier, why) for reading files in it."""
    p = str(folder or "").replace("\\", "/").rstrip("/")
    m = re.match(r"^([A-Za-z]):/?(.*)$", p) or re.match(r"^/(?:mnt/)?([A-Za-z])(?:/(.*))?$", p)
    if m:
        parts = [x for x in (m.group(2) or "").split("/") if x]
        norm = "//" + m.group(1).lower() + ("/" + "/".join(parts) if parts else "")
        deep = len(parts) >= 3 or (len(parts) == 2 and parts[0].lower() != "users")   # not a drive or a home folder
    elif p.startswith("/"):
        parts = [x for x in p.split("/") if x]
        norm, deep = "/" + p, len(parts) >= 2 or parts == ["tmp"]
    else:
        return "", "unknown", "not an absolute folder"
    if _PERM_SENSITIVE.search(norm):
        return "", "no", "may hold passwords, keys or browser data"
    if not deep:
        return "", "no", "too broad: a whole drive or your home folder"
    if "*" in norm:
        return "", "unknown", "a wildcard in the path"
    return "Read(" + norm.replace("(", "\\(").replace(")", "\\)") + "/**)", "read", "reads files in this folder"


def _perm_calls_to_rules(tool, inp):
    """Pure: one tool call -> [{"family", "rule", "tier", "why"}] -- one per
    simple command for shell tools; rule '' when none is suggested."""
    tool = str(tool or "")
    inp = inp if isinstance(inp, dict) else {}
    out = []
    if tool in _PERM_SHELLS:
        cmds, script = _perm_simple_commands(inp.get("command"), tool)
        if script:
            return [{"family": "script with loops or blocks", "rule": "", "tier": "unknown",
                     "why": "a script with loops or blocks: no single rule can describe it safely"}]
        for words in cmds:
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]) or words[0][:1] in ("$", "(", "{", "[", "@"):
                continue
            if _perm_head(words[0]) in _PERM_SKIP or (tool == "PowerShell" and words[0][:1] in "\"'"):
                continue   # a bare "string" in PowerShell just prints it
            c = _perm_classify(words)
            if c["family"] in ("&", "call", "start", "start-process", "saps") and len(words) > 1:
                c["family"] += " " + _perm_head(words[1])
            rule, why = "", c["why"]
            if c["tier"] in ("read", "project"):
                lead = words if c["exact"] else words[:c["n"]]
                if all(_PERM_WORD_OK.match(w) for w in lead):
                    rule = f"{tool}({' '.join(lead)})" if c["exact"] else f"{tool}({' '.join(lead)} *)"
                else:
                    why = "the command's first words have characters a rule can't hold safely"
            out.append({"family": c["family"], "rule": rule, "tier": c["tier"], "why": why, "text": " ".join(words)[:300]})
        return out
    if tool in ("Read", "Glob", "Grep"):
        p = inp.get("file_path") if tool == "Read" else inp.get("path")
        if not p:
            return out
        folder = os.path.dirname(str(p).replace("\\", "/")) if tool == "Read" else str(p)
        rule, tier, why = _perm_read_rule(folder)
        return [{"family": "read " + folder.replace("\\", "/"), "rule": rule, "tier": tier, "why": why}]
    if tool == "WebFetch":
        from urllib.parse import urlparse
        try:
            host = (urlparse(str(inp.get("url") or "")).hostname or "").lower()
        except ValueError:
            host = ""
        if not re.match(r"^[a-z0-9.-]+\.[a-z0-9-]+$", host):
            return out
        return [{"family": host, "rule": f"WebFetch(domain:{host})", "tier": "read", "why": "reads pages on this site"}]
    if tool == "WebSearch":
        return [{"family": "web search", "rule": "WebSearch", "tier": "read", "why": "searches the web"}]
    if tool == "Skill":
        name = str(inp.get("skill") or "")
        if not re.match(r"^[\w:.-]{1,120}$", name):
            return out
        return [{"family": "skill " + name, "rule": f"Skill({name})", "tier": "read",
                 "why": "loads the skill's instructions; its tool calls still ask"}]
    m = re.match(r"^mcp__(.+?)__(.+)$", tool)
    if m:
        if not re.match(r"^[\w.-]+$", tool):
            return out
        server = re.sub(r"^(claude_ai_|plugin_[^_]+_)", "", m.group(1)).replace("_", " ")
        if _PERM_MCP_READ.match(m.group(2)):
            return [{"family": f"{server} · {m.group(2)}", "rule": tool, "tier": "read", "why": f"reads from {server}"}]
        return [{"family": f"{server} · {m.group(2)}", "rule": "", "tier": "no",
                 "why": f"acts in {server}: creates, sends or changes things"}]
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return [{"family": "file edits", "rule": "", "tier": "no",
                 "why": "file changes: use accept-edits mode (Shift+Tab) for a session instead of a lasting rule"}]
    return [{"family": tool, "rule": "", "tier": "unknown", "why": "not on AOC's list of safe tools -- decide yourself"}]


def _perm_parse_rule(rule):
    """Pure: 'Bash(git status *)' -> ('Bash', 'git status *'); 'WebSearch' -> ('WebSearch', None)."""
    m = re.match(r"^([\w.-]+)(?:\((.*)\))?$", str(rule or "").strip(), re.S)
    return (m.group(1), m.group(2)) if m else ("", None)


def _perm_rule_tier(rule):
    """Pure: an allow rule -> (tier, why, broad). broad = it matches more than
    one exact command/file (a wildcard or a bare tool name)."""
    tool, content = _perm_parse_rule(rule)
    if not tool:
        return "unknown", "not a rule", False
    if tool in _PERM_SHELLS:
        if content is None or content.strip() in ("", "*"):
            return "no", "allows every command", True
        prefix = content[:-2] if content.endswith((" *", ":*")) else None
        words = re.findall(r'"[^"]*"|\'[^\']*\'|[^\s"\']+', (prefix if prefix is not None else content).strip())
        if not words:
            return "no", "allows every command", True
        c = _perm_classify(words)
        if prefix is None:   # an exact command (a * inside still makes it one specific command)
            broad = False
            if c["exact"] or (c["tier"] in ("read", "project") and len(words) >= c["n"]):
                return c["tier"], c["why"], broad
            return (c["tier"] if c["tier"] != "read" else "unknown"), c["why"], broad
        if "*" in prefix:
            return "unknown", "a wildcard inside the command", True
        if c["tier"] in ("read", "project") and not c["exact"] and len(words) >= c["n"]:
            return c["tier"], c["why"], True
        if c["tier"] in ("read", "project"):
            return "no", f"covers more than {c['family']}", True
        # words past the risky part make it narrow ('git commit -m "fix: x *'); a lone
        # quote or flags don't ('python -c ' *')
        narrow = any(not w.startswith("-") and w.strip("'\"") for w in words[c["n"]:])
        return c["tier"], c["why"], not narrow
    if tool == "Read":
        if content is None:
            return "no", "reads any file, including keys and browser data", True
        if not content.endswith("/**"):
            return "unknown", "not a folder rule", "*" in content
        folder = content[:-3].replace("\\(", "(").replace("\\)", ")")
        if folder.startswith("//"):          # absolute: //c/Users/x, //tmp
            _, tier, why = _perm_read_rule(folder[1:])
            return tier, why, True
        if _PERM_SENSITIVE.search(folder):
            return "no", "may hold passwords, keys or browser data", True
        if folder.rstrip("/") in ("", "~", "."):
            return "no", "too broad: a whole drive or your home folder", True
        return "read", "reads files in this folder", True
    if tool in ("Edit", "Write", "MultiEdit"):
        if content is None or content.strip() in ("", "**", "/**", "//**"):
            return "no", "changes any file anywhere without asking", True
        return "unknown", "lets Claude change files without asking", True
    if tool == "WebFetch":
        if content and re.match(r"^domain:[a-z0-9.-]+\.[a-z0-9-]+$", content):
            return "read", "reads pages on this site", False
        return "unknown", "not a single domain", content is None
    if tool == "WebSearch":
        return "read", "searches the web", False
    if tool == "Skill":
        return ("read", "loads the skill's instructions", False) if content and re.match(r"^[\w:.-]+$", content) \
            else ("unknown", "", content is None)
    m = re.match(r"^mcp__(.+?)(?:__(.+))?$", tool)
    if m:
        server = re.sub(r"^(claude_ai_|plugin_[^_]+_)", "", m.group(1)).replace("_", " ")
        if not m.group(2) or m.group(2) == "*":
            return "no", f"allows every {server} tool, including ones that send or change things", True
        if _PERM_MCP_READ.match(m.group(2)):
            return "read", f"reads from {server}", False
        return "no", f"acts in {server}: creates, sends or changes things", False
    return "unknown", "", content is None


def _perm_rule_covers(have, want) -> bool:
    """Pure: does the allow rule `have` already allow everything `want` does?"""
    t1, c1 = _perm_parse_rule(have)
    t2, c2 = _perm_parse_rule(want)
    if not t1 or t1 != t2:
        return False
    if c1 is None or (t1 in _PERM_SHELLS and c1.strip() in ("", "*")):
        return True
    if c2 is None:
        return False
    if t1 == "Read" and c1.endswith("/**") and c2.endswith("/**"):
        return (c2[:-3] + "/").startswith(c1[:-3] + "/")
    if c1.endswith((" *", ":*")):
        p1 = c1[:-2]
        p2 = c2[:-2] if c2.endswith((" *", ":*")) else c2
        return p2 == p1 or p2.startswith(p1 + " ")
    return c1 == c2


def _perm_hook_installed(settings) -> bool:
    """Pure: is AOC's PermissionRequest hook in ~/.claude/settings.json?"""
    hooks = ((settings or {}).get("hooks") or {}).get("PermissionRequest") or []
    return "aoc_permission" in json.dumps(hooks)


def _perm_log_add(data, now):
    """Append one prompt (POST /permission/request body) to permission_log.jsonl."""
    tool = str(data.get("tool_name") or "")[:160]
    if not tool or tool in _PERM_NOT_PROMPTS:
        return
    inp = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    rec = {"t": round(now, 3), "sid": str(data.get("session_id") or "")[:80], "cwd": str(data.get("cwd") or "")[:500],
           "tool": tool, "input": {k: str(inp[k])[:4000] for k in _PERM_INPUT_FIELDS if inp.get(k) not in (None, "")}}
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    with _perm_log_lock:
        os.makedirs(os.path.dirname(PERM_LOG_FILE), exist_ok=True)
        try:
            size = os.path.getsize(PERM_LOG_FILE)
        except OSError:
            size = 0
        if size > _PERM_LOG_MAX_BYTES:
            keep = [r for r in _perm_log_read(0) if r["t"] >= now - _PERM_LOG_KEEP_S]
            keep = keep[len(keep) // 2:] if sum(len(json.dumps(r)) for r in keep) > _PERM_LOG_MAX_BYTES // 2 else keep
            tmp = PERM_LOG_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in keep)
            os.replace(tmp, PERM_LOG_FILE)
        with open(PERM_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)


def _perm_log_read(since):
    """Prompts logged at or after `since` (bad lines skipped)."""
    out = []
    try:
        with open(PERM_LOG_FILE, encoding="utf-8") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(r, dict) and isinstance(r.get("t"), (int, float)) and r["t"] >= since and r.get("tool"):
                    r["input"] = r.get("input") if isinstance(r.get("input"), dict) else {}
                    out.append(r)
    except FileNotFoundError:
        pass
    return out


def _perm_match_lines(lines, prompts):
    """Pure-ish: one session's transcript lines -> fills each logged prompt's
    "use_id", "outcome" (allowed | rejected) and "wait" (s from the prompt to
    the call's result, so it includes the command's own run time) by finding
    the matching tool call: same tool and input, made just before the prompt."""
    uses, results = [], {}
    for raw in lines:
        if (b'"tool_' not in raw) if isinstance(raw, bytes) else ('"tool_' not in raw):
            continue
        try:
            o = json.loads(raw)
        except (TypeError, ValueError):
            continue
        content = (o.get("message") or {}).get("content") if isinstance(o, dict) else None
        if not isinstance(content, list):
            continue
        t = _replay_epoch(o.get("timestamp"))
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and t is not None:
                uses.append((t, str(b.get("name") or ""), _perm_input_key(b.get("name"), b.get("input")), str(b.get("id") or "")))
            elif b.get("type") == "tool_result":
                txt = _replay_result_text(b.get("content")).lower()
                rej = bool(b.get("is_error")) and ("doesn't want to proceed" in txt or "user rejected" in txt)
                results[str(b.get("tool_use_id") or "")] = (t, rej)
    taken = set()
    for p in sorted(prompts, key=lambda p: p["t"]):
        best = None
        for t, name, key, uid in uses:
            if uid in taken or name != p["tool"] or key != p.get("key", "") or t > p["t"] + 5 or t < p["t"] - 900:
                continue
            if best is None or t > best[0]:
                best = (t, uid)
        if best is None:
            continue
        taken.add(best[1])
        p["use_id"] = best[1]
        r = results.get(best[1])
        if r and r[0] is not None:
            p["outcome"] = "rejected" if r[1] else "allowed"
            p["wait"] = min(_PERM_WAIT_CAP_S, max(0.0, r[0] - p["t"]))


_perm_match_cache = {}


def _perm_resolve(prompts):
    """Fill outcome/wait for logged prompts from their sessions' transcripts
    (main + subagents; cached per file size/mtime)."""
    by_sid = {}
    for p in prompts:
        by_sid.setdefault(p["sid"], []).append(p)
    for sid, ps in by_sid.items():
        main = _find_transcript(sid)
        if not main:
            continue
        files = [main] + glob.glob(os.path.join(glob.escape(os.path.splitext(main)[0]), "subagents", "*.jsonl"))
        sig = []
        for f in files:
            try:
                st = os.stat(f)
                sig.append((f, st.st_size, st.st_mtime))
            except OSError:
                pass
        keys = tuple(sorted((p["t"], p["tool"], p.get("key", "")) for p in ps))
        hit = _perm_match_cache.get(sid)
        if hit and hit[0] == (tuple(sig), keys):
            done = hit[1]
        else:
            work = [dict(p) for p in ps]

            def lines():
                for f, _, _ in sig:
                    try:
                        with open(f, "rb") as fh:
                            yield from fh
                    except OSError:
                        continue
            _perm_match_lines(lines(), work)
            done = {(p["t"], p["tool"], p.get("key", "")): {k: p[k] for k in ("use_id", "outcome", "wait") if k in p}
                    for p in work}
            _perm_match_cache[sid] = ((tuple(sig), keys), done)
        for p in ps:
            p.update(done.get((p["t"], p["tool"], p.get("key", "")), {}))


def _perm_settings_files():
    """[(key, path)] of every settings file with allow rules: your user
    settings, ~/.claude/settings.local.json (= your home folder's project
    settings) and each known project's .claude/settings(.local).json."""
    home = os.path.expanduser("~")
    out = [("settings", os.path.join(home, ".claude", "settings.json")),
           ("settings_local", os.path.join(home, ".claude", "settings.local.json"))]
    seen = {os.path.normcase(os.path.abspath(p)) for _, p in out}
    try:
        folders = list((_fix_read_json(os.path.join(home, ".claude.json"), {}).get("projects") or {}))[:300]
    except ValueError:
        folders = []
    for folder in folders:
        for name in ("settings.json", "settings.local.json"):
            p = os.path.join(folder, ".claude", name)
            k = os.path.normcase(os.path.abspath(p))
            if k not in seen and os.path.exists(p):
                seen.add(k)
                out.append(("project", p))
    return out


def _perm_report(prompts, denials, saved, user_allow, days) -> dict:
    """Pure: the PERMISSIONS tab.
    prompts: logged prompts {t, sid, label, tool, input, outcome?, wait?, use_id?};
    denials: transcript denials {t, sid, label, tool, input, reason, secs, use_id};
    saved: allow rules in settings files {file, path, rule};
    user_allow: the rules in ~/.claude/settings.json (apply to every project)."""
    groups = {}

    def add(tool, inp, field, t=None, sid="", label="", secs=0.0, example=""):
        for c in _perm_calls_to_rules(tool, inp):
            k = c["rule"] or f"{tool}\x00{c['family']}"
            g = groups.get(k)
            if g is None:
                g = groups[k] = {"rule": c["rule"], "tier": c["tier"], "why": c["why"], "family": c["family"],
                                 "tool": tool, "asked": 0, "rejected": 0, "denied": 0, "saved": 0, "secs": 0.0,
                                 "sessions": set(), "example": "", "last_t": 0, "last_sid": "", "last_label": ""}
            g[field] += 1
            g["secs"] += secs or 0.0
            if sid:
                g["sessions"].add(sid)
            example_c = c.get("text") or example   # the simple command itself, not the whole chain
            if example_c and (t or 0) >= g["last_t"]:
                g.update(example=example_c[:300], last_t=t or 0, last_sid=sid, last_label=label)
    seen_use = set()
    waited = 0.0
    for p in prompts:
        if p.get("use_id"):
            seen_use.add(p["use_id"])
        field = "rejected" if p.get("outcome") == "rejected" else "asked"
        waited += p.get("wait") or 0.0
        add(p["tool"], p.get("input"), field, p["t"], p.get("sid", ""), p.get("label", ""), p.get("wait") or 0.0,
            _perm_input_key(p["tool"], p.get("input")) or p["tool"])
    n_denied = n_rejected_tx = 0
    for d in denials:
        if d.get("use_id") and d["use_id"] in seen_use:
            continue
        kind = _PERM_DENIAL_REASONS.get(d.get("reason"))
        if not kind:
            continue
        if kind == "denied":
            n_denied += 1
        else:
            n_rejected_tx += 1
        add(d["tool"], d.get("input"), kind, d.get("t"), d.get("sid", ""), d.get("label", ""), d.get("secs") or 0.0,
            _perm_input_key(d["tool"], d.get("input")) or d["tool"])
    one_offs = 0
    risky = []
    for s in saved:
        tool, content = _perm_parse_rule(s["rule"])
        tier, why, broad = _perm_rule_tier(s["rule"])
        if broad and tier == "no":
            risky.append({"file": s["file"], "path": s["path"], "rule": s["rule"], "why": why})
        if tool in _PERM_SHELLS and content and not broad:
            one_offs += 1
            add(tool, {"command": content}, "saved", example=content)
    suggest, blocked = [], []
    for g in groups.values():
        g["sessions"] = len(g["sessions"])
        g["secs"] = round(g["secs"])
        evidence = g["asked"] + g["rejected"] + g["denied"] + g["saved"]
        ok = g["rule"] and g["tier"] in ("read", "project")
        if ok and g["rejected"] > g["asked"] + g["saved"]:
            ok, g["why"] = False, "you said no more often than yes"
        if ok:
            g["covered"] = any(_perm_rule_covers(r, g["rule"]) for r in user_allow)
            if evidence >= 2 or g["asked"] + g["denied"] >= 1:
                suggest.append(g)
        elif evidence:
            blocked.append(g)
    rank = lambda g: (-(g["asked"] + g["rejected"] + g["denied"]), -g["saved"], -g["secs"])
    suggest.sort(key=lambda g: (g["covered"],) + rank(g))
    blocked.sort(key=rank)
    files = {}
    for s in saved:
        files[s["path"]] = files.get(s["path"], 0) + 1
    return {
        "days": days, "prompts": len(prompts), "asked": sum(1 for p in prompts if p.get("outcome") != "rejected"),
        "rejected": sum(1 for p in prompts if p.get("outcome") == "rejected") + n_rejected_tx,
        "waited": round(waited), "denied": n_denied,
        "saved": len(saved), "one_offs": one_offs, "files": [{"path": p, "rules": n} for p, n in files.items()],
        "suggest": suggest[:25], "blocked": blocked[:15], "risky": risky[:40],
        "user_rules": len(user_allow),
    }


def _permission_advice(days, now=None):
    """GET /permission_advice?days=: the PERMISSIONS tab."""
    now = now or time.time()
    since = now - days * 86400
    titles = _session_titles()
    prompts = [p for p in _perm_log_read(since) if p["tool"] not in _PERM_NOT_PROMPTS]
    for p in prompts:
        p["key"] = _perm_input_key(p["tool"], p["input"])
        p["label"] = titles.get(p.get("sid"), "")
    _perm_resolve(prompts)
    denials = [f for s in _fail_scans(days, now) for f in s["fails"] if f.get("reason") in _PERM_DENIAL_REASONS]
    saved = []
    for key, path in _perm_settings_files():
        try:
            doc = _fix_read_json(path, {})
        except ValueError:
            continue
        for r in ((doc.get("permissions") or {}).get("allow") or []) if isinstance(doc.get("permissions"), dict) else []:
            if isinstance(r, str):
                saved.append({"file": key, "path": path, "rule": r})
    user_allow = [s["rule"] for s in saved if s["file"] == "settings"]
    report = _perm_report(prompts, denials, saved, user_allow, days)
    first = _perm_log_read(0)[:1]
    try:
        hook = _perm_hook_installed(_fix_read_json(_fix_paths()["settings"], {}))
    except ValueError:
        hook = False
    report.update(log_since=first[0]["t"] if first else None, hook=hook, computed_at=now)
    return report


def _client_report_print_html(report, client):
    """A plain, printable page (browser -> Save as PDF) for one client's month."""
    import html as _html
    esc = lambda v: _html.escape(str(v), quote=True)
    g = next((x for x in report["clients"] if x["name"] == client), None)
    try:
        title_month = datetime.strptime(report["month"], "%Y-%m").strftime("%B %Y")
    except ValueError:
        title_month = report["month"]
    if g is None:
        return f"<!doctype html><meta charset=utf-8><title>Not found</title><p>No client named {esc(client)}.</p>"
    hm = lambda s: f"{int(s) // 3600}:{int(s) % 3600 // 60:02d}"  # h:mm, the same in every locale
    rows = "".join(
        f"<tr><td>{esc(s['title'])}</td><td>{esc(s['first_day'])}"
        f"{'' if s['last_day'] == s['first_day'] else ' – ' + esc(s['last_day'])}</td>"
        f"<td class=n>{s['commits'] or ''}</td><td>{esc(', '.join(s['repos']))}</td>"
        f"<td class=n>{hm(s.get('active_s', 0))}</td>"
        f"<td class=n>{s['tokens']:,}</td><td class=n>${s['cost']:.2f}</td></tr>" for s in g["sessions"])
    markup = (f"<tr><td colspan=6>Markup {g['markup']:g} %</td><td class=n>${g['billed'] - g['cost']:.2f}</td></tr>"
              f"<tr class=t><td colspan=6>AI usage total</td><td class=n>${g['billed']:.2f}</td></tr>") if g["markup"] else \
             f"<tr class=t><td colspan=6>AI usage total</td><td class=n>${g['cost']:.2f}</td></tr>"
    projects = g.get("projects") or []
    proj = "" if len(projects) < 2 else (
        "<h2>By project</h2><table><thead><tr><th>Project</th><th class=n>Sessions</th><th class=n>Time</th>"
        "<th class=n>Cost</th></tr></thead><tbody>" + "".join(
            f"<tr><td>{esc(p['name'])}</td><td class=n>{p['sessions']}</td><td class=n>{hm(p['active_s'])}</td>"
            f"<td class=n>${p['cost']:.2f}</td></tr>" for p in projects) + "</tbody></table>")
    rate, cur = g.get("rate", 0.0), g.get("currency", _CLIENT_CURRENCY)
    work = ""
    if rate:
        due = (f"<tr class=t><td colspan=3>Total due</td><td class=n>{g['billed'] + g['time_amount']:,.2f} USD</td></tr>"
               if cur == "USD" else "")
        work = (f"<h2>Work time</h2><table><tbody><tr><td>Active time with Claude Code ({hm(g.get('active_s', 0))},"
                f" rounded up to {_BILL_STEP_H:g} h)</td><td class=n>{g.get('hours', 0.0):.2f} h</td><td class=n>× {rate:,.2f} {esc(cur)}/h</td><td class=n>{g['time_amount']:,.2f} {esc(cur)}</td></tr>"
                f"{due}</tbody></table>")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{esc(g['name'] or 'Unassigned')} - AI usage {esc(report['month'])}</title>
<style>body{{font:14px/1.45 system-ui,Segoe UI,sans-serif;color:#111;max-width:860px;margin:32px auto;padding:0 20px}}
h1{{font-size:22px;margin:0 0 4px}}h2{{font-size:15px;margin:26px 0 6px}}.sub{{color:#555;margin:0 0 22px}}table{{width:100%;border-collapse:collapse}}
th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid #ddd;vertical-align:top}}th{{font-size:12px;color:#555;text-transform:uppercase;letter-spacing:.04em}}
.n{{text-align:right;white-space:nowrap}}tr.t td{{font-weight:700;border-top:2px solid #111;border-bottom:none}}
.foot{{margin-top:26px;color:#777;font-size:12px}}button{{float:right;padding:6px 14px;cursor:pointer}}@media print{{button{{display:none}}body{{margin:0}}}}</style></head>
<body><button onclick="print()">Print / Save as PDF</button>
<h1>{esc(g['name'] or 'Unassigned sessions')}</h1><p class="sub">AI coding assistant usage (Claude Code) · {esc(title_month)}</p>
<table><thead><tr><th>Work session</th><th>Date</th><th class=n>Commits</th><th>Repositories</th><th class=n>Time</th><th class=n>Tokens</th><th class=n>Cost</th></tr></thead>
<tbody>{rows}</tbody><tfoot><tr><td colspan=6>Subtotal</td><td class=n>${g['cost']:.2f}</td></tr>{markup}</tfoot></table>
{proj}{work}
<p class="foot">Costs at Anthropic API list prices, from Claude Code's own session records. Time = active time in this month (a pause counts {_WORKLOG_GAP_S // 60} min at most). Generated by AOC on {esc(datetime.now().strftime('%Y-%m-%d'))}.</p>
</body></html>"""


def _db_repair_transcript_costs(hist_conn, index_conn):
    """History rows for CLI sessions (no subagents) were written by the
    transcript scanner, which until 2026-10-06 counted every transcript line
    -- one per content block, each repeating the response's usage -- so
    their tokens/cost are ~2.4x too high. For every such row whose transcript
    still exists, take the deduplicated numbers from the index's usage table.
    Rows with agents (cost also has subagent spend) and rows whose transcript
    is gone are left alone. Idempotent; returns how many rows changed."""
    fixed = {sid: (int(tok or 0), float(cost or 0)) for sid, tok, cost in index_conn.execute(
        "SELECT session_id, SUM(inp) + SUM(out), SUM(cost) FROM usage GROUP BY session_id")}
    changed = 0
    for sid, tok, cost in hist_conn.execute(
            "SELECT id, tokens, cost FROM sessions WHERE COALESCE(agents, 0) = 0").fetchall():
        if sid not in fixed:
            continue
        ntok, ncost = fixed[sid]
        if ncost <= 0 or abs((cost or 0) - ncost) <= max(0.01, 0.01 * ncost):
            continue
        hist_conn.execute("UPDATE sessions SET tokens = ?, cost = ? WHERE id = ?", (ntok, round(ncost, 6), sid))
        changed += 1
    return changed


def _transcript_index_worker():
    """Keeps transcript_index.db current: after the startup grace, one tick a
    minute, or back-to-back (short pause) while the first build catches up."""
    projects_base = os.path.join(os.path.expanduser("~"), ".claude", "projects")
    time.sleep(_STARTUP_GRACE_S)
    conn = None
    while True:
        try:
            if conn is None:
                os.makedirs(AOC_DATA_DIR, exist_ok=True)
                conn = _transcript_index_connect()
            _transcript_index_progress["building"] = True
            seen, current, _spent = _transcript_index_tick(conn, projects_base)
            if _resolve_quiet_commits(conn, _run_git_quiet, time.time()):
                conn.commit()
            msgs = conn.execute("SELECT COUNT(*) FROM msgs").fetchone()[0]
            _transcript_index_progress.update(files=seen, indexed=current, messages=msgs,
                                              building=current < seen, updated_at=time.time())
            if current == seen and not _transcript_cost_repair_done[0]:
                _transcript_cost_repair_done[0] = True
                try:
                    with _db_lock:
                        hc = _db_conn()
                        n = _db_repair_transcript_costs(hc, conn)
                        hc.commit()
                        hc.close()
                    if n:
                        print(f"[history] repaired tokens/cost of {n} CLI session(s) from the transcript index")
                except Exception as e:
                    _log_bg_error("_db_repair_transcript_costs", e)
            time.sleep(2 if current < seen else 60)
        except Exception as e:
            _log_bg_error("_transcript_index_worker", e)
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
            conn = None
            time.sleep(60)


_threading.Thread(target=_transcript_index_worker, daemon=True).start()


_TS_RE = re.compile(r'"timestamp"\s*:\s*"([0-9T:.\-]+Z?)"')

def _transcript_span(path: str):
    """(first_ts, last_ts, ai_title) of a whole transcript, streamed line by
    line -- a regex for the timestamp, full JSON only for ai-title lines, so
    multi-MB transcripts stay cheap. The LAST ai-title wins: Claude Code
    re-titles a session as it goes."""
    first = last = title = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = _TS_RE.search(line)
                if m:
                    if not first:
                        first = m.group(1)
                    last = m.group(1)
                if '"ai-title"' in line:
                    try:
                        title = json.loads(line).get("aiTitle", "") or title
                    except Exception:
                        pass
    except OSError:
        pass
    return first, last, _fix_mojibake(title) if title else ""

def _db_backfill_transcript_sessions(projects_base=None):
    """One pass at startup over History rows written before the scanner
    recorded end time / duration / title (and while it stored UTC start
    times): re-derive them from each session's transcript if it still
    exists. A repaired row gets a non-NULL title ('' when the transcript has
    none), so it isn't re-read on the next start. Returns rows repaired."""
    projects_base = projects_base or os.path.join(os.path.expanduser("~"), ".claude", "projects")
    try:
        with _db_lock:
            c = _db_conn()
            ids = [r[0] for r in c.execute(
                "SELECT id FROM sessions WHERE ended_at IS NULL OR title IS NULL").fetchall()]
            c.close()
        if not ids or not os.path.isdir(projects_base):
            return 0
        wanted = set(ids)
        paths = {}
        for proj in os.listdir(projects_base):
            d = os.path.join(projects_base, proj)
            if not os.path.isdir(d):
                continue
            for fname in os.listdir(d):
                if fname.endswith(".jsonl") and fname[:-6] in wanted:
                    paths[fname[:-6]] = os.path.join(d, fname)
        fixed = 0
        for sid, path in paths.items():
            first, last, title = _transcript_span(path)
            if not first:
                continue
            with _db_lock:
                c = _db_conn()
                _db_upsert_transcript_session(c, sid, "", first, last, title=title)
                c.execute("UPDATE sessions SET title = COALESCE(title, '') WHERE id = ?", (sid,))
                c.commit()
                c.close()
            fixed += 1
        return fixed
    except Exception as e:
        _log_bg_error("_db_backfill_transcript_sessions", e)
        return 0

_threading.Thread(target=_db_backfill_transcript_sessions, daemon=True).start()
