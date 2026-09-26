"""
AOC Watchdog — keeps monitor.py alive on port 5151.
Polls /status every 30s; restarts after 2 consecutive failures.
Run via Task Scheduler at login (pythonw.exe for no console window).
"""
from __future__ import annotations
import datetime
import os
import subprocess
import sys
import time
import urllib.request

AOC_DIR        = os.path.dirname(os.path.abspath(__file__))
MONITOR        = os.path.join(AOC_DIR, "monitor.py")
LOG_FILE       = os.path.join(AOC_DIR, "watchdog.log")
PID_FILE       = os.path.join(AOC_DIR, "watchdog.pid")
STATUS_URL     = "http://127.0.0.1:5151/status"
CHECK_INTERVAL = 30    # seconds between health checks
FAIL_THRESHOLD = 2     # consecutive failures before restart
LOG_MAX_BYTES  = 200_000
MAX_BACKOFF    = 300   # max seconds between restart attempts

PYTHONW = os.path.join(
    os.path.dirname(sys.executable),
    "pythonw.exe" if sys.platform == "win32" else "python"
)
if not os.path.exists(PYTHONW):
    PYTHONW = sys.executable


_AOC_AUMID = "AOC.AgentOperationsCenter"
_AOC_SHORTCUT_NAME = "AOC Agent Operations Center.lnk"


def _show_native_toast(title: str, message: str) -> None:
    """Duplicated from monitor.py's _show_native_toast (same reasoning: watchdog.py
    is a standalone script, no import relationship between the two -- this
    codebase already duplicates process-kill logic here rather than sharing a
    module, see _kill()'s docstring). Needed here specifically because the whole
    point is notifying that monitor.py itself is unresponsive/restarting --
    monitor.py can't notify about its own crash while it's down."""
    try:
        def xml_esc(s: str) -> str:
            return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        def ps_esc(s: str) -> str:
            return str(s).replace("'", "''")
        title_x = ps_esc(xml_esc(title))
        msg_x = ps_esc(xml_esc(message))
        aumid_shortcut = os.path.join(
            os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", _AOC_SHORTCUT_NAME
        )
        aumid = _AOC_AUMID if os.path.exists(aumid_shortcut) else \
            r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
        # activationType="protocol" is fully OS-handled -- no click-handler
        # code needed, whether the user clicks the toast body (launch=) or
        # the explicit button (the <action> below). Hardcoded port, matching
        # STATUS_URL above -- no shared constant with monitor.py today.
        dashboard_url = "http://localhost:5151"
        toast_xml = (
            f'<toast activationType="protocol" launch="{dashboard_url}">'
            f'<visual><binding template="ToastGeneric"><text>{title_x}</text><text>{msg_x}</text></binding></visual>'
            f'<actions><action activationType="protocol" content="Open Dashboard" arguments="{dashboard_url}"/></actions>'
            f'</toast>'
        )
        ps = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
            "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null; "
            f"$xml = New-Object Windows.Data.Xml.Dom.XmlDocument; "
            f"$xml.LoadXml('{toast_xml}'); "
            "$toast = New-Object Windows.UI.Notifications.ToastNotification $xml; "
            f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{aumid}').Show($toast)"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", ps],
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except Exception as exc:
        _log(f"Failed to show toast: {exc}")


