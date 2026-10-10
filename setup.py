"""
AOC one-click setup.

Deploys the Claude Code hook scripts (from hooks/ in this repo) into
~/.claude/hooks/, wires the 4 required hook entries into
~/.claude/settings.json without disturbing anything else already there,
registers watchdog.py as a Task Scheduler task that starts monitor.py at
logon, and registers sentinel.py on its own periodic trigger to revive
watchdog.py itself if it ever hangs or gives up. Safe to re-run: every
step checks before writing, so running this twice never duplicates hook
entries or scheduled tasks.

Usage:
  python setup.py            apply changes
  python setup.py --dry-run  show exactly what would change, touch nothing
                              (no file writes; only read-only "schtasks /Query"
                              calls to report whether a task already exists)
  python setup.py --stop     stop every running AOC process from this directory
                              (used by the installer before an upgrade)
  python setup.py --uninstall remove AOC's hook entries, deployed hook scripts
                              and scheduled tasks, and stop its processes;
                              keeps the user's data in %LOCALAPPDATA%\\AOC
  python setup.py --cloud-url https://api.example.com --cloud-key aoc_live_...
                              also enable optional cloud forwarding (AOC SaaS) --
                              omit both flags to keep this machine local-only,
                              today's exact default behavior. See
                              hooks/aoc_hook.py's _read_cloud_config for what
                              this does and doesn't affect (never touches the
                              local monitor.py dashboard).
"""
import json
import os
import subprocess
import sys

DRY_RUN = "--dry-run" in sys.argv


def _get_arg_value(flag: str):
    """Simple `--flag value` extraction, matching this script's existing
    membership-check style (`"--dry-run" in sys.argv`) rather than
    pulling in argparse for two optional flags."""
    if flag in sys.argv:
        idx = sys.argv.index(flag)
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
    return None


_BOOL_FLAGS = {"--dry-run", "--stop", "--uninstall"}
_VALUE_FLAGS = {"--cloud-url", "--cloud-key"}


def _check_args(argv):
    """Validate argv[1:] before anything is touched. Returns (action, message):
    ("run", "") to proceed, ("help", "") for -h/--help, or ("error", why).
    Unknown flags used to be silently ignored, so a typo -- or a reasonable
    guess like --help -- ran the full, state-changing setup."""
    args = list(argv[1:])
    if any(a in ("-h", "--help", "/?") for a in args):
        return "help", ""
    i = 0
    while i < len(args):
        a = args[i]
        if a in _BOOL_FLAGS:
            i += 1
        elif a in _VALUE_FLAGS:
            if i + 1 >= len(args) or args[i + 1].startswith("--"):
                return "error", f"{a} needs a value"
            i += 2
        else:
            return "error", f"unknown argument: {a}"
    if ("--cloud-url" in args) != ("--cloud-key" in args):
        return "error", "--cloud-url and --cloud-key must be given together"
    return "run", ""


CLOUD_URL = _get_arg_value("--cloud-url")
CLOUD_KEY = _get_arg_value("--cloud-key")

AOC_DIR = os.path.dirname(os.path.abspath(__file__))
MONITOR_SCRIPT = os.path.join(AOC_DIR, "monitor.py")
WATCHDOG_SCRIPT = os.path.join(AOC_DIR, "watchdog.py")
SENTINEL_SCRIPT = os.path.join(AOC_DIR, "sentinel.py")

HOOKS_SRC_DIR = os.path.join(AOC_DIR, "hooks")
HOOKS_DST_DIR = os.path.join(os.path.expanduser("~"), ".claude", "hooks")
SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
STATUSLINE_SCRIPT = "aoc_statusline.py"
STATUSLINE_CHAIN_FILE = os.path.join(HOOKS_DST_DIR, "aoc_statusline_chain.json")
# PermissionRequest hook: holds a permission prompt for the dashboard while
# you're away (remote approve). Prints its decision on stdout, so like the
# statusline it runs under python.exe, not via run_hook.pyw/pythonw.
PERMISSION_SCRIPT = "aoc_permission.py"
PERMISSION_EVENT = "PermissionRequest"
PERMISSION_TIMEOUT_S = 1800  # aoc_permission.py gives up holding after 25 min

TASK_NAME = r"\AOC\AOC Watchdog"
SENTINEL_TASK_NAME = r"\AOC\AOC Sentinel"
SENTINEL_INTERVAL_MIN = 15  # Task Scheduler's own restart-on-failure only fires 3x for watchdog.py's process exiting; this catches it hanging or giving up entirely

