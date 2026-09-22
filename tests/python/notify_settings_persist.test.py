"""Tests _load_notify_settings/_save_notify_settings, extracted straight
from monitor.py -- specifically the snitch_url field's default value and
backward compatibility with settings files written before that field
existed. Runs against a scratch file, never the real
aoc_notify_settings.json."""
import sys, os, json, shutil, tempfile, threading

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_notify_persist_")
NOTIFY_SETTINGS_FILE = os.path.join(SCRATCH, "aoc_notify_settings.json")

ns = exec_functions(["_load_notify_settings", "_save_notify_settings", "_load_json_file"], {
    "json": json, "os": os,
    "NOTIFY_SETTINGS_FILE": NOTIFY_SETTINGS_FILE,
    "_notify_settings": {"quiet_start": "", "quiet_end": "", "muted_projects": [], "snitch_url": ""},
    "_notify_settings_lock": threading.Lock(),
})

c = Checker()

c.check("default snitch_url is empty string", ns["_notify_settings"]["snitch_url"] == "")

ns["_save_notify_settings"]({
    "quiet_start": "22:00", "quiet_end": "07:00",
    "muted_projects": ["AOC"], "snitch_url": "https://hc-ping.com/abc123",
})
c.check("save persists snitch_url in-memory", ns["_notify_settings"]["snitch_url"] == "https://hc-ping.com/abc123")

with open(NOTIFY_SETTINGS_FILE, encoding="utf-8") as f:
    on_disk = json.load(f)
c.check("save persists snitch_url to disk", on_disk.get("snitch_url") == "https://hc-ping.com/abc123")

# A settings file written before snitch_url existed shouldn't crash the load.
with open(NOTIFY_SETTINGS_FILE, "w", encoding="utf-8") as f:
    json.dump({"quiet_start": "", "quiet_end": "", "muted_projects": []}, f)
ns["_notify_settings"] = {"quiet_start": "", "quiet_end": "", "muted_projects": [], "snitch_url": ""}
ns["_load_notify_settings"]()
c.check("loading an old file (no snitch_url key) doesn't crash, defaults to empty",
         ns["_notify_settings"].get("snitch_url") == "")

shutil.rmtree(SCRATCH, ignore_errors=True)
c.finish()
