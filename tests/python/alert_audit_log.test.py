"""Tests _log_alert_fired, extracted straight from monitor.py. Runs against
a scratch logs directory (never the real LOGS_DIR/ALERT_AUDIT_LOG), same
style as backup_restore.test.py's scratch-dir approach. This is the audit
trail for every webhook _fire_webhook actually dispatches -- previously
nothing durable recorded that at all (the in-browser notification history
panel is a plain in-memory array that resets on every reload)."""
import sys, os, json, tempfile, shutil
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_alert_audit_")
LOGS_DIR = SCRATCH
ALERT_AUDIT_LOG = os.path.join(SCRATCH, "alert_audit.log")

ns = exec_functions(["_append_audit_entry", "_log_alert_fired"], {
    "os": os, "json": json, "datetime": datetime,
    "LOGS_DIR": LOGS_DIR, "ALERT_AUDIT_LOG": ALERT_AUDIT_LOG,
})
_log_alert_fired = ns["_log_alert_fired"]

c = Checker()

try:
    def read_entries():
        with open(ALERT_AUDIT_LOG, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    # 1. agent-shaped payload (done/error/stuck)
    _log_alert_fired({"event": "error", "agent": {"id": "ag_1", "name": "Explore repo", "status": "error", "cost": 0.5}})
    entries = read_entries()
    c.check("agent-shaped payload: one line written", len(entries) == 1)
    c.check("agent-shaped payload: event recorded", entries[0]["event"] == "error")
    c.check("agent-shaped payload: target falls back to agent name", entries[0]["target"] == "Explore repo")
    c.check("agent-shaped payload: project empty (agent payloads carry no project)", entries[0]["project"] == "")
    c.check("agent-shaped payload: ts is a non-empty string", isinstance(entries[0]["ts"], str) and len(entries[0]["ts"]) > 0)

    # 2. session-shaped payload (waiting_nudge/cost_spike/burn_spike)
    _log_alert_fired({"event": "cost_spike", "session": {"id": "s1", "project": "AOC", "display_name": "AOC session", "cost": 5.0}})
    entries = read_entries()
    c.check("session-shaped payload: appended as a second line", len(entries) == 2)
    c.check("session-shaped payload: project recorded", entries[1]["project"] == "AOC")
    c.check("session-shaped payload: target uses display_name", entries[1]["target"] == "AOC session")

    # 3. summary-only payload (weekly_digest) -- neither agent nor session dict
    _log_alert_fired({"event": "weekly_digest", "summary": {"sessions": 10}})
    entries = read_entries()
    c.check("summary-only payload: still logs a line, doesn't crash", len(entries) == 3)
    c.check("summary-only payload: event recorded", entries[2]["event"] == "weekly_digest")
    c.check("summary-only payload: target/project empty, not a crash", entries[2]["target"] == "" and entries[2]["project"] == "")

    # 4. malformed payload -- missing 'event' key entirely
    _log_alert_fired({})
    entries = read_entries()
    c.check("empty payload: still appends a line, doesn't raise", len(entries) == 4)
    c.check("empty payload: event defaults to empty string", entries[3]["event"] == "")

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

c.finish()
