"""Dangerous-command guard: monitor.py's _danger_check (built-in rules,
quote-aware: text arguments never count, each simple command of a chain on
its own; broad-path logic; custom patterns; rules switched off), settings
sanitize/save/merge, the hold (_danger_open/_danger_wait/_danger_decision/
_danger_hook) against the real remote-approve prompt store, and the HTTP
wiring: /hook/danger answered before the auth check, loopback only; plus
setup.py's second HTTP PreToolUse entry."""
import sys, os, json, re, tempfile, threading, time

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions, read_monitor_source
from lib.check import Checker

c = Checker()
data_dir = tempfile.mkdtemp(prefix="aoc_dg_")
pushes, toasts = [], []


class _Cond:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def notify_all(self): pass


import secrets as _secrets
import socket as _socket

ns = exec_functions(
    ["_PERM_POLL_STALE_S", "_perm_expire", "_perm_decide", "_perm_pending_by_session",
     "_PERM_WORD_OK", "_PERM_SCRIPT_WORDS", "_perm_head", "_perm_simple_commands",
     "DANGER_GUARD_FILE", "_DANGER_MODES", "_DANGER_HOLD_MAX_MIN", "_DANGER_MAX_PATTERNS", "_DANGER_RULES",
     "_DANGER_RULE_IDS", "_DANGER_TEXT_HEADS", "_DANGER_RM_HEADS", "_DANGER_SQL", "_danger_cfg_cache",
     "_load_json_file", "_sanitize_danger_guard", "_load_danger_guard", "_save_danger_guard",
     "_danger_unquote", "_danger_broad_path", "_danger_rm_hit", "_danger_git_hits", "_danger_db_hit",
     "_danger_disk_hit", "_danger_check", "_danger_reply", "_danger_open", "_danger_wait", "_danger_decision",
     "_danger_hook", "_danger_hook_installed", "_danger_view"],
    {"os": os, "json": json, "re": re, "time": time, "threading": threading, "_secrets": _secrets, "_socket": _socket,
     "AOC_DATA_DIR": os.path.join(data_dir, "AOC"), "_perm_lock": threading.Lock(), "_perm_requests": {},
     "_status_cond": _Cond(), "_status_version": [0],
     "_push_session_name": lambda sid: "Sess " + str(sid),
     "_push_notify": lambda *a, **k: pushes.append((a, k)) or 1,
     "_show_native_toast": lambda t, b: toasts.append((t, b)),
     "_log_bg_error": lambda *a: None})

CFG = ns["_sanitize_danger_guard"]({})
chk = ns["_danger_check"]


def ids(cmd, shell="Bash", cfg=None):
    return sorted({h["id"] for h in chk(cmd, shell, cfg or CFG)})


# ── settings ──
san = ns["_sanitize_danger_guard"]
c.check("defaults: hold, 10 min, all rules on, no patterns",
        CFG == {"mode": "hold", "hold_min": 10, "off_rules": [], "patterns": []})
c.check("hold_min clamped 1..60, garbage -> 10",
        san({"hold_min": 0})["hold_min"] == 1 and san({"hold_min": 999})["hold_min"] == 60 and san({"hold_min": "x"})["hold_min"] == 10)
c.check("bad mode -> hold", san({"mode": "nope"})["mode"] == "hold" and san({"mode": "off"})["mode"] == "off")
c.check("unknown rule ids dropped", san({"off_rules": ["force_push", "zzz"]})["off_rules"] == ["force_push"])
c.check("bad regex / dupes / blanks dropped", san({"patterns": ["a(", "kubectl delete", "kubectl delete", " "]})["patterns"] == ["kubectl delete"])
saved = ns["_save_danger_guard"]({"hold_min": 5, "patterns": ["x["]})
c.check("save reports rejected patterns", saved["rejected"] == ["x["] and saved["hold_min"] == 5)
ns["_save_danger_guard"]({"mode": "off"})
c.check("save merges: unsent fields keep their value", ns["_load_danger_guard"]() == {**CFG, "hold_min": 5, "mode": "off"})
ns["_save_danger_guard"]({"mode": "hold", "hold_min": 10})

# ── git ──
for cmd in ["git push --force", "git push -f origin main", "git push origin +main", "git push --force-with-lease",
            "git -C repo push -uf origin x", 'cd x && git push --force origin "main"']:
    c.check(f"force push: {cmd}", "force_push" in ids(cmd))
