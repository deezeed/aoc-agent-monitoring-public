"""hooks/aoc_statusline.py saves each session's context_window fill and
prompt cache state to %LOCALAPPDATA%\\AOC\\context\\<session_id>.json (run
for real, JSON on stdin, LOCALAPPDATA pointed at a scratch dir), shows
'ctx N%' in its own line, skips unusable input and unsafe session ids."""
import sys, os, json, shutil, subprocess, tempfile, time

sys.path.insert(0, os.path.dirname(__file__))
from lib.check import Checker

c = Checker()
ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
work = tempfile.mkdtemp(prefix="aoc_slctx_")
hooks_dir = os.path.join(work, "hooks")
os.makedirs(hooks_dir)
script = os.path.join(hooks_dir, "aoc_statusline.py")
shutil.copy(os.path.join(ROOT, "hooks", "aoc_statusline.py"), script)
appdata = os.path.join(work, "appdata")
ctx_dir = os.path.join(appdata, "AOC", "context")
env = dict(os.environ, LOCALAPPDATA=appdata)

exp = int(time.time()) + 1800
payload = {
    "session_id": "abc-123",
    "model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"},
    "workspace": {"current_dir": "C:\\work\\proj"},
    "context_window": {"total_input_tokens": 412000, "total_output_tokens": 900,
                       "context_window_size": 1000000, "used_percentage": 41.2,
                       "remaining_percentage": 58.8, "current_usage": None},
    "prompt_cache": {"warm": True, "ttl": "1h", "expires_at": exp, "recache_tokens_if_cold": 405000},
}


def run(data, raw=None):
    inp = raw if raw is not None else json.dumps(data).encode("utf-8")
    r = subprocess.run([sys.executable, script], input=inp, capture_output=True, env=env, timeout=30)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def saved(sid="abc-123"):
    with open(os.path.join(ctx_dir, sid + ".json"), encoding="utf-8") as f:
        return json.load(f)


code, out, err = run(payload)
c.check("exit 0, no stderr", code == 0 and not err.strip())
c.check("line shows ctx %", out == "Opus 5.5 · proj · ctx 41%")
s = saved()
c.check("file: fill + window + tokens", s["used_percentage"] == 41.2 and s["window"] == 1000000 and s["input_tokens"] == 412000)
c.check("file: cache expiry, ttl, recache tokens", s["cache_expires_at"] == exp and s["cache_ttl"] == "1h" and s["recache_tokens"] == 405000)
c.check("file: updated_at", abs(s["updated_at"] - time.time()) < 30)

path = os.path.join(ctx_dir, "abc-123.json")
mtime = os.path.getmtime(path)
time.sleep(0.05)
run(payload)
c.check("unchanged within a minute -> not rewritten", os.path.getmtime(path) == mtime)
payload["context_window"]["used_percentage"] = 55.0
run(payload)
c.check("changed fill -> rewritten", saved()["used_percentage"] == 55.0)

p2 = dict(payload, session_id="no-cache")
del p2["prompt_cache"]
run(p2)
s2 = saved("no-cache")
c.check("no prompt_cache -> cache fields None", s2["cache_expires_at"] is None and s2["cache_ttl"] is None and s2["recache_tokens"] is None)

p3 = dict(payload, session_id="early", context_window={"used_percentage": None, "context_window_size": 200000})
code, out, _ = run(p3)
c.check("null used_percentage (early session) -> no file, no ctx in line",
        not os.path.exists(os.path.join(ctx_dir, "early.json")) and "ctx" not in out and code == 0)

p4 = dict(payload, session_id="..\\..\\evil")
run(p4)
c.check("unsafe session id -> nothing written outside", sorted(os.listdir(ctx_dir)) == ["abc-123.json", "no-cache.json"]
        and not os.path.exists(os.path.join(appdata, "evil.json")))

p5 = dict(payload, session_id="bad-ttl", prompt_cache={"ttl": "9y", "expires_at": "soon"})
run(p5)
s5 = saved("bad-ttl")
c.check("garbage cache fields -> None", s5["cache_ttl"] is None and s5["cache_expires_at"] is None)

code, out, err = run(None, raw=json.dumps({"session_id": "x", "context_window": "nope"}).encode())
c.check("context_window not an object -> exit 0, no traceback", code == 0 and "Traceback" not in err)

shutil.rmtree(work, ignore_errors=True)
c.finish()
