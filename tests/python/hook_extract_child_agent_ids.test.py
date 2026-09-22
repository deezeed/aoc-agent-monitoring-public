"""Tests _extract_child_agent_ids, extracted straight from hooks/aoc_hook.py
(same lib.extract src= pattern as hook_extract_files_changed.test.py --
this function reads the exact same subagent transcript file, just looking
for nested Task/Agent tool_use blocks instead of Write/Edit/MultiEdit
ones). TREE view has always needed parent_id to draw any hierarchy, but
nothing ever populated it -- confirmed live (2026-07-29) that hashing a
real nested tool_use id from an actual subagent transcript on this
machine reproduced an agent id already present in history.db under the
matching description text, proving Claude Code fires that child's own
independent hook chain the same way it does for a top-level session."""
import sys, os, json, tempfile, shutil, hashlib

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import extract_functions
from lib.check import Checker

HOOK_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "hooks", "aoc_hook.py"))
with open(HOOK_PATH, encoding="utf-8") as f:
    HOOK_SRC = f.read()

# _parse_subagent_tool_uses is the shared single-pass file reader
# _extract_child_agent_ids now delegates to (see aoc_hook.py -- it also
# backs _extract_files_changed, so main() can parse a subagent's
# transcript once and hand the same block list to both instead of
# reading the file twice on the hook's blocking critical path).
src_map = extract_functions(["_extract_child_agent_ids", "agent_id_from_hook", "_parse_subagent_tool_uses"], src=HOOK_SRC)
ns = {"os": os, "json": json, "hashlib": hashlib}
exec(src_map["agent_id_from_hook"], ns)
exec(src_map["_parse_subagent_tool_uses"], ns)
exec(src_map["_extract_child_agent_ids"], ns)
_extract_child_agent_ids = ns["_extract_child_agent_ids"]
agent_id_from_hook = ns["agent_id_from_hook"]

c = Checker()

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_child_agent_ids_")


def write_transcript(agent_id, lines):
    sub_dir = os.path.join(SCRATCH, "session", "subagents")
    os.makedirs(sub_dir, exist_ok=True)
    path = os.path.join(sub_dir, f"agent-{agent_id}.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return os.path.join(SCRATCH, "session.jsonl")  # the parent transcript_path


def tool_use(name, tool_use_id, input_=None):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tool_use_id, "name": name, "input": input_ or {}}
    ]}}


try:
    # 1. no transcript_path / agent_id -> empty, no crash
    c.check("empty transcript_path -> []", _extract_child_agent_ids("", "a1") == [])
    c.check("empty agent_id -> []", _extract_child_agent_ids(r"C:\x\session.jsonl", "") == [])
    c.check("non-.jsonl transcript_path -> []", _extract_child_agent_ids(r"C:\x\session.txt", "a1") == [])

    # 2. subagent transcript file doesn't exist -> empty, no crash
    c.check("missing subagent file -> []", _extract_child_agent_ids(os.path.join(SCRATCH, "nope.jsonl"), "ghost") == [])

    # 3. a nested "Agent" tool_use -> one child id, matching agent_id_from_hook's
    # own hash of that same tool_use_id (the actual join-key mechanism this
    # feature relies on)
    tp = write_transcript("agentA", [
        {"type": "user", "message": {}},
        tool_use("Agent", "toolu_child_one", {"description": "Grep for X"}),
    ])
    result = _extract_child_agent_ids(tp, "agentA")
    expected = agent_id_from_hook({"tool_use_id": "toolu_child_one"}, "")
    c.check("nested Agent tool_use produces one child id", len(result) == 1)
    c.check("child id matches agent_id_from_hook's own hash of the tool_use_id",
            result[0] == expected)

    # 4. a nested "Task" tool_use (the other name Claude Code may use) is
    # also recognized
    tp = write_transcript("agentB", [
        tool_use("Task", "toolu_child_two"),
    ])
    result = _extract_child_agent_ids(tp, "agentB")
    c.check("nested Task tool_use is recognized the same way as Agent",
            result == [agent_id_from_hook({"tool_use_id": "toolu_child_two"}, "")])

    # 5. multiple nested spawns in one transcript -> multiple child ids, in order
    tp = write_transcript("agentC", [
        tool_use("Agent", "toolu_c1"),
        tool_use("Read", "toolu_read", {"file_path": r"C:\x\f.txt"}),  # not a spawn, ignored
        tool_use("Task", "toolu_c2"),
    ])
    result = _extract_child_agent_ids(tp, "agentC")
    c.check("multiple nested spawns all captured, non-spawn tool_use ignored",
            result == [agent_id_from_hook({"tool_use_id": "toolu_c1"}, ""),
                       agent_id_from_hook({"tool_use_id": "toolu_c2"}, "")])

    # 6. no nested spawns at all (an ordinary subagent that never delegated
    # further) -> empty list, not an error
    tp = write_transcript("agentD", [
        tool_use("Read", "toolu_x", {"file_path": r"C:\x\f.txt"}),
        tool_use("Bash", "toolu_y", {"command": "echo hi"}),
    ])
    c.check("subagent with no nested spawns -> []", _extract_child_agent_ids(tp, "agentD") == [])

    # 7. malformed JSON lines are skipped, not fatal
    sub_dir = os.path.join(SCRATCH, "session", "subagents")
    bad_path = os.path.join(sub_dir, "agent-agentE.jsonl")
    with open(bad_path, "w", encoding="utf-8") as f:
        f.write("not json at all\n")
        f.write(json.dumps(tool_use("Agent", "toolu_ok")) + "\n")
    c.check("malformed line skipped, valid nested spawn still parsed",
            _extract_child_agent_ids(os.path.join(SCRATCH, "session.jsonl"), "agentE") ==
            [agent_id_from_hook({"tool_use_id": "toolu_ok"}, "")])

    # 8. a tool_use block missing its own "id" is skipped rather than
    # producing a garbage child id (agent_id_from_hook would otherwise fall
    # back to hashing the empty description)
    tp = write_transcript("agentF", [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Agent", "input": {}}]}},
    ])
    c.check("tool_use block with no id produces no child id", _extract_child_agent_ids(tp, "agentF") == [])

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