for cmd in ["git push", "git push -u origin feature", "git push origin main:main", "git pull --force"]:
    c.check(f"not force push: {cmd}", "force_push" not in ids(cmd))
c.check("remote branch delete", ids("git push origin --delete old") == ["remote_branch_delete"]
        and ids("git push origin :old") == ["remote_branch_delete"])
for cmd in ["git reset --hard HEAD~1", "git clean -fdx", "git checkout -- .", "git restore .", "git stash clear"]:
    c.check(f"discard: {cmd}", ids(cmd) == ["git_discard"])
for cmd in ["git reset HEAD~1", "git reset --soft HEAD~1", "git clean -n", "git checkout main", "git restore --staged .",
            "git checkout -- src/a.py", "git stash drop", "git branch -D tmp", "git log --oneline"]:
    c.check(f"harmless git: {cmd}", ids(cmd) == [])

# ── rm / Remove-Item ──
for cmd in ["rm -rf /", "rm -rf ~", "rm -rf ~/projekty", "rm -Rf .", "rm -rf *", "rm -rf ../..", "rm -r -f /c/Users/marek",
            "rm -rf \"$DIR\"/", "rm -rf $HOME/*", "sudo rm -rf /usr", "rm --recursive /etc", "rm -rf C:/Users/marek/projekty"]:
    c.check(f"broad rm: {cmd}", ids(cmd) == ["rm_broad"])
for cmd in ["rm -rf node_modules", "rm -rf ./build", "rm -rf /tmp/aoc_x", "rm -f /", "rm -rf \"$DIR\"", "rm *.log",
            "rm -rf C:/Users/marek/projekty/aoc/dist", "rm -rf /home/u/proj/build", "rm -rf ~/projekty/x/build"]:
    c.check(f"narrow rm: {cmd}", ids(cmd) == [])
ps = "PowerShell"
for cmd in ["Remove-Item -Recurse -Force C:\\", "Remove-Item C:\\Users\\marek -Recurse", "rm -r -fo $env:USERPROFILE",
            "ri ~\\Documents -Rec", "Remove-Item -Path '.' -Recurse", "rd /s /q C:\\Users", "Get-Date; Remove-Item * -Recurse"]:
    c.check(f"broad PS: {cmd}", ids(cmd, ps) == ["rm_broad"])
for cmd in ["Remove-Item -Recurse -Force .\\dist", "Remove-Item C:\\Users\\marek -Force", "rm -Force x.txt",
            "Remove-Item -Recurse $env:TEMP\\aoc_x", "rd /s /q build"]:
    c.check(f"narrow PS: {cmd}", ids(cmd, ps) == [])
bp = ns["_danger_broad_path"]
c.check("broad paths", all(bp(p) for p in ["/", "C:", "C:\\", "D:/data", "/c", "/mnt/c/Users", "~", "$HOME", "..", "./*", "/home/u", "$X/*"]))
c.check("not broad", not any(bp(p) for p in ["", "build", "./build", "C:/a/b/c", "/tmp/x", "$X", "$X/build"]))

# ── SQL / DB / disk ──
for cmd in ['sqlite3 h.db "DROP TABLE sessions"', 'psql -c "TRUNCATE users"', "psql -c 'DELETE FROM users;'",
            'python -c "import sqlite3; c=sqlite3.connect(\'h.db\'); c.execute(\'DELETE FROM sessions\')"',
            "psql mydb <<'SQL'\nDROP DATABASE prod;\nSQL", 'mysql -e "drop schema app"']:
    c.check(f"sql destroy: {cmd[:40]}", ids(cmd) == ["sql_destroy"])
for cmd in ['psql -c "DELETE FROM users WHERE id = 3"', 'git commit -m "fix DROP TABLE bug"', 'echo "TRUNCATE x"',
            'grep -r "DELETE FROM" .', 'sqlite3 h.db "SELECT * FROM sessions"', 'gh pr create --body "drop table t"',
            "cat <<'EOF' > notes.md\nDROP TABLE here is just text\nEOF"]:
    c.check(f"not sql destroy: {cmd[:40]}", "sql_destroy" not in ids(cmd))
for cmd in ["npx prisma migrate reset", "prisma db push --force-reset", "dropdb app", "supabase db reset",
            "docker compose down -v", "docker volume rm pg", "docker volume prune -f"]:
    c.check(f"db reset: {cmd}", ids(cmd) == ["db_reset"])
