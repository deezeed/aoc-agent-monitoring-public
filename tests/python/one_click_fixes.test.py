"""One-click fixes (monitor.py): _fix_plan (allowlisted kinds only, already-done
detection, every known folder for MCP), _fix_do apply/undo (a value changed by
hand since is left alone, list item removal, CLAUDE.md line), and
_fix_apply/_fix_undo end to end over a fake home: backups, fixes.json log,
re-apply after a running session rewrote ~/.claude.json (_fix_settle)."""
import sys, os, json, re, glob, time, threading, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
tmp = tempfile.mkdtemp(prefix="aoc_fix_")
ns = exec_functions(["_FIX_ENV", "_FIX_NOTES", "_FIX_LOCK", "_fix_paths", "_fix_key", "_fix_read_json", "_fix_plan",
                     "_fix_get", "_fix_set", "_fix_do", "_fix_load_docs", "_fix_write", "_fix_load_log",
                     "_fix_save_log", "_fix_settle", "_fix_preview", "_fix_apply", "_fix_undo"],
                    {"json": json, "re": re, "os": os, "glob": glob, "time": time, "threading": threading,
                     "AOC_DATA_DIR": os.path.join(tmp, "aoc"), "FIXES_FILE": os.path.join(tmp, "aoc", "fixes.json")})
plan = ns["_fix_plan"]

S = {"enabledPlugins": {"vercel@official": True}, "hooks": {"x": 1}}
CJ = {"numStartups": 5, "projects": {"C:/a": {"disabledMcpServers": ["other"]}, "C:/b": {}, "C:/c": {"disabledMcpServers": ["claude.ai Vercel"]}}}
p = plan({"kind": "plugin", "target": "vercel@official"}, S, CJ, "")
c.check("plugin plan", p["ok"] and p["changes"] == [{"file": "settings", "op": "set", "path": ["enabledPlugins", "vercel@official"],
                                                    "before": True, "after": False}])
c.check("plugin already off", not plan({"kind": "plugin", "target": "x@y"}, {"enabledPlugins": {"x@y": False}}, CJ, "")["ok"])
c.check("bad plugin id rejected", not plan({"kind": "plugin", "target": "../evil"}, S, CJ, "")["ok"])
m = plan({"kind": "mcp", "target": "claude.ai Vercel"}, S, CJ, "")
c.check("mcp: every folder that doesn't have it yet", [ch["path"][1] for ch in m["changes"]] == ["C:/a", "C:/b"]
        and m["changes"][1]["before"] is None)
c.check("mcp already off everywhere", not plan({"kind": "mcp", "target": "other"},
                                               S, {"projects": {"C:/a": {"disabledMcpServers": ["other"]}}}, "")["ok"])
c.check("env allowlisted only", plan({"kind": "env", "target": "PYTHONIOENCODING"}, S, CJ, "")["ok"]
        and not plan({"kind": "env", "target": "PATH"}, S, CJ, "")["ok"])
c.check("note allowlisted + not twice", plan({"kind": "note", "target": "heredoc"}, S, CJ, "")["ok"]
        and not plan({"kind": "note", "target": "rm -rf"}, S, CJ, "")["ok"]
        and not plan({"kind": "note", "target": "heredoc"}, S, CJ, ns["_FIX_NOTES"]["heredoc"])["ok"])
c.check("unknown kind", not plan({"kind": "manual", "target": "x"}, S, CJ, "")["ok"])

# apply / undo in memory
docs = {"settings": json.loads(json.dumps(S)), "claude_json": json.loads(json.dumps(CJ)), "claude_md": "# Mine\n"}
ch = (plan({"kind": "plugin", "target": "vercel@official"}, S, CJ, "")["changes"] + m["changes"]
      + plan({"kind": "note", "target": "heredoc"}, S, CJ, "")["changes"])
ns["_fix_do"](ch, docs)
c.check("applied", docs["settings"]["enabledPlugins"]["vercel@official"] is False
        and docs["claude_json"]["projects"]["C:/a"]["disabledMcpServers"] == ["other", "claude.ai Vercel"]
        and docs["claude_json"]["projects"]["C:/b"]["disabledMcpServers"] == ["claude.ai Vercel"]
        and docs["claude_md"].endswith(ns["_FIX_NOTES"]["heredoc"] + "\n") and docs["claude_md"].startswith("# Mine"))
