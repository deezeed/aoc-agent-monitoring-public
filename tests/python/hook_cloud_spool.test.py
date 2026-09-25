"""Tests the hook's offline spool for AOC Cloud forwarding
(_post_cloud_with_retry, _cloud_spool_pending, _cloud_spool_append),
extracted straight from hooks/aoc_hook.py. monitor.py's side (draining)
is covered by cloud_commands.test.py."""
import sys, os, json, tempfile

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import extract_functions
from lib.check import Checker

HOOK_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "hooks", "aoc_hook.py"))
with open(HOOK_PATH, encoding="utf-8") as f:
    HOOK_SRC = f.read()

c = Checker()
tmp = tempfile.mkdtemp()
SPOOL = os.path.join(tmp, "aoc_cloud_spool.jsonl")


class FakeUrllib:
    """Stands in for urllib (request.Request/urlopen) -- `up` toggles the network."""
    def __init__(self):
        self.up = True
        self.sent = []
        outer = self

        class _Req:
            def __init__(self, url, data=None, method=None, headers=None):
                self.data = data

        class _Request:
            Request = _Req

            @staticmethod
            def urlopen(req, timeout=None):
                if not outer.up:
                    raise OSError("offline")
                outer.sent.append(json.loads(req.data)["n"])
        self.request = _Request


fake = FakeUrllib()
names = ["_post_cloud_with_retry", "_cloud_spool_pending", "_cloud_spool_append"]
src = extract_functions(names, src=HOOK_SRC)
ns = {"os": os, "json": json, "urllib": fake, "AOC_CLOUD_SPOOL_FILE": SPOOL,
      "_CLOUD_SPOOL_MAX_BYTES": 5 * 1024 * 1024}
for n in names:
    exec(src[n], ns)
# No real backoff sleeps in tests.
import time as _real_time
_real_time_sleep = _real_time.sleep
_real_time.sleep = lambda s: None


def spooled():
    if not os.path.exists(SPOOL):
        return []
    with open(SPOOL, encoding="utf-8") as f:
        return [json.loads(ln)["data"]["n"] for ln in f if ln.strip()]


post = ns["_post_cloud_with_retry"]

post("u", "k", "/update", {"n": 1})
c.check("online, empty spool: sent directly", fake.sent == [1] and spooled() == [])

fake.up = False
post("u", "k", "/update", {"n": 2})
c.check("offline: spooled instead of lost", spooled() == [2])

fake.up = True
post("u", "k", "/update", {"n": 3})
c.check("back online but spool non-empty: queued behind it, not sent ahead", fake.sent == [1] and spooled() == [2, 3])

os.replace(SPOOL, SPOOL + ".draining")
post("u", "k", "/update", {"n": 4})
c.check("while monitor.py drains: still queued behind", fake.sent == [1] and spooled() == [4])
os.remove(SPOOL + ".draining"); os.remove(SPOOL)

ns["_CLOUD_SPOOL_MAX_BYTES"] = 10
with open(SPOOL, "w", encoding="utf-8") as f:
    f.write("x" * 50 + "\n")
ns["_cloud_spool_append"]("/update", {"n": 5}, SPOOL)
with open(SPOOL, encoding="utf-8") as f:
    c.check("full spool: new data dropped, file not grown", f.read() == "x" * 50 + "\n")

_real_time.sleep = _real_time_sleep
c.finish()