# (event, matcher) pairs AOC needs — matches ~/.claude/settings.json's shape
HOOK_SPECS = [
    ("UserPromptSubmit", ""),
    ("PreToolUse", "Agent"),
    ("PostToolUse", "Agent"),
    ("Stop", ""),
    ("Notification", "permission_prompt|elicitation_dialog"),
]


def _find_pythonw() -> str:
    """Mirrors watchdog.py's own PYTHONW detection exactly, so the hook and
    the watchdog always agree on which interpreter to launch."""
    candidate = os.path.join(
        os.path.dirname(sys.executable),
        "pythonw.exe" if sys.platform == "win32" else "python",
    )
    return candidate if os.path.exists(candidate) else sys.executable


def deploy_hook_scripts(pythonw: str) -> None:
    if not DRY_RUN:
        os.makedirs(HOOKS_DST_DIR, exist_ok=True)

    for src_name in ("aoc_hook.py", "run_hook.pyw", STATUSLINE_SCRIPT, PERMISSION_SCRIPT):
        with open(os.path.join(HOOKS_SRC_DIR, src_name), encoding="utf-8") as f:
            content = f.read()
        content = content.replace("__MONITOR_SCRIPT__", MONITOR_SCRIPT.replace("\\", "/"))
        content = content.replace("__PYTHONW__", pythonw.replace("\\", "/"))
        dst_path = os.path.join(HOOKS_DST_DIR, src_name)

        if DRY_RUN:
            existing = ""
            if os.path.exists(dst_path):
                with open(dst_path, encoding="utf-8") as f:
                    existing = f.read()
            if existing == content:
                print(f"[dry-run] {dst_path} already up to date, no change")
            elif existing:
                print(f"[dry-run] would UPDATE {dst_path} (deployed content differs from this repo's version)")
            else:
                print(f"[dry-run] would CREATE {dst_path}")
        else:
            with open(dst_path, "w", encoding="utf-8") as f:
                f.write(content)

    if not DRY_RUN:
        print(f"[ok] deployed aoc_hook.py + run_hook.pyw + {STATUSLINE_SCRIPT} + {PERMISSION_SCRIPT} -> {HOOKS_DST_DIR}")


def deploy_cloud_config() -> None:
    """Optional -- only writes anything if both --cloud-url and
    --cloud-key were passed. Omitted (the default), cloud forwarding
    stays disabled and aoc_hook.py behaves exactly as it always has (see
    that file's _read_cloud_config) -- this machine's local monitor.py
    dashboard is entirely unaffected either way."""
    if not CLOUD_URL or not CLOUD_KEY:
        return
    if DRY_RUN:
        print(f"[dry-run] would write cloud forwarding config -> {HOOKS_DST_DIR} (url={CLOUD_URL})")
        return
    os.makedirs(HOOKS_DST_DIR, exist_ok=True)
    with open(os.path.join(HOOKS_DST_DIR, "aoc_cloud_url.txt"), "w", encoding="utf-8") as f:
        f.write(CLOUD_URL.strip())
    with open(os.path.join(HOOKS_DST_DIR, "aoc_api_key.txt"), "w", encoding="utf-8") as f:
        f.write(CLOUD_KEY.strip())
    print(f"[ok] wrote cloud forwarding config -> {HOOKS_DST_DIR} (url={CLOUD_URL})")


def _find_python_console(pythonw: str) -> str:
    """The statusline must print to stdout, which pythonw.exe can't --
    use the python.exe sitting next to it."""
    candidate = os.path.join(os.path.dirname(pythonw), "python.exe")
    return candidate if os.path.exists(candidate) else sys.executable


def _statusline_command(python: str) -> str:
    script = os.path.join(HOOKS_DST_DIR, STATUSLINE_SCRIPT).replace("\\", "/")
    return f'"{python.replace(chr(92), "/")}" "{script}"'


def _is_aoc_statusline(sl) -> bool:
    return isinstance(sl, dict) and \
        sl.get("command", "").replace("\\", "/").rstrip('"').endswith(STATUSLINE_SCRIPT)


