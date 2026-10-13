"""Prompt library: monitor.py's _sanitize_prompt / _prompts_upsert /
_prompts_delete / _prompts_used / _prompts_list over a scratch prompts.json
-- starters until you save one, same name updates in place, starter ids are
never reused, use counts sort the list, limits."""
import sys, os, json, re, secrets, shutil, tempfile, threading, time

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
data_dir = tempfile.mkdtemp(prefix="aoc_pl_")
ns = exec_functions(
    ["_load_json_file", "_LAUNCH_MODELS", "PROMPTS_FILE", "_PROMPTS_MAX", "_prompts_lock", "_PROMPT_STARTERS",
     "_sanitize_prompt", "_prompts_load", "_prompts_save", "_prompts_upsert", "_prompts_delete", "_prompts_used",
     "_prompts_list"],
    {"os": os, "json": json, "re": re, "time": time, "threading": threading, "_secrets": secrets,
     "AOC_DATA_DIR": os.path.join(data_dir, "AOC")})

lst = ns["_prompts_list"]()
c.check("no file -> the three starters", [p["id"] for p in lst] == ["starter-audit", "starter-tests", "starter-review"])
c.check("starters have {{date}} somewhere and no file yet", any("{{date}}" in p["text"] for p in lst) and not os.path.exists(ns["PROMPTS_FILE"]))

san = ns["_sanitize_prompt"]
c.check("needs a name", san({"text": "x"})[1] == "give the template a name")
c.check("needs text", san({"name": "x", "text": "  "})[1] == "the template is empty")
c.check("too long", "too long" in san({"name": "x", "text": "y" * 20001})[1])
t, _ = san({"name": "  Nightly   audit ", "text": " go ", "model": "GPT", "id": "../evil"}, now=5)
c.check("name squashed, text trimmed, unknown model dropped, unsafe id replaced",
        t["name"] == "Nightly audit" and t["text"] == "go" and t["model"] == "" and re.match(r"^[0-9a-f]{10}$", t["id"]) and t["updated"] == 5)
c.check("starter id never taken over", not san({"name": "x", "text": "y", "id": "starter-tests"})[0]["id"].startswith("starter-"))

r = ns["_prompts_upsert"]({"name": "Review PR {{number}}", "text": "Review PR #{{number}} on {{branch}}", "model": "sonnet", "cwd": "C:/p"})
c.check("add -> saved next to the starters", r["ok"] and len(ns["_prompts_load"]()) == 4)
rid = r["template"]["id"]
r2 = ns["_prompts_upsert"]({"name": "review pr {{number}}", "text": "Review PR #{{number}} carefully"})
c.check("same name (any case) -> updated in place, same id", r2["template"]["id"] == rid and len(ns["_prompts_load"]()) == 4
        and ns["_prompts_load"]()[-1]["text"] == "Review PR #{{number}} carefully")
r3 = ns["_prompts_upsert"]({"name": "Fix failing tests", "text": "my own version"})
c.check("editing a starter -> it gets its own id", r3["ok"] and not r3["template"]["id"].startswith("starter-")
        and [p["text"] for p in ns["_prompts_load"]() if p["name"] == "Fix failing tests"] == ["my own version"])

c.check("use counted", ns["_prompts_used"](rid, now=9) and ns["_prompts_used"](rid))
c.check("most used first", ns["_prompts_list"]()[0]["id"] == rid and ns["_prompts_list"]()[0]["used"] == 2)
c.check("update keeps the use count", ns["_prompts_upsert"]({"id": rid, "name": "Review PR {{number}}", "text": "v3"})["template"]["used"] == 2)
c.check("unknown id -> not counted", ns["_prompts_used"]("nope") is False)
c.check("delete", ns["_prompts_delete"](rid) and all(p["id"] != rid for p in ns["_prompts_load"]()))
c.check("delete unknown -> False", ns["_prompts_delete"]("nope") is False)
ns["_prompts_save"]([])
c.check("emptied library stays empty (no starters back)", ns["_prompts_list"]() == [])
ns["_prompts_save"]([{"id": f"t{i}", "name": f"n{i}", "text": "x"} for i in range(100)])
c.check("full at 100", "full" in ns["_prompts_upsert"]({"name": "one more", "text": "x"})["error"])
with open(ns["PROMPTS_FILE"], "w") as f:
    f.write("{broken")
c.check("broken file -> starters, no crash", len(ns["_prompts_list"]()) == 3)

shutil.rmtree(data_dir, ignore_errors=True)
c.finish()
