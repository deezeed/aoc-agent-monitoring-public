"""Permission advisor (monitor.py, History -> PERMISSIONS): splitting shell
commands outside quotes (scripts with blocks/loops kept whole), the safe /
project / never classifier, rules per tool call (shell, folders, domains,
MCP, skills), rule tiers for validation and the risky-rule list, coverage,
the prompt log, matching logged prompts to their transcript call (answer +
wait), _perm_report, the allow / unallow one-click fixes, and
_permission_advice over a fake home."""
import sys, os, json, re, glob, time, threading, tempfile, shutil
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions, read_monitor_source
from lib.check import Checker
import ast

c = Checker()
tmp = tempfile.mkdtemp(prefix="aoc_perm_")
src = read_monitor_source()
names = []
for node in ast.parse(src).body:
    if isinstance(node, ast.FunctionDef) and node.name.startswith(("_perm_", "_fix_", "_fail_")):
        names.append(node.name)
    elif isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id.startswith(("_PERM_", "_FIX_", "_FAIL_")):
                names.append(t.id)
names = [n for n in names if n not in ("_perm_open", "_perm_poll", "_perm_decide", "_perm_expire", "_perm_should_hold",
                                       "_perm_summary", "_perm_pending_by_session", "_fix_mojibake")]
names += ["PERM_LOG_FILE", "_MODEL_PRICING", "_CACHE_WRITE_1H_MULT", "_model_pricing", "_calc_cost", "_replay_epoch",
          "_replay_result_text", "_permission_advice", "_session_titles", "_fail_cache", "_fail_scan_cache",
          "_perm_match_cache", "_find_transcript", "_perm_log_lock"]
ns = exec_functions(names, {"json": json, "re": re, "os": os, "glob": glob, "time": time, "datetime": datetime,
                            "threading": threading, "TRANSCRIPT_INDEX_DB": os.path.join(tmp, "none.db"),
                            "AOC_DATA_DIR": os.path.join(tmp, "aoc"), "FIXES_FILE": os.path.join(tmp, "aoc", "fixes.json"),
                            "_log_bg_error": lambda *a: None, "_transcript_cursors_lock": threading.Lock(),
                            "_transcript_cursors": {}})
SC, CL, CR = ns["_perm_simple_commands"], ns["_perm_classify"], ns["_perm_calls_to_rules"]
TIER, COV = ns["_perm_rule_tier"], ns["_perm_rule_covers"]

# ── splitting ──
cmds, script = SC('cd "C:/a b" && grep -n "x\\|y" f.py | head -5; git status')
c.check("split outside quotes only (| inside a grep pattern kept)",
        [w[0] for w in cmds] == ["cd", "grep", "head", "git"] and cmds[1][2] == '"x\\|y"' and not script)
c.check("heredoc body dropped", [w[0] for w in SC("python - <<'EOF'\nimport os; os.remove('x')\nEOF\nls")[0]] == ["python", "ls"])
c.check("${VAR} and find {} are not blocks", not SC("echo ${HOME}/x && find . -exec ls {} ;")[1])
c.check("PowerShell block = script", SC("Get-ChildItem | ForEach-Object { Remove-Item $_ }", "PowerShell")[1])
c.check("bash loop = script", SC("for f in *.py; do wc -l $f; done")[1])
c.check("PowerShell try = script", SC("try { x } catch { y }", "PowerShell")[1])
hs = SC("git commit -m @'\nfix: don't break; really\nCo-Authored-By: x\n'@\ngit push", "PowerShell")[0]
c.check("PowerShell here-string kept in one command", [w[0] for w in hs] == ["git", "git"] and hs[1][1] == "push")
c.check("PowerShell backtick escape, backslash is a plain char", SC('Get-Content "C:\\x\\" ; ls', "PowerShell")[0][1][0] == "ls")

