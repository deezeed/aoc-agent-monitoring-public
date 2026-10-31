"""Change review (monitor.py) against a real throwaway git repo with a
non-ASCII path: _change_own (only the session's own edited files, no ../
tricks), seen marks tied to the file's size+mtime, _change_discard of a
modified / staged / deleted / untracked / staged-new file with a backup,
_change_undo (byte-identical, refuses when the file changed again unless
forced, never twice), backup listing + pruning, _change_stage."""
import sys, os, json, re, time, subprocess, tempfile, shutil, threading

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
if not shutil.which("git"):
    print("SKIP: needs git")
    c.finish()
base = tempfile.mkdtemp(prefix="aoc_review_")
bdir = os.path.join(base, "backups")
review = os.path.join(base, "review.json")
ns = exec_functions(["_git_out", "_parse_porcelain_z", "_change_kind", "_git_root_for", "_load_json_file",
                     "_CHANGE_BACKUPS_KEEP", "_change_own", "_file_sig", "_file_hash", "_change_review_load",
                     "_change_seen_set", "_change_seen_marks", "_change_status", "_change_backups", "_change_prune",
                     "_change_discard", "_change_undo", "_change_stage"],
                    {"os": os, "json": json, "re": re, "time": time, "subprocess": subprocess, "threading": threading,
                     "_NO_WINDOW": 0x08000000 if os.name == "nt" else 0,
                     "_CHANGE_BACKUP_DIR": bdir, "_CHANGE_REVIEW_FILE": review})

repo = os.path.join(base, "Počítač repo")
os.makedirs(os.path.join(repo, "src"))
git = lambda *a: subprocess.run(["git", "-C", repo, *a], capture_output=True, check=True).stdout.decode("utf-8")
git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t"); git("config", "core.autocrlf", "false")
P = lambda *x: os.path.join(repo, *x)


def write(p, text):
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def read(p):
    with open(p, "rb") as f:
        return f.read()


for name, text in [("src/a.py", "one\ntwo\n"), ("staged.py", "s\n"), ("gone.py", "bye\n"), ("keep.py", "k\n")]:
    write(P(*name.split("/")), text)
git("add", "-A"); git("commit", "-qm", "base")
write(P("src", "a.py"), "one\nTWO\nthree\n")
write(P("staged.py"), "s2\n"); git("add", "staged.py")
os.remove(P("gone.py"))
write(P("nový.txt"), "new\n")
write(P("added.py"), "added\n"); git("add", "added.py")
edited = [P("src", "a.py"), P("staged.py"), P("gone.py"), P("nový.txt"), P("added.py"), P("keep.py")]

# ── ownership ──
own = ns["_change_own"]
c.check("own: exact path", own(edited, P("src", "a.py")) == P("src", "a.py"))
c.check("own: case + slash insensitive (Windows)", os.name != "nt" or own(edited, P("SRC", "A.PY").replace("\\", "/")) == P("src", "a.py"))
c.check("own: ../ that resolves to an edited file is the same file", own(edited, os.path.join(repo, "src", "..", "keep.py")) == P("keep.py"))
c.check("own: a file not edited is refused", own(edited, P("other.py")) is None)
c.check("own: traversal outside refused", own(edited, os.path.join(repo, "..", "..", "Windows", "win.ini")) is None)
c.check("own: empty refused", own(edited, "") is None and own(edited, None) is None)

# ── seen marks ──
ns["_change_seen_set"]("s1", P("src", "a.py"), True)
c.check("seen: marked", ns["_change_seen_marks"]("s1", edited) == {P("src", "a.py"): True})
c.check("seen: other session not affected", ns["_change_seen_marks"]("s2", edited) == {})
write(P("src", "a.py"), "one\nTWO\nthree\nfour\n")
c.check("seen: cleared when the file changes again", ns["_change_seen_marks"]("s1", edited) == {})
ns["_change_seen_set"]("s1", P("src", "a.py"), True)
ns["_change_seen_set"]("s1", P("src", "a.py"), False)
c.check("seen: unmark", ns["_change_seen_marks"]("s1", edited) == {})

# ── discard + undo ──
disc, undo = ns["_change_discard"], ns["_change_undo"]
before = read(P("src", "a.py"))
r = disc(P("src", "a.py"), "s1", bdir=bdir)
c.check("discard modified: back to HEAD", r["ok"] and read(P("src", "a.py")) == b"one\ntwo\n" and r["backup"]["kind"] == "modified")
c.check("discard: backup holds the old content", read(os.path.join(bdir, r["backup"]["id"], "file")) == before)
c.check("discard: nothing left to discard", not disc(P("src", "a.py"), "s1", bdir=bdir)["ok"])
u = undo(r["backup"]["id"], bdir=bdir)
c.check("undo: byte-identical", u["ok"] and read(P("src", "a.py")) == before)
c.check("undo: only once", undo(r["backup"]["id"], bdir=bdir)["error"] == "already undone")

