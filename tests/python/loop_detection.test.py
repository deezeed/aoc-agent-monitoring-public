"""Loop detection for CLI sessions (monitor.py _loop_tool_key / _loop_track /
_loop_signal), fed with real transcript-shaped lines: failure streaks, the
same call failing again and again, the edit -> run -> fail cycle on one
file, and what counts as progress (your message, a successful commit)."""
import sys, os, json

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
ns = exec_functions(["_LOOP_RING_MAX", "_LOOP_ERROR_STREAK", "_LOOP_SAME_FAIL", "_LOOP_FILE_EDITS",
                     "_LOOP_FILE_ERRORS", "_LOOP_EDIT_TOOLS", "_loop_tool_key", "_loop_track", "_loop_signal"],
                    {"json": json})
track, signal, key = ns["_loop_track"], ns["_loop_signal"], ns["_loop_tool_key"]
n = [0]


def call(stats, name, inp, err, sidechain=False):
    """One tool_use line + its tool_result line, like Claude Code writes them."""
    n[0] += 1
    tid = f"toolu_{n[0]}"
    use = {"type": "assistant", "message": {"content": [{"type": "text", "text": "…"},
                                                        {"type": "tool_use", "id": tid, "name": name, "input": inp}]}}
    res = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "is_error": err,
                                                    "content": "boom" if err else "ok"}]}}
    if sidechain:
        use["isSidechain"] = res["isSidechain"] = True
    track(stats, use)
    track(stats, res)


def human(stats, text="try again", meta=False):
    track(stats, {"type": "user", "isMeta": meta, "message": {"role": "user", "content": text}})


# keys
c.check("Bash key = command, whitespace folded", key("Bash", {"command": "npm   test\n"}) == ("Bash: npm test", ""))
c.check("Edit key carries the file", key("Edit", {"file_path": "C:/p/a.py", "old_string": "x"}) == ("Edit: C:/p/a.py", "C:/p/a.py"))
c.check("Read is not an edit", key("Read", {"file_path": "C:/p/a.py"})[1] == "")
c.check("other tools: sorted args", key("Grep", {"b": 1, "a": 2})[0] == 'Grep: {"a": 2, "b": 1}')
c.check("garbage input", key(None, "x") == ("?: {}", ""))

# healthy work
st = {}
for i in range(30):
    call(st, "Bash", {"command": f"step {i}"}, err=(i % 4 == 0))
c.check("mixed, mostly fine -> no signal", signal(st["loop_ring"]) is None)
c.check("ring capped", len(st["loop_ring"]) == 40 or len(st["loop_ring"]) == 30)
for i in range(50):
    call(st, "Read", {"file_path": f"f{i}"}, err=False)
c.check("ring never grows past 40", len(st["loop_ring"]) == 40)

# failure streak
st = {}
for cmd in ["a", "b", "c", "d"]:
    call(st, "Bash", {"command": cmd}, err=True)
c.check("4 failures in a row -> not yet", signal(st["loop_ring"]) is None)
call(st, "Bash", {"command": "e"}, err=True)
s = signal(st["loop_ring"])
c.check("5 failures in a row -> errors", s and s["kind"] == "errors" and s["count"] == 5 and "Bash: e" in s["text"])
call(st, "Bash", {"command": "f"}, err=False)
c.check("a success breaks the streak", signal(st["loop_ring"]) is None)

# same call failing
st = {}
for i in range(3):
    call(st, "Bash", {"command": "npm test"}, err=True)
    call(st, "Read", {"file_path": f"x{i}"}, err=False)
s = signal(st["loop_ring"])
c.check("same command failing 3x -> same_fail", s and s["kind"] == "same_fail" and s["count"] == 3 and "npm test" in s["text"])

# edit -> run -> fail on one file
st = {}
for i in range(6):
    call(st, "Edit", {"file_path": "C:\\proj\\src\\app.py", "old_string": str(i), "new_string": str(i + 1)}, err=False)
    call(st, "Bash", {"command": f"pytest -k t{i}"}, err=(i < 4))
s = signal(st["loop_ring"])
c.check("6 edits + 4 failures -> edit_loop", s and s["kind"] == "edit_loop" and s["text"].startswith("app.py edited 6×"))
st2 = {}
for i in range(8):
    call(st2, "Edit", {"file_path": "C:/proj/big.py"}, err=False)
call(st2, "Bash", {"command": "pytest"}, err=True)
c.check("many edits but tests mostly pass -> no signal", signal(st2["loop_ring"]) is None)

# progress resets
st = {}
for i in range(5):
    call(st, "Bash", {"command": "make"}, err=True)
human(st, "<system-reminder>x</system-reminder>", meta=True)
c.check("meta user line is not you", signal(st["loop_ring"]) is not None)
human(st)
c.check("your message clears it", signal(st["loop_ring"]) is None and st["loop_ring"] == [])
for i in range(5):
    call(st, "Bash", {"command": "make"}, err=True)
call(st, "Bash", {"command": 'git commit -m "wip"'}, err=False)
c.check("successful commit clears it", st["loop_ring"] == [] and signal(st["loop_ring"]) is None)
for i in range(5):
    call(st, "Bash", {"command": "make"}, err=True)
call(st, "Bash", {"command": "git commit -m x"}, err=True)
c.check("failed commit is no progress", signal(st["loop_ring"])["kind"] == "errors")

# subagents and pending calls
st = {}
for i in range(6):
    call(st, "Bash", {"command": "x"}, err=True, sidechain=True)
c.check("subagent (sidechain) calls ignored", signal(st.get("loop_ring")) is None)
st = {}
track(st, {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "p1", "name": "Bash", "input": {"command": "sleep 99"}}]}})
c.check("call without a result yet doesn't count", signal(st["loop_ring"]) is None)
track(st, {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "a1", "name": "Agent", "input": {}}]}})
c.check("Agent calls not tracked", all(e["id"] != "a1" for e in st["loop_ring"]))
c.check("empty / None ring", signal([]) is None and signal(None) is None)
track(st, "garbage")
track(st, {"type": "user", "message": {"content": None}})
c.check("odd lines don't crash", True)

c.finish()
