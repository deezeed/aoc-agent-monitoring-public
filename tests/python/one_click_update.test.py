"""Tests the one-click update of installer builds (round #7 item 5), extracted
straight from monitor.py: release-notes parsing (SHA-256 line, "What's new"
bullets), _update_plan's asset/hash/version checks, the update job state
machine, _update_install_worker with a fake download + launch (nothing is
ever really downloaded or installed), _update_start's double-click guard,
and the one-time "What's new" state across restarts."""
import sys, os, json, time, re, hashlib, threading, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
SCRATCH = tempfile.mkdtemp(prefix="aoc_test_update_")
HASH = "95d8073e8cf16a89ed3e7a9266b056b311a7bb2665322bf526a85caae1ab62d8"
PREFIX = "https://github.com/deezeed/aoc-agent-monitoring-public/releases/download/"
BODY = f"""AOC 1.0.360

## What's new since v1.0.314

- Ask AOC: questions about your own history in plain words
- Inbox: everything that needs you, with one-tap answers
* Dangerous-command guard: your OK before Claude runs a destructive command

## Install

- Download **AOC-Setup-1.0.360.exe** and run it

SHA-256: `{HASH.upper()}`
"""


def release(version="1.0.360", body=BODY, assets=None):
    name = f"AOC-Setup-{version}.exe"
    if assets is None:
        assets = [{"name": name + ".sig", "browser_download_url": f"{PREFIX}v{version}/{name}.sig"},
                  {"name": name, "browser_download_url": f"{PREFIX}v{version}/{name}"}]
    return {"tag_name": "v" + version, "body": body, "assets": assets}