r = disc(P("staged.py"), "s1", bdir=bdir)
c.check("discard staged: index + worktree back to HEAD", r["ok"] and r["backup"]["staged"] and read(P("staged.py")) == b"s\n"
        and "staged.py" not in git("status", "--porcelain"))
c.check("undo staged: content back (unstaged)", undo(r["backup"]["id"], bdir=bdir)["ok"] and read(P("staged.py")) == b"s2\n")

r = disc(P("gone.py"), "s1", bdir=bdir)
c.check("discard deleted: file comes back", r["ok"] and read(P("gone.py")) == b"bye\n" and not r["backup"]["existed"])
c.check("undo deleted: gone again", undo(r["backup"]["id"], bdir=bdir)["ok"] and not os.path.exists(P("gone.py")))

r = disc(P("nový.txt"), "s1", bdir=bdir)
bid_new = r["backup"]["id"]
c.check("discard untracked: moved out, kept in backup", r["ok"] and not os.path.exists(P("nový.txt"))
        and read(os.path.join(bdir, bid_new, "file")) == b"new\n")
write(P("nový.txt"), "someone wrote it again\n")
u = undo(bid_new, bdir=bdir)
c.check("undo refuses when the file changed since", not u["ok"] and u.get("conflict") and read(P("nový.txt")) == b"someone wrote it again\n")
c.check("undo force overwrites", undo(bid_new, bdir=bdir, force=True)["ok"] and read(P("nový.txt")) == b"new\n")

r = disc(P("added.py"), "s1", bdir=bdir)
c.check("discard staged new file: unstaged + moved out", r["ok"] and not os.path.exists(P("added.py"))
        and "added.py" not in git("status", "--porcelain"))
c.check("undo staged new file: back and staged again", undo(r["backup"]["id"], bdir=bdir)["ok"]
        and read(P("added.py")) == b"added\n" and "A  added.py" in git("status", "--porcelain"))

sig0 = ns["_file_sig"](P("src", "a.py"))
write(P("src", "a.py"), "one\nTWO\nthree\nfour\nfive!\n")
r = disc(P("src", "a.py"), "s1", bdir=bdir, expect_sig=sig0)
c.check("discard refuses when the file changed since the panel was shown", not r["ok"] and r.get("conflict")
        and read(P("src", "a.py")) == b"one\nTWO\nthree\nfour\nfive!\n" and ns["_change_backups"]("s1", bdir=bdir) == [])
r = disc(P("src", "a.py"), "s1", bdir=bdir, expect_sig=ns["_file_sig"](P("src", "a.py")))
c.check("discard with the current sig goes through", r["ok"] and read(P("src", "a.py")) == b"one\ntwo\n")
c.check("...and undo restores it", undo(r["backup"]["id"], bdir=bdir)["ok"] and read(P("src", "a.py")) == b"one\nTWO\nthree\nfour\nfive!\n")

c.check("discard: unchanged file refused", disc(P("keep.py"), "s1", bdir=bdir)["error"] == "no uncommitted change in this file")
c.check("discard: outside any repo refused", not disc(os.path.join(base, "loose.txt"), "s1", bdir=bdir)["ok"])
c.check("undo: bad id (no path tricks)", undo("..\\..\\x", bdir=bdir)["error"] == "bad id" and undo("20260101-000000-abcdef", bdir=bdir)["error"] == "backup not found")

# ── listing + pruning ──
r1 = disc(P("src", "a.py"), "s1", bdir=bdir, now=time.time() + 5)
lst = ns["_change_backups"]("s1", bdir=bdir)
c.check("backups: not-undone only, this session", [m["id"] for m in lst] == [r1["backup"]["id"]])
c.check("backups: other session sees none", ns["_change_backups"]("s2", bdir=bdir) == [])
ns["_change_prune"](bdir, keep=1)
c.check("prune keeps the newest", os.listdir(bdir) == [r1["backup"]["id"]])
undo(r1["backup"]["id"], bdir=bdir)

# ── stage ──
s = ns["_change_stage"]([P("src", "a.py"), P("nový.txt")])
st = git("-c", "core.quotepath=off", "status", "--porcelain")
c.check("stage: both added to the index", s["ok"] and "M  src/a.py" in st and "A  nový.txt" in st)

shutil.rmtree(base, ignore_errors=True)
c.finish()
