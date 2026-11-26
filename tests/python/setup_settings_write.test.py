"""setup.py writes ~/.claude/settings.json safely (audit FIND-024/025):
a backup first (last 5 kept), then tmp + os.replace, keeping the file's own
line endings and final newline; uninstall goes on when settings.json can't be
read. _save_settings is extracted straight from setup.py and runs on a temp
file only -- never the real ~/.claude."""
import glob
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SETUP_PY = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "setup.py"))
c = Checker()
tmp = tempfile.mkdtemp(prefix="aoc_setup_settings_")
path = os.path.join(tmp, ".claude", "settings.json")
ns = exec_functions(["SETTINGS_BACKUPS_KEPT", "_save_settings"],
                    {"os": os, "json": json, "SETTINGS_FILE": os.path.join(tmp, "never-used.json")}, path=SETUP_PY)
save = ns["_save_settings"]

# a fresh install: no file, no backup
save({"a": 1}, path=path, stamp="0")
c.check("no settings.json yet -> written, no backup", json.load(open(path)) == {"a": 1}
        and not glob.glob(path + ".bak-*"))

# the user's own file, CRLF with a final newline
with open(path, "wb") as f:
    f.write(b'{\r\n  "theme": "dark"\r\n}\r\n')
save({"theme": "dark", "hooks": {}}, path=path, stamp="1")
raw = open(path, "rb").read()
c.check("written: new content", json.loads(raw) == {"theme": "dark", "hooks": {}})
c.check("its CRLF and final newline kept", b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"") and raw.endswith(b"\r\n"))
c.check("the previous file backed up byte for byte",
        open(path + ".bak-aoc-setup-1", "rb").read() == b'{\r\n  "theme": "dark"\r\n}\r\n')
c.check("no temp file left", not os.path.exists(path + ".aoc-setup-tmp"))

# LF without final newline stays that way
with open(path, "wb") as f:
    f.write(b'{\n  "x": 1\n}')
save({"x": 2}, path=path, stamp="2")
raw = open(path, "rb").read()
c.check("LF, no final newline -> kept", b"\r\n" not in raw and not raw.endswith(b"\n") and json.loads(raw) == {"x": 2})

# only the newest 5 backups stay
for i in range(3, 10):
    save({"n": i}, path=path, stamp=str(i))
baks = sorted(os.path.basename(p) for p in glob.glob(path + ".bak-aoc-setup-*"))
c.check("only the last 5 backups kept", baks == ["settings.json.bak-aoc-setup-%d" % i for i in range(5, 10)])

# a failure while writing leaves the old file whole
before = open(path, "rb").read()
try:
    save({"bad": object()}, path=path, stamp="z")   # not JSON-serializable: fails before the swap
except TypeError:
    pass
c.check("a write that fails leaves settings.json as it was", open(path, "rb").read() == before)

# uninstall: an unreadable settings.json no longer stops it (FIND-025)
src = open(SETUP_PY, encoding="utf-8").read()
body = src[src.index("def uninstall()"):src.index("def main()")]
c.check("uninstall reads settings.json inside try and goes on",
        "try:\n        settings = _load_settings()" in body.replace("\r\n", "\n")
        and "settings = None" in body and "if settings is not None" in body)
c.check("uninstall still removes tasks and stops processes after that", body.index("settings = None") < body.index("schtasks")
        and "stop_aoc_processes()" in body)

c.finish()
