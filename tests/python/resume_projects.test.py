"""Today -> "Pick up where you left off" (monitor.py): _resume_match (your
"continue with X" opening, typos and diacritics included), the project list
from a MEMORY.md index, _resume_cards (one card per project, your usual
verb, folder cards only when memory knows few projects, temp folders
skipped) and the --resume path of _launch_validate / _launch_argv /
_launch_session."""
import sys, os, re, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
ns = exec_functions(["_RESUME_DAYS", "_RESUME_MAX_CARDS", "_RESUME_VERB", "_resume_norm", "_resume_match",
                     "_resume_projects_from_index", "_resume_cards"], {"os": os, "re": re})
m = ns["_resume_match"]
names = ["AOC", "Phantom AI", "OmniSocial", "Memebot", "Jarvis"]
c.check("plain", m("pokračuj v AOC", names) == ("AOC", True))
c.check("typo in verb", m("pkračuj v omnisocial", names) == ("OmniSocial", True))
c.check("typo in verb + 's'", m("pokračuejem s omnisocial", names) == ("OmniSocial", True))
c.check("inflected name", m("pokračuj v memebotovi", names) == ("Memebot", True))
c.check("two-word name, extra text", m("Pokračujeme v Phantom AI, minule sme skončili pri X", names) == ("Phantom AI", True))
c.check("english", m("continue with jarvis please", names) == ("Jarvis", True))
c.check("question, no project", m("v čom vieme pokračovať", names) == (None, False))
c.check("verb with an unknown thing", m("pokračuj v niečom inom", names) == (None, False))
c.check("other message naming a project", m("oprav bug v Memebot API", names) == ("Memebot", False))
c.check("name only as part of a word -> no", m("aocx je iný projekt", names) == (None, False))
c.check("empty", m("", names) == (None, False) and m(None, []) == (None, False))

idx = """# Memory Index

- [User Profile](user_profile.md) — who
- [AOC](project_aoc.md) — Agent Operations Center dashboard
- [Memebot](sub/project_memebot.md) - Solana trader
- [Monitor Protocol](feedback_monitor_protocol.md) — not a project
"""
c.check("index: project entries only", ns["_resume_projects_from_index"](idx) ==
        [("AOC", "project_aoc.md", "Agent Operations Center dashboard"), ("Memebot", "sub/project_memebot.md", "Solana trader")])
c.check("index: empty", ns["_resume_projects_from_index"]("") == [])

home = r"C:\Users\m"
projects = [("AOC", "project_aoc.md", "dashboard"), ("Memebot", "project_memebot.md", "trader"),
            ("OmniSocial", "project_omnisocial.md", "social")]
S = lambda i, first, cwd=home, cost=1.0, last=0, title="": {"id": i, "first": first, "cwd": cwd, "cost": cost, "last": last, "title": title}
sessions = [S("s1", "pokračuj v AOC", last=500, cost=2.5, title="AOC round"),
            S("s2", "pokračuj v memebote", last=400),
            S("s3", "pkračuj v aoc", last=300, cost=1.25),
            S("s4", "something else entirely", cwd=r"C:\p\other", last=200),
            S("s5", "Pokračuj v OmniSocial", r"C:\p\omni", last=100)]
cards = ns["_resume_cards"](projects, sessions, home, 1000, open_ids=["s3"])
by = {x["name"]: x for x in cards}
c.check("cards: one per project, newest first", [x["name"] for x in cards] == ["AOC", "Memebot", "OmniSocial"])
c.check("cards: prompt = your verb, name spelled right", by["AOC"]["prompt"] == "pokračuj v AOC" and by["Memebot"]["prompt"] == "pokračuj v Memebot")
c.check("cards: count + cost + last", by["AOC"]["sessions"] == 2 and by["AOC"]["cost"] == 3.75 and by["AOC"]["last"]["title"] == "AOC round")
c.check("cards: resume id = last session, open ids", by["AOC"]["resume_id"] == "s1" and by["AOC"]["open"] == ["s3"] and by["Memebot"]["open"] == [])
c.check("cards: cwd from last session", by["OmniSocial"]["cwd"] == r"C:\p\omni" and by["AOC"]["hook"] == "dashboard")
c.check("cards: >= 3 projects -> no folder cards", not any(x["folder"] for x in cards))

few = ns["_resume_cards"]([], [S("a", "fix the build", r"C:\p\api", last=50), S("b", "x", home, last=60),
                               S("c", "y", r"C:\Users\m\AppData\Local\Temp\claude\scratchpad", last=70),
                               S("d", "add tests", r"C:\p\api", last=40)], home, 1000)
c.check("folder cards: home + temp skipped, one per folder", [(x["name"], x["folder"]) for x in few] == [("api", True)])
c.check("folder card: repeats that folder's last opening", few[0]["prompt"] == "fix the build" and few[0]["sessions"] == 2)
c.check("no sessions -> english default verb", ns["_resume_cards"](projects, [], home, 1000) == [])
eng = ns["_resume_cards"](projects, [S("e", "continue with memebot")], home, 1000)
c.check("english verb -> 'with'", eng[0]["prompt"] == "continue with Memebot")
many = [S(f"x{i}", f"pokračuj v P{i:02d}", last=i) for i in range(12)]
c.check("cards capped", len(ns["_resume_cards"]([(f"P{i:02d}", "f", "") for i in range(12)], many, home, 1000)) == ns["_RESUME_MAX_CARDS"])

# --resume through the launcher
tmp = tempfile.mkdtemp(prefix="aoc_rs_")
ln = exec_functions(["_LAUNCH_MODELS", "_LAUNCH_MIN_GAP_S", "_launch_last", "_launch_validate", "_launch_argv",
                     "_folder_trusted", "_load_json_file", "_launch_session"],
                    {"os": os, "re": re, "time": __import__("time"), "subprocess": None, "_find_claude_exe": lambda: "claude.exe"})
sid = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
args, err = ln["_launch_validate"]({"cwd": tmp, "resume": f" {sid} "})
c.check("resume id accepted", err is None and args["resume"] == sid)
c.check("bad resume id refused", ln["_launch_validate"]({"cwd": tmp, "resume": "x; del *"})[1] == "bad session id")
c.check("no resume key when absent", "resume" not in ln["_launch_validate"]({"cwd": tmp})[0])
c.check("argv: --resume first", ln["_launch_argv"]("c", {"resume": sid, "model": "opus", "prompt": ""}) == ["c", "--resume", sid, "--model", "opus"])
calls = []


class P:
    pid = 7


r = ln["_launch_session"]({"cwd": tmp, "resume": sid, "prompt": "ignored"}, now=10,
                          popen=lambda argv, cwd=None, creationflags=0: calls.append(argv) or P())
c.check("resume drops the prompt", r["ok"] and calls == [["claude.exe", "--resume", sid]])
shutil.rmtree(tmp, ignore_errors=True)
c.finish()
