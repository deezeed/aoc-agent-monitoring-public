"""Remote tunnel workers (audit FIND-020 / FIND-021).

FIND-020: once cloudflared printed its URL nobody read its output again, so
a full pipe stalled it on its next log line (ngrok, logging every connection
with --log stdout, was never read while up). And when ngrok gave no URL,
reading its pipe to the end hung the thread with ngrok still running. Now
the output is read to the end (_tunnel_drain), a tunnel that drops by
itself says so, and a Stop is still just off. FIND-021: the downloaded
cloudflared is kept only if Cloudflare signed it.

The pipe case runs for real: a stand-in cloudflared (a Python script named
`tunnel`, run as `python tunnel --url ...`) prints the URL, then far more
log than a pipe holds, then exits."""
import sys, os, re, subprocess, tempfile, threading, time, shutil
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker, wait_until

c = Checker()
work = tempfile.mkdtemp(prefix="aoc_tunnel_")
started = []


def kill(p):
    if p is not None and p.poll() is None:
        p.kill()
        p.wait(10)


ns = exec_functions(
    ["_cf_signature_ok", "_cf_signature", "_download_cloudflared_worker", "_tunnel_drain", "_tunnel_end_state",
     "_start_tunnel_worker", "_stop_tunnel"],
    {"os": os, "re": re, "threading": threading, "time": time, "PORT": 5151,
     "_tunnel_status": ["off"], "_tunnel_url": [None], "_tunnel_proc": [None], "_tunnel_error_detail": [None],
     "_tunnel_dl_pct": [0], "_tunnel_provider": [None], "_CF_EXE": os.path.join(work, "cloudflared.exe"),
     "_CF_DL_URL": "https://example.invalid/cloudflared.exe", "_find_cloudflared": lambda: sys.executable,
     "_write_tunnel_pid": lambda pid: None, "_hard_kill_proc": kill,
     "_start_tunnel_worker_thread": lambda: started.append(1)})
st, url, proc_, detail = ns["_tunnel_status"], ns["_tunnel_url"], ns["_tunnel_proc"], ns["_tunnel_error_detail"]

# ── FIND-021: only a Cloudflare-signed cloudflared is kept ──
ok = ns["_cf_signature_ok"]
cf_subject = ('CN="Cloudflare, Inc.", O="Cloudflare, Inc.", L=San Francisco, S=California, C=US, '
              'SERIALNUMBER=4710875, OID.2.5.4.15=Private Organization')
c.check("valid + signed by Cloudflare (the real subject)", ok("Valid", cf_subject))
c.check("not signed / tampered / expired -> no", not ok("NotSigned", "") and not ok("HashMismatch", cf_subject)
        and not ok("NotTrusted", cf_subject) and not ok("", cf_subject))
c.check("valid but someone else's -> no", not ok("Valid", 'CN="Cloudflare, Inc.", O=Evil Corp, C=US')
        and not ok("Valid", 'CN=x, O="Cloudflare, Inc. Evil", C=US') and not ok("Valid", None))

calls = []


def fake_run(argv, **kw):
    calls.append((argv, kw.get("env", {}).get("AOC_SIG_PATH")))
    return SimpleNamespace(stdout="Valid\r\n" + cf_subject + "\r\n")


c.check("signature read through PowerShell, the path passed as data (env), not in the script",
        ns["_cf_signature"]("C:\\x'; rm -rf ~; '.exe", run=fake_run) == ("Valid", cf_subject)
        and calls[0][1] == "C:\\x'; rm -rf ~; '.exe" and "rm -rf" not in " ".join(calls[0][0]))


def retrieve(u, tmp, progress):
    open(tmp, "wb").write(b"MZ fake")


ns["_download_cloudflared_worker"](retrieve=retrieve, signature=lambda p: ("NotSigned", ""))
c.check("unsigned download: not kept, .part gone, error says why, no tunnel started",
        not os.path.exists(ns["_CF_EXE"]) and not os.path.exists(ns["_CF_EXE"] + ".part") and st[0] == "error"
        and "isn't signed by Cloudflare" in detail[0] and not started)
ns["_download_cloudflared_worker"](retrieve=retrieve, signature=lambda p: ("Valid", cf_subject))
c.check("signed download: kept, then the tunnel starts", os.path.exists(ns["_CF_EXE"]) and started == [1])

# ── the pieces ──
recent, seen = [], []
ns["_tunnel_drain"](iter([f"line {i}\n" for i in range(50)] + ["\n"]), recent, seen.append)
c.check("drain: reads to the end, keeps the last 20 lines, every line to on_line",
        len(seen) == 51 and recent == [f"line {i}" for i in range(30, 50)])


def boom():
    yield "a\n"
    raise UnicodeDecodeError("utf-8", b"", 0, 1, "x")


r2 = []
ns["_tunnel_drain"](boom(), r2)
c.check("drain: a decode error ends it quietly", r2 == ["a"])
es = ns["_tunnel_end_state"]
c.check("end: stopped by you -> just off", es("cloudflared", True, 1, True, ["x"]) is None)
c.check("end: no URL + failed -> error with its last line", es("cloudflared", False, 1, False, ["ERR bad"]) == ("error", "ERR bad")
        and es("ngrok", False, 1, False, []) == ("error", "ngrok exited with code 1"))
c.check("end: no URL + exit 0 -> off (as before)", es("cloudflared", False, 0, False, ["bye"]) is None)
c.check("end: up, then dropped by itself -> error that says so",
        es("cloudflared", True, 0, False, ["conn lost"]) == ("error", "the tunnel stopped by itself: conn lost"))

# ── for real: a stand-in cloudflared that logs far more than a pipe holds ──
fake_url = "https://quiet-river-1234.trycloudflare.com"
script = os.path.join(work, "tunnel")
open(script, "w").write(
    "import sys, time\n"
    "mode = open(sys.argv[0] + '.mode').read().strip()\n"
    f"print('INF Your quick Tunnel has been created! Visit it at: {fake_url}', flush=True)\n"
    "if mode == 'flood':\n"
    "    for i in range(20000):\n"
    "        print(f'INF Registered tunnel connection connIndex={i % 4} ' + 'x' * 60, flush=True)\n"
    "    print('ERR connection lost, giving up', flush=True)\n"
    "    sys.exit(3)\n"
    "time.sleep(120)\n")
here = os.getcwd()
os.chdir(work)   # `python tunnel --url ...` runs ./tunnel
try:
    open(script + ".mode", "w").write("flood")
    t = threading.Thread(target=ns["_start_tunnel_worker"], daemon=True)
    t.start()
    t.join(60)
    c.check("a flood of log after the URL doesn't stall it (the old loop waited on a full pipe for good)",
            not t.is_alive())
    c.check("... and the drop is shown, with its last line", st[0] == "error"
            and detail[0] == "the tunnel stopped by itself: ERR connection lost, giving up" and url[0] is None)

    open(script + ".mode", "w").write("idle")
    t = threading.Thread(target=ns["_start_tunnel_worker"], daemon=True)
    t.start()
    c.check("a working tunnel: ready with its URL", wait_until(lambda: st[0] == "ready", 30) and url[0] == fake_url)
    ns["_stop_tunnel"]()
    t.join(30)
    c.check("Stop: off, no error -- it was you, not a drop",
            not t.is_alive() and st[0] == "off" and detail[0] is None and proc_[0] is None)
finally:
    os.chdir(here)
    kill(proc_[0])
    shutil.rmtree(work, ignore_errors=True)
c.finish()
