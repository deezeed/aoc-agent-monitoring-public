"""Tests watchdog.py's _alive() against a real local HTTP server: a slow
/status within STATUS_TIMEOUT counts as alive (a transient stall must not
trigger a restart), one past it doesn't, and a closed port fails fast.
Also pins the tolerance constants so they aren't tightened by accident.
Extracted straight from watchdog.py."""
import sys, os, time, threading, socket, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions, extract_functions
from lib.check import Checker

WATCHDOG_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "watchdog.py"))

c = Checker()

DELAY = [0.0]

class SlowStatus(BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(DELAY[0])
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")
    def log_message(self, *a):
        pass

class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # case 3's client hangs up on purpose before the reply

server = QuietServer(("127.0.0.1", 0), SlowStatus)
server.daemon_threads = True
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{server.server_address[1]}/status"

def alive_with(timeout):
    return exec_functions(["_alive"], {"urllib": urllib, "STATUS_URL": url,
                                       "STATUS_TIMEOUT": timeout}, path=WATCHDOG_PATH)["_alive"]

# 1. the real constants
consts = {}
exec("\n".join(extract_functions(["STATUS_TIMEOUT", "FAIL_THRESHOLD"], path=WATCHDOG_PATH).values()), consts)
c.check("STATUS_TIMEOUT is 10 s", consts["STATUS_TIMEOUT"] == 10)
c.check("FAIL_THRESHOLD is 3", consts["FAIL_THRESHOLD"] == 3)

# 2. slow but within the timeout -> alive (scaled down: 0.5 s reply, 1.5 s timeout)
DELAY[0] = 0.5
c.check("slow /status within timeout counts as alive", alive_with(1.5)() is True)

# 3. slower than the timeout -> not alive
DELAY[0] = 1.0
c.check("/status past the timeout counts as down", alive_with(0.3)() is False)

# 4. nothing listening -> not alive, and fast (no waiting out the timeout)
s = socket.socket(); s.bind(("127.0.0.1", 0)); dead_port = s.getsockname()[1]; s.close()
dead = exec_functions(["_alive"], {"urllib": urllib, "STATUS_URL": f"http://127.0.0.1:{dead_port}/status",
                                   "STATUS_TIMEOUT": 10}, path=WATCHDOG_PATH)["_alive"]
t0 = time.time()
r = dead()
c.check("closed port counts as down", r is False)
c.check("closed port fails well before STATUS_TIMEOUT", time.time() - t0 < 5)

server.shutdown()
c.finish()
