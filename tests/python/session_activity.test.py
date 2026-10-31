"""Per-minute session activity (monitor.py) for the Timeline / Heat views when
no agent ran: _activity_track counts transcript lines per minute (sidechain
lines and non-message lines skipped, capped to _ACTIVITY_MINUTES_MAX), and
_sessions_activity returns them since a cut-off with each session's edited
files."""
import sys, os, threading

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
ns = exec_functions(["_SESSION_EDITED_MAX", "_ACTIVITY_MINUTES_MAX", "_activity_track", "_sessions_activity"],
                    {"_human_text": lambda o: o.get("human") if isinstance(o, dict) else None,
                     "_iso_to_epoch": lambda s: float(s) if s else None,
                     "_loop_tool_key": lambda name, inp: (name, (inp or {}).get("file_path")),
                     "_is_commit_call": lambda name, inp: False,
                     "_transcript_cursors": {}, "_transcript_cursors_lock": threading.Lock()})
track = ns["_activity_track"]
stats = {}
line = lambda ts, **kw: {"type": "assistant", "timestamp": str(ts), "message": {"content": []}, **kw}
for ts in (600, 610, 659, 660):
    track(stats, line(ts), 0)
track(stats, line(700, isSidechain=True), 0)
track(stats, {"type": "summary", "timestamp": "720"}, 0)
track(stats, {"type": "user", "timestamp": "725", "human": "hi", "message": {"content": "hi"}}, 0)
track(stats, line(730, message={"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "C:/x/a.py"}}]}), 0)
m = stats["activity"]["minutes"]
c.check("lines counted per minute; sidechain and non-message lines skipped", m == {600: 3, 660: 1, 720: 2})
c.check("your message keeps the minutes (only the 'since' counters reset)", "edited" in stats["activity"] and stats["activity"]["edited"] == ["C:/x/a.py"])

ns["_ACTIVITY_MINUTES_MAX"] = 3
track.__globals__["_ACTIVITY_MINUTES_MAX"] = 3
for ts in (1200, 1260, 1320):
    track(stats, line(ts), 0)
c.check("capped to the newest minutes", sorted(stats["activity"]["minutes"]) == [1200, 1260, 1320])

cur = ns["_transcript_cursors"]
cur["s1"] = {"stats": stats}
cur["s2"] = {"stats": {"activity": {"minutes": {60: 2}}}}
cur["s3"] = {"stats": {}}
out = ns["_sessions_activity"](1250)
c.check("since cut-off, oldest first, edited files, idle sessions left out",
        out == {"s1": {"minutes": [[1260, 1], [1320, 1]], "edited": ["C:/x/a.py"]}})
c.finish()
