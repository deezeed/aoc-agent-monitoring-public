"""Tests _extract_files_changed, extracted straight from hooks/aoc_hook.py
(not monitor.py -- lib.extract's extract_functions() accepts an explicit
`src` override for exactly this case). The Agent tool's own PostToolUse
payload only carries aggregate stats (toolStats.editFileCount/linesAdded/
linesRemoved), never per-file paths -- confirmed against a live captured
payload, 2026-07-27. Claude Code writes each subagent's own transcript
separately at <parent_transcript_dir>/subagents/agent-<agentId>.jsonl
(also confirmed live), keyed by Claude Code's own agentId -- NOT AOC's
internal ag_<hash> id, a real bug caught during live testing (the first
implementation attempt used the wrong id and always found nothing)."""
import sys, os, json, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import extract_functions
from lib.check import Checker

HOOK_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "hooks", "aoc_hook.py"))
with open(HOOK_PATH, encoding="utf-8") as f:
    HOOK_SRC = f.read()

# _parse_subagent_tool_uses is the shared single-pass file reader
# _extract_files_changed now delegates to (see aoc_hook.py -- it also
# backs _extract_child_agent_ids, so main() can parse a subagent's
# transcript once and hand the same block list to both instead of
# reading the file twice on the hook's blocking critical path).
src_map = extract_functions(["_extract_files_changed", "_parse_subagent_tool_uses"], src=HOOK_SRC)
ns = {"os": os, "json": json}
exec(src_map["_parse_subagent_tool_uses"], ns)
exec(src_map["_extract_files_changed"], ns)
_extract_files_changed = ns["_extract_files_changed"]

c = Checker()

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_files_changed_")


def write_transcript(agent_id, lines):
    sub_dir = os.path.join(SCRATCH, "session", "subagents")
    os.makedirs(sub_dir, exist_ok=True)
    path = os.path.join(sub_dir, f"agent-{agent_id}.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return os.path.join(SCRATCH, "session.jsonl")  # the parent transcript_path


def tool_use(name, input_):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": input_}]}}


try:
    # 1. no transcript_path / agent_id -> empty, no crash
    c.check("empty transcript_path -> []", _extract_files_changed("", "a1") == [])
    c.check("empty agent_id -> []", _extract_files_changed(r"C:\x\session.jsonl", "") == [])
    c.check("non-.jsonl transcript_path -> []", _extract_files_changed(r"C:\x\session.txt", "a1") == [])

    # 2. subagent transcript file doesn't exist -> empty, no crash
    c.check("missing subagent file -> []", _extract_files_changed(os.path.join(SCRATCH, "nope.jsonl"), "ghost") == [])

    # 3. Write tool_use -> type "new", lines counted from content
    tp = write_transcript("agentA", [
        {"type": "user", "message": {}},
        tool_use("Write", {"file_path": r"C:\proj\new.txt", "content": "line1\nline2\nline3"}),
    ])
    result = _extract_files_changed(tp, "agentA")
    c.check("Write produces one file entry", len(result) == 1)
    c.check("Write entry path is normalized to forward slashes (AOC's UI does "
            "path.split('/').pop() for the filename in several places -- a raw "
            "Windows path never splits on that)", result[0]["path"] == "C:/proj/new.txt")
    c.check("Write entry type is 'new'", result[0]["type"] == "new")
    c.check("Write entry line count matches content", result[0]["lines"] == 3)

    # 4. Edit tool_use -> type "changed"
    tp = write_transcript("agentB", [
        tool_use("Edit", {"file_path": r"C:\proj\existing.txt", "old_string": "a\nb", "new_string": "a\nb\nc\nd"}),
    ])
    result = _extract_files_changed(tp, "agentB")
    c.check("Edit produces one file entry", len(result) == 1)
    c.check("Edit entry type is 'changed'", result[0]["type"] == "changed")
    c.check("Edit entry lines uses the larger side's line count", result[0]["lines"] == 4)

    # 5. Same path edited twice -> deduped to one entry (last write wins)
    tp = write_transcript("agentC", [
        tool_use("Write", {"file_path": r"C:\proj\dupe.txt", "content": "v1"}),
        tool_use("Edit", {"file_path": r"C:\proj\dupe.txt", "old_string": "v1", "new_string": "v2\nv3"}),
    ])
    result = _extract_files_changed(tp, "agentC")
    c.check("same path touched twice dedupes to one entry", len(result) == 1)
    c.check("dedup keeps the later (Edit) entry's type", result[0]["type"] == "changed")

    # 6. MultiEdit -> type "changed", lines summed across edits
    tp = write_transcript("agentD", [
        tool_use("MultiEdit", {"file_path": r"C:\proj\multi.txt", "edits": [
            {"old_string": "a", "new_string": "a\nb"},
            {"old_string": "x\ny\nz", "new_string": "x"},
        ]}),
    ])
    result = _extract_files_changed(tp, "agentD")
    c.check("MultiEdit produces one file entry", len(result) == 1)
    c.check("MultiEdit entry type is 'changed'", result[0]["type"] == "changed")
    c.check("MultiEdit sums lines across all edits", result[0]["lines"] == 2 + 3)

    # 7. Non-file tools (Read, Bash) are ignored entirely
    tp = write_transcript("agentE", [
        tool_use("Read", {"file_path": r"C:\proj\readonly.txt"}),
        tool_use("Bash", {"command": "echo hi"}),
    ])
    c.check("Read/Bash tool_use blocks produce no entries", _extract_files_changed(tp, "agentE") == [])

    # 8. malformed JSON lines are skipped, not fatal
    sub_dir = os.path.join(SCRATCH, "session", "subagents")
    bad_path = os.path.join(sub_dir, "agent-agentF.jsonl")
    with open(bad_path, "w", encoding="utf-8") as f:
        f.write("not json at all\n")
        f.write(json.dumps(tool_use("Write", {"file_path": r"C:\proj\ok.txt", "content": "x"})) + "\n")
    c.check("malformed line skipped, valid line still parsed",
            len(_extract_files_changed(os.path.join(SCRATCH, "session.jsonl"), "agentF")) == 1)

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