# ── classifier ──
def tier(cmd):
    r = CL(re.findall(r'"[^"]*"|\'[^\']*\'|[^\s"\']+', cmd))
    return r["tier"], r["n"]
c.check("git status read", tier("git status --short") == ("read", 2))
c.check("git -C path status", tier("git -C /x status") == ("read", 4))
c.check("git -c config = no", tier("git -c core.pager=x log")[0] == "no")
c.check("git push / log no", tier("git push origin")[0] == "no" and tier("git log --oneline")[0] == "no")
c.check("bare git covers everything", CL(["git"])["why"].startswith("covers every git"))
c.check("npm run test project, 3 words", tier("npm run test -- -t x") == ("project", 3) and tier("npm run lint:fix")[0] == "project")
c.check("npm run deploy / bare / install no", tier("npm run deploy")[0] == "no" and tier("npm run")[0] == "no"
        and tier("npm install x")[0] == "no")
c.check("npm test / yarn build project", tier("npm test")[0] == "project" and tier("yarn build")[0] == "project")
c.check("npx", tier("npx vitest run") == ("project", 2) and tier("npx create-app")[0] == "no"
        and tier("npx playwright test") == ("project", 3) and tier("npx playwright install")[0] == "no")
c.check("python -m", tier("python -m pytest -q") == ("project", 3) and tier("py -m pip list") == ("read", 4)
        and tier("python -m http.server")[0] == "no" and tier("python build.py")[0] == "no")
c.check("project bins", tier("pytest tests") == ("project", 1) and tier("cargo test")[0] == "project")
c.check("read commands", tier("grep -rn x .")[0] == "read" and tier("Get-ChildItem -Recurse")[0] == "read"
        and tier("Select-String x")[0] == "read")
c.check("never: delete / write / system / network / install / deploy",
        all(tier(x)[0] == "no" for x in ("rm -rf x", "Remove-Item x", "Set-Content f x", "Stop-Process -Id 1",
                                         "curl -X POST", "winget install x", "vercel deploy", "taskkill /f", "sed -i s/a/b/ f")))
c.check("script-block cmdlets never", all(tier(x)[0] == "no" for x in ("Where-Object x", "Select-Object a", "Sort-Object b", "% { }")))
c.check("unknown program / not a PS verb", tier("iscc x.iss")[0] == "unknown" and tier("drift-doctor check")[0] == "unknown")
c.check("gh", tier("gh run list") == ("read", 3) and tier("gh api /x")[0] == "no" and tier("gh pr create")[0] == "no")
c.check("docker", tier("docker ps -a")[0] == "read" and tier("docker compose ps") == ("read", 3) and tier("docker rm x")[0] == "no")
v = CL(["node", "--version"])
c.check("--version = exact rule, read", v["tier"] == "read" and v["exact"])
c.check("read-only only for the standard program, not one from a path", tier("./tools/grep x")[0] == "unknown"
        and TIER("Bash(./tools/grep *)")[0] == "unknown" and tier("venv/Scripts/python -m pytest")[0] == "project")
c.check("hostname/date not read (they can set)", tier("hostname x")[0] != "read" and tier("date -s x")[0] != "read")

# ── rules per call ──
r = CR("Bash", {"command": "cd /x && git status --short | grep foo && rm -rf y"})
c.check("one entry per simple command, cd skipped", [x["rule"] for x in r] == ["Bash(git status *)", "Bash(grep *)", ""]
        and r[2]["why"] == "deletes or moves files")
c.check("exact version rule", CR("Bash", {"command": "node --version"})[0]["rule"] == "Bash(node --version)")
r = CR("Bash", {"command": '"C:\\x\\pytest.exe" -q'})
c.check("odd characters -> no rule, says why", r[0]["rule"] == "" and r[0]["tier"] == "project" and "characters" in r[0]["why"])
c.check("env prefix / bare PS string skipped", CR("Bash", {"command": "FOO=1 ls"}) == []
        and CR("PowerShell", {"command": '"done"'}) == [])