def _log(msg: str) -> None:
    ts   = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_MAX_BYTES:
            with open(LOG_FILE, encoding="utf-8") as f:
                lines = f.readlines()
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines[len(lines) // 2:])
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _alive() -> bool:
    try:
        urllib.request.urlopen(STATUS_URL, timeout=4)
        return True
    except Exception:
        return False


def _start_monitor() -> subprocess.Popen | None:
    try:
        kw: dict = {"cwd": AOC_DIR}
        if sys.platform == "win32":
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
        return subprocess.Popen([PYTHONW, MONITOR, "--headless"], **kw)
    except Exception as exc:
        _log(f"Failed to start monitor: {exc}")
        return None


def _kill(proc: subprocess.Popen | None) -> None:
    """terminate() -> kill() -> taskkill /F, verifying the PID is actually gone.
    Popen.terminate()/kill() have been proven unreliable on this machine for
    other long-running child processes (cloudflared, ngrok — see monitor.py's
    _hard_kill_proc); the same risk applies here. If the old monitor.py
    survives, the fresh one _start_monitor() launches next immediately exits
    via monitor.py's own single-instance port guard, so the "restart" would
    silently do nothing and the unresponsive process stays unresponsive."""
    if proc is None or proc.poll() is not None:
        return
    pid = proc.pid
    try:
        proc.terminate()
        for _ in range(10):
            if proc.poll() is not None:
                return
            time.sleep(0.3)
        proc.kill()
        for _ in range(10):
            if proc.poll() is not None:
                return
            time.sleep(0.3)
    except Exception as exc:
        _log(f"terminate/kill raised for monitor (PID {pid}): {exc}")
    if proc.poll() is None and sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                            capture_output=True, timeout=5,
                            creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception as exc:
            _log(f"taskkill raised for monitor (PID {pid}): {exc}")
    if proc.poll() is None:
        _log(f"Could not kill monitor (PID {pid}) after terminate/kill/taskkill — it may still be running")


def _is_python_process(pid: int) -> bool:
    """Best-effort check that `pid` names an actual python/pythonw process.
    Windows recycles PIDs quickly, so merely finding *some* live process at
    old_pid (the original check) isn't enough — if the watchdog that wrote
    the lock file died and its PID got reused by an unrelated process, the
    old check would wrongly conclude "another watchdog is alive" and this
    instance would refuse to start, silently, forever (nothing runs main()
    afterward, so nothing gets logged either — see _acquire_lock)."""
    try:
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            buf = ctypes.create_unicode_buffer(260)
            size = ctypes.c_uint(260)
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return "python" in buf.value.lower()
            return True  # couldn't read the name but the process exists — assume real, conservative
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        return True  # can't verify — fall back to the original (conservative) assumption


def _acquire_lock() -> bool:
    """Return True if this process is the sole watchdog, False if another is running."""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
            if old_pid != os.getpid() and _is_python_process(old_pid):
                _log(f"Another watchdog appears to be running (PID {old_pid}) — exiting")
                return False  # another watchdog is alive
        except Exception as exc:
            _log(f"Could not read/parse PID file — assuming stale, continuing: {exc}")
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
    except Exception as exc:
        _log(f"Failed to write PID file — lock not persisted: {exc}")
    return True


def _release_lock() -> None:
    try:
        os.remove(PID_FILE)
    except Exception as exc:
        _log(f"Failed to remove PID file on shutdown: {exc}")


def main() -> None:
    if sys.platform == "win32" and not _acquire_lock():
        # Another watchdog instance is already running — exit silently
        return

    _log("=== AOC Watchdog started ===")
    time.sleep(10)  # give OS time to finish login

    proc: subprocess.Popen | None = None
    fails = 0
    backoff = 10

    if not _alive():
        _log("Monitor not running at startup — starting now")
        proc = _start_monitor()
        if proc:
            _log(f"Monitor started (PID {proc.pid})")
        else:
            _log("Monitor failed to start — will retry in loop")
        time.sleep(5)

    while True:
        try:
            time.sleep(CHECK_INTERVAL)

            if _alive():
                if fails > 0:
                    _log("Monitor recovered — back online")
                    backoff = 10
                fails = 0
            else:
                fails += 1
                _log(f"Monitor not responding (fail {fails}/{FAIL_THRESHOLD})")

                if fails >= FAIL_THRESHOLD:
                    _log("Restarting monitor...")
                    _show_native_toast("AOC Watchdog", "monitor.py stopped responding — restarting it now")
                    _kill(proc)
                    time.sleep(backoff)
                    proc = _start_monitor()
                    fails = 0
                    if proc:
                        _log(f"Monitor restarted (PID {proc.pid})")
                        backoff = 10
                    else:
                        _log(f"Monitor failed to restart — next attempt in {backoff}s")
                        _show_native_toast("AOC Watchdog", f"monitor.py failed to restart — retrying in {backoff}s")
                        backoff = min(backoff * 2, MAX_BACKOFF)

        except KeyboardInterrupt:
            _log("Watchdog stopping")
            _kill(proc)
            _release_lock()
            return
        except Exception as exc:
            _log(f"Watchdog loop error: {exc}")
            time.sleep(5)


if __name__ == "__main__":
    main()
