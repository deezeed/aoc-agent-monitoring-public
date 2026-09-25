"""Regression test for a startup failure that py_compile, imports and every
other test miss: Python 3.9 on Windows, running a file *as a script* with
no coding declaration, checks UTF-8 in 512-byte chunks of each line, so a
multi-byte character (✕, —, ⛔ ... the embedded dashboard is full of them)
straddling a chunk boundary on a long line kills startup with a bogus
"SyntaxError: Non-UTF-8 code". It happened for real on 2026-09-25: an
edit that lengthened a few dashboard lines put monitor.py into a watchdog
restart loop.

Reproduces the real path: each entry script is copied with an early
`raise SystemExit(0)` spliced in after its header and run as a script, so
Python compiles the whole file exactly as on startup but executes
nothing. On Linux (BUFSIZ 8192) it can't reproduce; CI runs this suite
on Windows."""
import sys, os, subprocess, tempfile

sys.path.insert(0, os.path.dirname(__file__))
from lib.check import Checker

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
ENTRY_SCRIPTS = ["monitor.py", "watchdog.py", "sentinel.py", "setup.py", os.path.join("hooks", "aoc_hook.py")]

c = Checker()
tmp = tempfile.mkdtemp()

for rel in ENTRY_SCRIPTS:
    with open(os.path.join(ROOT, rel), "rb") as f:
        lines = f.read().split(b"\n")
    # Keep a coding declaration (lines 1-2) where Python looks for it.
    head = 0
    while head < 2 and head < len(lines) and (lines[head].startswith(b"#!") or b"coding" in lines[head]):
        head += 1
    # ...and after any `from __future__` import, which must precede other code.
    future = [i for i, l in enumerate(lines) if l.startswith(b"from __future__ import")]
    if future:
        head = max(head, future[-1] + 1)
    patched = lines[:head] + [b"raise SystemExit(0)"] + lines[head:]
    path = os.path.join(tmp, os.path.basename(rel))
    with open(path, "wb") as f:
        f.write(b"\n".join(patched))
    r = subprocess.run([sys.executable, path], capture_output=True, text=True, encoding="utf-8", errors="replace")
    c.check(f"{rel} compiles when run as a script (exit {r.returncode}{': ' + r.stderr.strip().splitlines()[-1] if r.returncode else ''})",
            r.returncode == 0)

# The guard itself: monitor.py has long lines full of multi-byte characters,
# so it must keep its declaration even if today's lines happen to be safe.
with open(os.path.join(ROOT, "monitor.py"), "rb") as f:
    first_two = f.read(400).split(b"\n")[:2]
c.check("monitor.py keeps its coding declaration on line 1-2", any(b"coding: utf-8" in l for l in first_two))

c.finish()
