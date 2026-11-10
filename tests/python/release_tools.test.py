"""Health -> RELEASE: Publish release (installer/publish_release.ps1 dry run
-> confirm -> -Publish -> download the asset back and check its SHA-256)
and Sync public mirror (export_public_release.ps1 -> stage -> confirm ->
commit + push). Every command goes through an injected fake runner, so
nothing is ever published, exported or pushed by this test. The mirror
part also runs real git against temp repos (a bare one as "origin")."""
import os, re, sys, time, hashlib, subprocess, tempfile, threading
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions, read_monitor_source
from lib.check import Checker

c = Checker()
NAMES = ["_release_run", "_release_tools_available","_ps_quote", "_ps_script_args", "_ps_error_line", "_parse_publish_dry_run",
         "_publish_dry_run", "_release_asset_sha256", "_publish_confirm", "_rel_git", "_sync_prepare", "_sync_confirm",
         "_release_action", "_release_status_view", "_restart_blockers"]
AOC = tempfile.mkdtemp(prefix="aoc-rel-")


def fresh(**extra):
    g = {"os": os, "re": re, "time": time, "datetime": datetime, "subprocess": subprocess, "_threading": threading,
         "AOC_DIR": AOC, "PUBLISH_SCRIPT": "C:\\aoc\\installer\\publish_release.ps1",
         "EXPORT_SCRIPT": "C:\\aoc\\export_public_release.ps1", "PUBLIC_REPO": "deezeed/aoc-agent-monitoring-public",
         "PUBLIC_MIRROR_DIR": "", "_release_job": {"kind": "", "state": "idle", "step": "", "error": "",
                                                   "plan": None, "result": None, "at": 0},
         "_release_lock": threading.Lock()}
    g.update(extra)
    return exec_functions(NAMES, g)


ns = fresh()
SHA = "ab" * 32
DRY = f"""Installer : C:\\aoc\\installer\\build\\release-42274868424\\AOC-Setup-1.0.410.exe
Version   : 1.0.410  (tag v1.0.410 on deezeed/aoc-agent-monitoring-public)
SHA-256   : {SHA}
CI run    : 42274868424 (6ed1896)
Changes   : 3 since v1.0.384

----- release notes -----
AOC 1.0.410

## What's new since v1.0.384

- History -> Clients: hourly rate in the invoice export
- Plan-limit forecast → warns 30 min ahead
- Watchdog: in a dev checkout, reload monitor.py only after a commit

## Install

SHA-256: `{SHA}`
-------------------------
WARNING: 1 line(s) read like developer notes (commit subject, no Release-note trailer):
  - Watchdog: in a dev checkout, reload monitor.py only after a commit
Publish with -AllowInternalNotes and reword them on GitHub; next time ...

Dry run -- nothing published. Re-run with -Publish to create the release.
"""

# ── pure helpers
p = ns["_parse_publish_dry_run"](DRY.replace("\n", "\r\n"))
c.check("dry run parsed: version, sha, run, commit", (p["version"], p["sha256"], p["run_id"], p["commit"]) ==
        ("1.0.410", SHA, "42274868424", "6ed1896") and p["ok"])
c.check("changes + previous tag", p["changes"] == 3 and p["since"] == "v1.0.384")
c.check("notes block, non-ASCII kept", p["notes"].startswith("AOC 1.0.410") and "→ warns" in p["notes"] and p["notes"].endswith(f"`{SHA}`"))
c.check("developer-note warnings listed (and only those)", p["warnings"] == ["Watchdog: in a dev checkout, reload monitor.py only after a commit"])
c.check("no warning block -> []", ns["_parse_publish_dry_run"](DRY.split("WARNING:")[0])["warnings"] == [])
bad = ns["_parse_publish_dry_run"]("Version   : 1.0.410\nSHA-256   : nothex\nCI run    : 1 (abcdef1)\n")
c.check("a malformed sha -> not ok", not bad["ok"] and bad["sha256"] == "")
c.check("empty output -> not ok", not ns["_parse_publish_dry_run"]("")["ok"])

q = ns["_ps_quote"]
c.check("ps quote doubles single quotes", q("it's") == "'it''s'")
a = ns["_ps_script_args"]("C:\\Users\\m\\Počítač\\AOC\\installer\\publish_release.ps1", ["-RunId", "42", "-Publish"])
c.check("powershell, no profile, non-interactive", a[:3] == ["powershell", "-NoProfile", "-NonInteractive"] and a[-2] == "-Command")
c.check("UTF-8 output, script quoted, switches bare, values quoted, exit code passed on",
        a[-1] == "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); & "
                 "'C:\\Users\\m\\Počítač\\AOC\\installer\\publish_release.ps1' -RunId '42' -Publish; exit $LASTEXITCODE")
