"""Startup context (monitor.py, History -> CONTEXT): _ctx_scan_lines over
transcript lines (attachments before the first API call, skill listing per
plugin incl. continuation lines, MCP tools + instructions per server incl.
servers that connect later, Skill / slash-command / MCP usage, calls counted
once without JSON parsing), labels, and _ctx_report's costs and tips."""
import sys, os, json, re, glob, time, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
ns = exec_functions(["_MODEL_PRICING", "_CACHE_WRITE_1H_MULT", "_model_pricing", "_calc_cost",
                     "_CTX_CHARS_PER_TOKEN", "_CTX_CACHE_S", "_CTX_LATE_MARKERS", "_CTX_MSG_ID", "_CTX_REQ_ID",
                     "_ctx_cache", "_ctx_tokens", "_ctx_mcp_key", "_ctx_mcp_label", "_ctx_plugin",
                     "_ctx_scan_lines", "_ctx_report", "_context_report"],
                    {"json": json, "re": re, "os": os, "glob": glob, "time": time})

lbl, key, plug = ns["_ctx_mcp_label"], ns["_ctx_mcp_key"], ns["_ctx_plugin"]
c.check("labels", [lbl(x) for x in ("claude_ai_Google_Drive", "plugin_design_figma", "github")]
        == ["claude.ai Google Drive", "figma (design plugin)", "github"])
c.check("instructions name and tool segment share a key", key("claude.ai Google Drive") == key("claude_ai_Google_Drive"))
c.check("plugins", [plug(x) for x in ("vercel:deploy", "/design:critique", "simplify", "")]
        == ["vercel", "design", "(no plugin)", "(no plugin)"])

SKILLS = ("- vercel:deploy: Deploy the project.\n- vercel:env: Manage env vars.\n"
          "- simplify: Review changed code.\nTRIGGER when the user asks.\n- design:critique: Feedback.")
U1 = {"input_tokens": 3, "cache_creation_input_tokens": 50_000, "cache_read_input_tokens": 0}
U2 = {"input_tokens": 3, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 50_000}


def att(a):
    return {"type": "attachment", "cwd": r"C:\work\shop", "attachment": a}


def asst(mid, content, u=U2, side=False):
    return {"type": "assistant", "requestId": "req_" + mid, "isSidechain": side,
            "message": {"id": "msg_" + mid, "model": "claude-opus-5-5", "usage": u, "content": content}}


lines = [
    att({"type": "skill_listing", "content": SKILLS, "skillCount": 4}),
    att({"type": "deferred_tools_delta", "addedLines": ["WebFetch", "mcp__claude_ai_Gmail__send", "mcp__claude_ai_Gmail__read",
                                                        "mcp__plugin_design_figma__authenticate"]}),
    att({"type": "mcp_instructions_delta", "addedNames": ["claude.ai Gmail"], "addedBlocks": ["## claude.ai Gmail\n" + "x" * 370]}),
    att({"type": "agent_listing_delta", "addedLines": ["- Explore: search"]}),
    att({"type": "instructions", "files": [{"path": r"C:\u\.claude\CLAUDE.md", "type": "User", "content": "y" * 37000}]}),
    att({"type": "prompt_snapshot", "systemPrompt": ["z" * 3700, {"not": "text"}]}),
    {"type": "user", "cwd": r"C:\work\shop", "message": {"content": "a" * 370}},
    {"type": "user", "isMeta": True, "message": {"content": "meta " * 50}},
    asst("1", [{"type": "text", "text": "hi"}], U1),
    asst("1", [{"type": "tool_use", "name": "Skill", "input": {"skill": "vercel:deploy"}}], U1),   # same call, 2nd block
    asst("2", [{"type": "tool_use", "name": "mcp__claude_ai_Gmail__send", "input": {}}]),
    asst("3", [{"type": "text", "text": "plain"}]),                                                 # fast path
    asst("4", [{"type": "text", "text": "side"}], side=True),                                       # subagent: not counted
    {"type": "user", "message": {"content": "<command-name>/design:critique</command-name>"}},
    att({"type": "skill_listing", "content": "- late:skill: after the first call"}),                # ignored
    att({"type": "deferred_tools_delta", "addedLines": ["mcp__claude_ai_Vercel__deploy"]}),          # late server: counted
    att({"type": "mcp_instructions_delta", "addedNames": ["claude.ai Vercel"], "addedBlocks": ["## claude.ai Vercel\nhi"]}),
]
raw = [json.dumps(o).encode() for o in lines] + [b"{broken"]
s = ns["_ctx_scan_lines"](raw)
c.check("first call size", s["first"] == 50_003)
c.check("calls: each API call once, subagent line skipped", s["calls"] == 3)
c.check("your prompt counted, meta not", s["prompt_chars"] == 370)
c.check("skills per plugin, continuation line to its skill",
        s["skills"]["vercel"][0] == 2 and s["skills"]["(no plugin)"] == [1, len("- simplify: Review changed code.") + 1 + len("TRIGGER when the user asks.") + 1]
        and s["skills"]["design"][0] == 1 and "late" not in s["skills"])
