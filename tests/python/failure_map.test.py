"""Failure map (monitor.py, History -> FAILURES): _fail_reason (readable
reasons, denials apart, Python exceptions, exit codes, normalized
fallback), _fail_target (program a shell command ran: last real command,
heredoc bodies ignored), _fail_scan_lines (only error results resolved,
time + next turn's cost shared among results, idle gaps capped), and
_fail_report's groups, tips and rates; _failure_map over a fake
~/.claude/projects incl. subagent files."""
import sys, os, json, re, glob, time, tempfile, shutil
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
ns = exec_functions(["_MODEL_PRICING", "_CACHE_WRITE_1H_MULT", "_model_pricing", "_calc_cost", "_replay_epoch",
                     "_replay_result_text", "_FAIL_CACHE_S", "_FAIL_GAP_CAP_S", "_FAIL_TOOL_ID", "_FAIL_RESULT_ID",
                     "_FAIL_IS_ERROR", "_FAIL_ASSISTANT", "_FAIL_SHELLS", "_FAIL_TRIVIAL", "_FAIL_SUBCMD", "_FAIL_REASONS", "_fail_reason",
                     "_fail_target", "_fail_scan_lines", "_fail_report", "_FAIL_HINTS", "_fail_files",
                     "_failure_map", "_fail_cache", "_FIX_FOR_REASON"],
                    {"json": json, "re": re, "os": os, "glob": glob, "time": time, "datetime": datetime,
                     "TRANSCRIPT_INDEX_DB": "/nonexistent/x.db", "_log_bg_error": lambda *a: None})

R = ns["_fail_reason"]
c.check("denial", R("Bash", "The user doesn't want to proceed with this tool use.")[:2] == ("You rejected the call", "denied"))
c.check("auto mode unavailable = denied", R("Bash", "The server-side auto mode classifier gave no verdict (error)")[1] == "denied")
c.check("edit before read", R("Edit", "<tool_use_error>File has not been read yet. Read it first</tool_use_error>")[0]
        == "Edited before reading the file")
c.check("missing rg = program, not file", R("Grep", "ENOENT: no such file or directory, uv_spawn 'rg'")[0] == "Program not found")
c.check("shell quoting", R("Bash", "Exit code 2\n/usr/bin/bash: -c: line 9: unexpected EOF while looking for matching `''")[0]
        == "Shell quoting error")
tb = "Exit code 1\nTraceback (most recent call last):\n  File \"x.py\", line 1\nUnicodeEncodeError: 'charmap' codec can't encode"
r = R("Bash", tb)
c.check("python exception + last line as detail", r[0] == "Python UnicodeEncodeError" and r[2].startswith("UnicodeEncodeError"))
c.check("exit code", R("Bash", "Exit code 128\nfatal: not a git repository")[:1] == ("Exit code 128",)
        and R("Bash", "Exit code 128\nfatal: not a git repository")[2] == "fatal: not a git repository")
c.check("HTTP 403 is not a timeout", R("Bash", "Exit code 1\nurllib.error.HTTPError: HTTP Error 403: Forbidden")[0] == "Exit code 1")
c.check("fallback normalized", R("X", "Failed after 3 tries on C:\\tmp\\a.txt")[0] == "Failed after N tries on <path>")
c.check("empty", R("X", "")[0] == "Error with no message")

T = ns["_fail_target"]
c.check("targets", [T("Bash", {"command": x}) for x in (
    'cd "/c/x y" && git push -q origin HEAD 2>&1 | tail -2',
    "python -m pytest -q tests",
    "FOO=1 npm run build",
    "python C:/s/build.py --fast",
    "cat > f.py <<'EOF'\nimport x\nrm -rf /\nEOF\npython f.py",
    "ls; echo hi",
)] == ["git push", "python", "npm run", "python build.py", "python f.py", ""])
c.check("powershell & exe", T("PowerShell", {"command": '& "C:\\Program Files\\Git\\bin\\git.exe" status'}) == "git status")
c.check("non-shell -> ''", T("Read", {"file_path": "x"}) == "")

# ── scanning ──
U = {"input_tokens": 10, "output_tokens": 1000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 100_000}


