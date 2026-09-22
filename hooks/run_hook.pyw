import sys, os, subprocess, tempfile

# Deployment template: PYTHONW is filled in by setup.py when it copies this
# file into ~/.claude/hooks/. HOOK is derived from this file's own deployed
# location, so it doesn't need templating.
PYTHONW = "__PYTHONW__"
HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aoc_hook.py")

data = b""
try:
    if sys.stdin and hasattr(sys.stdin, "buffer"):
        data = sys.stdin.buffer.read()
except Exception:
    pass
if not data:
    try:
        data = os.read(0, 1 << 20)
    except Exception:
        pass

if not data:
    sys.exit(0)

tmp = os.path.join(tempfile.gettempdir(), f"aoc_hook_{os.getpid()}.json")
try:
    with open(tmp, "wb") as f:
        f.write(data)

    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0

    p = subprocess.Popen(
        [PYTHONW, HOOK, "--file", tmp],
        startupinfo=si,
        creationflags=0x08000000,  # CREATE_NO_WINDOW
    )
    if p:
        try:
            p.wait(timeout=25)
        except subprocess.TimeoutExpired:
            pass
finally:
    try:
        os.remove(tmp)
    except Exception:
        pass
