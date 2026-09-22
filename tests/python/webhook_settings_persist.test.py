"""Tests _sanitize_webhook_events and _save_webhook_settings, extracted
straight from monitor.py. Regression test for a real bug: the
/webhook_settings POST handler used to whitelist only done/error/stuck
when saving `events`, silently dropping burn_spike/weekly_digest/
waiting_nudge on every save even though the client always sends all six
and every background worker already reads them -- confirmed dead against
the real aoc_webhook_settings.json (it had never once contained those
three keys). Runs against a scratch file, never the real one."""
import sys, os, json, shutil, tempfile, threading

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ns = exec_functions(["_sanitize_webhook_events"])
_sanitize_webhook_events = ns["_sanitize_webhook_events"]

c = Checker()

# ── _sanitize_webhook_events ──

# 1. all eight keys present and true -> all pass through as True
full = _sanitize_webhook_events({
    "done": True, "error": True, "stuck": True,
    "burn_spike": True, "weekly_digest": True, "waiting_nudge": True, "cost_spike": True,
    "budget_alert": True,
})
c.check("all 8 known keys are present in the output", set(full.keys()) == {
    "done", "error", "stuck", "burn_spike", "weekly_digest", "waiting_nudge", "cost_spike", "budget_alert",
})
c.check("all 8 values pass through as True when explicitly set", all(full.values()))

# 2. THE BUG: an events dict shaped like the old client payload (all 6 keys,
# including the 3 that used to get silently dropped) must round-trip every key
old_style_payload = {
    "done": True, "error": True, "stuck": False,
    "burn_spike": True, "weekly_digest": True, "waiting_nudge": True,
}
sanitized = _sanitize_webhook_events(old_style_payload)
c.check("burn_spike is NOT dropped (the actual bug)", sanitized["burn_spike"] is True)
c.check("weekly_digest is NOT dropped (the actual bug)", sanitized["weekly_digest"] is True)
c.check("waiting_nudge is NOT dropped (the actual bug)", sanitized["waiting_nudge"] is True)

# 3. missing keys fall back to the documented defaults
defaults = _sanitize_webhook_events({})
c.check("missing done defaults to True", defaults["done"] is True)
c.check("missing error defaults to True", defaults["error"] is True)
c.check("missing stuck defaults to False", defaults["stuck"] is False)
c.check("missing burn_spike defaults to False", defaults["burn_spike"] is False)
c.check("missing weekly_digest defaults to False", defaults["weekly_digest"] is False)
c.check("missing waiting_nudge defaults to False", defaults["waiting_nudge"] is False)
c.check("missing cost_spike defaults to False", defaults["cost_spike"] is False)
c.check("missing budget_alert defaults to False", defaults["budget_alert"] is False)

# 4. non-bool truthy/falsy values are coerced to real booleans
coerced = _sanitize_webhook_events({"burn_spike": 1, "waiting_nudge": 0})
c.check("truthy non-bool coerced to True", coerced["burn_spike"] is True)
c.check("falsy non-bool coerced to False", coerced["waiting_nudge"] is False)

# ── _save_webhook_settings persistence, using the fixed sanitizer ──
SCRATCH = tempfile.mkdtemp(prefix="aoc_test_webhook_persist_")
WEBHOOK_SETTINGS_FILE = os.path.join(SCRATCH, "aoc_webhook_settings.json")

ns2 = exec_functions(["_save_webhook_settings"], {
    "json": json,
    "WEBHOOK_SETTINGS_FILE": WEBHOOK_SETTINGS_FILE,
    "_webhook_settings": {"url": "", "events": {}},
    "_webhook_settings_lock": threading.Lock(),
})
ns2["_save_webhook_settings"]({
    "url": "https://example.com/hook",
    "events": _sanitize_webhook_events({"burn_spike": True, "weekly_digest": True, "waiting_nudge": True, "done": True, "error": True, "stuck": False}),
})
with open(WEBHOOK_SETTINGS_FILE, encoding="utf-8") as f:
    on_disk = json.load(f)
c.check("saved file contains burn_spike:true (previously always lost)", on_disk["events"]["burn_spike"] is True)
c.check("saved file contains weekly_digest:true (previously always lost)", on_disk["events"]["weekly_digest"] is True)
c.check("saved file contains waiting_nudge:true (previously always lost)", on_disk["events"]["waiting_nudge"] is True)

shutil.rmtree(SCRATCH, ignore_errors=True)
c.finish()