c.check("& program family", CR("PowerShell", {"command": '& "C:\\Inno\\ISCC.exe" x.iss'})[0]["family"] == "& iscc")
r = CR("PowerShell", {"command": "Get-ChildItem | ForEach-Object { $_.Name }"})
c.check("script -> one entry, no rule", len(r) == 1 and r[0]["rule"] == "" and "script" in r[0]["family"])
c.check("PowerShell rule keeps the tool", CR("PowerShell", {"command": "Get-Content a.txt"})[0]["rule"] == "PowerShell(Get-Content *)")
c.check("Read -> folder rule", CR("Read", {"file_path": "C:\\Users\\me\\proj\\src\\a.py"})[0]["rule"] == "Read(//c/Users/me/proj/src/**)")
c.check("Read folder with parens escaped", ns["_perm_read_rule"]("C:/Program Files (x86)/Inno Setup 6")[0]
        == "Read(//c/Program Files \\(x86\\)/Inno Setup 6/**)")
c.check("git-bash and /mnt paths", ns["_perm_read_rule"]("/c/Users/me/proj")[0] == "Read(//c/Users/me/proj/**)"
        and ns["_perm_read_rule"]("/mnt/c/Users/me/proj")[0] == "Read(//c/Users/me/proj/**)"
        and ns["_perm_read_rule"]("/tmp")[0] == "Read(//tmp/**)")
c.check("drive root / Users / home too broad, Program Files ok", ns["_perm_read_rule"]("C:/")[1] == "no"
        and ns["_perm_read_rule"]("C:/Users/me")[1] == "no" and ns["_perm_read_rule"]("D:/games")[1] == "no"
        and ns["_perm_read_rule"]("C:/Windows/System32")[1] == "read")
c.check("sensitive / too broad folders refused", CR("Read", {"file_path": "C:/Users/me/.ssh/id_rsa"})[0]["tier"] == "no"
        and CR("Read", {"file_path": "C:/Users/me/x.txt"})[0]["why"].startswith("too broad")
        and CR("Grep", {"path": "C:/Users/me/AppData/Local/Google/Chrome/User Data"})[0]["tier"] == "no")
c.check("Glob without path -> nothing", CR("Glob", {"pattern": "*.py"}) == [])
c.check("WebFetch domain", CR("WebFetch", {"url": "https://Docs.Example.com:443/a?b"})[0]["rule"] == "WebFetch(domain:docs.example.com)"
        and CR("WebFetch", {"url": "nonsense"}) == [])
c.check("MCP read vs write", CR("mcp__claude_ai_Gmail__search_threads", {})[0]["rule"] == "mcp__claude_ai_Gmail__search_threads"
        and CR("mcp__claude_ai_Gmail__send_message", {})[0]["rule"] == "" and "Gmail" in CR("mcp__claude_ai_Gmail__send_message", {})[0]["why"])
c.check("Skill / WebSearch / Edit / other", CR("Skill", {"skill": "vercel:deploy"})[0]["rule"] == "Skill(vercel:deploy)"
        and CR("WebSearch", {"query": "x"})[0]["rule"] == "WebSearch" and CR("Edit", {})[0]["tier"] == "no"
        and CR("Agent", {})[0]["tier"] == "unknown")

# ── rule tiers (validation + risky list) ──
c.check("safe prefix rule", TIER("Bash(git status *)")[:1] == ("read",) and TIER("Bash(npm run test *)")[0] == "project")
c.check("legacy :* prefix", TIER("Bash(git status:*)")[0] == "read")
c.check("too short prefix = broad no", TIER("Bash(git *)") == ("no", "covers every git command", True)
        and TIER("Bash(npm run *)")[0] == "no" and TIER("Bash(gh run *)")[0] == "no")