def _merge_statusline(settings: dict, command: str):
    """Pure core of merge_statusline_settings. Returns (action, chain):
    action is "added" / "updated" / "unchanged" / "wrapped"; chain is the
    user's own statusLine object to save to aoc_statusline_chain.json
    ("wrapped" only) -- the AOC script runs it and prints its output, so
    their status bar looks exactly as before."""
    current = settings.get("statusLine")
    if _is_aoc_statusline(current):
        if current.get("command") == command:
            return "unchanged", None
        current["command"] = command
        return "updated", None
    new = {"type": "command", "command": command, "padding": 0}
    if isinstance(current, dict) and current.get("command"):
        for k in ("padding", "refreshInterval"):
            if k in current:
                new[k] = current[k]
        settings["statusLine"] = new
        return "wrapped", current
    settings["statusLine"] = new
    return "added", None


def _remove_statusline(settings: dict, chain):
    """Pure core of the uninstall path: puts the user's wrapped statusLine
    back (chain), or drops AOC's. Returns True if settings changed."""
    if not _is_aoc_statusline(settings.get("statusLine")):
        return False
    if isinstance(chain, dict) and chain.get("command"):
        settings["statusLine"] = chain
    else:
        del settings["statusLine"]
    return True


def merge_statusline_settings(pythonw: str) -> None:
    settings = _load_settings()
    action, chain = _merge_statusline(settings, _statusline_command(_find_python_console(pythonw)))
    if DRY_RUN:
        msg = {"added": "would add the AOC statusLine (plan-limit meter)",
               "updated": "would update the AOC statusLine command",
               "unchanged": "AOC statusLine already configured, no change",
               "wrapped": "would wrap your existing statusLine (its output stays the same) "
                          f"and save it to {STATUSLINE_CHAIN_FILE}"}[action]
        print(f"[dry-run] {msg}")
        return
    if action == "unchanged":
        print(f"[ok] AOC statusLine already present in {SETTINGS_FILE}")
        return
    if chain is not None:
        os.makedirs(HOOKS_DST_DIR, exist_ok=True)
        with open(STATUSLINE_CHAIN_FILE, "w", encoding="utf-8") as f:
            json.dump({"statusLine": chain}, f, indent=2)
    _save_settings(settings)
    print(f"[ok] statusLine {action} in {SETTINGS_FILE}" +
          (" -- your previous one still runs, AOC just records the plan limits" if action == "wrapped" else ""))


def _hook_command(pythonw: str) -> str:
    """Both paths quoted: an unquoted command breaks for anyone whose
    profile path has a space in it (C:/Users/John Smith/...)."""
    run_hook = os.path.join(HOOKS_DST_DIR, "run_hook.pyw").replace("\\", "/")
    return f'"{pythonw.replace(chr(92), "/")}" "{run_hook}"'


def _is_aoc_hook(h: dict) -> bool:
    cmd = h.get("command", "").replace("\\", "/").rstrip('"')
    return cmd.endswith("run_hook.pyw") or cmd.endswith(PERMISSION_SCRIPT)


def _permission_command(python: str) -> str:
    script = os.path.join(HOOKS_DST_DIR, PERMISSION_SCRIPT).replace("\\", "/")
    return f'"{python.replace(chr(92), "/")}" "{script}"'


def _merge_permission_hook(settings: dict, command: str) -> str:
    """Pure core: AOC's PermissionRequest entry (own command + timeout).
    Returns "added", "updated" or "unchanged"."""
    entries = settings.setdefault("hooks", {}).setdefault(PERMISSION_EVENT, [])
    for entry in entries:
        for h in entry.get("hooks", []):
            if _is_aoc_hook(h):
                if h.get("command") == command and h.get("timeout") == PERMISSION_TIMEOUT_S:
                    return "unchanged"
                h["command"] = command
                h["timeout"] = PERMISSION_TIMEOUT_S
                return "updated"
    entries.append({"matcher": "", "hooks": [{"type": "command", "command": command,
                                              "timeout": PERMISSION_TIMEOUT_S}]})
    return "added"