def ts(s):
    return "2026-10-14T10:%02d:%02d.000Z" % (s // 60, s % 60)


def use(tid, name, inp, s, mid):
    return {"type": "assistant", "timestamp": ts(s), "requestId": "r" + mid,
            "message": {"id": "msg_" + mid, "model": "claude-opus-5-5", "usage": U,
                        "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def results(s, *rs):
    return {"type": "user", "timestamp": ts(s), "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "is_error": err, "content": text} for tid, err, text in rs]}}


lines = [
    use("toolu_1", "Bash", {"command": "git push"}, 0, "a"),
    use("toolu_2", "Read", {"file_path": "x"}, 0, "a"),
    results(5, ("toolu_1", True, "Exit code 1\nrejected"), ("toolu_2", False, "ok")),
    {"type": "assistant", "timestamp": ts(15), "requestId": "rb",
     "message": {"id": "msg_b", "model": "claude-opus-5-5", "usage": U, "content": [{"type": "text", "text": "retry"}]}},
    use("toolu_3", "Edit", {"file_path": "y"}, 20, "c"),
    results(21, ("toolu_3", True, [{"type": "text", "text": "<tool_use_error>String to replace not found in file.</tool_use_error>"}])),
    use("toolu_4", "Bash", {"command": "ls"}, 59 * 60, "d"),          # reacted to after 59 min: capped
    "{broken",
]
raw = [json.dumps(o).encode() if isinstance(o, dict) else o.encode() for o in lines]
sc = ns["_fail_scan_lines"](raw, "sid1", "My session")
f = sc["fails"]
c.check("tool calls counted", sc["calls"] == 4)
c.check("only errors", [x["tool"] for x in f] == ["Bash", "Edit"])
c.check("target + reason + label", f[0]["target"] == "git push" and f[0]["reason"] == "Exit code 1" and f[0]["label"] == "My session")
full = ns["_calc_cost"](10, 1000, 0, 100_000, "claude-opus-5-5")
c.check("next call's cost shared by results (1 of 2 failed)", abs(f[0]["cost"] - full / 2) < 1e-9)
c.check("time = run (5 s) + reaction (10 s)", f[0]["secs"] == 15)
c.check("idle gap capped", f[1]["secs"] == 1 + 600)
c.check("result content as list", f[1]["reason"] == "Text to replace not found")

big = {"calls": 100, "fails": [dict(f[0], sid="s%d" % i, t=2e9 + i, detail="d%d" % i) for i in range(4)]
       + [dict(f[1], kind="failed")] + [dict(f[0], kind="denied", reason="You rejected the call")]}
rep = ns["_fail_report"]([big, sc], 30)
c.check("totals: denied apart", rep["failed"] == 7 and rep["denied"] == 1 and rep["calls"] == 104)
c.check("rate", rep["rate"] == round(7 / 104 * 100, 1))
g = rep["top"][0]
c.check("top group", (g["tool"], g["target"], g["reason"], g["count"], g["sessions"]) == ("Bash", "git push", "Exit code 1", 5, 5))
c.check("latest case for replay", g["last_sid"] == "s3" and g["example"] == "d3")
c.check("by tool / reason", rep["by_tool"][0]["tool"] == "Bash" and {x["reason"] for x in rep["by_reason"]} ==
        {"Exit code 1", "Text to replace not found"})
c.check("denied rows", rep["denied_rows"][0]["reason"] == "You rejected the call")
c.check("no tip under 3 cases / without hint", rep["tips"] == [])
many = {"calls": 10, "fails": [dict(f[1], sid="e%d" % i) for i in range(3)]}
tips = ns["_fail_report"]([many], 7)["tips"]
c.check("tip with hint + monthly scaling", len(tips) == 1 and "CRLF" in tips[0]["detail"]
        and abs(tips[0]["cost_month"] - round(f[1]["cost"] * 3 * 30 / 7, 2)) < 0.011)
c.check("no fix for a reason without one", "fix" not in tips[0])
enc = {"calls": 10, "fails": [dict(f[1], sid="u%d" % i, reason="Python UnicodeEncodeError") for i in range(3)]}
c.check("fix attached for a known reason", ns["_fail_report"]([enc], 30)["tips"][0]["fix"] == {"kind": "env", "target": "PYTHONIOENCODING"})
c.check("empty", ns["_fail_report"]([], 30)["calls"] == 0)

# ── _failure_map over a fake home, subagents mapped to their session ──
home = tempfile.mkdtemp(prefix="aoc_fail_")
try:
    d = os.path.join(home, ".claude", "projects", "C--work")
    os.makedirs(os.path.join(d, "sidA", "subagents"))
    for p in (os.path.join(d, "sidA.jsonl"), os.path.join(d, "sidA", "subagents", "agent-1.jsonl")):
        with open(p, "wb") as fh:
            fh.write(b"\n".join(raw) + b"\n")
    os.environ["USERPROFILE"] = os.environ["HOME"] = home
    now = datetime.fromisoformat("2026-10-14T12:00:00+00:00").timestamp()
    m = ns["_failure_map"](30, now=now)
    c.check("main + subagent scanned, both under the session", m["failed"] == 4 and m["sessions"] == 1
            and m["top"][0]["last_sid"] == "sidA")
    c.check("cached", ns["_failure_map"](30, now=now + 60) is m)
    c.check("old failures outside the period dropped", ns["_failure_map"](7, now=now + 9 * 86400)["calls"] == 0
            or ns["_failure_map"](7, now=now + 9 * 86400)["failed"] == 0)
finally:
    shutil.rmtree(home, ignore_errors=True)
c.finish()