c.check("dangerous prefix broad", TIER("PowerShell(Remove-Item *)") == ("no", "deletes or moves files", True)
        and TIER("Bash(python -c ' *)")[2] is True)
c.check("narrow risky-looking rules not broad", TIER("Bash(git commit -m 'fix: x *)")[2] is False
        and TIER("Bash(npx create-next-app@latest app --ts *)")[2] is False
        and TIER('Bash(npx x --alias "@/*")')[2] is False)
c.check("bare shell tool", TIER("Bash") == ("no", "allows every command", True) and TIER("Bash(*)")[0] == "no")
c.check("exact version ok", TIER("Bash(node --version)")[0] == "read" and TIER("Bash(rm x)")[2] is False)
c.check("Read rules", TIER("Read(//c/Users/me/proj/x/**)")[0] == "read" and TIER("Read(//mnt/c/Users/me/**)")[:3:2] == ("no", True)
        and TIER("Read(//c/Users/me/.ssh/**)")[0] == "no" and TIER("Read(//tmp/**)")[0] == "read" and TIER("Read")[0] == "no")
c.check("MCP", TIER("mcp__claude_ai_Vercel")[2] is True and TIER("mcp__claude_ai_Vercel__list_teams")[0] == "read"
        and TIER("mcp__claude_ai_Vercel__deploy_to_vercel")[:3:2] == ("no", False))
c.check("WebFetch / Skill / Edit", TIER("WebFetch(domain:github.com)")[0] == "read" and TIER("Skill(run)")[0] == "read"
        and TIER("Edit")[:3:2] == ("no", True))

# ── coverage ──
c.check("covers", COV("Bash(git *)", "Bash(git status *)") and COV("Bash(git status *)", "Bash(git status *)")
        and COV("Bash(git status:*)", "Bash(git status *)") and COV("Bash", "Bash(ls *)")
        and COV("Read(//c/a/**)", "Read(//c/a/b/**)"))
c.check("doesn't cover", not COV("Bash(git st *)", "Bash(git status *)") and not COV("PowerShell(ls *)", "Bash(ls *)")
        and not COV("Read(//c/a/**)", "Read(//c/ab/**)") and not COV("Bash(git status)", "Bash(git status *)"))
c.check("hook installed", ns["_perm_hook_installed"]({"hooks": {"PermissionRequest": [{"hooks": [{"command": "py aoc_permission.py"}]}]}})
        and not ns["_perm_hook_installed"]({}))

# ── matching logged prompts to transcript calls ──
def ts(sec):
    return datetime.fromtimestamp(sec, timezone.utc).isoformat().replace("+00:00", "Z")
T = 1_790_000_000
def use(uid, t, cmd, tool="Bash"):
    return json.dumps({"type": "assistant", "timestamp": ts(t), "message": {"content": [
        {"type": "tool_use", "id": uid, "name": tool, "input": {"command": cmd}}]}}).encode()
def res(uid, t, err=False, text="ok"):
    return json.dumps({"type": "user", "timestamp": ts(t), "message": {"content": [
        {"type": "tool_result", "tool_use_id": uid, "is_error": err, "content": text}]}}).encode()
lines = [use("t1", T, "npm run test"), res("t1", T + 31), use("t2", T + 100, "npm run test"),
         res("t2", T + 103, True, "The user doesn't want to proceed with this tool use."), use("t3", T + 200, "ls")]
P = [{"t": T + 1, "tool": "Bash", "key": "npm run test"}, {"t": T + 101, "tool": "Bash", "key": "npm run test"},
     {"t": T + 201, "tool": "Bash", "key": "pwd"}]
ns["_perm_match_lines"](lines, P)
c.check("allowed + wait from the prompt to the result", P[0]["use_id"] == "t1" and P[0]["outcome"] == "allowed" and P[0]["wait"] == 30)
c.check("second identical prompt -> its own call, rejected", P[1]["use_id"] == "t2" and P[1]["outcome"] == "rejected")
c.check("no matching call -> left alone", "outcome" not in P[2])

