"""Tests _set_session_note, extracted straight from monitor.py. Covers the
per-session note feature (distinct from the single global session_note
scratchpad, which the /notes endpoint already owned before this)."""
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ns = exec_functions(["_set_session_note"])
_set_session_note = ns["_set_session_note"]

c = Checker()

# 1. writing a note to a session that exists succeeds and is stored under "note"
status = {"sessions": {"s1": {"project": "AOC"}}}
ok = _set_session_note(status, "s1", "waiting on PR #42 review")
c.check("returns True for a known session_id", ok is True)
c.check("note stored under sessions[id]['note']", status["sessions"]["s1"]["note"] == "waiting on PR #42 review")
c.check("other fields on the session are untouched", status["sessions"]["s1"]["project"] == "AOC")

# 2. unknown session_id is rejected, no mutation happens
status2 = {"sessions": {"s1": {}}}
ok2 = _set_session_note(status2, "does-not-exist", "some note")
c.check("returns False for an unknown session_id", ok2 is False)
c.check("no new session gets created as a side effect", "does-not-exist" not in status2["sessions"])

# 3. note is capped at 2000 chars, same as the global /notes endpoint
status3 = {"sessions": {"s1": {}}}
long_note = "x" * 3000
_set_session_note(status3, "s1", long_note)
c.check("note is capped at 2000 chars", len(status3["sessions"]["s1"]["note"]) == 2000)

# 4. overwriting an existing note replaces it (not appended)
status4 = {"sessions": {"s1": {"note": "old note"}}}
_set_session_note(status4, "s1", "new note")
c.check("overwriting replaces the previous note", status4["sessions"]["s1"]["note"] == "new note")

# 5. missing "sessions" key doesn't crash (setdefault handles it)
status5 = {}
ok5 = _set_session_note(status5, "s1", "note")
c.check("missing sessions dict handled gracefully, still returns False for unknown id", ok5 is False)

c.finish()