c.check("a value that looks like code stays a string", "'x; rm -r C:\\'" in ns["_ps_script_args"]("s.ps1", ["-Dest", "x; rm -r C:\\"])[-1])
err = "not publishing: notes have developer-sounding lines\nAt C:\\aoc\\installer\\publish_release.ps1:150 char:5\n+     throw ...\n"
c.check("error line = the thrown message", ns["_ps_error_line"](err) == "not publishing: notes have developer-sounding lines")
c.check("error line: otherwise the last line", ns["_ps_error_line"]("a\n\nlast one\n") == "last one" and ns["_ps_error_line"]("") == "no output")

d = tempfile.mkdtemp()
c.check("not a checkout -> unavailable", ns["_release_tools_available"](d) is False)
os.makedirs(os.path.join(d, ".git"))
c.check("git checkout -> available", ns["_release_tools_available"](d) is True)
open(os.path.join(d, "VERSION"), "w").write("1.0.1")
c.check("installed copy (VERSION) -> unavailable", ns["_release_tools_available"](d) is False)
wt = tempfile.mkdtemp()
open(os.path.join(wt, ".git"), "w").write("gitdir: x")
c.check("a worktree (.git file) counts as a checkout", ns["_release_tools_available"](wt) is True)

rb = ns["_restart_blockers"]
c.check("a running release blocks the code reload", "release / mirror sync running" in rb({}, "off", None, False, "running"))
c.check("ready / done / idle don't", all(rb({}, "off", None, False, s) == [] for s in ("ready", "done", "idle", None)))


# ── publish flow, through _release_action with a fake runner
class Fake:
    def __init__(self, rules):
        self.rules, self.calls = rules, []

    def __call__(self, args, cwd=None):
        self.calls.append((list(args), cwd))
        line = " ".join(args)
        for pat, reply in self.rules:
            if re.search(pat, line):
                return reply(args) if callable(reply) else reply
        return 0, ""


sync = lambda fn: fn()
ns = fresh()
act, job = ns["_release_action"], ns["_release_job"]
fake = Fake([(r"-Publish", (0, "Published v1.0.410 -> https://github.com/deezeed/aoc-agent-monitoring-public/releases/tag/v1.0.410\n")),
             (r"publish_release\.ps1", (0, DRY))])
c.check("confirm before any check is refused", act("publish", "confirm", {}, run=fake, spawn=sync)["ok"] is False and not fake.calls)
c.check("a non-numeric run id is refused", act("publish", "prepare", {"run_id": "1; x"}, run=fake, spawn=sync)["ok"] is False)
c.check("unknown action refused", act("publish", "launch", {}, run=fake, spawn=sync)["ok"] is False and act("x", "prepare", {})["ok"] is False)
r = act("publish", "prepare", {"run_id": "42274868424"}, run=fake, spawn=sync)
c.check("prepare -> ready with the dry run's plan", r["ok"] and job["state"] == "ready" and job["kind"] == "publish"
        and job["plan"]["version"] == "1.0.410")
c.check("the dry run is only a dry run (no -Publish)", len(fake.calls) == 1 and "-Publish" not in fake.calls[0][0][-1]
        and "-RunId '42274868424'" in fake.calls[0][0][-1])
c.check("confirm for the other kind is refused", act("sync", "confirm", {}, run=fake, spawn=sync)["ok"] is False)
r = act("publish", "confirm", {"allow_internal": False}, run=fake, spawn=sync, sha_of_asset=lambda v: SHA)
c.check("warnings + no tick -> error, nothing published", job["state"] == "error" and "publish anyway" in job["error"]
        and not any("-Publish" in a[-1] for a, _ in fake.calls))
act("publish", "cancel", {})
c.check("cancel -> idle, plan gone", job["state"] == "idle" and job["plan"] is None)
act("publish", "prepare", {}, run=fake, spawn=sync)
c.check("no run id -> the script picks the newest green run (no -RunId)", "-RunId" not in fake.calls[-1][0][-1])
asked = []
r = act("publish", "confirm", {"allow_internal": True}, run=fake, spawn=sync, sha_of_asset=lambda v: asked.append(v) or SHA)
pub = fake.calls[-1][0][-1]
c.check("confirm publishes exactly the checked run, -AllowInternalNotes only when ticked",
        "-RunId '42274868424' -Publish -AllowInternalNotes" in pub)
c.check("done: url + asset verified against the plan's sha", job["state"] == "done" and job["result"]["verified"] is True
        and job["result"]["url"].endswith("/v1.0.410") and asked == ["1.0.410"])