# ── report ──
prompts = [{"t": T, "sid": "s1", "label": "Proj", "tool": "Bash", "input": {"command": "npm run test"}, "outcome": "allowed",
            "wait": 40, "use_id": "u1"},
           {"t": T + 5, "sid": "s2", "tool": "Bash", "input": {"command": "git status"}, "outcome": "allowed", "wait": 5},
           {"t": T + 9, "sid": "s2", "tool": "Bash", "input": {"command": "cat a | head"}, "outcome": "rejected", "wait": 2},
           {"t": T + 9, "sid": "s2", "tool": "Bash", "input": {"command": "cat b"}, "outcome": "rejected", "wait": 2},
           {"t": T + 9, "sid": "s2", "tool": "Bash", "input": {"command": "python x.py"}, "outcome": "allowed", "wait": 9}]
denials = [{"t": T, "sid": "s1", "tool": "Bash", "input": {"command": "npm run test"}, "reason": "Denied by auto mode", "use_id": "u1"},
           {"t": T, "sid": "s3", "tool": "Bash", "input": {"command": "grep x f"}, "reason": "Auto-mode check unavailable", "use_id": "u9", "secs": 7},
           {"t": T, "sid": "s3", "tool": "Bash", "input": {"command": "ls"}, "reason": "Exit code 1", "use_id": "u8"}]
saved = [{"file": "settings_local", "path": "L", "rule": "Bash(grep -n foo bar.py)"},
         {"file": "settings_local", "path": "L", "rule": "Bash(grep -c x y)"},
         {"file": "settings_local", "path": "L", "rule": "Bash(wc -l a)"},
         {"file": "settings_local", "path": "L", "rule": "PowerShell(Remove-Item *)"},
         {"file": "settings", "path": "U", "rule": "Bash(git status *)"},
         {"file": "project", "path": "P", "rule": "Bash(node *)"}]
rep = ns["_perm_report"](prompts, denials, saved, ["Bash(git status *)"], 30)
sug = {g["rule"]: g for g in rep["suggest"]}
c.check("prompt counted, denial with the same call id not again", sug["Bash(npm run test *)"]["asked"] == 1
        and sug["Bash(npm run test *)"]["denied"] == 0 and sug["Bash(npm run test *)"]["secs"] == 40)
c.check("example = the simple command, not the whole chain",
        next(g for g in rep["blocked"] if g["rule"] == "Bash(cat *)")["example"] in ("cat a", "cat b"))
c.check("denial + saved one-offs", sug["Bash(grep *)"]["denied"] == 1 and sug["Bash(grep *)"]["saved"] == 2)
c.check("single saved one-off isn't enough", "Bash(wc *)" not in sug)
c.check("already allowed flagged and sorted last", sug["Bash(git status *)"]["covered"] and rep["suggest"][-1]["rule"] == "Bash(git status *)")
c.check("mostly rejected -> blocked with why", "Bash(cat *)" not in sug
        and any(g["rule"] == "Bash(cat *)" and "said no" in g["why"] for g in rep["blocked"]))
c.check("unsafe asked -> blocked", any(g["family"] == "python" and g["asked"] == 1 for g in rep["blocked"]))
c.check("non-permission failures ignored", not any(g["family"] == "ls" for g in rep["blocked"] + rep["suggest"]))
c.check("risky: broad + dangerous only, any file", [x["rule"] for x in rep["risky"]] == ["PowerShell(Remove-Item *)", "Bash(node *)"])
c.check("kpis", rep["prompts"] == 5 and rep["rejected"] == 2 and rep["denied"] == 1 and rep["waited"] == 58
        and rep["one_offs"] == 3 and rep["saved"] == 6 and {f["path"] for f in rep["files"]} == {"L", "U", "P"})

