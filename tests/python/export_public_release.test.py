"""export_public_release.ps1 copies only git-tracked files of its allowlist.
Runs the real script (a copy) against a temp git repo: an untracked file in
docs/ (e.g. a private audit), a scratch file in tests/ and a file outside
the allowlist must stay out; tracked files, nested folders, a non-ASCII
name and uncommitted edits of tracked files go in; a tracked file deleted
from the working tree is skipped."""
import os, sys, shutil, subprocess, tempfile

sys.path.insert(0, os.path.dirname(__file__))
from lib.check import Checker

c = Checker()
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
work = tempfile.mkdtemp(prefix="aoc-export-")
repo, dest = os.path.join(work, "priv"), os.path.join(work, "pub")


def put(rel, text="x\n"):
    p = os.path.join(repo, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def git(*args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8")


os.makedirs(repo)
git("init", "-q")
git("config", "user.email", "t@example.com")
git("config", "user.name", "t")
shutil.copy(os.path.join(ROOT, "export_public_release.ps1"), repo)
for rel in ["monitor.py", "README.md", "docs/index.html", "server/01_core.py", "web/js/01_app.js",
            "tests/python/a.test.py", "tests/python/lib/check.py", "hooks/aoc_hook.py", "docs/čeština.md",
            "saas-backend/app.py", "tests/gone.py"]:
    put(rel)
git("add", "-A")
git("commit", "-q", "-m", "init")
# working-tree state the export must respect
put("docs/aoc_upgrade_audit/08_EXECUTIVE_SUMMARY.md", "PRIVATE AUDIT\n")
put("tests/_scratch.py")
put("tests/python/__pycache__/a.cpython-312.pyc")
put("server/01_core.py", "edited, not committed\n")
os.remove(os.path.join(repo, "tests", "gone.py"))

r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    os.path.join(repo, "export_public_release.ps1"), "-Dest", dest],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
c.check("script exits 0", r.returncode == 0)
if r.returncode:
    print(r.stdout, r.stderr)

exported = sorted(os.path.relpath(os.path.join(d, f), dest).replace(os.sep, "/")
                  for d, _, fs in os.walk(dest) for f in fs)
c.check("untracked audit in docs/ is NOT exported", not any(p.startswith("docs/aoc_upgrade_audit") for p in exported))
c.check("untracked scratch file in tests/ is NOT exported", "tests/_scratch.py" not in exported)
c.check("no __pycache__ in the export", not any("__pycache__" in p for p in exported))
c.check("non-allowlisted saas-backend/ is NOT exported", not any(p.startswith("saas-backend") for p in exported))
c.check("tracked file deleted from the tree is skipped", "tests/gone.py" not in exported)
c.check("export script itself is not exported", "export_public_release.ps1" not in exported)
c.check("tracked allowlisted files are exported, nested paths kept", exported == sorted([
    "README.md", "docs/index.html", "docs/čeština.md", "hooks/aoc_hook.py", "monitor.py", "server/01_core.py",
    "tests/python/a.test.py", "tests/python/lib/check.py", "web/js/01_app.js"]))
with open(os.path.join(dest, "server", "01_core.py"), encoding="utf-8") as f:
    c.check("working-tree content (uncommitted edit) is what gets exported", f.read() == "edited, not committed\n")
c.check("summary counts tracked files", "Exported 9 tracked files" in r.stdout)

# re-export into the existing dest clears stale files but keeps .git
os.makedirs(os.path.join(dest, ".git"))
put("docs/index.html", "v2\n")
open(os.path.join(dest, "stale.txt"), "w").close()
r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    os.path.join(repo, "export_public_release.ps1"), "-Dest", dest],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
c.check("re-export: exits 0, stale file gone, .git kept", r.returncode == 0
        and not os.path.exists(os.path.join(dest, "stale.txt")) and os.path.isdir(os.path.join(dest, ".git")))

# not a git checkout -> refuse instead of exporting everything
norepo = os.path.join(work, "norepo")
os.makedirs(os.path.join(norepo, "docs"))
shutil.copy(os.path.join(ROOT, "export_public_release.ps1"), norepo)
open(os.path.join(norepo, "docs", "secret.md"), "w").close()
r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    os.path.join(norepo, "export_public_release.ps1"), "-Dest", os.path.join(work, "pub2")],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
c.check("no git checkout -> fails, nothing exported",
        r.returncode != 0 and not os.path.exists(os.path.join(work, "pub2", "docs", "secret.md")))

shutil.rmtree(work, ignore_errors=True)
c.finish()
