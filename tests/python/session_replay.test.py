"""Session replay: monitor.py's _replay_steps over a synthetic transcript --
prompts vs slash commands vs meta lines, assistant text, tool calls matched
to their results (error flag, output, duration), git commits, each API
call's cost counted once and put on the first visible step of that call
(thinking-only calls kept as their own step), sidechain lines skipped,
truncation; _find_transcript's id check and _session_replay's cache."""
import sys, os, json, re, glob, shutil, tempfile, threading

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
from datetime import datetime

ns = exec_functions(
    ["_MODEL_PRICING", "_model_pricing", "_CACHE_WRITE_1H_MULT", "_calc_cost", "_perm_summary", "_GIT_COMMIT_RE",
     "_is_commit_call", "_REPLAY_MAX_STEPS", "_REPLAY_TEXT_MAX", "_replay_cache", "_replay_cache_lock", "_replay_epoch",
     "_replay_result_text", "_replay_steps", "_find_transcript", "_session_replay"],
    {"os": os, "json": json, "re": re, "glob": glob, "threading": threading, "datetime": datetime,
     "_transcript_cursors": {}, "_transcript_cursors_lock": threading.Lock()})
calc = ns["_calc_cost"]

U = lambda inp, out, cw=0, cr=0: {"input_tokens": inp, "output_tokens": out, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr}
T = lambda s: f"2026-10-13T10:00:{s:02d}.000Z"


def asst(mid, req, block, usage, s, model="claude-opus-5-5", side=False):
    return json.dumps({"type": "assistant", "timestamp": T(s), "requestId": req, "isSidechain": side,
                       "message": {"id": mid, "model": model, "usage": usage, "content": [block]}})


def user(content, s, meta=False):
    return json.dumps({"type": "user", "timestamp": T(s), "isMeta": meta, "message": {"content": content}})