act("publish", "cancel", {})
act("publish", "prepare", {}, run=Fake([(r"publish_release", (0, DRY.split("WARNING:")[0]))]), spawn=sync)
act("publish", "confirm", {}, run=fake, spawn=sync, sha_of_asset=lambda v: "cd" * 32)
c.check("a mismatching asset -> done but NOT verified", job["state"] == "done" and job["result"]["verified"] is False
        and "-AllowInternalNotes" not in fake.calls[-1][0][-1])
act("publish", "cancel", {})
act("publish", "prepare", {}, run=Fake([(r"publish_release", (0, DRY.split("WARNING:")[0]))]), spawn=sync)


def boom(v):
    raise RuntimeError("couldn't download the published installer: 404")
act("publish", "confirm", {}, run=fake, spawn=sync, sha_of_asset=boom)
c.check("published but the download check failed -> done, unverified, reason kept (not 'failed')",
        job["state"] == "done" and not job["result"]["verified"] and "404" in job["result"]["verify_error"])
act("publish", "cancel", {})
act("publish", "prepare", {}, run=Fake([(r"publish_release", (1, "release v1.0.410 already exists on x\nAt C:\\a.ps1:90 char:1\n+ throw\n"))]), spawn=sync)
c.check("a failing dry run -> error with the script's message", job["state"] == "error" and job["error"] == "release v1.0.410 already exists on x")
act("publish", "cancel", {})
act("publish", "prepare", {}, run=Fake([(r"publish_release", (0, "Version : ?\n"))]), spawn=sync)
c.check("unreadable dry run output -> error, not a half plan", job["state"] == "error" and "couldn't read" in job["error"])

# one job at a time, and the background thread really runs
act("publish", "cancel", {})
gate = threading.Event()
slow = Fake([(r"publish_release", lambda a: (gate.wait(5), (0, DRY))[1])])
r1 = act("publish", "prepare", {}, run=slow)
c.check("running in the background", r1["ok"] and job["state"] == "running" and job["step"].startswith("dry run"))
c.check("second job while running -> refused", act("sync", "prepare", {}, run=slow)["ok"] is False)
c.check("cancel while running -> refused", act("publish", "cancel", {})["ok"] is False)
c.check("status view while running", ns["_release_status_view"]()["state"] == "running")
gate.set()
for _ in range(100):
    if job["state"] != "running":
        break
    time.sleep(0.05)
c.check("thread finished -> ready", job["state"] == "ready")


# _release_asset_sha256: downloads into a temp dir via gh, hashes, cleans up
def gh_dl(args):
    dest = args[args.index("--dir") + 1]
    open(os.path.join(dest, "AOC-Setup-1.0.410.exe"), "wb").write(b"installer bytes")
    gh_dl.dir = dest
    return 0, ""
got = ns["_release_asset_sha256"]("1.0.410", Fake([(r"^gh release download v1\.0\.410 --repo deezeed/aoc-agent-monitoring-public "
                                                   r"--pattern AOC-Setup-1\.0\.410\.exe", gh_dl)]))
c.check("asset sha = sha256 of the downloaded file", got == hashlib.sha256(b"installer bytes").hexdigest())
c.check("temp download dir removed", not os.path.exists(gh_dl.dir))
try:
    ns["_release_asset_sha256"]("1.0.410", Fake([(r"gh release", (1, "release not found"))]))
    c.check("failed download raises", False)
except RuntimeError as e:
    c.check("failed download raises", "release not found" in str(e))


# ── mirror sync with real git: temp private repo, temp mirror, bare origin
def git(repo, *a):
    return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True)
def mkrepo(path):
    os.makedirs(path, exist_ok=True)
    git(path, "init", "-q"); git(path, "config", "user.email", "t@t"); git(path, "config", "user.name", "t")
    git(path, "config", "core.autocrlf", "false")

tmp = tempfile.mkdtemp(prefix="aoc-sync-")
priv, mirror, origin = (os.path.join(tmp, n) for n in ("priv", "mirror", "origin.git"))
mkrepo(priv)
open(os.path.join(priv, "monitor.py"), "w").write("x = 1\n")
git(priv, "add", "-A"); git(priv, "commit", "-qm", "init")
subprocess.run(["git", "init", "-q", "--bare", origin], capture_output=True)
mkrepo(mirror)
open(os.path.join(mirror, "README.md"), "w").write("old\n")
git(mirror, "add", "-A"); git(mirror, "commit", "-qm", "old"); git(mirror, "remote", "add", "origin", origin)
git(mirror, "push", "-q", "-u", "origin", "HEAD:master")


def real_git_fake_export(args, cwd=None):
    """export_public_release.ps1 stand-in: writes files into -Dest; git runs for real."""
    if args[0] == "powershell":
        assert "export_public_release.ps1" in args[-1] and "-Dest " + ns["_ps_quote"](mirror) in args[-1]
        open(os.path.join(mirror, "README.md"), "w").write("new\n")
        open(os.path.join(mirror, "monitor.py"), "w").write("x = 1\n")
        return 0, "AOC public release export\n"
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


