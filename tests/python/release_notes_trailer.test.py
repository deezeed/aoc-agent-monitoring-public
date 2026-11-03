"""installer/publish_release.ps1 builds "What's new" from commit subjects; a
commit can override its line with a "Release-note:" git trailer, or drop
out with "Release-note: skip". Runs the script's own ConvertTo-ReleaseNoteLines
(dot-sourced, AOC_RELEASE_NOTES_SELFTEST stops it before any gh call) on
the output of the exact git log format the script uses, from a throwaway
repo with real commits."""
import os, re, shutil, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(__file__))
from lib.check import Checker

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(ROOT, "installer", "publish_release.ps1")
c = Checker()
# installer/ is private-only (not in the public export), git/powershell may be missing
if not os.path.exists(SCRIPT) or not shutil.which("git") or not shutil.which("powershell"):
    print("SKIP: needs installer/publish_release.ps1, git and powershell")
    c.finish()
    sys.exit(0)
src = open(SCRIPT, encoding="utf-8").read()
fmt = re.search(r'--format="([^"]+)"', src).group(1)

repo = tempfile.mkdtemp()
git = lambda *a: subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True, encoding="utf-8", check=True).stdout
git("init", "-q")
git("config", "user.email", "t@t")
git("config", "user.name", "t")
git("commit", "-q", "--allow-empty", "-m", "base")
base = git("rev-parse", "HEAD").strip()
git("commit", "-q", "--allow-empty", "-m", "Plain subject only")
git("commit", "-q", "--allow-empty", "-m", "Refactor _foo internals\n\nBody text.\n\nRelease-note: Faster startup on slow disks")
git("commit", "-q", "--allow-empty", "-m", "Test-only tweak\n\nRelease-note: skip")
git("commit", "-q", "--allow-empty", "-m", "Fix a thing\n\nRelease-note: Dashboard: names no longer cut off on phones\nCo-Authored-By: X <x@y>")
git("commit", "-q", "--allow-empty", "-m", "Two features\n\nRelease-note: First feature\nRelease-note: Second feature")
raw_file = os.path.join(repo, "raw.txt")
with open(raw_file, "w", encoding="utf-8") as f:
    f.write(git("log", "--no-merges", f"--format={fmt}", f"{base}..HEAD"))

ps = (f"$env:AOC_RELEASE_NOTES_SELFTEST='1'; . '{SCRIPT}'; "
      f"[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
      f"ConvertTo-ReleaseNoteLines ([IO.File]::ReadAllText('{raw_file}')) | ForEach-Object {{ \"LINE:$_\" }}")
out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                     capture_output=True, text=True, encoding="utf-8")
lines = [l[5:] for l in out.stdout.splitlines() if l.startswith("LINE:")]

c.check("script ran cleanly", out.returncode == 0)
c.check("one line per non-skipped commit, newest first",
        lines == ["First feature", "Second feature", "Dashboard: names no longer cut off on phones", "Faster startup on slow disks", "Plain subject only"])
c.check("two trailers = two lines, subject dropped", "Two features" not in lines)
c.check("trailer replaces the subject", "Refactor _foo internals" not in lines)
c.check("'skip' drops the commit", "Test-only tweak" not in lines)
c.check("other trailers (Co-Authored-By) don't leak in", not any("Co-Authored" in l for l in lines))

# Get-InternalSoundingSubjects: untrailed subjects that read like dev notes
git("commit", "-q", "--allow-empty", "-m", "A/B compare: git plumbing goes through the shared _wt_* worktree API")
git("commit", "-q", "--allow-empty", "-m", "Tests: non-ASCII labels no longer crash")
git("commit", "-q", "--allow-empty", "-m", "Fix crash in monitor.py on startup")
git("commit", "-q", "--allow-empty", "-m", "Refactor _ask internals\n\nRelease-note: Ask AOC answers faster")
git("commit", "-q", "--allow-empty", "-m", "Inbox: everything that needs you, with one-tap answers")
git("commit", "-q", "--allow-empty", "-m", "Work log for any period in one click, per project or client")
with open(raw_file, "w", encoding="utf-8") as f:
    f.write(git("log", "--no-merges", f"--format={fmt}", f"{base}..HEAD"))
ps = (f"$env:AOC_RELEASE_NOTES_SELFTEST='1'; . '{SCRIPT}'; "
      f"[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
      f"Get-InternalSoundingSubjects ([IO.File]::ReadAllText('{raw_file}')) | ForEach-Object {{ \"LINE:$_\" }}")
out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                     capture_output=True, text=True, encoding="utf-8")
flagged = [l[5:] for l in out.stdout.splitlines() if l.startswith("LINE:")]
c.check("internal check ran cleanly", out.returncode == 0)
c.check("code identifiers / plumbing flagged", "A/B compare: git plumbing goes through the shared _wt_* worktree API" in flagged)
c.check("'Tests:' prefix flagged", "Tests: non-ASCII labels no longer crash" in flagged)
c.check("file names flagged", "Fix crash in monitor.py on startup" in flagged)
c.check("a trailer clears an internal-looking subject", "Refactor _ask internals" not in flagged)
c.check("user-facing subjects pass", not any(s.startswith(("Inbox:", "Work log", "Plain subject")) for s in flagged))
c.check("exactly the three internal ones", len(flagged) == 3)
c.finish()