c.check("uses: Skill tool + slash command", s["skill_uses"] == {"vercel": 1, "design": 1})
c.check("mcp uses", s["mcp_uses"] == {key("claude_ai_Gmail"): 1})
g = s["mcp"][key("claude.ai Gmail")]
c.check("mcp server: tools + instructions, instructions' name", g[0] == "claude.ai Gmail" and g[1] == 27 + 27 + 19 + 370)
c.check("late MCP server counted", s["mcp"][key("claude.ai Vercel")][0] == "claude.ai Vercel")
c.check("plugin MCP label", s["mcp"][key("plugin_design_figma")][0] == "figma (design plugin)")
c.check("parts", set(s["parts"]) == {"skills", "deferred tool names", "MCP server instructions", "agent types",
                                      "CLAUDE.md & memory", "system prompt"} and s["parts"]["system prompt"] == 3700)
c.check("files", s["files"] == {r"C:\u\.claude\CLAUDE.md": ["User", 37000]})
c.check("no API call -> None", ns["_ctx_scan_lines"]([json.dumps(lines[0]).encode()]) is None)

# ── report ──
cost = ns["_calc_cost"]
r = ns["_ctx_report"]([s], 30)
start = 50_003 - 100
want = cost(0, 0, start, 0, "claude-opus-5-5") + cost(0, 0, 0, start * 2, "claude-opus-5-5")
c.check("cost: one write + reads on later calls", abs(r["cost"] - round(want, 2)) < 0.006)
c.check("avg tokens", r["avg_tokens"] == start and r["sessions"] == 1)
c.check("breakdown has the rest", r["breakdown"][0]["part"] == "built-in tools & rest"
        and sum(b["tokens"] for b in r["breakdown"]) <= start + 5)
tips = {t["title"]: t for t in r["tips"]}
c.check("no tip for used plugins / used MCP", not any("vercel" == t.split(": ")[-1] or "Gmail" in t for t in tips))
c.check("big file tip", "Big instructions file: CLAUDE.md" in tips)
c.check("small unused things under the threshold -> no tip", "Unused MCP server: claude.ai Vercel" not in tips)
c.check("projects", r["projects"][0]["project"] == "shop" and r["projects"][0]["sessions"] == 1)
# an unused plugin with a big listing
big = dict(s, skills={**s["skills"], "finance": [8, 4000]}, mcp={**s["mcp"], "x": ["claude.ai Shopify", 4000]})
r2 = ns["_ctx_report"]([big, s], 7)
t2 = {t["title"]: t for t in r2["tips"]}
c.check("unused plugin tip", "Unused plugin: finance" in t2 and "`/plugin`" in t2["Unused plugin: finance"]["detail"])
c.check("unused MCP tip", "Unused MCP server: claude.ai Shopify" in t2)
sk = next(g for g in r2["skills"] if g["plugin"] == "finance")
c.check("monthly scaling (7 days -> x30/7)", abs(t2["Unused plugin: finance"]["saving_month"] - round(sk["cost"] * 30 / 7, 2)) < 0.011)
c.check("tips sorted by saving", [t["saving_month"] for t in r2["tips"]] == sorted((t["saving_month"] for t in r2["tips"]), reverse=True))
lean = dict(s, files={})
c.check("lean case", ns["_ctx_report"]([lean], 30)["tips"][0]["kind"] == "fine")
c.check("no sessions", ns["_ctx_report"]([], 30) == {"days": 30, "sessions": 0})

# ── _context_report over a fake ~/.claude/projects, cached ──
home = tempfile.mkdtemp(prefix="aoc_ctx_")
try:
    d = os.path.join(home, ".claude", "projects", "C--work")
    os.makedirs(os.path.join(d, "sid", "subagents"))
    with open(os.path.join(d, "sid.jsonl"), "wb") as f:
        f.write(b"\n".join(raw) + b"\n")
    with open(os.path.join(d, "sid", "subagents", "agent-1.jsonl"), "wb") as f:   # not a session start
        f.write(b"\n".join(raw) + b"\n")
    old = os.path.join(d, "old.jsonl")
    with open(old, "wb") as f:
        f.write(b"\n".join(raw) + b"\n")
    os.utime(old, (time.time() - 40 * 86400,) * 2)
    os.environ["USERPROFILE"] = os.environ["HOME"] = home
    rep = ns["_context_report"](30)
    c.check("scan: main transcripts in the period only", rep["sessions"] == 1)
    os.remove(os.path.join(d, "sid.jsonl"))
    c.check("cached", ns["_context_report"](30)["sessions"] == 1)
    c.check("cache expires", ns["_context_report"](30, now=time.time() + 16 * 60)["sessions"] == 0)
finally:
    shutil.rmtree(home, ignore_errors=True)
c.finish()