def _merge_hook_entries(settings: dict, command: str):
    """Pure core of merge_hook_settings. Adds a missing AOC entry for each
    HOOK_SPECS (event, matcher), and rewrites an existing AOC entry whose
    command differs (e.g. AOC reinstalled with a different Python), leaving
    every non-AOC hook untouched. Returns (added, updated) lists of
    (event, matcher)."""
    hooks = settings.setdefault("hooks", {})
    added, updated = [], []
    for event, matcher in HOOK_SPECS:
        found = False
        for entry in hooks.get(event, []):
            if entry.get("matcher", "") != matcher:
                continue
            for h in entry.get("hooks", []):
                if _is_aoc_hook(h):
                    found = True
                    if h.get("command") != command:
                        h["command"] = command
                        updated.append((event, matcher))
        if not found:
            hooks.setdefault(event, []).append({
                "matcher": matcher,
                "hooks": [{"type": "command", "command": command}],
            })
            added.append((event, matcher))
    return added, updated


def _remove_hook_entries(settings: dict) -> int:
    """Pure core of the uninstall path: drops every AOC hook command, then
    any entry / event left with no hooks. Returns how many were removed."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    removed = 0
    for event in list(hooks):
        kept_entries = []
        for entry in hooks[event]:
            inner = entry.get("hooks", [])
            kept = [h for h in inner if not _is_aoc_hook(h)]
            removed += len(inner) - len(kept)
            if kept:
                entry["hooks"] = kept
                kept_entries.append(entry)
        if kept_entries:
            hooks[event] = kept_entries
        else:
            del hooks[event]
    if not hooks:
        del settings["hooks"]
    return removed


def _load_settings() -> dict:
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_settings(settings: dict) -> None:
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


def _fmt_specs(specs) -> str:
    return ", ".join(f"{e}/{m or '(no matcher)'}" for e, m in specs)


def merge_hook_settings(pythonw: str) -> None:
    """Adds AOC's hook entries to ~/.claude/settings.json without
    disturbing anything else already there (other hooks, enabledPlugins,
    theme, etc.). Idempotent: re-running never duplicates entries, and
    brings an existing entry's command up to date."""
    settings = _load_settings()
    added, updated = _merge_hook_entries(settings, _hook_command(pythonw))
    perm = _merge_permission_hook(settings, _permission_command(_find_python_console(pythonw)))
    if perm == "added":
        added.append((PERMISSION_EVENT, ""))
    elif perm == "updated":
        updated.append((PERMISSION_EVENT, ""))

    if DRY_RUN:
        if added:
            print(f"[dry-run] would add hook entries to {SETTINGS_FILE} for: {_fmt_specs(added)}")
        if updated:
            print(f"[dry-run] would update the AOC hook command for: {_fmt_specs(updated)}")
        if not added and not updated:
            print(f"[dry-run] {SETTINGS_FILE} already has all AOC hook entries, no change")
        return

    if added or updated:
        _save_settings(settings)
        if added:
            print(f"[ok] added AOC hook entries to {SETTINGS_FILE}: {_fmt_specs(added)}")
        if updated:
            print(f"[ok] updated AOC hook command in {SETTINGS_FILE}: {_fmt_specs(updated)}")
    else:
        print(f"[ok] AOC hook entries already present in {SETTINGS_FILE}")


def register_watchdog_task(pythonw: str) -> None:
    tr = f'"{pythonw}" "{WATCHDOG_SCRIPT}"'
    cmd = ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", tr,
           "/SC", "ONLOGON", "/RL", "LIMITED", "/F"]
    if DRY_RUN:
        exists = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME],
                                 capture_output=True, text=True).returncode == 0
        print(f"[dry-run] task {'already exists, would overwrite' if exists else 'does not exist, would CREATE'}: {TASK_NAME}")
        print(f"[dry-run] would run: {' '.join(cmd)}")
        print(f"[dry-run] would then start it immediately via: schtasks /Run /TN \"{TASK_NAME}\"")
        return

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"[ok] registered Task Scheduler task '{TASK_NAME}' (trigger: at logon)")
    else:
        print(f"[FAIL] could not register Task Scheduler task: {result.stderr.strip()}")
        print("       'Access is denied' here usually means this script is running in a")
        print("       restricted/non-interactive shell (e.g. an automation tool), not a real")
        print("       permissions problem — schtasks needs your normal interactive session.")
        print("       Re-run this script from a regular PowerShell or cmd window.")
        return

    # Start it now too — _acquire_lock() in watchdog.py refuses to run a
    # second instance if one's already alive, so this is safe to fire even
    # if AOC is already running (e.g. re-running this script).
    run_result = subprocess.run(
        ["schtasks", "/Run", "/TN", TASK_NAME],
        capture_output=True, text=True,
    )
    if run_result.returncode == 0:
        print(f"[ok] started '{TASK_NAME}' now (no reboot/logoff needed)")
    else:
        print(f"[WARN] could not start '{TASK_NAME}' immediately: {run_result.stderr.strip()}")
        print("       it will still start automatically at next logon.")


