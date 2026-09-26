"""
AOC Sentinel — the watchdog's own watchdog.

watchdog.py restarts monitor.py when it stops responding, but nothing
watches watchdog.py itself. Task Scheduler's own "restart on failure"
setting on the AOC Watchdog task only fires 3 times if watchdog.py's
*process* exits/crashes -- it never catches a hang (still running, stuck
loop), and gives up permanently after 3 attempts until the next logon.

This script runs independently on its own periodic Task Scheduler trigger
(not just at logon) and just tries to (re)launch watchdog.py every time.
No PID-liveness checking is duplicated here: watchdog.py's own
_acquire_lock() already refuses to run a second instance and exits near-
instantly if a real one is alive, so launching it unconditionally is safe
and cheap when everything is already healthy. The only extra step is
telling the two cases apart (already-alive vs. genuinely revived) so the
toast is only shown when it actually did something.
"""
from __future__ import annotations
import os
import subprocess
import sys
import time
import datetime

AOC_DIR    = os.path.dirname(os.path.abspath(__file__))
WATCHDOG   = os.path.join(AOC_DIR, "watchdog.py")
LOG_FILE   = os.path.join(AOC_DIR, "sentinel.log")
LOG_MAX_BYTES = 200_000

PYTHONW = os.path.join(
    os.path.dirname(sys.executable),
    "pythonw.exe" if sys.platform == "win32" else "python"
)
if not os.path.exists(PYTHONW):
    PYTHONW = sys.executable

_AOC_AUMID = "AOC.AgentOperationsCenter"
_AOC_SHORTCUT_NAME = "AOC Agent Operations Center.lnk"


def _show_native_toast(title: str, message: str) -> None:
    """Duplicated from watchdog.py's own copy (itself duplicated from
    monitor.py) -- same reasoning: standalone script, no import
    relationship, and the whole point is notifying when the *other* two
    processes can't notify about their own state."""
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
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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


def main() -> None:
    if sys.platform != "win32":
        return
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        proc = subprocess.Popen(
            [PYTHONW, WATCHDOG],
            startupinfo=si,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except Exception as e:
        _log(f"failed to launch watchdog.py: {e}")
        return

    # A watchdog.py that loses the _acquire_lock() race (another instance
    # already alive) returns and exits within a few statements -- well
    # under a second. One that wins it sleeps 10s before doing anything
    # else. Still alive after 3s means this launch was the one that
    # actually revived a dead watchdog, not a harmless no-op deferral.
    time.sleep(3)
    if proc.poll() is None:
        _log("watchdog.py was not running -- launched a fresh instance")
        _show_native_toast("AOC Sentinel", "watchdog.py had stopped entirely -- restarted it")
    else:
        _log("watchdog.py already running -- deferred as expected, no action needed")


if __name__ == "__main__":
    main()