for cmd in ["npx prisma migrate dev", "docker compose down", "docker volume ls", "supabase start"]:
    c.check(f"not db reset: {cmd}", ids(cmd) == [])
for cmd in ["format D:", "diskpart", "mkfs.ext4 /dev/sdb1", "dd if=/dev/zero of=/dev/sda", "Clear-Disk -Number 1"]:
    c.check(f"disk wipe: {cmd}", ids(cmd, ps if cmd.startswith("Clear") else "Bash") == ["disk_wipe"])
c.check("dd to a file is fine", ids("dd if=/dev/zero of=img.bin bs=1M count=1") == [])

# ── chains, patterns, off ──
c.check("chain: only the dangerous part counts, part reported",
        [(h["id"], h["part"]) for h in chk("npm test && git push --force && echo done", "Bash", CFG)] == [("force_push", "git push --force")])
c.check("two rules at once", ids("git reset --hard && rm -rf ~") == ["git_discard", "rm_broad"])
c.check("quoted && is not a separator", ids('echo "a && git push --force"') == [])
c.check("rule switched off", ids("git push -f", cfg=san({"off_rules": ["force_push"]})) == [])
c.check("mode off -> nothing", ids("rm -rf /", cfg=san({"mode": "off"})) == [])
pc = san({"patterns": [r"\bkubectl\s+delete\b"]})
c.check("custom pattern (case-insensitive)", ids("KUBECTL delete ns prod", cfg=pc) == ["pattern"] and ids("kubectl get pods", cfg=pc) == [])
c.check("empty / None command", chk("", "Bash", CFG) == [] and chk(None, "Bash", CFG) == [])

# ── the hold ──
reqs = ns["_perm_requests"]
hook = {"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Bash", "cwd": "C:/x",
        "tool_input": {"command": "git push --force", "description": "Push the rewrite"}}
c.check("harmless command -> {} at once, nothing held",
        ns["_danger_hook"]({**hook, "tool_input": {"command": "ls"}}, cfg=CFG) == {} and not reqs)
c.check("other tools / events ignored",
        ns["_danger_hook"]({**hook, "tool_name": "Write"}, cfg=CFG) == {}
        and ns["_danger_hook"]({**hook, "hook_event_name": "PostToolUse"}, cfg=CFG) == {} and ns["_danger_hook"](None) == {})

seen = {}


def fake_wait(decision, message=""):
    def w(rid, hold_s, gone_fn=None):
        seen["pending"] = ns["_perm_pending_by_session"](time.time())
        seen["hold_s"] = hold_s
        ns["_perm_decide"](rid, decision, message)
        return ns["_danger_wait"](rid, hold_s, sleep_fn=lambda s: None)
    return w


pushes.clear(); toasts.clear()
r = ns["_danger_hook"](hook, cfg=CFG, wait=fake_wait("allow"))
out = r["hookSpecificOutput"]
c.check("allowed -> permissionDecision allow", out["permissionDecision"] == "allow" and out["hookEventName"] == "PreToolUse")
p = seen["pending"]["s1"][0]
c.check("held as a Needs-your-OK prompt with the danger label",
        p["danger"] == "Force push" and p["summary"] == "Bash: git push --force" and "Push the rewrite" in p["detail"])
c.check("hold time = hold_min", seen["hold_s"] == 600)
c.check("phone push urgent + toast", pushes and pushes[0][1].get("urgent") is True and toasts and "dangerous" in toasts[0][0])
c.check("request gone after the answer", not reqs)

r = ns["_danger_hook"](hook, cfg=CFG, wait=fake_wait("deny", "use a new branch"))
reason = r["hookSpecificOutput"]["permissionDecisionReason"]
c.check("denied -> deny with the user's words", r["hookSpecificOutput"]["permissionDecision"] == "deny" and "use a new branch" in reason)
r = ns["_danger_hook"](hook, cfg=CFG, wait=fake_wait("terminal"))
c.check("answer in terminal -> ask", r["hookSpecificOutput"]["permissionDecision"] == "ask")

# real wait loop: timeout and hang-up
clock = [1000.0]
rid = ns["_danger_open"](hook, chk("git push -f", "Bash", CFG), clock[0])


def tick(s):
    clock[0] += s


