"""Phone push (Web Push): monitor.py's _webpush_encrypt against the RFC 8291
appendix A test vector (byte for byte) and a receiver-side decrypt of a
random message; _vapid_auth's ES256 JWT verifies with the public key and
has the right audience; the endpoint allowlist (no SSRF), subscription
sanitizing, the away/always/off rule, subscribe/unsubscribe files, and
_push_send_one's request (headers, body) against a fake push service."""
import sys, os, json, base64, shutil, tempfile, threading, time

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

c = Checker()
data_dir = tempfile.mkdtemp(prefix="aoc_push_")
ns = exec_functions(
    ["_load_json_file", "PUSH_VAPID_FILE", "PUSH_SUBS_FILE", "PUSH_SETTINGS_FILE", "_PUSH_MODES", "_PUSH_HOSTS",
     "_PUSH_MAX_SUBS", "_push_lock", "_b64u", "_b64u_dec", "_webpush_encrypt", "_vapid_keys", "_vapid_auth",
     "_push_endpoint_ok", "_push_sanitize_sub", "_push_subs", "_push_save_subs", "_push_subscribe",
     "_push_unsubscribe", "_load_push_settings", "_save_push_settings", "_push_should_send", "_push_send_one"],
    {"os": os, "json": json, "time": time, "base64": base64, "threading": threading,
     "AOC_DATA_DIR": os.path.join(data_dir, "AOC")})
d = ns["_b64u_dec"]
J = lambda s: d(s.replace(" ", ""))