errors = []
try:
    ns = exec_functions(
        ["AOC_RELEASE_DL_PREFIX", "_WHATS_NEW_MAX_ITEMS", "_UPDATE_TRANSITIONS", "_INSTALLER_SILENT_ARGS",
         "_parse_version", "_update_job_next", "_release_sha256", "_release_whats_new", "_update_plan",
         "_whats_new_next", "_whats_new_load", "_whats_new_save", "_whats_new_startup", "_whats_new_view",
         "_whats_new_dismiss", "_update_job_set", "_file_sha256", "_update_install_worker", "_update_start"],
        {"os": os, "json": json, "time": time, "re": re, "_threading": threading,
         "WHATS_NEW_FILE": os.path.join(SCRATCH, "whats_new.json"), "UPDATE_DIR": os.path.join(SCRATCH, "update"),
         "_UPDATE_INSTALL_WAIT_S": 0, "_latest_release": [None],
         "_update_job": {"state": "idle", "pct": 0, "version": "", "error": ""},
         "_update_job_lock": threading.Lock(), "_whats_new_lock": threading.Lock(),
         "_log_bg_error": lambda where, e: errors.append((where, e))})
    nxt = ns["_update_job_next"]
    sha_of = ns["_release_sha256"]
    whats = ns["_release_whats_new"]
    plan_of = ns["_update_plan"]

    # 1. notes parsing
    c.check("SHA-256 line parsed, lowercased", sha_of(BODY) == HASH)
    c.check("no SHA-256 line -> ''", sha_of("## What's new\n- x") == "" and sha_of(None) == "")
    c.check("short hash -> ''", sha_of("SHA-256: `abc`") == "")
    c.check("two different hashes -> '' (ambiguous)", sha_of(BODY + f"\nSHA-256: `{'0' * 64}`") == "")
    c.check("same hash twice -> still fine", sha_of(BODY + f"\nSHA-256: `{HASH}`") == HASH)
    items = whats(BODY)
    c.check("What's new: 3 bullets (- and *)", len(items) == 3 and items[0].startswith("Ask AOC"))
    c.check("What's new stops at the next heading", not any("Download" in i for i in items))
    c.check("no What's new heading -> []", whats("## Install\n- a") == [] and whats(None) == [])
    c.check("capped at _WHATS_NEW_MAX_ITEMS", len(whats("## What's new\n" + "\n".join(f"- i{n}" for n in range(99)))) == 40)
    c.check("long items truncated", len(whats("## What's new\n- " + "x" * 999)[0]) == 300)

    # 2. update plan
    p = plan_of("1.0.314", release())
    c.check("newer release -> ok plan", p["ok"] is True)
    c.check("plan carries url/name/hash/version",
            p.get("url") == f"{PREFIX}v1.0.360/AOC-Setup-1.0.360.exe" and p.get("name") == "AOC-Setup-1.0.360.exe"
            and p.get("sha256") == HASH and p.get("version") == "1.0.360")
    c.check("plan carries the What's new list", p.get("notes") == items)
    c.check("dev tree (no VERSION) -> refused", plan_of("", release())["ok"] is False)
    c.check("no release fetched yet -> refused", plan_of("1.0.314", None)["ok"] is False)
    c.check("same version -> already up to date", "up to date" in plan_of("1.0.360", release())["error"])
    c.check("older release -> refused", plan_of("1.0.400", release())["ok"] is False)
    c.check("no hash in notes -> refused", "SHA-256" in plan_of("1.0.314", release(body="## What's new\n- a"))["error"])
    c.check("no exe asset -> refused", plan_of("1.0.314", release(assets=[]))["ok"] is False)
    evil = [{"name": "AOC-Setup-1.0.360.exe", "browser_download_url": "https://evil.example/AOC-Setup-1.0.360.exe"}]
    c.check("asset off the public repo -> refused", plan_of("1.0.314", release(assets=evil))["ok"] is False)
    other = [{"name": "AOC-Setup-1.0.359.exe", "browser_download_url": f"{PREFIX}v1.0.360/AOC-Setup-1.0.359.exe"}]
    c.check("asset of another version -> refused", plan_of("1.0.314", release(assets=other))["ok"] is False)
    c.check("junk asset entries ignored", plan_of("1.0.314", release(assets=["x", None] + release()["assets"]))["ok"] is True)

    # 3. state machine
    j = {"state": "idle", "pct": 0, "version": "", "error": ""}
    j = nxt(j, "start", version="1.0.360")
    c.check("idle + start -> downloading", j["state"] == "downloading" and j["version"] == "1.0.360" and j["pct"] == 0)
    c.check("progress clamps to 0..99", nxt(j, "progress", pct=150)["pct"] == 99 and nxt(j, "progress", pct=-3)["pct"] == 0)
    c.check("second start while downloading ignored", nxt(j, "start", version="9.9.9") == j)
    j2 = nxt(nxt(j, "downloaded"), "verified")
    c.check("downloaded -> verifying -> installing", j2["state"] == "installing" and j2["pct"] == 100)
    c.check("verified before downloaded is ignored", nxt(j, "verified")["state"] == "downloading")
    e = nxt(j, "fail", error="boom")
    c.check("fail -> error with message", e["state"] == "error" and e["error"] == "boom")
    c.check("error + start -> downloading, error cleared", nxt(e, "start", version="1")["error"] == "" and nxt(e, "start")["state"] == "downloading")
    c.check("fail while idle ignored", nxt({"state": "idle"}, "fail", error="x")["state"] == "idle")
    c.check("input job not mutated", j["state"] == "downloading")

    # 4. install worker: good download -> verified, notes saved, installer launched silently
    payload = b"fake installer bytes"
    good = hashlib.sha256(payload).hexdigest()
    wn = os.path.join(SCRATCH, "wn_worker.json")
    upd = os.path.join(SCRATCH, "upd")
    launched = []
    def fake_dl(url, dest, progress):
        progress(42)
        c.check("progress reaches the job mid-download", ns["_update_job"]["pct"] == 42 and ns["_update_job"]["state"] == "downloading")
        with open(dest, "wb") as f:
            f.write(payload)
    plan = dict(p, sha256=good)
    ns["_update_job"].update(nxt(ns["_update_job"], "start", version=plan["version"]))
    ns["_update_install_worker"](plan, "1.0.314", download_fn=fake_dl, launch_fn=lambda exe, log: launched.append((exe, log)),
                                 update_dir=upd, whats_new_path=wn, wait_s=0)
    c.check("installer launched once with the downloaded exe",
            len(launched) == 1 and launched[0][0] == os.path.join(upd, "AOC-Setup-1.0.360.exe"))
    c.check("install log goes next to it", launched[0][1] == os.path.join(upd, "install.log"))
    c.check("still alive after the wait -> error naming the log",
            ns["_update_job"]["state"] == "error" and "install.log" in ns["_update_job"]["error"])
    saved = json.load(open(wn, encoding="utf-8"))
    c.check("What's new saved as pending for the new version",
            saved.get("pending") == {"version": "1.0.360", "items": items} and saved.get("seen") == "1.0.314")

    # 5. install worker: hash mismatch -> refused, file deleted, never launched
    launched.clear()
    ns["_update_job"].update({"state": "idle"}); ns["_update_job"].update(nxt(ns["_update_job"], "start", version="1.0.360"))
    ns["_update_install_worker"](dict(p), "1.0.314", download_fn=fake_dl, launch_fn=lambda *a: launched.append(a),
                                 update_dir=upd, whats_new_path=wn, wait_s=0)
    c.check("mismatch -> error", ns["_update_job"]["state"] == "error" and "checksum mismatch" in ns["_update_job"]["error"])
    c.check("mismatch -> not launched", launched == [])
    c.check("mismatch -> downloaded file removed", not os.path.exists(os.path.join(upd, "AOC-Setup-1.0.360.exe")))

    # 6. install worker: download error -> error state, logged
    def bad_dl(url, dest, progress):
        raise OSError("network down")
    ns["_update_job"].update(nxt(ns["_update_job"], "start", version="1.0.360"))
    ns["_update_install_worker"](plan, "1.0.314", download_fn=bad_dl, launch_fn=lambda *a: launched.append(a),
                                 update_dir=upd, whats_new_path=wn, wait_s=0)
    c.check("download error -> error state", ns["_update_job"]["state"] == "error" and "network down" in ns["_update_job"]["error"])
    c.check("download error -> logged, not launched", errors and errors[-1][0] == "_update_install_worker" and launched == [])

    # 7. _update_start: refusals don't touch the job; running job blocks a second start
    ns["_update_job"].clear(); ns["_update_job"].update({"state": "idle", "pct": 0, "version": "", "error": ""})
    r = ns["_update_start"]("1.0.360", release())
    c.check("start refused when up to date", r["ok"] is False and ns["_update_job"]["state"] == "idle")
    ns["_update_job"]["state"] = "downloading"
    r = ns["_update_start"]("1.0.314", release())
    c.check("start refused while one runs", r["ok"] is False and "already" in r["error"])

    # 8. What's new across restarts
    wnn = ns["_whats_new_next"]
    st, f = wnn({}, "1.0.314")
    c.check("fresh install: records version, shows nothing", st == {"seen": "1.0.314", "show": None} and f is False)
    st, f = wnn({"seen": "1.0.314", "pending": {"version": "1.0.360", "items": ["a"]}}, "1.0.360")
    c.check("updated by AOC: pending list shown, no fetch",
            st == {"seen": "1.0.360", "show": {"version": "1.0.360", "items": ["a"]}} and f is False)
    st, f = wnn({"seen": "1.0.314"}, "1.0.360")
    c.check("installed by hand: needs a fetch", st["show"] == {"version": "1.0.360", "items": None} and f is True)
    st, f = wnn({"seen": "1.0.314", "pending": {"version": "1.0.400", "items": ["x"]}}, "1.0.360")
    c.check("stale pending for another version -> fetch instead", f is True and "pending" not in st)
    st, f = wnn({"seen": "1.0.360", "show": None}, "1.0.360")
    c.check("same version again: nothing", f is False and st["show"] is None)
    st, f = wnn({"seen": "1.0.360", "show": {"version": "1.0.360", "items": None}}, "1.0.360")
    c.check("earlier fetch failed -> retried next start", f is True)
    c.check("dev tree: untouched", wnn({"seen": "1"}, "") == ({"seen": "1"}, False))
    c.check("junk state handled", wnn("garbage", "1.0.1")[0] == {"seen": "1.0.1", "show": None})

    path = os.path.join(SCRATCH, "wn_start.json")
    ns["_whats_new_save"]({"seen": "1.0.314"}, path)
    ns["_whats_new_startup"]("1.0.360", path=path, fetch_fn=lambda v: ["fetched for " + v])
    c.check("startup fetches a hand-installed release's notes",
            ns["_whats_new_view"]("1.0.360", path=path) == {"version": "1.0.360", "items": ["fetched for 1.0.360"]})
    c.check("view is empty in a dev tree", ns["_whats_new_view"]("", path=path) == {})
    ns["_whats_new_dismiss"](path=path)
    c.check("dismissed -> not shown again", ns["_whats_new_view"]("1.0.360", path=path) == {})
    ns["_whats_new_startup"]("1.0.360", path=path, fetch_fn=lambda v: 1 / 0)
    c.check("restart after dismiss: still nothing", ns["_whats_new_view"]("1.0.360", path=path) == {})
    path2 = os.path.join(SCRATCH, "wn_fail.json")
    ns["_whats_new_save"]({"seen": "1.0.314"}, path2)
    n_err = len(errors)
    ns["_whats_new_startup"]("1.0.360", path=path2, fetch_fn=lambda v: 1 / 0)
    c.check("fetch failure logged, nothing shown", len(errors) == n_err + 1 and ns["_whats_new_view"]("1.0.360", path=path2) == {})
    c.check("missing file -> {}", ns["_whats_new_load"](os.path.join(SCRATCH, "nope.json")) == {})

    # 9. same silent flags as CI's upgrade job
    wf = open(os.path.join(os.path.dirname(__file__), "..", "..", ".github", "workflows", "tests.yml"), encoding="utf-8").read()
    c.check("silent flags match CI's upgrade step", all(f"'{a}'" in wf for a in ns["_INSTALLER_SILENT_ARGS"]))
finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