st = ns["_danger_wait"](rid, 5, now_fn=lambda: clock[0], sleep_fn=tick)
c.check("no answer -> timeout, request removed", st == ("timeout", "") and rid not in reqs)
c.check("timeout -> deny that says not to retry",
        "do not retry" in ns["_danger_decision"]("timeout", "", [{"label": "Force push"}], 10)["hookSpecificOutput"]["permissionDecisionReason"].lower())
rid = ns["_danger_open"](hook, chk("git push -f", "Bash", CFG), clock[0])
calls = [0]


def gone():
    calls[0] += 1
    return calls[0] > 2


st = ns["_danger_wait"](rid, 600, now_fn=lambda: clock[0], sleep_fn=tick, gone_fn=gone)
c.check("Claude Code hung up -> gone, request removed", st == ("gone", "") and rid not in reqs)
c.check("gone -> {} (nobody is listening)", ns["_danger_decision"]("gone", "", [], 10) == {})
rid = ns["_danger_open"](hook, chk("git push -f", "Bash", CFG), clock[0])
pend_age_ok = True
st_box = []
t = threading.Thread(target=lambda: st_box.append(ns["_danger_wait"](rid, 30)))
t.start()
time.sleep(1.2)
c.check("held request stays fresh while waiting (not expired by the stale sweep)",
        "s1" in ns["_perm_pending_by_session"](time.time() + 10))
ns["_perm_decide"](rid, "allow")
t.join(5)
c.check("real-time wait returns the dashboard's answer", st_box == [("allow", "")])

# ── view / hook-installed ──
v = ns["_danger_view"](CFG, {"hooks": {"PreToolUse": [{"matcher": "Bash|PowerShell", "hooks": [{"type": "http", "url": "http://127.0.0.1:5151/hook/danger"}]}]}})
c.check("view lists every rule + hook installed", len(v["rules"]) == len(ns["_DANGER_RULES"]) and v["hook_installed"] is True)
c.check("hook missing detected", ns["_danger_view"](CFG, {})["hook_installed"] is False)

# ── HTTP wiring + /status + setup.py ──
mon = read_monitor_source()
post = mon.index("    def do_POST(self):")
c.check("do_POST answers /hook/danger before the auth check, loopback only",
        post < mon.index('== "/hook/danger" and self._is_direct_loopback()') < mon.index("if not self._check_auth():", post))
c.check("the handler passes a hang-up check", "gone_fn=lambda: _socket_hung_up(self.connection)" in mon)
c.check("/status marks a session with a held dangerous command as waiting (permission)",
        'if q.get("danger")]' in mon and 's["waiting_kind"] = "permission"' in mon)
c.check("hold max stays under setup.py's hook timeout", ns["_DANGER_HOLD_MAX_MIN"] * 60 < 3700)

setup_path = os.path.join(os.path.dirname(__file__), "..", "..", "setup.py")
sp = exec_functions(["_is_run_hook", "_is_held_hook", "_is_budget_hook", "_merge_http_hook", "_merge_budget_hook",
                     "_merge_danger_hook", "BUDGET_HOOK_URL", "BUDGET_HOOK_TIMEOUT_S", "DANGER_HOOK_URL",
                     "DANGER_HOOK_MATCHER", "DANGER_HOOK_TIMEOUT_S", "PERMISSION_SCRIPT"],
                    {"os": os}, path=setup_path)
s = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "mine.sh"}]}]}}
c.check("budget then danger entry added", sp["_merge_budget_hook"](s) == "added" and sp["_merge_danger_hook"](s) == "added")
pre = s["hooks"]["PreToolUse"]
c.check("danger entry: shell tools, own url, long timeout",
        {"matcher": "Bash|PowerShell", "hooks": [{"type": "http", "url": "http://127.0.0.1:5151/hook/danger", "timeout": 3700}]} in pre)
c.check("budget entry untouched by the danger merge",
        {"matcher": "*", "hooks": [{"type": "http", "url": "http://127.0.0.1:5151/hook/pretool", "timeout": 3}]} in pre)
c.check("re-run -> both unchanged", sp["_merge_budget_hook"](s) == "unchanged" and sp["_merge_danger_hook"](s) == "unchanged")
pre[-1]["hooks"][0]["timeout"] = 5
c.check("drifted danger entry updated in place", sp["_merge_danger_hook"](s) == "updated" and pre[-1]["hooks"][0]["timeout"] == 3700 and len(pre) == 3)

c.finish()
