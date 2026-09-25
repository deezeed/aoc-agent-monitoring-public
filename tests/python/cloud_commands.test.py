"""Tests the AOC Cloud remote Force Stop poller and offline spool drain
(_kill_session_core, _process_cloud_commands, _drain_cloud_spool),
extracted straight from monitor.py. The cloud side lives in
saas-backend/app/services/commands.py; this is the half that runs on the
customer's machine."""
import sys, os, json, tempfile, threading

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()


class FakeRun:
    def __init__(self, tasklist_out):
        self.tasklist_out = tasklist_out
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        class R: pass
        r = R()
        r.stdout = self.tasklist_out if cmd[0] == "tasklist" else ""
        return r


def make_ns(sessions, tasklist_out="claude.exe  4242 Console"):
    state = {"sessions": sessions}
    audit = []
    run = FakeRun(tasklist_out)
    ns = exec_functions(
        ["_kill_session_core", "_process_cloud_commands", "_drain_cloud_spool"],
        extra_globals={
            "json": json, "os": os,
            "_status_lock": threading.Lock(),
            "_load_status": lambda: state,
            "_save_status": lambda s: state.update(s),
            "_run": run,
            "_log_kill_attempt": lambda sid, sess, pid, outcome, addr="": audit.append((sid, outcome, addr)),
            "_log_bg_error": lambda where, e: None,
            "CLOUD_SPOOL_FILE": "unused",
        },
    )
    return ns, state, audit, run


# ── _kill_session_core: same checks as the old inline handler ──────────────

ns, state, audit, run = make_ns({"s1": {"host_pid": 4242, "session_active": True}})
c.check("kill: verified claude.exe is killed", ns["_kill_session_core"]("s1", "cloud") == "killed")
c.check("kill: taskkill issued for the recorded PID", ["taskkill", "/F", "/PID", "4242"] in run.calls)
c.check("kill: session marked inactive + dismissed", state["sessions"]["s1"]["dismissed"] is True
        and state["sessions"]["s1"]["session_active"] is False)
c.check("kill: audit records the cloud as origin", audit == [("s1", "killed", "cloud")])

ns, state, audit, run = make_ns({"s1": {"host_pid": 4242}}, tasklist_out="INFO: No tasks are running")
c.check("kill: reused PID is refused", ns["_kill_session_core"]("s1") == "refused_pid_mismatch")
c.check("kill: nothing killed on mismatch", not any(cmd[0] == "taskkill" for cmd in run.calls))

ns, state, audit, run = make_ns({"s1": {}})
c.check("kill: no host_pid is refused", ns["_kill_session_core"]("s1") == "refused_no_host_pid")


# ── _process_cloud_commands ────────────────────────────────────────────────

class HTTPErr(Exception):
    def __init__(self, code):
        self.code = code


def cloud(pending, fail_reports=None):
    calls = []

    def req(url, key, method, path, data=None, timeout=8):
        calls.append((method, path, data))
        if method == "GET":
            return pending
        if fail_reports is not None:
            raise fail_reports
        return {}
    return req, calls


ns, state, audit, run = make_ns({"s1": {"host_pid": 4242}})
req, calls = cloud([{"id": 5, "command": "kill", "session_id": "s1"},
                    {"id": 6, "command": "kill", "session_id": "other-machine"}])
ns["_cloud_request"] = req
unreported = {}
ns["_process_cloud_commands"]("u", "k", unreported)
c.check("poll: known session killed and reported",
        ("POST", "/api/commands/5/result", {"outcome": "killed"}) in calls)
c.check("poll: other machine's session left alone (not reported)",
        not any(p == "/api/commands/6/result" for _, p, _ in calls))
c.check("poll: nothing left unreported", unreported == {})

ns, state, audit, run = make_ns({"s1": {"host_pid": 4242}})
req, calls = cloud([{"id": 5, "command": "kill", "session_id": "s1"}], fail_reports=OSError("offline"))
ns["_cloud_request"] = req
unreported = {}
ns["_process_cloud_commands"]("u", "k", unreported)
c.check("poll: failed report kept for retry", unreported == {5: "killed"})
kills_before = sum(1 for cmd in run.calls if cmd[0] == "taskkill")
ns["_process_cloud_commands"]("u", "k", unreported)  # same command still pending on the cloud
c.check("poll: never re-executes a kill whose report is pending",
        sum(1 for cmd in run.calls if cmd[0] == "taskkill") == kills_before)

req, calls = cloud([], fail_reports=HTTPErr(409))
ns["_cloud_request"] = req
ns["_process_cloud_commands"]("u", "k", unreported)
c.check("poll: report dropped once the cloud says the command is closed", unreported == {})

req, calls = cloud([{"id": 9, "command": "reboot", "session_id": "s1"}])
ns["_cloud_request"] = req
ns["_process_cloud_commands"]("u", "k", {})
c.check("poll: unknown command types ignored", [m for m, _, _ in calls] == ["GET"])


# ── _drain_cloud_spool ─────────────────────────────────────────────────────

tmp = tempfile.mkdtemp()
spool = os.path.join(tmp, "spool.jsonl")


def write_spool(path, jobs):
    with open(path, "w", encoding="utf-8") as f:
        for j in jobs:
            f.write(json.dumps(j) + "\n")


sent = []
fail_after = [None]


def spool_req(url, key, method, path, data=None, timeout=8):
    if fail_after[0] is not None and len(sent) >= fail_after[0]:
        raise OSError("offline")
    sent.append(data["n"])

ns, *_ = make_ns({})
ns["_cloud_request"] = spool_req
write_spool(spool, [{"path": "/update", "data": {"n": i}} for i in range(3)])
c.check("drain: sends all, oldest first", ns["_drain_cloud_spool"]("u", "k", spool) == 3 and sent == [0, 1, 2])
c.check("drain: spool files gone when done", not os.path.exists(spool) and not os.path.exists(spool + ".draining"))

sent.clear(); fail_after[0] = 1
write_spool(spool, [{"path": "/update", "data": {"n": i}} for i in range(3)])
ns["_drain_cloud_spool"]("u", "k", spool)
with open(spool + ".draining", encoding="utf-8") as f:
    left = [json.loads(ln)["data"]["n"] for ln in f if ln.strip()]
c.check("drain: stops at first failure and keeps the rest", sent == [0] and left == [1, 2])

# New updates spooled meanwhile go to a fresh spool and are sent only after
# the older .draining ones.
write_spool(spool, [{"path": "/update", "data": {"n": 3}}])
sent.clear(); fail_after[0] = None
ns["_drain_cloud_spool"]("u", "k", spool)
ns["_drain_cloud_spool"]("u", "k", spool)
c.check("drain: order kept across a failed drain", sent == [1, 2, 3])

with open(spool, "w", encoding="utf-8") as f:
    f.write('{"path": "/update", "data": {"n": 7}}\n{torn line\n')
sent.clear()
c.check("drain: corrupt line dropped, not retried forever", ns["_drain_cloud_spool"]("u", "k", spool) == 2 and sent == [7])

c.finish()