ns = fresh(AOC_DIR=priv, PUBLIC_MIRROR_DIR=mirror)
act, job = ns["_release_action"], ns["_release_job"]
day = datetime.now().strftime("%Y-%m-%d")
psha = git(priv, "rev-parse", "--short", "HEAD").stdout.strip()
act("sync", "prepare", {}, run=real_git_fake_export, spawn=sync)
pl = job["plan"] or {}
c.check("sync prepare -> ready, 2 files staged", job["state"] == "ready" and pl.get("changed") == 2)
c.check("status lines + diffstat summary", sorted(l[3:].strip() for l in pl.get("status", [])) == ["README.md", "monitor.py"]
        and "2 files changed" in pl.get("summary", ""))
c.check("commit message names date + private sha", pl.get("message") == f"AOC public mirror {day} @{psha}")
c.check("clean private checkout -> no dirty warning", pl.get("private_dirty") == 0)
c.check("nothing committed or pushed by prepare", git(mirror, "log", "--oneline").stdout.count("\n") == 1
        and git(origin, "log", "--oneline").stdout.count("\n") == 1)
act("sync", "confirm", {}, run=real_git_fake_export, spawn=sync)
res = job["result"] or {}
c.check("confirm -> committed + pushed", job["state"] == "done" and res.get("pushed"))
c.check("origin got exactly that commit + message", git(origin, "log", "-1", "--format=%h %s").stdout.strip()
        == f"{res.get('commit')} AOC public mirror {day} @{psha}")
act("sync", "cancel", {})
open(os.path.join(priv, "monitor.py"), "a").write("y = 2\n")
act("sync", "prepare", {}, run=real_git_fake_export, spawn=sync)
c.check("no changes -> ready with 0, but the private checkout's edits are flagged",
        job["state"] == "ready" and job["plan"]["changed"] == 0 and job["plan"]["private_dirty"] == 1)
act("sync", "confirm", {}, run=real_git_fake_export, spawn=sync)
c.check("nothing to commit -> error, no empty commit", job["state"] == "error" and "nothing to commit" in job["error"]
        and git(origin, "log", "--oneline").stdout.count("\n") == 2)
act("sync", "cancel", {})
ns2 = fresh(AOC_DIR=priv, PUBLIC_MIRROR_DIR=os.path.join(tmp, "nope"))
ns2["_release_action"]("sync", "prepare", {}, run=real_git_fake_export, spawn=sync)
c.check("mirror isn't a git checkout -> error before exporting anything", ns2["_release_job"]["state"] == "error"
        and "isn't a git checkout" in ns2["_release_job"]["error"])
# a push that fails keeps the commit and says so
open(os.path.join(mirror, "extra.txt"), "w").write("e\n")
git(mirror, "remote", "set-url", "origin", os.path.join(tmp, "missing.git"))
ns3 = fresh(AOC_DIR=priv, PUBLIC_MIRROR_DIR=mirror)
ns3["_release_job"].update(kind="sync", state="ready", plan={"mirror": mirror, "changed": 1, "message": "m"})
git(mirror, "add", "-A")
ns3["_release_action"]("sync", "confirm", {}, run=real_git_fake_export, spawn=sync)
c.check("push failure -> error naming the local commit", ns3["_release_job"]["state"] == "error"
        and "but the push failed" in ns3["_release_job"]["error"])

# ── routes + wiring
src = read_monitor_source()
# one namespace for all server parts: a second def of the same name in a later
# part silently replaces these (an earlier _git_out(run, repo) did exactly that)
dupes = [n for n in NAMES if len(re.findall(r"^def " + n + r"\(", src, re.M)) != 1]
c.check("every release helper is defined exactly once across server/", dupes == [])
seg = src[src.index('elif path_no_qs.startswith("/release/"):'):]
seg = seg[:seg.index("elif path_no_qs", 10)]
c.check("POST /release/*: this PC only, dev checkout only", "_is_direct_loopback()" in seg and "_release_tools_available()" in seg)
g = src[src.index('elif path_no_qs == "/release/status":'):]
g = g[:g.index("elif path_no_qs", 10)]
c.check("GET /release/status hides the buttons off this PC", "self._is_direct_loopback()" in g)
c.check("both restart-blocker call sites pass the release state", src.count('_hooks_health["repairing"],') >= 2
        and src.count("_release_job.get(\"state\"))") >= 2)
c.check("AOC_PUBLIC_MIRROR is env-only", 'os.environ.get("AOC_PUBLIC_MIRROR")' in src and src.count("AOC_PUBLIC_MIRROR") == 2)
c.finish()