# ── prompt log ──
ns["PERM_LOG_FILE"] = os.path.join(tmp, "aoc", "permission_log.jsonl")
ns["_perm_log_add"]({"tool_name": "Bash", "session_id": "s1", "cwd": "C:/x",
                     "tool_input": {"command": "npm test", "description": "Run tests", "timeout": 5}}, T)
ns["_perm_log_add"]({"tool_name": "", "tool_input": {}}, T + 1)
with open(ns["PERM_LOG_FILE"], "a", encoding="utf-8") as f:
    f.write("not json\n")
ns["_perm_log_add"]({"tool_name": "mcp__x__send", "session_id": "s2", "tool_input": {"to": "a@b"}}, T + 2)
got = ns["_perm_log_read"](0)
c.check("logged with the identifying fields only, bad lines skipped", len(got) == 2 and got[0]["input"] == {"command": "npm test"}
        and got[1]["input"] == {} and got[0]["cwd"] == "C:/x")
ns["_perm_log_add"]({"tool_name": "AskUserQuestion", "session_id": "s3", "tool_input": {"questions": []}}, T + 3)
c.check("questions to you aren't permission prompts", len(ns["_perm_log_read"](0)) == 2)
c.check("since filter", [r["tool"] for r in ns["_perm_log_read"](T + 1)] == ["mcp__x__send"])
ns["_PERM_LOG_MAX_BYTES"] = 300
for i in range(10):
    ns["_perm_log_add"]({"tool_name": "Bash", "session_id": "s", "tool_input": {"command": "x" * 40}}, T + 10 + i)
c.check("pruned when the file grows too big", os.path.getsize(ns["PERM_LOG_FILE"]) < 900 and ns["_perm_log_read"](0)[-1]["t"] == T + 19)
ns["_perm_log_add"]({"tool_name": "Bash", "tool_input": {}}, T + 400 * 86400)
c.check("old entries dropped on prune", all(r["t"] >= T + 300 * 86400 for r in ns["_perm_log_read"](0)))

# ── one-click fixes: allow / unallow ──
plan = ns["_fix_plan"]
p = plan({"kind": "allow", "target": "Bash(git status *)"}, {"hooks": {}}, {}, "", {})
c.check("allow creates permissions when missing", p["ok"] and p["changes"] == [{"file": "settings", "op": "set", "path": ["permissions"],
                                                                            "before": None, "after": {"allow": ["Bash(git status *)"]}}])
p = plan({"kind": "allow", "target": "Bash(git status *)"}, {"permissions": {"allow": ["Bash(ls *)"]}}, {}, "", {})
c.check("allow appends", p["changes"][0]["op"] == "list_add" and p["changes"][0]["before"] == ["Bash(ls *)"])
c.check("allow refuses dangerous / broad / already there",
        not plan({"kind": "allow", "target": "Bash(rm *)"}, {}, {}, "", {})["ok"]
        and "covers every git" in plan({"kind": "allow", "target": "Bash(git *)"}, {}, {}, "", {})["error"]
        and not plan({"kind": "allow", "target": "Bash"}, {}, {}, "", {})["ok"]
        and not plan({"kind": "allow", "target": "mcp__x__send_mail"}, {}, {}, "", {})["ok"]
        and plan({"kind": "allow", "target": "Bash(ls *)"}, {"permissions": {"allow": ["Bash(ls *)"]}}, {}, "", {})["error"] == "already allowed")
L = {"permissions": {"allow": ["a", "PowerShell(Remove-Item *)", "b"]}}
p = plan({"kind": "unallow", "target": "settings_local|PowerShell(Remove-Item *)"}, {}, {}, "", L)
c.check("unallow from the local file, index kept", p["ok"] and p["changes"][0]["file"] == "settings_local" and p["changes"][0]["index"] == 1)
c.check("unallow: unknown file / missing rule", not plan({"kind": "unallow", "target": "project|x"}, {}, {}, "", L)["ok"]
        and not plan({"kind": "unallow", "target": "settings_local|zzz"}, {}, {}, "", L)["ok"])
