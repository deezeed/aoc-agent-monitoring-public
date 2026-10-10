"""Session changes (monitor.py): which files a session edited
(_activity_track "edited"), the git parsers, and _session_changes /
_session_file_diff against a real throwaway git repo with a non-ASCII path:
modified / new / deleted / committed files, a file outside any repo, and
the diff of an untracked file."""
import sys, os, json, subprocess, tempfile, shutil, threading

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
if not shutil.which("git"):
    print("SKIP: needs git")
    c.finish()
ns = exec_functions(["_LOOP_EDIT_TOOLS", "_loop_tool_key", "_GIT_COMMIT_RE", "_is_commit_call", "_SESSION_EDITED_MAX", "_human_text", "_activity_track",
                     "_git_out", "_parse_porcelain_z", "_parse_numstat", "_change_kind", "_git_root_for",
                     "_session_changes", "_session_file_diff"],
                    {"os": os, "json": json, "re": __import__("re"), "subprocess": subprocess, "_NO_WINDOW": 0x08000000 if os.name == "nt" else 0,
                     "_iso_to_epoch": lambda ts: None})

# ── parsers ──
st = ns["_parse_porcelain_z"](" M src/a.py\0?? new file.txt\0R  b.py\0old_b.py\0 D gone.py\0")
c.check("porcelain -z: codes, spaces in names, rename source skipped",
        st == {"src/a.py": " M", "new file.txt": "??", "b.py": "R ", "gone.py": " D"})
c.check("numstat", ns["_parse_numstat"]("3\t1\tsrc/a.py\n-\t-\timg.png\n") == {"src/a.py": (3, 1), "img.png": (None, None)})
k = ns["_change_kind"]
c.check("kinds", [k(x) for x in ("??", " M", "MM", "A ", " D", "R ")] == ["untracked", "modified", "modified", "added", "deleted", "renamed"])

# ── edited list from the transcript ──
stats = {}
for i, (name, path) in enumerate([("Edit", "C:/r/a.py"), ("Read", "C:/r/x.py"), ("Write", "C:/r/b.py"), ("Edit", "C:/r/a.py")]):
    ns["_activity_track"](stats, {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": f"t{i}", "name": name, "input": {"file_path": path}}]}}, 0)
c.check("edited: edit/write only, deduped, most recent last", stats["activity"]["edited"] == ["C:/r/b.py", "C:/r/a.py"])
ns["_activity_track"](stats, {"type": "user", "message": {"content": "next please"}}, 0)
c.check("edited survives your next message", stats["activity"]["edited"] == ["C:/r/b.py", "C:/r/a.py"])

# ── real repo ──
base = tempfile.mkdtemp(prefix="aoc_chg_long_folder_name_")
repo = os.path.join(base, "Počítač repo")
os.makedirs(os.path.join(repo, "src"))
if os.name == "nt":
    # the session may know a path by its short 8.3 name (CI's temp dir is
    # C:\Users\RUNNER~1\...) while git reports the long one
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    if ctypes.windll.kernel32.GetShortPathNameW(repo, buf, 1024):
        repo = buf.value
    print("repo path used:", repo.encode("ascii", "backslashreplace").decode())
git = lambda *a: subprocess.run(["git", "-C", repo, *a], capture_output=True, check=True)
git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
P = lambda *x: os.path.join(repo, *x)
for name, text in [("src/a.py", "one\ntwo\n"), ("keep.py", "same\n"), ("gone.py", "bye\n")]:
    with open(P(*name.split("/")), "w", encoding="utf-8") as f:
        f.write(text)
git("add", "-A"); git("commit", "-qm", "base")
with open(P("src", "a.py"), "w", encoding="utf-8") as f:
    f.write("one\nTWO\nthree\n")
with open(P("nový.txt"), "w", encoding="utf-8") as f:
    f.write("a\nb\n")
os.remove(P("gone.py"))
with open(P("unrelated.py"), "w", encoding="utf-8") as f:
    f.write("not edited by the session\n")
outside = os.path.join(base, "loose.txt")
with open(outside, "w") as f:
    f.write("x")
edited = [P("src", "a.py"), P("nový.txt"), P("gone.py"), P("keep.py"), outside]
r = ns["_session_changes"](edited)
c.check("one repo, named after its folder", len(r["repos"]) == 1 and r["repos"][0]["name"] == "Počítač repo")
files = {f["rel"]: f for f in r["repos"][0]["files"]}
c.check("only the session's changed files (unrelated.py not listed)", sorted(files) == ["gone.py", "nový.txt", "src/a.py"])
c.check("modified with numstat", files["src/a.py"]["kind"] == "modified" and (files["src/a.py"]["ins"], files["src/a.py"]["dels"]) == (2, 1))
c.check("new file counted as untracked with its lines", files["nový.txt"]["kind"] == "untracked" and files["nový.txt"]["ins"] == 2)
c.check("deleted", files["gone.py"]["kind"] == "deleted")
c.check("committed/unchanged + outside counted", r["clean"] == 1 and r["outside"] == 1)
c.check("branch reported", r["repos"][0]["branch"] in ("master", "main"))
c.check("absolute paths kept for the diff call", files["src/a.py"]["path"] == P("src", "a.py"))

d = ns["_session_file_diff"](P("src", "a.py"))
c.check("diff of a modified file", "-two" in d and "+TWO" in d and "+three" in d)
d = ns["_session_file_diff"](P("nový.txt"))
c.check("untracked file shown as all added", d.startswith("--- /dev/null") and "+a\n+b" in d)
c.check("file outside git -> empty", ns["_session_file_diff"](outside) == "")
c.check("nothing edited -> empty", ns["_session_changes"]([]) == {"repos": [], "clean": 0, "outside": 0})

shutil.rmtree(base, ignore_errors=True)
c.finish()
