"""Tests _ws_key, extracted straight from monitor.py. Had zero coverage
until now despite being the entire WebSocket handshake -- if this ever
computed the wrong Sec-WebSocket-Accept value, every browser's WebSocket
client would refuse the handshake and both the live /events feed and the
terminal WebSocket would silently stop working.

Verified against the canonical worked example from RFC 6455 section 1.3
(the WebSocket protocol spec itself), not a value derived by re-deriving
the same formula the function itself uses -- an independent, authoritative
expected value is what actually catches a broken implementation here."""
import sys, os, hashlib, base64

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ns = exec_functions(["_ws_key"], {"_hashlib": hashlib, "_b64": base64})
_ws_key = ns["_ws_key"]

c = Checker()

# RFC 6455 section 1.3's own worked example.
rfc_key = "dGhlIHNhbXBsZSBub25jZQ=="
rfc_expected_accept = "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
c.check("matches the RFC 6455 section 1.3 canonical worked example", _ws_key(rfc_key) == rfc_expected_accept)

# Different input -> different output (not a constant/broken no-op).
c.check("different keys produce different accept values", _ws_key("anotherRandomKeyValue==") != rfc_expected_accept)

# Deterministic: same input always produces the same output.
c.check("same key produces the same accept value every time", _ws_key(rfc_key) == _ws_key(rfc_key))

# Output is always valid base64 (a SHA-1 digest is always 20 bytes -> 28
# base64 chars with one '=' padding char).
out = _ws_key("someOtherClientKey123==")
c.check("output is well-formed base64 of the expected SHA-1-digest length", len(out) == 28 and out.endswith('='))

c.finish()