docs = {"settings_local": json.loads(json.dumps(L)), "settings": {"hooks": {}}}
ns["_fix_do"](p["changes"], docs)
c.check("removed", docs["settings_local"]["permissions"]["allow"] == ["a", "b"])
ns["_fix_do"](p["changes"], docs, undo=True)
c.check("undo puts it back where it was", docs["settings_local"] == L)
a = plan({"kind": "allow", "target": "Bash(git status *)"}, docs["settings"], {}, "", {})
ns["_fix_do"](a["changes"], docs)
ns["_fix_do"](a["changes"], docs, undo=True)
c.check("allow + undo leaves settings exactly as before", docs["settings"] == {"hooks": {}})

# end to end over a fake home: apply writes settings.json, undo restores bytes
home = os.path.join(tmp, "home")
os.makedirs(os.path.join(home, ".claude"))
os.environ["USERPROFILE"] = os.environ["HOME"] = home
sp = os.path.join(home, ".claude", "settings.json")
raw = b'{\r\n  "hooks": {}\r\n}\r\n'
with open(sp, "wb") as f:
    f.write(raw)
lp = os.path.join(home, ".claude", "settings.local.json")
with open(lp, "w", encoding="utf-8") as f:
    json.dump({"permissions": {"allow": ["Bash(node *)", "Bash(grep -n a b)", "Bash(grep x y)"]}}, f, indent=2)
r = ns["_fix_apply"]({"kind": "allow", "target": "Bash(grep *)"}, settle=False)
c.check("applied to ~/.claude/settings.json", r["ok"] and json.load(open(sp, encoding="utf-8"))["permissions"]["allow"] == ["Bash(grep *)"])
ns["_fix_undo"](r["record"]["id"], settle=False)
c.check("undo restores the file byte for byte", open(sp, "rb").read() == raw)
r2 = ns["_fix_apply"]({"kind": "unallow", "target": "settings_local|Bash(node *)"}, settle=False)
c.check("unallow edits settings.local.json", r2["ok"] and "Bash(node *)" not in json.load(open(lp, encoding="utf-8"))["permissions"]["allow"])

# _permission_advice: log + transcript + settings files
proj = os.path.join(home, ".claude", "projects", "C--work")
os.makedirs(proj)
with open(os.path.join(proj, "sid-aaaa-1111.jsonl"), "wb") as f:
    f.write(b"\n".join([use("toolu_a", T, "npm run build"), res("toolu_a", T + 20),
                        use("toolu_b", T + 50, "head -5 x"), res("toolu_b", T + 51, True, "Permission for this action was denied by auto mode")]) + b"\n")
os.utime(os.path.join(proj, "sid-aaaa-1111.jsonl"), (T + 60, T + 60))
os.remove(ns["PERM_LOG_FILE"])
ns["_perm_log_add"]({"tool_name": "Bash", "session_id": "sid-aaaa-1111", "tool_input": {"command": "npm run build"}}, T + 2)
ns["_fix_apply"]({"kind": "allow", "target": "Bash(ls *)"}, settle=False)
d = ns["_permission_advice"](30, now=T + 3600)
sug = {g["rule"]: g for g in d["suggest"]}
c.check("advice: prompt matched to its call (wait), denial from the transcript",
        sug["Bash(npm run build *)"]["asked"] == 1 and sug["Bash(npm run build *)"]["secs"] == 18
        and sug["Bash(head *)"]["denied"] == 1 and d["log_since"] == T + 2)
c.check("advice: settings files read, user rules counted, hook state", d["user_rules"] == 1 and d["hook"] is False
        and d["one_offs"] == 2 and sug["Bash(grep *)"]["saved"] == 2)

shutil.rmtree(tmp, ignore_errors=True)
c.finish()
