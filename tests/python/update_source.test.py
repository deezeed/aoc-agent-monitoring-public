"""AOC_UPDATE_SOURCE: monitor.py's test-only stand-in for the GitHub release
API (CI's installer/one_click_update_e2e.py). _update_source accepts only a
plain-http loopback address; the three release URLs default to GitHub and
switch together; nothing but the environment can set it; the SHA-256 check
in _update_plan still applies. Plus the e2e script's stand-in server serves
exactly what _update_plan accepts."""
import sys, os, re, json, hashlib, tempfile, urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions, extract_functions, read_monitor_source
from lib.check import Checker

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
c = Checker()
src_fn = exec_functions(["_update_source"], {"re": re})["_update_source"]
c.check("unset -> GitHub", src_fn(None) == "" and src_fn("") == "")
c.check("loopback http with port", src_fn("http://127.0.0.1:8765") == "http://127.0.0.1:8765/")
c.check("trailing slash / whitespace ok", src_fn(" http://localhost:8765/ ") == "http://localhost:8765/")
for bad in ("https://127.0.0.1:8765/", "http://evil.example:8765/", "http://127.0.0.1/", "http://127.0.0.2:80/",
            "http://127.0.0.1:8765/x", "http://127.0.0.1:8765@evil.example/", "file:///C:/x"):
    c.check(f"refused: {bad}", src_fn(bad) == "")


def consts(source):
    ns = {"_UPDATE_SOURCE": source}
    defs = extract_functions(["AOC_RELEASES_API", "AOC_RELEASE_DL_PREFIX", "AOC_RELEASE_TAG_API"])
    for k in ("AOC_RELEASES_API", "AOC_RELEASE_DL_PREFIX", "AOC_RELEASE_TAG_API"):
        exec(defs[k], ns)
    return ns["AOC_RELEASES_API"], ns["AOC_RELEASE_DL_PREFIX"], ns["AOC_RELEASE_TAG_API"]

c.check("default: the public repo on GitHub", consts("") == (
    "https://api.github.com/repos/deezeed/aoc-agent-monitoring-public/releases/latest",
    "https://github.com/deezeed/aoc-agent-monitoring-public/releases/download/",
    "https://api.github.com/repos/deezeed/aoc-agent-monitoring-public/releases/tags/"))
c.check("override: all three move together", consts("http://127.0.0.1:9/") == (
    "http://127.0.0.1:9/releases/latest", "http://127.0.0.1:9/releases/download/", "http://127.0.0.1:9/releases/tags/"))

src = read_monitor_source()
c.check("read from the environment only", src.count("AOC_UPDATE_SOURCE") >= 1 and
        src.count('os.environ.get("AOC_UPDATE_SOURCE")') == 1 and "_UPDATE_SOURCE =" in src and
        len(re.findall(r"^_UPDATE_SOURCE\s*=", src, re.M)) == 1)

# the e2e script's stand-in release vs the real _update_plan under the override
# (installer/ is private-only -- the public export has no e2e script)
if not os.path.exists(os.path.join(ROOT, "installer", "one_click_update_e2e.py")):
    print("SKIP: stand-in release checks (no installer/one_click_update_e2e.py)")
    c.finish()
    sys.exit(0)
sys.path.insert(0, os.path.join(ROOT, "installer"))
import one_click_update_e2e as e2e
d = tempfile.mkdtemp(prefix="aoc_updsrc_")
exe = os.path.join(d, "AOC-Setup-1.0.9.exe")
open(exe, "wb").write(b"dummy installer bytes")
base, hits = e2e.start_server(exe, "1.0.9")
_, prefix, _ = consts(src_fn(base))
plan_ns = exec_functions(["_WHATS_NEW_MAX_ITEMS", "_parse_version", "_release_sha256", "_release_whats_new", "_update_plan"],
                         {"re": re, "AOC_RELEASE_DL_PREFIX": prefix})
rel = json.loads(urllib.request.urlopen(base + "releases/latest", timeout=5).read())
plan = plan_ns["_update_plan"]("1.0.8", rel)
c.check("the stand-in release is a valid one-click plan", plan.get("ok") is True and plan["version"] == "1.0.9")
c.check("plan carries the served file's SHA-256", plan.get("sha256") == hashlib.sha256(b"dummy installer bytes").hexdigest())
c.check("plan carries the What's new items", plan.get("notes") == e2e.ITEMS)
c.check("download URL is served", urllib.request.urlopen(plan["url"], timeout=5).read() == b"dummy installer bytes")
c.check("tags/v<ver> answers too (What's new after a manual install)",
        json.loads(urllib.request.urlopen(base + "releases/tags/v1.0.9", timeout=5).read())["tag_name"] == "v1.0.9")
c.check("same release under the GitHub prefix is refused (URL must match the source)",
        exec_functions(["_WHATS_NEW_MAX_ITEMS", "_parse_version", "_release_sha256", "_release_whats_new", "_update_plan"],
                       {"re": re, "AOC_RELEASE_DL_PREFIX": consts("")[1]})["_update_plan"]("1.0.8", rel).get("ok") is False)
rel_nosha = dict(rel, body=rel["body"].replace("SHA-256", "Hash"))
c.check("no SHA-256 in the notes -> refused, override or not", plan_ns["_update_plan"]("1.0.8", rel_nosha).get("ok") is False)
c.finish()