# ── RFC 8291 appendix A ──
as_priv = ec.derive_private_key(int.from_bytes(J("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1())
ua_public = J("BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcx aOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4")
auth = J("BTBZMqHH6r4Tts7J_aSIgg")
salt = J("DGv6ra1nlYgDCS1FRnbzlw")
plain = J("V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24")
body = ns["_webpush_encrypt"](plain, ua_public, auth, as_private=as_priv, salt=salt)
header = J("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z 9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml mlMoZIIgDll6e3vCYLocInmYWAmS6Tlz AC8wEqKK6PBru3jl7A8")
ct = J("8pfeW0KbunFT06SuDKoJH9Ql87S1QUrd irN6GcG7sFz1y1sqLgVi1VhjVkHsUoEs bI_0LpXMuGvnzQ")
c.check("RFC 8291 header (86 octets)", body[:86] == header and len(header) == 86)
c.check("RFC 8291 ciphertext byte for byte", body[86:] == ct)


# ── receiver side: decrypt a random message we encrypted ──
def decrypt(body, ua_priv, auth):
    salt, idlen = body[:16], body[20]
    as_pub = body[21:21 + idlen]
    ua_pub = ua_priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = ua_priv.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    ikm = HKDF(hashes.SHA256(), 32, auth, b"WebPush: info\x00" + ua_pub + as_pub).derive(shared)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    return AESGCM(cek).decrypt(nonce, body[21 + idlen:], None)


ua_priv = ec.generate_private_key(ec.SECP256R1())
ua_pub = ua_priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
auth2 = os.urandom(16)
msg = json.dumps({"title": "🔐 AOC needs your OK", "body": "Bash: npm publish — čakám"}, ensure_ascii=False).encode()
b1 = ns["_webpush_encrypt"](msg, ua_pub, auth2)
c.check("round trip: device decrypts it (padding delimiter 0x02 last)", decrypt(b1, ua_priv, auth2) == msg + b"\x02")
c.check("fresh salt + key each message", ns["_webpush_encrypt"](msg, ua_pub, auth2)[:16] != b1[:16])

# ── VAPID ──
priv, pub = ns["_vapid_keys"]()
priv2, pub2 = ns["_vapid_keys"]()
c.check("VAPID key created once and kept", pub == pub2 and len(d(pub)) == 65)
hdr = ns["_vapid_auth"]("https://fcm.googleapis.com/fcm/send/abc", priv, pub, now=1000)
c.check("header shape: vapid t=..., k=<public key>", hdr.startswith("vapid t=") and hdr.endswith(", k=" + pub))
jwt = hdr[len("vapid t="):hdr.index(", k=")]
h64, c64, s64 = jwt.split(".")
claims = json.loads(d(c64))
c.check("JWT header ES256", json.loads(d(h64)) == {"typ": "JWT", "alg": "ES256"})
c.check("aud = push service origin, exp 12 h, sub https", claims["aud"] == "https://fcm.googleapis.com"
        and claims["exp"] == 1000 + 12 * 3600 and claims["sub"].startswith("https://"))
sig = d(s64)
pubkey = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), d(pub))
try:
    pubkey.verify(encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")),
                  f"{h64}.{c64}".encode(), ec.ECDSA(hashes.SHA256()))
    ok = len(sig) == 64
except Exception:
    ok = False
c.check("JWT signature (raw r||s) verifies with the public key", ok)

# ── endpoint allowlist ──
okep = ns["_push_endpoint_ok"]
c.check("known push services accepted", okep("https://fcm.googleapis.com/fcm/send/x") and okep("https://web.push.apple.com/Q")
        and okep("https://updates.push.services.mozilla.com/wpush/v2/x") and okep("https://wns2-par02p.notify.windows.com/w/?token=1"))
c.check("http, other hosts, look-alikes rejected", not okep("http://fcm.googleapis.com/x") and not okep("https://127.0.0.1/x")
        and not okep("https://fcm.googleapis.com.evil.com/x") and not okep("https://evilnotify.windows.com/x") and not okep(None))

# ── subscriptions ──
good = {"subscription": {"endpoint": "https://fcm.googleapis.com/fcm/send/dev1",
                         "keys": {"p256dh": ns["_b64u"](ua_pub), "auth": ns["_b64u"](auth2)}}, "label": "Android phone"}
c.check("bad subscription rejected", ns["_push_subscribe"]({"subscription": {"endpoint": "https://example.com/x"}})["ok"] is False)
c.check("short key rejected", ns["_push_sanitize_sub"]({"subscription": {**good["subscription"], "keys": {"p256dh": "AAAA", "auth": "AAAA"}}}) is None)
c.check("subscribe ok", ns["_push_subscribe"](good) == {"ok": True, "devices": 1})
c.check("same endpoint again -> replaced, not duplicated", ns["_push_subscribe"](good)["devices"] == 1)
c.check("stored with label", ns["_push_subs"]()[0]["label"] == "Android phone")
c.check("unsubscribe", ns["_push_unsubscribe"]("https://fcm.googleapis.com/fcm/send/dev1") and ns["_push_subs"]() == [])
c.check("unsubscribe unknown -> False", ns["_push_unsubscribe"]("nope") is False)

# ── when to send ──
sh = ns["_push_should_send"]
c.check("away mode: idle long enough -> send", sh("away", 400, 5, False) and not sh("away", 30, 5, False))
c.check("away mode: idle unknown -> no", not sh("away", None, 5, False))
c.check("urgent (held prompt) -> send even at the PC", sh("away", 0, 5, True))
c.check("always -> send", sh("always", 0, 5, False))
c.check("off -> never, even urgent", not sh("off", 9999, 5, True))
c.check("settings default away, garbage -> away", ns["_load_push_settings"]() == {"mode": "away"}
        and ns["_save_push_settings"]({"mode": "loud"}) == {"mode": "away"} and ns["_save_push_settings"]({"mode": "off"}) == {"mode": "off"})

# ── the request to the push service ──
seen = {}


class FakeResp:
    status = 201

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req, timeout=None):
    seen["url"], seen["headers"], seen["body"] = req.full_url, dict(req.header_items()), req.data
    return FakeResp()


sub = ns["_push_sanitize_sub"](good)
code = ns["_push_send_one"](sub, b'{"title":"t"}', urlopen=fake_urlopen)
hd = {k.lower(): v for k, v in seen["headers"].items()}
c.check("201 from the push service", code == 201 and seen["url"] == sub["endpoint"])
c.check("headers: vapid auth, aes128gcm, TTL, urgency", hd["authorization"].startswith("vapid t=") and hd["content-encoding"] == "aes128gcm"
        and hd["ttl"] == "3600" and hd["urgency"] == "high")
c.check("body decrypts on the device", decrypt(seen["body"], ua_priv, auth2) == b'{"title":"t"}\x02')

shutil.rmtree(data_dir, ignore_errors=True)
c.finish()