def register_sentinel_task(pythonw: str) -> None:
    """The watchdog's own watchdog: a periodic (not just at-logon) task that
    just tries to relaunch watchdog.py every run. Cheap and safe when
    watchdog.py is already alive (its own _acquire_lock() makes the new
    attempt exit near-instantly); only matters on the rare occasion
    watchdog.py itself has hung or given up after using up Task Scheduler's
    own 3-attempt restart-on-failure budget."""
    tr = f'"{pythonw}" "{SENTINEL_SCRIPT}"'
    cmd = ["schtasks", "/Create", "/TN", SENTINEL_TASK_NAME, "/TR", tr,
           "/SC", "MINUTE", "/MO", str(SENTINEL_INTERVAL_MIN), "/RL", "LIMITED", "/F"]
    if DRY_RUN:
        exists = subprocess.run(["schtasks", "/Query", "/TN", SENTINEL_TASK_NAME],
                                 capture_output=True, text=True).returncode == 0
        print(f"[dry-run] task {'already exists, would overwrite' if exists else 'does not exist, would CREATE'}: {SENTINEL_TASK_NAME}")
        print(f"[dry-run] would run: {' '.join(cmd)}")
        return

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"[ok] registered Task Scheduler task '{SENTINEL_TASK_NAME}' (every {SENTINEL_INTERVAL_MIN} min)")
    else:
        print(f"[FAIL] could not register sentinel task: {result.stderr.strip()}")
        print("       same 'Access is denied' caveat as the watchdog task above applies here.")


def validate() -> None:
    print()
    print("--- validation ---")
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            settings = json.load(f)
        events = settings.get("hooks", {})
        missing = [e for e, _ in HOOK_SPECS if e not in events] + \
                  ([] if PERMISSION_EVENT in events else [PERMISSION_EVENT])
        if missing:
            print(f"[WARN] settings.json missing hook events: {missing}")
        else:
            print(f"[ok] settings.json has all {len(HOOK_SPECS) + 1} AOC hook events")
        sl = settings.get("statusLine")
        print(f"[{'ok' if _is_aoc_statusline(sl) else 'WARN'}] statusLine "
              + ("runs the AOC plan-limit recorder" if _is_aoc_statusline(sl) else "is not AOC's -- the plan-limit meter stays empty"))
    except Exception as e:
        print(f"[FAIL] could not read back settings.json: {e}")

    for fname in ("aoc_hook.py", "run_hook.pyw", STATUSLINE_SCRIPT, PERMISSION_SCRIPT):
        p = os.path.join(HOOKS_DST_DIR, fname)
        print(f"[{'ok' if os.path.exists(p) else 'FAIL'}] {p}")

    result = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME],
        capture_output=True, text=True,
    )
    print(f"[{'ok' if result.returncode == 0 else 'FAIL'}] Task Scheduler task '{TASK_NAME}'")

    print(f"[{'ok' if os.path.exists(SENTINEL_SCRIPT) else 'FAIL'}] {SENTINEL_SCRIPT}")
    sentinel_result = subprocess.run(
        ["schtasks", "/Query", "/TN", SENTINEL_TASK_NAME],
        capture_output=True, text=True,
    )
    print(f"[{'ok' if sentinel_result.returncode == 0 else 'FAIL'}] Task Scheduler task '{SENTINEL_TASK_NAME}'")


def wait_for_dashboard(timeout_s: float = 60.0) -> bool:
    """watchdog.py sleeps 10 s at startup before launching monitor.py, so the
    dashboard isn't up the moment its task starts. Waiting here lets the
    installer open the browser on a page that actually loads."""
    import time
    import urllib.request
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            urllib.request.urlopen("http://127.0.0.1:5151/status", timeout=3)
            print("[ok] dashboard is up at http://localhost:5151")
            return True
        except Exception:
            time.sleep(1)
    print(f"[WARN] dashboard not reachable after {timeout_s:.0f}s -- it should come up at next logon")
    return False


