# -*- coding: utf-8 -*-
"""AOC PermissionRequest hook: answer Claude Code's permission prompts from
the AOC dashboard (or your phone, through the tunnel).

Claude Code runs this BEFORE it shows its own "Allow this?" dialog and waits
for it, so it must not hold the prompt while you sit at the PC. It asks
monitor.py (POST /permission/request) whether to hold:
  mode "away"   (default) -- only when this PC has had no keyboard/mouse
                input for idle_min minutes
  mode "always" -- every prompt goes to the dashboard first
  mode "off"    -- never
Not holding = exit at once with no output: the normal terminal dialog.

While holding it polls GET /permission/poll every second and prints the
dashboard's decision (allow / deny). Touching the PC again, "Answer in
terminal" on the dashboard, monitor.py going away or MAX_HOLD_S all release
the prompt to the terminal dialog -- nothing is ever decided without you.

Runs under the console python.exe (like the statusline), not pythonw: the
decision has to reach Claude Code on stdout. Deployment template: setup.py
fills in MONITOR_SCRIPT when it copies this file into ~/.claude/hooks/.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

AOC_URL = "http://127.0.0.1:5151"
MONITOR_SCRIPT = "__MONITOR_SCRIPT__"
AOC_TOKEN_FILE = os.path.join(os.path.dirname(MONITOR_SCRIPT), "aoc_token.txt")
MAX_HOLD_S = 25 * 60      # settings.json gives this hook timeout 1800 s
POLL_S = 1.0
MAX_POLL_FAILURES = 5     # monitor.py unreachable this many polls in a row -> release


def idle_seconds():
    """Seconds since the last keyboard/mouse input on this PC, None if unknown."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(lii)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return None
        # both 32-bit millisecond tick counts; the mask handles the 49-day wrap
        return ((ctypes.windll.kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return None


def _token():
    try:
        with open(AOC_TOKEN_FILE, encoding="utf-8") as f:
            t = f.read().strip()
            return t if len(t) >= 16 else None
    except Exception:
        return None


def _call(path, data=None, timeout=3):
    headers = {"Content-Type": "application/json; charset=utf-8"}
    tok = _token()
    if tok:
        headers["X-AOC-Token"] = tok
    body = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(AOC_URL + path, data=body, headers=headers,
                                 method="GET" if data is None else "POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def decision_output(state, message=""):
    """Pure: the JSON Claude Code expects for an allow/deny, None otherwise."""
    if state == "allow":
        decision = {"behavior": "allow"}
    elif state == "deny":
        decision = {"behavior": "deny",
                    "message": message or "Denied from the AOC dashboard."}
    else:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}


def _set_waiting(hook, waiting, message=""):
    data = {"session_id": hook.get("session_id", ""), "cwd": hook.get("cwd", ""),
            "session_active": True, "waiting_on_you": waiting}
    if waiting:
        data["waiting_kind"] = "permission"
        data["waiting_message"] = message
    try:
        _call("/update", data, timeout=2)
    except Exception:
        pass


def run(hook, sleep=time.sleep, clock=time.monotonic, idle=idle_seconds):
    """Returns the decision dict to print, or None (= terminal dialog)."""
    try:
        reply = _call("/permission/request", {
            "session_id": hook.get("session_id", ""), "cwd": hook.get("cwd", ""),
            "tool_name": hook.get("tool_name", ""), "tool_input": hook.get("tool_input") or {},
            "tool_use_id": hook.get("tool_use_id", ""), "idle_s": idle(),
        })
    except Exception:
        return None
    if not reply.get("hold") or not reply.get("id"):
        return None
    rid = reply["id"]
    _set_waiting(hook, True, reply.get("summary") or f"{hook.get('tool_name', '')} needs your OK")
    started, failures = clock(), 0
    while clock() - started < MAX_HOLD_S:
        sleep(POLL_S)
        q = {"id": rid}
        i = idle()
        if i is not None:
            q["idle"] = f"{i:.1f}"
        try:
            r = _call("/permission/poll?" + urllib.parse.urlencode(q))
            failures = 0
        except Exception:
            failures += 1
            if failures >= MAX_POLL_FAILURES:
                return None
            continue
        state = r.get("state")
        if state == "pending":
            continue
        out = decision_output(state, r.get("message") or "")
        if out:
            _set_waiting(hook, False)
        return out
    return None


def main():
    try:
        raw = sys.stdin.buffer.read()
        hook = json.loads(raw.decode("utf-8", errors="replace") or "{}")
    except Exception:
        return
    if not isinstance(hook, dict):
        return
    out = run(hook)
    if out:
        sys.stdout.write(json.dumps(out))
        sys.stdout.flush()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # never break Claude Code's own permission flow
    sys.exit(0)
