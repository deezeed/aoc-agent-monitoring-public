"""AOC_PORT / AOC_DATA_DIR / preview mode: _env_port (pure), the three
notification senders bail out in a preview, and a real second instance
started with AOC_PORT + AOC_DATA_DIR serves /status on its own port and
keeps its data in its own directory -- the way forks and previews run a
branch without touching the live AOC on 5151."""
import sys, os, re, socket, subprocess, tempfile, time, urllib.request, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions, read_monitor_source, MONITOR_PATH
from lib.check import Checker

c = Checker()
envp = exec_functions(["_env_port"], {})["_env_port"]
c.check("unset -> 5151", envp(None) == 5151)
c.check("number string", envp(" 5240 ") == 5240)
c.check("garbage -> 5151", envp("abc") == 5151 and envp("") == 5151)
c.check("privileged / out of range -> 5151", envp("80") == 5151 and envp("70000") == 5151)

src = read_monitor_source()
c.check("PORT comes from AOC_PORT", 'PORT = _env_port(os.environ.get("AOC_PORT"))' in src)
c.check("any port but 5151 is a preview", 'AOC_PREVIEW = PORT != 5151 or os.environ.get("AOC_PREVIEW") == "1"' in src)
c.check("a preview has its own instance lock", '"monitor.pid" if PORT == 5151 else f"monitor-{PORT}.pid"' in src)
for fn in ("_win_toast", "_fire_webhook", "_push_send_one"):
    body = src[src.index(f"def {fn}("):]
    body = body[:body.index("\ndef ", 10)]
    c.check(f"{fn} is silent in a preview", re.search(r"\n    if AOC_PREVIEW:\n        return", body) is not None)

# a real second instance
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
data = tempfile.mkdtemp(prefix="aoc_prev_")
env = {**os.environ, "AOC_PORT": str(port), "AOC_DATA_DIR": data}
proc = subprocess.Popen([sys.executable, MONITOR_PATH, "--headless"], env=env, cwd=os.path.dirname(MONITOR_PATH),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
up = False
try:
    for _ in range(120):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=2).read()
            up = True
            break
        except Exception:
            if proc.poll() is not None:
                break
            time.sleep(0.5)
    c.check("second instance serves /status on AOC_PORT", up)
    c.check("its data lives in AOC_DATA_DIR", os.path.exists(os.path.join(data, "history.db")))
finally:
    proc.kill()
    proc.wait(10)
    shutil.rmtree(data, ignore_errors=True)
    try:
        os.remove(os.path.join(os.path.dirname(MONITOR_PATH), f"monitor-{port}.pid"))  # killed, so never released
    except OSError:
        pass
c.finish()
