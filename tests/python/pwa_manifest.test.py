"""Tests _build_manifest, extracted straight from monitor.py. This is the
PWA manifest's token-carrying logic: a tunneled/remote session's ?token=
must be re-embedded into every URL the manifest points at, or an
installed PWA icon 401s the moment it's opened."""
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ns = exec_functions(["_build_manifest"])
_build_manifest = ns["_build_manifest"]

c = Checker()

# 1. no token (typical localhost case) -> plain URLs, no query string
plain = _build_manifest("")
c.check("no token -> start_url has no query string", plain["start_url"] == "/")
c.check("no token -> icon src has no query string", plain["icons"][0]["src"] == "/icon.svg")

# 2. a token is carried into every URL the manifest points at
tokened = _build_manifest("abc123")
c.check("token carried into start_url", tokened["start_url"] == "/?token=abc123")
c.check("token carried into icon src", tokened["icons"][0]["src"] == "/icon.svg?token=abc123")

# 3. structural shape stays correct regardless of token
c.check("name present", tokened["name"] == "AOC — Agent Operations Center")
c.check("short_name present", tokened["short_name"] == "AOC")
c.check("display is standalone (installable, not just a bookmark)", tokened["display"] == "standalone")
c.check("exactly one icon entry, sized 'any' (SVG scales)", len(tokened["icons"]) == 1 and tokened["icons"][0]["sizes"] == "any")
c.check("icon type is svg", tokened["icons"][0]["type"] == "image/svg+xml")

c.finish()
