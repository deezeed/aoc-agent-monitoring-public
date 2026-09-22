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


CLOUD_URL = _get_arg_value("--cloud-url")
CLOUD_KEY = _get_arg_value("--cloud-key")

AOC_DIR = os.path.dirname(os.path.abspath(__file__))
MONITOR_SCRIPT = os.path.join(AOC_DIR, "monitor.py")
WATCHDOG_SCRIPT = os.path.join(AOC_DIR, "watchdog.py")
SENTINEL_SCRIPT = os.path.join(AOC_DIR, "sentinel.py")

HOOKS_SRC_DIR = os.path.join(AOC_DIR, "hooks")
HOOKS_DST_DIR = os.path.join(os.path.expanduser("~"), ".claude", "hooks")
SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")

TASK_NAME = r"\AOC\AOC Watchdog"
SENTINEL_TASK_NAME = r"\AOC\AOC Sentinel"
SENTINEL_INTERVAL_MIN = 15  # Task Scheduler's own restart-on-failure only fires 3x for watchdog.py's process exiting; this catches it hanging or giving up entirely

# (event, matcher) pairs AOC needs — matches ~/.claude/settings.json's shape
HOOK_SPECS = [
    ("UserPromptSubmit", ""),
    ("PreToolUse", "Agent"),
    ("PostToolUse", "Agent"),
    ("Stop", ""),
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

    for src_name in ("aoc_hook.py", "run_hook.pyw"):
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
        print(f"[ok] deployed aoc_hook.py + run_hook.pyw -> {HOOKS_DST_DIR}")


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


def merge_hook_settings(pythonw: str) -> None:
    """Adds AOC's 4 hook entries to ~/.claude/settings.json without
    disturbing anything else already there (other hooks, enabledPlugins,
    theme, etc.). Idempotent: checks for an existing run_hook.pyw command
    under the right matcher before appending, so re-running never
    duplicates entries."""
    command = (pythonw.replace("\\", "/") + " " +
               os.path.join(HOOKS_DST_DIR, "run_hook.pyw").replace("\\", "/"))

    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            settings = json.load(f)
    else:
        settings = {}

    hooks = settings.setdefault("hooks", {})
    to_add = []
    for event, matcher in HOOK_SPECS:
        entries = hooks.get(event, [])
        already = any(
            entry.get("matcher", "") == matcher and
            any(h.get("command", "").replace("\\", "/").endswith("run_hook.pyw")
                for h in entry.get("hooks", []))
            for entry in entries
        )
        if not already:
            to_add.append((event, matcher))

    if DRY_RUN:
        if to_add:
            print(f"[dry-run] would add hook entries to {SETTINGS_FILE} for: " +
                  ", ".join(f"{e}/{m or '(no matcher)'}" for e, m in to_add))
        else:
            print(f"[dry-run] {SETTINGS_FILE} already has all AOC hook entries, no change")
        return

    for event, matcher in to_add:
        hooks.setdefault(event, []).append({
            "matcher": matcher,
            "hooks": [{"type": "command", "command": command}],
        })

    if to_add:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        print(f"[ok] added AOC hook entries to {SETTINGS_FILE}")
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
        missing = [e for e, _ in HOOK_SPECS if e not in events]
        if missing:
            print(f"[WARN] settings.json missing hook events: {missing}")
        else:
            print("[ok] settings.json has all 4 AOC hook events")
    except Exception as e:
        print(f"[FAIL] could not read back settings.json: {e}")

    for fname in ("aoc_hook.py", "run_hook.pyw"):
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


def main():
    if sys.platform != "win32":
        print("AOC's hook/watchdog/notification code is Windows-only — this installer won't work elsewhere.")
        sys.exit(1)

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
    register_watchdog_task(pythonw)
    register_sentinel_task(pythonw)

    if DRY_RUN:
        print()
        print("Dry run complete -- nothing was changed. Run without --dry-run to apply.")
        return

    validate()

    print()
    print("Done. Already-open Claude Code sessions won't pick up the new hook")
    print("config until restarted — start a new session and check")
    print("http://localhost:5151 for it to show up.")


if __name__ == "__main__":
    main()
