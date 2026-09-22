"""Tests _waiting_on_you_from_line, extracted straight from monitor.py.
This is the transcript-scanner fallback for the "waiting on you" session
state (the primary signal is the Stop/UserPromptSubmit heartbeat hook,
which isn't unit-testable here since it lives in aoc_hook.py and talks to
a live server -- this covers the logic that keeps working even when that
hook silently misses)."""
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ns = exec_functions(["_waiting_on_you_from_line"])
_waiting_on_you_from_line = ns["_waiting_on_you_from_line"]

c = Checker()

# 1. assistant turn ending in a tool call -> still working, not waiting
c.check(
    "assistant + stop_reason=tool_use -> False (still working)",
    _waiting_on_you_from_line("assistant", "tool_use") is False,
)

# 2. assistant turn ending normally -> waiting on the human
c.check(
    "assistant + stop_reason=end_turn -> True (waiting)",
    _waiting_on_you_from_line("assistant", "end_turn") is True,
)

# 3. other real stop reasons also count as "turn is over"
c.check(
    "assistant + stop_reason=max_tokens -> True (waiting)",
    _waiting_on_you_from_line("assistant", "max_tokens") is True,
)
c.check(
    "assistant + stop_reason=stop_sequence -> True (waiting)",
    _waiting_on_you_from_line("assistant", "stop_sequence") is True,
)

# 4. an assistant line with no stop_reason yet (shouldn't happen in practice,
# but defensively must not be treated as a turn boundary either way)
c.check(
    "assistant + stop_reason=None -> None (not a turn boundary, don't touch the flag)",
    _waiting_on_you_from_line("assistant", None) is None,
)

# 5. any user entry -- a real reply or a tool_result -- means not waiting
c.check(
    "user line -> False (not waiting, regardless of stop_reason arg)",
    _waiting_on_you_from_line("user", None) is False,
)

# 6. side-channel metadata lines never touch the flag
for meta_type in ("ai-title", "agent-name", "mode", "permission-mode", "summary"):
    c.check(
        f'"{meta_type}" line -> None (metadata, not a turn boundary)',
        _waiting_on_you_from_line(meta_type, None) is None,
    )

c.finish()
