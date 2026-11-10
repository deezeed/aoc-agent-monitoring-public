"""Shared sessions (server/16_ws_status.py): one terminal, several people --
start / list / stop, replay buffer, broadcast, who may type, the sweep. A
fake pty and fake sockets; the real thing is checked over the network
separately."""
import json, os, queue, re, struct, sys, tempfile, threading, time

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
killed = []
S = exec_functions(["_shared", "_shared_lock", "_SHARED_SCROLLBACK", "_SHARED_IDLE_S", "_shared_trim", "_shared_people",
                    "_shared_broadcast", "_ws_send_raise", "_shared_start", "_shared_list", "_shared_stop", "_shared_sweep",
                    "_shared_input_allowed", "_SharedOut", "_shared_out", "_shared_out_lock", "_ws_recv", "_WS_MAX_FRAME"],
                   {"os": os, "re": re, "time": time, "json": json, "threading": threading, "_struct": struct, "_queue": queue,
                    "_hard_kill_pty": lambda p: killed.append(p) or setattr(p, "alive", False)})


class FakePty:
    def __init__(self):
        self.alive, self.out, self.written = True, [], []

    def isalive(self):
        return self.alive

    def read(self, n):
        if self.out:
            return self.out.pop(0)
        if not self.alive:
            raise EOFError
        time.sleep(0.02)
        return ""

    def write(self, t):
        self.written.append(t)


class FakeSock:
    def __init__(self, dead=False):
        self.frames, self.dead = [], dead

    def sendall(self, b):
        if self.dead:
            raise ConnectionError
        self.frames.append(b)


def frames_text(sock):
    out = []
    for f in sock.frames:
        ln = f[1] & 0x7f
        off = 2 if ln < 126 else 4 if ln == 126 else 10
        out.append((f[0], f[off:]))
    return out


c.check("trim keeps the newest bytes", S["_shared_trim"](b"abcdef", 3) == b"def" and S["_shared_trim"](b"ab", 3) == b"ab")
c.check("who may type: owner, member", S["_shared_input_allowed"]("owner") and S["_shared_input_allowed"]("member")
        and not S["_shared_input_allowed"]("viewer"))
d = tempfile.mkdtemp()
owner = {"id": "owner", "name": "Marek", "role": "owner"}
c.check("start needs an existing folder", "folder" in S["_shared_start"]({"cwd": "C:\\nope\\x"}, owner, spawn=lambda c, k: FakePty())["error"])
pty = FakePty()
r = S["_shared_start"]({"cwd": d, "kind": "shell", "name": "  Pair   on AOC "}, owner, spawn=lambda c_, k: pty)
c.check("started", r["ok"] and len(r["id"]) == 8)
e = S["_shared"][r["id"]]
c.check("name cleaned, kind, who started it", e["name"] == "Pair on AOC" and e["kind"] == "shell" and e["by"] == "Marek")
a, b, gone = FakeSock(), FakeSock(), FakeSock(dead=True)
e["clients"][a] = owner
e["clients"][b] = {"id": "marco", "name": "Marco", "role": "member"}
e["clients"][gone] = {"id": "eva", "name": "Eva", "role": "viewer"}
pty.out.append("hello\r\n")
time.sleep(0.3)
c.check("output: to everyone, as binary frames, kept for newcomers", any(op == 0x82 and p == b"hello\r\n" for op, p in frames_text(a))
        and any(p == b"hello\r\n" for _, p in frames_text(b)) and e["buf"].endswith(b"hello\r\n"))
c.check("a dead client is dropped", gone not in e["clients"])
c.check("people: one line each", S["_shared_people"](e) == [{"name": "Marek", "role": "owner"}, {"name": "Marco", "role": "member"}])
S["_shared_broadcast"](e, "\x00who:Marco", skip=b)
time.sleep(0.2)     # sends go through each client's writer thread
c.check("who's typing goes to the others as text", any(op == 0x81 and p == "\x00who:Marco".encode() for op, p in frames_text(a))
        and not any(p == "\x00who:Marco".encode() for _, p in frames_text(b)))
