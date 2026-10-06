"""Tests _assistant_text, extracted straight from monitor.py: the visible
text of a main-thread assistant transcript line, which the scanner keeps as
the session's last_message (shown on sessions waiting on you)."""
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
f = exec_functions(["_assistant_text"])["_assistant_text"]
A = lambda blocks, **kw: dict({"type": "assistant", "message": {"content": blocks}}, **kw)

c.check("text blocks joined", f(A([{"type": "text", "text": " Done. "}, {"type": "tool_use"},
                                   {"type": "text", "text": "Shall I push?"}])) == "Done.\n\nShall I push?")
c.check("no text (tool call / thinking only) -> ''", f(A([{"type": "tool_use"}, {"type": "thinking", "thinking": "x"}])) == "")
c.check("subagent lines ignored", f(A([{"type": "text", "text": "sub"}], isSidechain=True)) == "")
c.check("user lines ignored", f({"type": "user", "message": {"content": "hi"}}) == "")
c.check("string content / junk -> ''", f(A("plain")) == "" and f(None) == "" and f("x") == "")
long = f(A([{"type": "text", "text": "a" * 3000 + "END?"}]))
c.check("long text keeps the end", len(long) == 1501 and long.startswith("…") and long.endswith("END?"))

c.finish()
