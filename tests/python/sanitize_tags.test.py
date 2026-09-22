"""Tests _sanitize_tags and _tags_list, extracted straight from
monitor.py. _sanitize_tags is the write-side guard for the /history_tags
endpoint (same defensive shape _sanitize_project_budgets already uses for
structured user input); _tags_list is its read-side inverse, turning the
comma-separated DB column back into a list."""
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ns = exec_functions(["_sanitize_tags", "_tags_list"])
_sanitize_tags = ns["_sanitize_tags"]
_tags_list = ns["_tags_list"]

c = Checker()

# ── _sanitize_tags ──
c.check("non-list input returns empty list", _sanitize_tags("not a list") == [])
c.check("non-list input (dict) returns empty list", _sanitize_tags({"a": 1}) == [])
c.check("plain valid list passes through, order preserved", _sanitize_tags(["refactor", "bugfix"]) == ["refactor", "bugfix"])
c.check("blank/whitespace-only entries dropped", _sanitize_tags(["refactor", "  ", ""]) == ["refactor"])
c.check("entries trimmed of surrounding whitespace", _sanitize_tags(["  refactor  "]) == ["refactor"])
c.check("case-insensitive dedup, first-seen casing kept", _sanitize_tags(["Refactor", "refactor", "REFACTOR"]) == ["Refactor"])
c.check("commas stripped out (the column's own separator)", _sanitize_tags(["a,b,c"]) == ["abc"])
c.check("each tag capped at 30 chars", len(_sanitize_tags(["x" * 50])[0]) == 30)
c.check("non-string entries coerced via str()", _sanitize_tags([123, True]) == ["123", "True"])
c.check("total tag count capped at 10", len(_sanitize_tags([f"tag{i}" for i in range(20)])) == 10)
c.check("cap keeps the first 10, not an arbitrary subset", _sanitize_tags([f"tag{i}" for i in range(20)])[0] == "tag0")
c.check("empty list stays empty", _sanitize_tags([]) == [])

# ── _tags_list ──
c.check("comma-separated string splits into a list", _tags_list("refactor,bugfix") == ["refactor", "bugfix"])
c.check("single tag, no comma", _tags_list("refactor") == ["refactor"])
c.check("empty string -> empty list", _tags_list("") == [])
c.check("None -> empty list, not a crash", _tags_list(None) == [])

# ── round-trip: sanitize then list back out matches (module already
# guarantees no tag contains a comma, so join/split can't corrupt data) ──
tags = _sanitize_tags(["Refactor", "bugfix", "  experiment  "])
c.check("sanitize -> join -> _tags_list round-trips cleanly",
        _tags_list(",".join(tags)) == tags)

c.finish()