ns["_fix_do"](ch, docs, undo=True)
c.check("undone exactly", docs["settings"] == S and docs["claude_json"] == CJ and docs["claude_md"].strip() == "# Mine")
docs2 = {"settings": {"enabledPlugins": {"vercel@official": "hand"}}, "claude_json": {}, "claude_md": ""}
sk = ns["_fix_do"](ch[:1], docs2, undo=True)
c.check("hand-changed value left alone on undo", len(sk) == 1 and docs2["settings"]["enabledPlugins"]["vercel@official"] == "hand")
d3 = {"settings": {}}
ns["_fix_do"]([{"file": "settings", "op": "set", "path": ["env", "PYTHONIOENCODING"], "before": None, "after": "utf-8"}], d3)
c.check("parents created", d3["settings"] == {"env": {"PYTHONIOENCODING": "utf-8"}})

# ── end to end over a fake home ──
home = os.path.join(tmp, "home")
os.makedirs(os.path.join(home, ".claude"))
os.environ["USERPROFILE"] = os.environ["HOME"] = home
paths = ns["_fix_paths"]()
json.dump(S, open(paths["settings"], "w"))
json.dump(CJ, open(paths["claude_json"], "w"))
r = ns["_fix_apply"]({"kind": "plugin", "target": "vercel@official"}, settle=False)
c.check("apply ok + recorded", r["ok"] and ns["_fix_load_log"]()[0]["key"] == "plugin:vercel@official")
c.check("file changed, rest kept", json.load(open(paths["settings"]))["enabledPlugins"]["vercel@official"] is False
        and json.load(open(paths["settings"]))["hooks"] == {"x": 1})
c.check("backup made", len(glob.glob(paths["settings"] + ".bak-aoc-fix-*")) == 1)
c.check("preview after apply says done", ns["_fix_preview"]({"kind": "plugin", "target": "vercel@official"})["ok"] is False)
u = ns["_fix_undo"](r["record"]["id"], settle=False)
c.check("undo ok, log emptied", u["ok"] and json.load(open(paths["settings"])) == S and ns["_fix_load_log"]() == [])
c.check("undo unknown id", not ns["_fix_undo"]("nope", settle=False)["ok"])
r = ns["_fix_apply"]({"kind": "note", "target": "utf8_open"}, settle=False)
c.check("CLAUDE.md created", r["ok"] and open(paths["claude_md"], encoding="utf-8").read().strip() == ns["_FIX_NOTES"]["utf8_open"])
# a running session writes its own copy of ~/.claude.json back
r = ns["_fix_apply"]({"kind": "mcp", "target": "claude.ai Vercel"}, settle=False)
json.dump(CJ | {"numStartups": 6}, open(paths["claude_json"], "w"))
ok = ns["_fix_settle"](paths, r["record"]["changes"], retries=2, wait=0)
after = json.load(open(paths["claude_json"]))
c.check("settle re-applies over a session's write, keeps its other changes",
        ok and after["numStartups"] == 6 and "claude.ai Vercel" in after["projects"]["C:/b"]["disabledMcpServers"])
c.check("backups pruned to 5", len(glob.glob(paths["claude_json"] + ".bak-aoc-fix-*")) <= 5)
u = ns["_fix_undo"](r["record"]["id"], settle=False)
after = json.load(open(paths["claude_json"]))
c.check("mcp undo removes only its item", u["ok"] and after["projects"]["C:/a"]["disabledMcpServers"] == ["other"]
        and "disabledMcpServers" not in after["projects"]["C:/b"])
c.check("fix key", ns["_fix_key"]({"kind": "env", "target": "X"}) == "env:X")
# line endings and a missing final newline survive apply + undo byte for byte
orig = json.dumps(dict(S, note="a — b"), indent=2).replace("\n", "\r\n").encode()   # \u escapes stay escapes
open(paths["settings"], "wb").write(orig)
r = ns["_fix_apply"]({"kind": "plugin", "target": "vercel@official"}, settle=False)
now = open(paths["settings"], "rb").read()
c.check("CRLF kept on apply, no final newline added", b"\r\n" in now and b"\n" not in now.replace(b"\r\n", b"") and not now.endswith(b"\n"))
ns["_fix_undo"](r["record"]["id"], settle=False)
c.check("undo restores the exact bytes", open(paths["settings"], "rb").read() == orig)
shutil.rmtree(tmp, ignore_errors=True)
c.finish()
