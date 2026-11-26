"""AOC's git commits work on a PC with no git identity (server/10_sessions.py
_wt_git). Found by the Orchestrate benchmark: with no `user.email` anywhere,
the commit of an agent's work failed silently, its folder was removed, and
the agent was told its files "aren't in your folder" -- three retries of
work that was right the first time. A configured identity is never replaced."""
import os
import subprocess
import sys
import shutil
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
W = exec_functions(["_WT_IDENTITY", "_wt_needs_identity", "_wt_git"],
                   {"subprocess": subprocess, "_NO_WINDOW": 0x08000000 if os.name == "nt" else 0})
need = W["_wt_needs_identity"]
c.check("commit and merge write commits", need(["commit", "-m", "x"]) and need(["merge", "--no-ff", "b"]))
c.check("...also after other -c options", need(["-c", "core.autocrlf=false", "commit", "-q", "-m", "x"]))
c.check("a call naming its author is left alone",
        not need(["-c", "user.name=AOC", "-c", "user.email=aoc@localhost", "merge", "b"]))
c.check("reads are left alone", not need(["status"]) and not need(["log", "--format=%an"]) and not need([]))

work = tempfile.mkdtemp(prefix="aoc-ident-")
empty = os.path.join(work, "empty.gitconfig")
open(empty, "w").close()
# this test's git sees no global or system identity, whatever the PC has
os.environ.update(GIT_CONFIG_GLOBAL=empty, GIT_CONFIG_NOSYSTEM="1")
for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL"):
    os.environ.pop(k, None)
git = W["_wt_git"]


def repo(name, ident=None):
    path = os.path.join(work, name)
    os.makedirs(path)
    git(["init", "-q", "-b", "main"], path)
    if ident:
        git(["config", "user.name", ident[0]], path)
        git(["config", "user.email", ident[1]], path)
    with open(os.path.join(path, "a.txt"), "w") as f:
        f.write("a\n")
    git(["add", "-A"], path)
    return path


bare = repo("no-identity")
raw = subprocess.run(["git", "commit", "-q", "-m", "plain"], cwd=bare, capture_output=True, text=True)
c.check("setup: plain git really can't commit here", raw.returncode != 0)
rc, _, err = git(["commit", "-q", "-m", "first"], bare)
c.check("_wt_git commits anyway", rc == 0)
c.check("...as AOC", git(["log", "-1", "--format=%an <%ae>"], bare)[1].strip() == "AOC <aoc@localhost>")
git(["checkout", "-q", "-b", "side"], bare)
with open(os.path.join(bare, "b.txt"), "w") as f:
    f.write("b\n")
git(["add", "-A"], bare)
git(["commit", "-q", "-m", "side"], bare)
git(["checkout", "-q", "main"], bare)
rc, _, err = git(["merge", "--no-ff", "--no-edit", "side"], bare)
c.check("a --no-ff merge makes its commit too", rc == 0 and git(["log", "-1", "--format=%P"], bare)[1].count(" ") == 1)

mine = repo("own-identity", ("Marek", "marek@example.com"))
rc, _, _ = git(["commit", "-q", "-m", "first"], mine)
c.check("a repo's own identity is kept", rc == 0 and
        git(["log", "-1", "--format=%an <%ae>"], mine)[1].strip() == "Marek <marek@example.com>")

half = repo("name-only", ("Only Name", ""))
git(["config", "--unset", "user.email"], half)
rc, _, _ = git(["commit", "-q", "-m", "first"], half)
c.check("half an identity (no email) gets AOC's whole one", rc == 0 and
        git(["log", "-1", "--format=%ae"], half)[1].strip() == "aoc@localhost")

shutil.rmtree(work, ignore_errors=True)
c.finish()
