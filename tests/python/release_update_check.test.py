"""Tests the installer-build update check (_read_installed_version,
_parse_version, _release_update_status, _check_release_once), extracted
straight from monitor.py. _check_release_once runs against a local HTTP
server standing in for GitHub's releases/latest endpoint."""
import sys, os, json, time, threading, tempfile, shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
SCRATCH = tempfile.mkdtemp(prefix="aoc_test_release_")

EXE = "https://github.com/deezeed/aoc-agent-monitoring-public/releases/download/v1.0.200/AOC-Setup-1.0.200.exe"
HTML = "https://github.com/deezeed/aoc-agent-monitoring-public/releases/tag/v1.0.200"
FALLBACK = "https://github.com/deezeed/aoc-agent-monitoring-public/releases/latest"

try:
    ns = exec_functions(
        ["_read_installed_version", "_parse_version", "_release_update_status", "_check_release_once"],
        {"os": os, "json": json, "time": time})
    read_version = ns["_read_installed_version"]
    parse = ns["_parse_version"]
    status = ns["_release_update_status"]

    # 1. VERSION file
    vf = os.path.join(SCRATCH, "VERSION")
    c.check("no VERSION file -> '' (git checkout)", read_version(vf) == "")
    with open(vf, "w", encoding="utf-8") as f:
        f.write("1.0.150\r\n")
    c.check("VERSION read and stripped", read_version(vf) == "1.0.150")

    # 2. version parsing -- numeric, not string, comparison
    c.check("v-prefixed tag parses", parse("v1.0.123") == (1, 0, 123))
    c.check("plain version parses", parse("1.0.9") == (1, 0, 9))
    c.check("1.0.10 > 1.0.9 (numeric)", parse("1.0.10") > parse("1.0.9"))
    c.check("garbage -> None", parse("latest") is None and parse("") is None and parse(None) is None)
    c.check("pre-release suffix -> None", parse("v1.0.3-beta") is None)

    # 3. newer release with an .exe asset -> stale, links the installer
    rel = {"tag_name": "v1.0.200", "html_url": HTML,
           "assets": [{"browser_download_url": EXE.replace(".exe", ".zip")},
                      {"browser_download_url": EXE}]}
    s = status("1.0.150", rel, 123.0)
    c.check("newer release -> stale", s["stale"] is True)
    c.check("links the .exe asset", s["url"] == EXE)
    c.check("latest has no v prefix", s["latest"] == "1.0.200" and s["installed"] == "1.0.150")
    c.check("mode is release", s["mode"] == "release" and s["checked_at"] == 123.0)

    # 4. same / older release -> not stale
    c.check("same version -> not stale", status("1.0.200", rel, 0)["stale"] is False)
    c.check("installed newer than release -> not stale", status("1.0.300", rel, 0)["stale"] is False)

    # 5. no .exe asset -> release page; unparseable tag -> not stale
    s = status("1.0.1", {"tag_name": "v1.0.200", "html_url": HTML, "assets": []}, 0)
    c.check("no exe asset -> html_url", s["url"] == HTML and s["stale"] is True)
    c.check("unparseable tag -> not stale", status("1.0.1", {"tag_name": "nightly"}, 0)["stale"] is False)

    # 6. only github.com https URLs reach the UI
    evil = {"tag_name": "v9.9.9", "html_url": "javascript:alert(1)",
            "assets": [{"browser_download_url": "https://evil.example/AOC-Setup.exe"}]}
    c.check("non-github URL replaced by releases/latest", status("1.0.1", evil, 0)["url"] == FALLBACK)

    # 7. _check_release_once over HTTP
    seen = {}
    class FakeGitHub(BaseHTTPRequestHandler):
        def do_GET(self):
            seen["ua"] = self.headers.get("User-Agent")
            seen["accept"] = self.headers.get("Accept")
            body = json.dumps(rel).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *a):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    ns["AOC_RELEASES_API"] = f"http://127.0.0.1:{server.server_address[1]}/releases/latest"
    s = ns["_check_release_once"]("1.0.150")
    c.check("HTTP check -> stale with installer URL", s["stale"] is True and s["url"] == EXE)
    c.check("sends a User-Agent naming the version", seen.get("ua") == "AOC/1.0.150")
    c.check("asks for the GitHub JSON media type", seen.get("accept") == "application/vnd.github+json")
    server.shutdown()

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