lines = [
    user("<command-name>/login</command-name>", 0),
    user("Caveat: meta line", 0, meta=True),
    user("Fix the parser please", 1),
    asst("m1", "r1", {"type": "thinking", "thinking": "hmm"}, U(100, 50, 1000), 2),
    asst("m1", "r1", {"type": "text", "text": "Looking at it."}, U(100, 50, 1000), 2),
    asst("m1", "r1", {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest -q"}}, U(100, 50, 1000), 2),
    user([{"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "2 failed"}], 14),
    asst("m2", "r2", {"type": "thinking", "thinking": "only thinking"}, U(10, 5, 0, 2000), 15),
    asst("m3", "r3", {"type": "tool_use", "id": "t2", "name": "Edit",
                      "input": {"file_path": "C:/p/parser.py", "old_string": "a", "new_string": "b"}}, U(20, 10, 0, 3000), 16),
    user([{"type": "tool_result", "tool_use_id": "t2", "content": [{"type": "text", "text": "ok"}]}], 17),
    asst("m4", "r4", {"type": "tool_use", "id": "t3", "name": "Bash", "input": {"command": "cd x && git add -A && git commit -m fix"}}, U(5, 5), 18),
    user([{"type": "tool_result", "tool_use_id": "t3", "content": "[master abc123] fix"}], 19),
    asst("s1", "rs", {"type": "text", "text": "subagent chatter"}, U(999, 999), 19, side=True),
    "not json at all",
    asst("m5", "r5", {"type": "text", "text": "Fixed and committed. Anything else?"}, U(30, 20, 0, 3000), 20, model="claude-sonnet-5"),
]
r = ns["_replay_steps"](lines)
st, sm = r["steps"], r["summary"]
kinds = [(s["kind"], s.get("tool") or s["text"][:20]) for s in st]
c.check("order and kinds", kinds == [("command", "/login"), ("prompt", "Fix the parser pleas"), ("text", "Looking at it."),
                                    ("tool", "Bash"), ("text", "(thinking)"), ("tool", "Edit"), ("tool", "Bash"),
                                    ("text", "Fixed and committed.")])
c.check("meta line and sidechain skipped, bad JSON ignored", not any("Caveat" in s["text"] or "subagent" in s["text"] for s in st))
c1 = round(calc(100, 50, 1000, 0, "claude-opus-5-5"), 5)
c.check("call m1's cost once, on its first visible step (text, not thinking)", st[2].get("cost") == c1 and "cost" not in st[3])
c.check("tool result matched: error + output + seconds", st[3]["err"] is True and st[3]["result"] == "2 failed" and st[3]["secs"] == 12.0)
c.check("thinking-only call kept as its own step with its cost",
        st[4]["text"] == "(thinking)" and st[4]["cost"] == round(calc(10, 5, 0, 2000, "claude-opus-5-5"), 5))
c.check("Edit summary + diff detail, list result -> ok", st[5]["text"] == "Edit: C:/p/parser.py" and st[5]["detail"] == "- a\n+ b"
        and st[5]["err"] is False and st[5]["result"] == "ok")
c.check("git commit flagged", st[6].get("commit") is True)
c.check("model + tokens on the costed step", st[7]["model"] == "claude-sonnet-5" and st[7]["tokens"] == 3050)
c.check("summary counts", (sm["calls"], sm["prompts"], sm["tools"], sm["errors"], sm["commits"]) == (5, 1, 3, 1, 1))
expected_total = sum(calc(*a) for a in [(100, 50, 1000, 0, "claude-opus-5-5"), (10, 5, 0, 2000, "claude-opus-5-5"),
                                        (20, 10, 0, 3000, "claude-opus-5-5"), (5, 5, 0, 0, "claude-opus-5-5"),
                                        (30, 20, 0, 3000, "claude-sonnet-5")])
c.check("total = each call once (sidechain excluded)", abs(sm["cost"] - round(expected_total, 4)) < 1e-9)
c.check("start/end from timestamps", sm["end"] - sm["start"] == 20)
c.check("top = most expensive steps first", sm["top"][0] == max((s for s in st if s.get("cost")), key=lambda s: s["cost"])["i"])
c.check("indices are positions", [s["i"] for s in st] == list(range(len(st))))

many = [user(f"prompt {i}", 1) for i in range(30)]
r = ns["_replay_steps"](many, max_steps=10)
c.check("truncated keeps the last steps", r["truncated"] and len(r["steps"]) == 10 and r["steps"][0]["text"] == "prompt 20"
        and r["steps"][0]["i"] == 0)
c.check("long text capped", len(ns["_replay_steps"]([user("x" * 5000, 1)])["steps"][0]["text"]) == 1200)
c.check("bad timestamp -> None", ns["_replay_epoch"]("yesterday") is None)

# ── finding the file + cache ──
home = tempfile.mkdtemp(prefix="aoc_rp_")
os.environ["USERPROFILE"] = home
os.environ["HOME"] = home
proj = os.path.join(home, ".claude", "projects", "C--proj")
os.makedirs(proj)
sid = "0b1c2d3e-aaaa-bbbb-cccc-0123456789ab"
with open(os.path.join(proj, sid + ".jsonl"), "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
c.check("bad ids never touch the disk", ns["_find_transcript"]("../../etc/passwd") == "" and ns["_find_transcript"]("a*") == "")
c.check("past session found by glob", ns["_find_transcript"](sid).endswith(sid + ".jsonl"))
r1 = ns["_session_replay"](sid)
c.check("replay ok", r1["ok"] and len(r1["steps"]) == 8)
c.check("same file -> cached object", ns["_session_replay"](sid) is r1)
with open(os.path.join(proj, sid + ".jsonl"), "a", encoding="utf-8") as f:
    f.write("\n" + user("one more", 30))
c.check("file grew -> re-read", len(ns["_session_replay"](sid)["steps"]) == 9)
c.check("unknown session -> error", ns["_session_replay"]("ffffffff-0000")["ok"] is False)
shutil.rmtree(home, ignore_errors=True)
c.finish()
