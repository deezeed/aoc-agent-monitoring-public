"""Who on the network may connect, and the token out of the address bar
(audit FIND-005).

AOC listens on every interface (phone, Remote Machines, invited people), but
without a token or a person another device could only get a 401 -- now its
connection is closed unread (_Server.verify_request -> _connection_allowed),
and opens by itself once there is a token, a person or a waiting invite.
The page drops ?token= from the address bar (kept in localStorage for a
refresh: `/` without a token answers with _relogin_page), and every answer
says Referrer-Policy: no-referrer. Unit checks, then a real preview."""
import sys, os, re, socket, subprocess, tempfile, time, shutil, urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions, read_monitor_source, read_web_source, MONITOR_PATH
from lib.check import Checker

c = Checker()
loaded = []
ns = exec_functions(["_lan_cache", "_lan_open", "_connection_allowed", "_relogin_page"],
                    {"time": time, "_auth_token": None, "_users_load": lambda: loaded.append(1) or {"users": [], "invites": []}})
lo = ns["_lan_open"]
c.check("no token, nobody -> closed to other devices", lo(None, {"users": [], "invites": []}, now=100) is False)
c.check("a token -> open", lo("t" * 32, {}, now=100) is True)
c.check("an active person -> open; only disabled ones -> closed",
        lo(None, {"users": [{"id": "a"}]}, now=100) and not lo(None, {"users": [{"id": "a", "disabled": True}]}, now=100))
c.check("an invite still waiting -> open (its link is opened on the phone); used / expired -> closed",
        lo(None, {"invites": [{"expires": 200}]}, now=100)
        and not lo(None, {"invites": [{"expires": 200, "used_by": "a"}]}, now=100)
        and not lo(None, {"invites": [{"expires": 50}]}, now=100))

ca = ns["_connection_allowed"]
c.check("this PC always connects", ca("127.0.0.1", now=1000) and ca("127.0.0.5", now=1000) and ca("::1", now=1000)
        and not loaded)
c.check("another device: refused while closed", ca("192.168.1.20", now=1000) is False and len(loaded) == 1)
ns["_auth_token"] = "t" * 32
c.check("people.json read at most every 5 s (a connection storm stays cheap)",
        ca("192.168.1.20", now=1003) is False and len(loaded) == 1)
c.check("... then a new token opens it", ca("192.168.1.20", now=1006) is True and len(loaded) == 2)

page = ns["_relogin_page"]()
c.check("relogin page: comes back with the stored token, only if the URL has none",
        "localStorage.getItem('aoc_token')" in page and "location.replace('/?token='" in page and "has('token')" in page)
c.check("relogin page: a token that fails is forgotten, not retried in a loop",
        "localStorage.removeItem('aoc_token')" in page and "no longer works" in page)

src = read_monitor_source()
c.check("the server checks every new connection",
        re.search(r"def verify_request\(self, request, client_address\):.*?_connection_allowed\(client_address\[0\]\)", src, re.S))
c.check("a broken check fails open (the token check still guards every request)",
        re.search(r"_connection_allowed\(client_address\[0\]\)\s+except Exception as e:.*?return True", src, re.S))
web = read_web_source()
c.check("the page drops ?token= from the address bar and keeps it for a refresh",
        "searchParams.delete('token')" in web and "history.replaceState" in web and "localStorage.setItem('aoc_token'" in web)

# ── a real preview ──
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
work = tempfile.mkdtemp(prefix="aoc_lan_")
# run from a copy: its token file (next to monitor.py) is then surely absent -- the closed case
app = os.path.join(work, "app")
os.makedirs(app)
shutil.copy(MONITOR_PATH, app)
for d in ("server", "web"):
    shutil.copytree(os.path.join(os.path.dirname(MONITOR_PATH), d), os.path.join(app, d))
env = {**os.environ, "AOC_PORT": str(port), "AOC_DATA_DIR": os.path.join(work, "data")}
proc = subprocess.Popen([sys.executable, os.path.join(app, "monitor.py"), "--headless"], env=env, cwd=app,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def get(path, host="127.0.0.1", headers=None):
    req = urllib.request.Request(f"http://{host}:{port}{path}", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")


def lan_ip():
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        u.connect(("10.255.255.255", 1))
        return u.getsockname()[0]
    except OSError:
        return ""
    finally:
        u.close()


try:
    up = False
    for _ in range(120):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=2).read()
            up = True
            break
        except Exception:
            if proc.poll() is not None:
                break
            time.sleep(0.5)
    c.check("preview up", up)
    st, hdr, _ = get("/status")
    c.check("every answer: Referrer-Policy no-referrer", st == 200 and hdr.get("Referrer-Policy") == "no-referrer")
    st, hdr, body = get("/", headers={"Origin": "http://evil.example"})
    c.check("`/` without a token: the relogin page (401, HTML)", st == 401 and "aoc_token" in body
            and hdr.get("Content-Type", "").startswith("text/html"))
    st, _, body = get("/status", headers={"Origin": "http://evil.example"})
    c.check("other routes without a token: plain 401 as before", st == 401 and "aoc_token" not in body)
    ip = lan_ip()
    if ip and not ip.startswith("127."):
        try:
            st = get("/status", host=ip)[0]
        except Exception as e:   # closed unread: reset / empty reply
            st = type(e).__name__
        c.check(f"from this PC's LAN address, no token / person: closed before a byte is read ({st})",
                st not in (200, 401))
    else:
        c.check("no LAN address on this machine -- LAN part skipped", True)
finally:
    proc.kill()
    proc.wait(10)
    shutil.rmtree(work, ignore_errors=True)
c.finish()
