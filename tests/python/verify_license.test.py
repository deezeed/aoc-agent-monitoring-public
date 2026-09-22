"""Tests the license (Free/Pro) module, extracted straight from monitor.py:
_verify_license, _license_info, _is_pro, and the load/save/delete lifecycle.
Uses a real Ed25519 keypair generated fresh for this test run (not the
embedded production public key -- _AOC_LICENSE_PUBKEY is swapped for a
matching test key via extra_globals) so the test never needs the real
private key, which must never exist in this repo."""
import sys, os, json, base64, tempfile, threading

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

_priv = Ed25519PrivateKey.generate()
_pub_bytes = _priv.public_key().public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)

_tmpdir = tempfile.mkdtemp(prefix="aoc_test_license_")

ns = exec_functions(
    ["_load_json_file", "_AOC_LICENSE_PUBKEY", "LICENSE_FILE", "_license_lock", "_license",
     "_load_license", "_save_license", "_delete_license", "_verify_license", "_license_info", "_is_pro"],
    {"os": os, "json": json, "base64": base64, "threading": threading, "AOC_DIR": _tmpdir},
)
# _verify_license looks up _AOC_LICENSE_PUBKEY in its __globals__ (this same
# `ns` dict) at call time, not at def time -- so swapping it for our
# freshly-generated test public key here means the function never needs the
# real embedded production key (whose private half must never exist in
# this repo) to be fully exercised
ns["_AOC_LICENSE_PUBKEY"] = _pub_bytes

_verify_license = ns["_verify_license"]
_load_license = ns["_load_license"]
_save_license = ns["_save_license"]
_delete_license = ns["_delete_license"]
_license_info = ns["_license_info"]
_is_pro = ns["_is_pro"]


def make_key(payload: dict, priv=_priv) -> str:
    body = json.dumps(payload).encode()
    sig = priv.sign(body)
    return ("AOC-PRO-"
            + base64.urlsafe_b64encode(body).decode().rstrip("=")
            + "." + base64.urlsafe_b64encode(sig).decode().rstrip("="))


c = Checker()

valid_key = make_key({"lic": "test-uuid", "email": "buyer@example.com", "tier": "pro", "issued": "2026-09-15"})

# 1. A validly-signed key round-trips to its payload
payload = _verify_license(valid_key)
c.check("valid key verifies and returns its payload", payload is not None and payload["email"] == "buyer@example.com")

# 2. Tampering with the payload (while leaving the signature alone) is rejected
head, sig_part = valid_key.rsplit(".", 1)
tampered_payload_key = head[:-4] + "AAAA" + "." + sig_part
c.check("tampered payload is rejected", _verify_license(tampered_payload_key) is None)

# 3. Tampering with the signature is rejected
tampered_sig_key = head + "." + ("A" * len(sig_part))
c.check("tampered signature is rejected", _verify_license(tampered_sig_key) is None)

# 4. A key signed by a DIFFERENT private key (not matching the embedded
# public key) is rejected -- this is the actual "can't forge a key" property
other_priv = Ed25519PrivateKey.generate()
forged_key = make_key({"lic": "forged", "email": "attacker@example.com", "tier": "pro", "issued": "2026-09-15"}, priv=other_priv)
c.check("a key signed by a different keypair is rejected", _verify_license(forged_key) is None)

# 5. Garbage / malformed input never raises, just returns None
c.check("garbage string returns None, doesn't raise", _verify_license("not-a-license-key") is None)
c.check("empty string returns None", _verify_license("") is None)
c.check("missing AOC-PRO- prefix returns None", _verify_license("PRO-abc.def") is None)
c.check("missing the dot separator returns None", _verify_license("AOC-PRO-abcdef") is None)

# 6. A validly-signed key with the wrong tier is rejected (forward-compat:
# a future "trial"/"free" tier key must not unlock Pro)
wrong_tier_key = make_key({"lic": "x", "email": "x@x.com", "tier": "trial", "issued": "2026-09-15"})
c.check("a validly-signed non-pro-tier key is rejected", _verify_license(wrong_tier_key) is None)

# 7. Full save/load/delete lifecycle via the real file-backed functions
c.check("no license active before any key is saved", _is_pro() is False)
c.check("license_info reports free tier with nothing active", _license_info()["tier"] == "free")

_save_license(valid_key)
c.check("is_pro() true immediately after saving a valid key", _is_pro() is True)
info = _license_info()
c.check("license_info reports pro tier after activation", info["tier"] == "pro")
c.check("license_info surfaces the email from the payload", info["email"] == "buyer@example.com")
c.check("aoc_license.json was actually written to disk", os.path.exists(os.path.join(_tmpdir, "aoc_license.json")))

_delete_license()
c.check("is_pro() false immediately after deactivation", _is_pro() is False)
c.check("aoc_license.json removed on deactivation", not os.path.exists(os.path.join(_tmpdir, "aoc_license.json")))

# 8. Saving an invalid key string directly (bypassing the route's own
# _verify_license-before-save check) still leaves _is_pro() false -- the
# module never trusts LICENSE_FILE's content without re-verifying it
_save_license("AOC-PRO-garbage.garbage")
c.check("a garbage key written to disk is still treated as inactive on load", _is_pro() is False)
_delete_license()

c.finish()
