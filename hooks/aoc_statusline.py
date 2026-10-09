# -*- coding: utf-8 -*-
"""AOC statusline for Claude Code.

Claude Code runs the configured statusLine command with a JSON description of
the session on stdin. For Pro/Max plans that JSON carries the real plan usage
Anthropic reports -- rate_limits.five_hour / seven_day, each
{used_percentage: 0-100, resets_at: epoch seconds} -- which hooks never see.
This script saves it to %LOCALAPPDATA%\\AOC\\rate_limits.json, where
monitor.py reads it for the dashboard's limit meter and the 80 % alerts.

Output: if setup.py found a statusLine of yours already configured, it saved
it to aoc_statusline_chain.json next to this file -- that command is run with
the same stdin and its output printed unchanged. Otherwise a compact line:
  Opus 5.5 · my-project · 5h 42% ↻13:20 · wk 18%

Never fails loudly: a statusline that errors would blank Claude Code's
status bar, so every step is best-effort.
"""
import json
import os
import subprocess
import sys
import time

AOC_DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "AOC")
RATE_LIMITS_FILE = os.path.join(AOC_DATA_DIR, "rate_limits.json")
# one small file per session: context_window fill + prompt cache expiry
CONTEXT_DIR = os.path.join(AOC_DATA_DIR, "context")
CHAIN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aoc_statusline_chain.json")
KINDS = ("five_hour", "seven_day")
REWRITE_AFTER_S = 60  # rewrite unchanged numbers at most once a minute


def extract_limits(data):
    """{kind: {used_percentage, resets_at}} for the windows present, or {}."""
    rl = data.get("rate_limits") if isinstance(data, dict) else None
    out = {}
    if not isinstance(rl, dict):
        return out
    for kind in KINDS:
        w = rl.get(kind)
        if isinstance(w, dict) and isinstance(w.get("used_percentage"), (int, float)) \
                and isinstance(w.get("resets_at"), (int, float)):
            out[kind] = {"used_percentage": float(w["used_percentage"]), "resets_at": int(w["resets_at"])}
    return out


def should_write(limits, existing, now):
    """Write when the numbers changed, or the file is older than
    REWRITE_AFTER_S (monitor.py shows 'updated N min ago' from it)."""
    if not limits:
        return False
    if not isinstance(existing, dict):
        return True
    if any(existing.get(k) != limits.get(k) for k in KINDS):
        return True
    return now - (existing.get("updated_at") or 0) >= REWRITE_AFTER_S


def save_limits(limits, session_id, now):
    try:
        with open(RATE_LIMITS_FILE, encoding="utf-8") as f:
            existing = json.load(f)
    except Exception:
        existing = None
    if not should_write(limits, existing, now):
        return
    os.makedirs(AOC_DATA_DIR, exist_ok=True)
    payload = dict(limits, updated_at=now, session_id=session_id or "")
    tmp = f"{RATE_LIMITS_FILE}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    for _ in range(5):  # monitor.py may have the file open for a moment
        try:
            os.replace(tmp, RATE_LIMITS_FILE)
            return
        except PermissionError:
            time.sleep(0.05)
    try:
        os.remove(tmp)
    except OSError:
        pass


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def extract_context(data):
    """The session's context fill and prompt cache state, or {} when Claude
    Code didn't send a usable context_window (older versions, or before the
    first API response). Cache fields are None when absent."""
    cw = data.get("context_window") if isinstance(data, dict) else None
    if not isinstance(cw, dict):
        return {}
    pct, size = _num(cw.get("used_percentage")), _num(cw.get("context_window_size"))
    if pct is None or not size:
        return {}
    pc = data.get("prompt_cache")
    pc = pc if isinstance(pc, dict) else {}
    ttl = pc.get("ttl")
    return {
        "used_percentage": float(pct),
        "window": int(size),
        "input_tokens": int(_num(cw.get("total_input_tokens")) or 0),
        "cache_expires_at": _num(pc.get("expires_at")),
        "cache_ttl": ttl if ttl in ("5m", "1h") else None,
        "recache_tokens": _num(pc.get("recache_tokens_if_cold")),
    }


def _safe_session_id(sid):
    sid = str(sid or "")
    return sid if sid and len(sid) <= 80 and all(c.isalnum() or c in "-_" for c in sid) else ""


def save_context(ctx, session_id, now):
    """%LOCALAPPDATA%\\AOC\\context\\<session_id>.json -- one file per session,
    so concurrent sessions never race on a shared file. Same write rule as
    the limits file: on change, or once a minute."""
    sid = _safe_session_id(session_id)
    if not ctx or not sid:
        return
    path = os.path.join(CONTEXT_DIR, sid + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            existing = json.load(f)
    except Exception:
        existing = None
    if isinstance(existing, dict) and all(existing.get(k) == v for k, v in ctx.items()) \
            and now - (existing.get("updated_at") or 0) < REWRITE_AFTER_S:
        return
    os.makedirs(CONTEXT_DIR, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dict(ctx, updated_at=now), f)
    for _ in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05)
    try:
        os.remove(tmp)
    except OSError:
        pass


def format_line(data, limits, now):
    parts = []
    model = (data.get("model") or {}).get("display_name") if isinstance(data.get("model"), dict) else None
    if model:
        parts.append(model)
    cwd = (data.get("workspace") or {}).get("current_dir") or data.get("cwd") or ""
    if cwd:
        parts.append(os.path.basename(cwd.rstrip("\\/")) or cwd)
    ctx = extract_context(data)
    if ctx:
        parts.append(f"ctx {ctx['used_percentage']:.0f}%")
    fh = limits.get("five_hour")
    if fh:
        reset = time.strftime("%H:%M", time.localtime(fh["resets_at"])) if fh["resets_at"] > now else "reset"
        parts.append(f"5h {fh['used_percentage']:.0f}% ↻{reset}")
    sd = limits.get("seven_day")
    if sd:
        parts.append(f"wk {sd['used_percentage']:.0f}%")
    return " · ".join(parts)


def run_chained(raw):
    """The user's own statusLine command, saved by setup.py, or None."""
    try:
        with open(CHAIN_FILE, encoding="utf-8") as f:
            cmd = (json.load(f).get("statusLine") or {}).get("command")
    except Exception:
        return None
    if not cmd:
        return None
    try:
        r = subprocess.run(cmd, shell=True, input=raw, capture_output=True, timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.stdout
    except Exception:
        return b""


def main():
    raw = sys.stdin.buffer.read()
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        data = {}
    now = time.time()
    limits = extract_limits(data)
    try:
        save_limits(limits, data.get("session_id") if isinstance(data, dict) else "", now)
    except Exception:
        pass
    try:
        save_context(extract_context(data), data.get("session_id") if isinstance(data, dict) else "", now)
    except Exception:
        pass
    chained = run_chained(raw)
    if chained is not None:
        sys.stdout.buffer.write(chained)
    else:
        sys.stdout.buffer.write(format_line(data if isinstance(data, dict) else {}, limits, now).encode("utf-8"))
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