lst = S["_shared_list"]()["sessions"]
c.check("listed with who's there", lst[0]["id"] == r["id"] and lst[0]["alive"] and [p["name"] for p in lst[0]["people"]] == ["Marek", "Marco"])
big = S["_ws_send_raise"]
s2 = FakeSock()
big(s2, b"x" * 70000)
c.check("frames over 64 KB use the 8-byte length", s2.frames[0][1] == 127 and struct.unpack(">Q", s2.frames[0][2:10])[0] == 70000)
c.check("stop: the pty killed, gone from the list", S["_shared_stop"]({"id": r["id"]})["ok"] and pty in killed
        and S["_shared_list"]()["sessions"] == [])
time.sleep(0.2)
c.check("stop: everyone told it ended, then a close frame", any(p == "\x00ended".encode() for _, p in frames_text(a))
        and bytes([0x88, 0x00]) in a.frames)
c.check("stop an unknown one", not S["_shared_stop"]({"id": "nope"})["ok"])
p2, p3 = FakePty(), FakePty()
r2 = S["_shared_start"]({"cwd": d}, owner, spawn=lambda c_, k: p2)
r3 = S["_shared_start"]({"cwd": d}, owner, spawn=lambda c_, k: p3)
c.check("default kind is Claude Code, named after the folder", S["_shared"][r2["id"]]["kind"] == "claude"
        and S["_shared"][r2["id"]]["name"] == os.path.basename(d))
p2.alive = False
S["_shared"][r3["id"]]["last_client"] = time.time() - 5 * 3600
S["_shared_sweep"]()
c.check("sweep: an ended one and one nobody opened for 4 h are gone", S["_shared"] == {} and p3 in killed)

# a stalled client can't hold up the others (found in review: one blocking sendall froze the session)
class StuckSock(FakeSock):
    def __init__(self):
        super().__init__()
        self.gate, self.shut = threading.Event(), False

    def sendall(self, b):
        self.gate.wait()
        super().sendall(b)

    def shutdown(self, how):
        self.shut = True
        self.gate.set()


p4 = FakePty()
r4 = S["_shared_start"]({"cwd": d}, owner, spawn=lambda c_, k: p4)
e4 = S["_shared"][r4["id"]]
fast, stuck = FakeSock(), StuckSock()
e4["clients"][fast] = owner
e4["clients"][stuck] = {"id": "eva", "name": "Eva", "role": "viewer"}
for i in range(S["_SharedOut"].LIMIT + 5):
    S["_shared_broadcast"](e4, b"x")
time.sleep(0.3)
c.check("a stalled client: the others still get everything", len(fast.frames) == S["_SharedOut"].LIMIT + 5)
c.check("... and once it's too far behind it is dropped (its socket shut)", stuck not in e4["clients"] and stuck.shut)
S["_shared_stop"]({"id": r4["id"]})


# frames: the length a client claims is capped; unmasking is right
class RecvSock:
    def __init__(self, data):
        self.data = data

    def recv(self, n):
        out, self.data = self.data[:n], self.data[n:]
        return out


def frame(payload, mask=b"\x01\x02\x03\x04"):
    ln = len(payload)
    hdr = bytes([0x81, 0x80 | ln]) if ln < 126 else bytes([0x81, 0x80 | 126]) + struct.pack(">H", ln)
    return hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


c.check("recv: a masked frame unmasks right", S["_ws_recv"](RecvSock(frame("ahoj, čau".encode() * 50))) == (1, "ahoj, čau".encode() * 50))
c.check("recv: an empty frame", S["_ws_recv"](RecvSock(frame(b""))) == (1, b""))
huge = bytes([0x82, 0x80 | 127]) + struct.pack(">Q", 2 ** 40) + b"\x00" * 4
c.check("recv: a frame claiming 1 TB is refused, nothing allocated", S["_ws_recv"](RecvSock(huge)) == (None, None))
c.check("recv: over the limit is refused", S["_ws_recv"](RecvSock(frame(b"x" * 200)), limit=100) == (None, None))
c.finish()