def stop_aoc_processes() -> None:
    """Ends the scheduled tasks' current runs and kills every python
    process running a script from this AOC_DIR (monitor, watchdog,
    sentinel). The installer runs this before replacing files on an
    upgrade: a running monitor keeps the bundled python DLLs locked."""
    for task in (TASK_NAME, SENTINEL_TASK_NAME):
        subprocess.run(["schtasks", "/End", "/TN", task], capture_output=True, text=True)
    # Normalized match: the hook launches monitor.py with forward slashes,
    # the scheduled tasks with backslashes, and case can differ too.
    aoc_dir = os.path.normpath(AOC_DIR).lower().replace("'", "''")
    ps = ("Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
          f"Where-Object {{ $_.ProcessId -ne {os.getpid()} -and $_.CommandLine -and "
          f"$_.CommandLine.Replace('/', '\\').ToLower().Contains('{aoc_dir}\\') }} | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $_.ProcessId }")
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                            capture_output=True, text=True, timeout=60,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    pids = result.stdout.split()
    print(f"[ok] stopped {len(pids)} AOC process(es)" + (f": {', '.join(pids)}" if pids else ""))


def uninstall() -> None:
    """Reverses main(): hooks first (so a running Claude Code session can't
    respawn monitor.py through ensure_monitor), then the scheduled tasks
    (so sentinel can't revive watchdog), then the processes. Leaves the
    user's data (%LOCALAPPDATA%\\AOC: history.db, logs, backups) alone."""
    settings = _load_settings()
    removed = _remove_hook_entries(settings)
    chain = None
    try:
        with open(STATUSLINE_CHAIN_FILE, encoding="utf-8") as f:
            chain = json.load(f).get("statusLine")
    except Exception:
        pass
    sl_changed = _remove_statusline(settings, chain)
    if removed or sl_changed:
        _save_settings(settings)
    print(f"[ok] removed {removed} AOC hook entr{'y' if removed == 1 else 'ies'} from {SETTINGS_FILE}")
    if sl_changed:
        print("[ok] " + ("restored your previous statusLine" if chain else "removed the AOC statusLine"))

    for name in ("aoc_hook.py", "run_hook.pyw", STATUSLINE_SCRIPT, PERMISSION_SCRIPT, os.path.basename(STATUSLINE_CHAIN_FILE)):
        try:
            os.remove(os.path.join(HOOKS_DST_DIR, name))
            print(f"[ok] deleted {os.path.join(HOOKS_DST_DIR, name)}")
        except FileNotFoundError:
            pass

    for task in (TASK_NAME, SENTINEL_TASK_NAME):
        subprocess.run(["schtasks", "/End", "/TN", task], capture_output=True, text=True)
        r = subprocess.run(["schtasks", "/Delete", "/TN", task, "/F"], capture_output=True, text=True)
        print(f"[{'ok' if r.returncode == 0 else 'skip'}] delete task '{task}'")

    stop_aoc_processes()


def main():
    action, why = _check_args(sys.argv)
    if action == "help":
        print(__doc__.strip())
        return
    if action == "error":
        print(f"setup.py: {why}. Nothing was changed.", file=sys.stderr)
        print("Run `python setup.py --help` for usage.", file=sys.stderr)
        sys.exit(2)

    if sys.platform != "win32":
        print("AOC's hook/watchdog/notification code is Windows-only — this installer won't work elsewhere.")
        sys.exit(1)

    if "--stop" in sys.argv:
        stop_aoc_processes()
        return
    if "--uninstall" in sys.argv:
        uninstall()
        return

    pythonw = _find_pythonw()
    print(f"AOC dir:      {AOC_DIR}")
    print(f"pythonw.exe:  {pythonw}")
    print(f"hooks target: {HOOKS_DST_DIR}")
    if DRY_RUN:
        print("mode:         DRY RUN -- no files will be written or tasks created/modified")
    print()

    deploy_hook_scripts(pythonw)
    deploy_cloud_config()
    merge_hook_settings(pythonw)
    merge_statusline_settings(pythonw)
    register_watchdog_task(pythonw)
    register_sentinel_task(pythonw)

    if DRY_RUN:
        print()
        print("Dry run complete -- nothing was changed. Run without --dry-run to apply.")
        return

    validate()
    wait_for_dashboard()

    print()
    print("Done. Already-open Claude Code sessions won't pick up the new hook")
    print("config until restarted — start a new session and check")
    print("http://localhost:5151 for it to show up.")


if __name__ == "__main__":
    main()
