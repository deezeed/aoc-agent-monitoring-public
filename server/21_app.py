# AOC server, part 21: stall diagnostics, server start, tray, toasts, app mode, __main__.
# monitor.py exec()s server/NN_*.py in number order into ONE shared
# namespace (its own) -- not an importable module: names from earlier
# parts are plain globals here, and __file__ is monitor.py.
# ── /status stall diagnostics ────────────────────────────────────────────────
# watchdog.py restarts monitor.py when two consecutive /status probes exceed
# its 4s timeout, and that still happens a few times a day with no code change
# and no error anywhere (pythonw.exe has no console, so nothing is printed).
# This probes our own /status the same way watchdog does, with
# faulthandler.dump_traceback_later armed around each probe: if the probe is
# still pending after _STALL_THRESHOLD_S, every thread's stack is written to
# STALL_LOG *while* the stall is in progress. faulthandler dumps from its own
# native thread without needing the GIL, so this still works when the stall is
# one thread hogging the GIL (a pure-Python dump would only get to run after
# the hog had already finished and moved on).
STALL_LOG = os.path.join(LOGS_DIR, "status_stalls.log")
_STALL_PROBE_INTERVAL_S = 5
_STALL_THRESHOLD_S = 2.0
_STALL_LOG_MAX_BYTES = 1_000_000

def _open_stall_log():
    """Open STALL_LOG for appending, rotating once to .1 past the size cap."""
    os.makedirs(os.path.dirname(STALL_LOG), exist_ok=True)
    if os.path.exists(STALL_LOG) and os.path.getsize(STALL_LOG) > _STALL_LOG_MAX_BYTES:
        os.replace(STALL_LOG, STALL_LOG + ".1")
    return open(STALL_LOG, "a", encoding="utf-8")

def _probe_status_once(probe, threshold_s: float, log_file):
    """Run `probe` (a blocking /status fetch) with a faulthandler stack dump
    to log_file armed for threshold_s. Returns the elapsed seconds if the
    probe took at least threshold_s (the dump has then been written, followed
    here by a summary line naming the threads), else None."""
    import faulthandler
    log_file.flush()
    size_before = os.fstat(log_file.fileno()).st_size
    # monotonic, not time.time(): a wall-clock adjustment mid-probe must not
    # read as a stall
    started = time.monotonic()
    faulthandler.dump_traceback_later(threshold_s, repeat=False, file=log_file)
    try:
        probe()
    except Exception:
        pass
    finally:
        faulthandler.cancel_dump_traceback_later()
    elapsed = time.monotonic() - started
    if elapsed < threshold_s:
        return None
    # faulthandler's timer is a native wait that doesn't count time the
    # machine spent suspended, so a "stall" can cross the threshold here with
    # no dump written (seen 2026-09-28 14:38) -- say so instead of leaving a
    # summary line that points at stacks that aren't there
    dumped = os.fstat(log_file.fileno()).st_size > size_before
    # faulthandler prints thread ids only, as "0x%0*lx" padded to
    # sizeof(unsigned long) -- 8 hex digits on Windows, 16 on 64-bit POSIX
    import struct
    width = struct.calcsize("L") * 2
    # A thread that has been start()ed but isn't running yet is listed with
    # ident None -- formatting that as hex raised, and the summary line was
    # lost exactly when threads were being spawned mid-stall.
    names = ", ".join(f"0x{t.ident:0{width}x}={t.name}" if t.ident is not None else f"(starting)={t.name}"
                      for t in threading.enumerate())
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    where = (f"stacks above dumped at {threshold_s:.0f}s" if dumped else
             "NO stack dump fired -- the process was likely suspended or starved, not stuck")
    log_file.write(f"===== {stamp} /status probe took {elapsed:.1f}s "
                   f"({where}; threads: {names})\n\n")
    log_file.flush()
    return elapsed

def _status_stall_worker():
    import urllib.request as _ur
    probe = lambda: _ur.urlopen(f"http://127.0.0.1:{PORT}/status", timeout=30).read()
    while True:
        time.sleep(_STALL_PROBE_INTERVAL_S)
        try:
            with _open_stall_log() as log_file:
                _probe_status_once(probe, _STALL_THRESHOLD_S, log_file)
        except Exception as e:
            _log_bg_error("_status_stall_worker", e)


def _start_server():
    """Start the HTTP server in the current thread (blocking)."""
    class _Server(ThreadingHTTPServer):
        allow_reuse_address = True
    _Server(("0.0.0.0", PORT), Handler).serve_forever()


def _make_tray_icon():
    """Build a 64x64 AOC hexagon tray icon using Pillow."""
    from PIL import Image, ImageDraw
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy, r = size // 2, size // 2, size // 2 - 4
    import math
    pts = [(cx + r * math.cos(math.radians(a - 90)), cy + r * math.sin(math.radians(a - 90)))
           for a in range(0, 360, 60)]
    d.polygon(pts, fill=(0, 196, 232, 220), outline=(0, 232, 200, 255))
    d.ellipse([cx - 12, cy - 12, cx + 12, cy + 12], fill=(0, 130, 160, 200))
    return img


_STARTUP_LNK = os.path.join(
    os.environ.get("APPDATA", ""),
    "Microsoft", "Windows", "Start Menu", "Programs", "Startup", "AOC Monitor.lnk"
)

def _is_startup_enabled():
    return os.path.exists(_STARTUP_LNK)

def _toggle_startup():
    if _is_startup_enabled():
        try: os.remove(_STARTUP_LNK)
        except Exception: pass
    else:
        try:
            import winshell  # optional; fall back to VBScript
        except ImportError:
            winshell = None
        if winshell:
            with winshell.shortcut(_STARTUP_LNK) as sc:
                sc.path = r"C:\Users\marek\AppData\Local\Programs\Python\Python39\pythonw.exe"
                sc.arguments = f'"{os.path.abspath(__file__)}" --app'
                sc.working_directory = AOC_DIR
                sc.description = "AOC Agent Operations Center"
        else:
            # Use VBScript as fallback (always available on Windows)
            vbs = f"""
Set WS = CreateObject("WScript.Shell")
Set sc = WS.CreateShortcut("{_STARTUP_LNK}")
sc.TargetPath = "C:\\Users\\marek\\AppData\\Local\\Programs\\Python\\Python39\\pythonw.exe"
sc.Arguments = Chr(34) & "{os.path.abspath(__file__)}" & Chr(34) & " --app"
sc.WorkingDirectory = "{AOC_DIR}"
sc.Description = "AOC Agent Operations Center"
sc.Save
""".strip()
            tmp_vbs = os.path.join(os.environ.get("TEMP", AOC_DIR), "_aoc_startup.vbs")
            with open(tmp_vbs, "w") as f:
                f.write(vbs)
            subprocess.run(["cscript", "//nologo", tmp_vbs], creationflags=_NO_WINDOW)
            try: os.remove(tmp_vbs)
            except Exception: pass


_AOC_AUMID = "AOC.AgentOperationsCenter"
_AOC_SHORTCUT_NAME = "AOC Agent Operations Center.lnk"

def _register_aumid_shortcut():
    """One-time (idempotent -- checks Test-Path first, same as the shortcut
    check here) registration of AOC's own AppUserModelID via a Start Menu
    shortcut, so _show_native_toast's notifications carry AOC's own name/icon
    instead of borrowing powershell.exe's. WshShell.CreateShortcut can't set
    the System.AppUserModel.ID property -- only IShellLinkW's IPropertyStore
    can -- hence the COM interop via Add-Type. Best-effort: any failure here
    just means _show_native_toast keeps using the powershell.exe AUMID
    fallback, which already works fine, so this never blocks startup."""
    try:
        shortcut_path = os.path.join(
            os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", _AOC_SHORTCUT_NAME
        )
        if os.path.exists(shortcut_path):
            return
        ps_script = r'''
$AppId = "''' + _AOC_AUMID + r'''"
$ShortcutPath = "''' + shortcut_path.replace("\\", "\\\\") + r'''"

Add-Type @"
using System;
using System.Runtime.InteropServices;

[ComImport, Guid("00021401-0000-0000-C000-000000000046")]
internal class CShellLink { }

[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("000214F9-0000-0000-C000-000000000046")]
internal interface IShellLinkW {
    void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszFile, int cchMaxPath, IntPtr pfd, uint fFlags);
    void GetIDList(out IntPtr ppidl);
    void SetIDList(IntPtr pidl);
    void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszName, int cchMaxName);
    void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
    void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszDir, int cchMaxPath);
    void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
    void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszArgs, int cchMaxPath);
    void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
    void GetHotkey(out short pwHotkey);
    void SetHotkey(short wHotkey);
    void GetShowCmd(out int piShowCmd);
    void SetShowCmd(int iShowCmd);
    void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszIconPath, int cchIconPath, out int piIcon);
    void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
    void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, uint dwReserved);
    void Resolve(IntPtr hwnd, uint fFlags);
    void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
}

[StructLayout(LayoutKind.Sequential, Pack = 4)]
internal struct PROPERTYKEY {
    public Guid fmtid;
    public int pid;
    public PROPERTYKEY(Guid fmtid, int pid) { this.fmtid = fmtid; this.pid = pid; }
}

[StructLayout(LayoutKind.Explicit)]
internal struct PROPVARIANT_UNION {
    [FieldOffset(0)] public IntPtr pwszVal;
}

[StructLayout(LayoutKind.Sequential)]
internal struct PROPVARIANT {
    public ushort vt;
    public ushort wReserved1;
    public ushort wReserved2;
    public ushort wReserved3;
    public PROPVARIANT_UNION union;

    public static PROPVARIANT FromString(string s) {
        PROPVARIANT pv = new PROPVARIANT();
        pv.vt = 31; // VT_LPWSTR
        pv.union.pwszVal = Marshal.StringToCoTaskMemUni(s);
        return pv;
    }
    public void Clear() {
        if (union.pwszVal != IntPtr.Zero) { Marshal.FreeCoTaskMem(union.pwszVal); union.pwszVal = IntPtr.Zero; }
    }
}

[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")]
internal interface IPropertyStore {
    int GetCount(out uint propertyCount);
    int GetAt(uint propertyIndex, out PROPERTYKEY key);
    int GetValue(ref PROPERTYKEY key, out PROPVARIANT pv);
    int SetValue(ref PROPERTYKEY key, ref PROPVARIANT pv);
    int Commit();
}

[ComImport, Guid("0000010b-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IPersistFile {
    void GetClassID(out Guid pClassID);
    int IsDirty();
    void Load([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, uint dwMode);
    void Save([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, bool fRemember);
    void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string pszFileName);
    void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string ppszFileName);
}

public class ShortcutHelper {
    static readonly PROPERTYKEY PKEY_AppUserModel_ID = new PROPERTYKEY(new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), 5);

    public static void CreateWithAumid(string shortcutPath, string targetPath, string aumid, string description) {
        IShellLinkW link = (IShellLinkW)new CShellLink();
        link.SetPath(targetPath);
        link.SetArguments("");
        link.SetDescription(description);

        IPropertyStore propStore = (IPropertyStore)link;
        PROPVARIANT pv = PROPVARIANT.FromString(aumid);
        PROPERTYKEY key = PKEY_AppUserModel_ID;
        propStore.SetValue(ref key, ref pv);
        propStore.Commit();
        pv.Clear();

        IPersistFile file = (IPersistFile)link;
        file.Save(shortcutPath, true);
    }
}
"@

if (-not (Test-Path $ShortcutPath)) {
    [ShortcutHelper]::CreateWithAumid($ShortcutPath, "$PSHOME\powershell.exe", $AppId, "AOC - Agent Operations Center")
}
'''
        tmp_ps1 = os.path.join(os.environ.get("TEMP", AOC_DIR), "_aoc_register_aumid.ps1")
        try:
            with open(tmp_ps1, "w", encoding="utf-8") as f:
                f.write(ps_script)
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", tmp_ps1],
                creationflags=_NO_WINDOW, timeout=15,
            )
        finally:
            try:
                os.remove(tmp_ps1)
            except Exception:
                pass
    except Exception as e:
        _log_bg_error("_register_aumid_shortcut", e)


def _show_native_toast(title: str, message: str, kind=None, project=""):
    """Toast + phone push for one alert. With kind= (a _NOTIFY_TYPES key)
    each channel goes through _notify_gate (the type's Settings -> Notify
    toggles, muted projects, quiet hours) and lands in the notification
    log; without it both channels fire as before."""
    toast_ok = push_ok = True
    if kind:
        toast_ok = _notify_gate(kind, "toast", project)[0]
        push_ok = _notify_gate(kind, "push", project)[0]
    if push_ok:
        try:
            if _push_notify(title, message, tag=str(title)[:40]):
                _notify_log_add(kind, "push", title, message)
        except Exception as e:
            _log_bg_error("push", e)
    if toast_ok:
        _win_toast(title, message)
        _notify_log_add(kind, "toast", title, message)


def _win_toast(title: str, message: str):
    """Fire a real Windows notification without any extra dependency (no
    pystray/pywebview needed -- those only run in --app/tray mode, but the
    watchdog actually runs monitor.py --headless, where _tray_monitor's own
    icon.notify() below never executes at all).

    Tried System.Windows.Forms.NotifyIcon.ShowBalloonTip first (needs no
    AppUserModelID) -- live-tested, confirmed it does NOT actually show
    anything on this machine (likely Windows deprioritizing/dropping legacy
    balloon tips outright, not a Focus Assist block -- ToastEnabled=1, no
    quiet-hours flag set). Switched to the real WinRT toast API instead.
    First used powershell.exe's own already-registered AUMID (live-tested,
    worked) as a fallback-safe default; now prefers AOC's own AUMID
    (_register_aumid_shortcut, called once at startup) for AOC's own
    name/icon on the notification instead of PowerShell's -- also
    live-tested and confirmed showing distinctly from the borrowed one.
    Toast only -- _show_native_toast adds the phone push."""
    if AOC_PREVIEW:
        return  # a preview instance never pings you (see AOC_PREVIEW)
    try:
        def xml_esc(s: str) -> str:
            return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        def ps_esc(s: str) -> str:
            return str(s).replace("'", "''")
        title_x = ps_esc(xml_esc(title))
        msg_x = ps_esc(xml_esc(message))
        # Use AOC's own AUMID only if its shortcut actually exists -- checked
        # at call time (cheap stat), not assumed from _register_aumid_shortcut
        # having been *attempted*, since that call is itself best-effort and
        # could have failed. Falls back to the always-registered
        # powershell.exe AUMID so a failed registration never regresses
        # notifications back to completely silent.
        aumid_shortcut = os.path.join(
            os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", _AOC_SHORTCUT_NAME
        )
        aumid = _AOC_AUMID if os.path.exists(aumid_shortcut) else \
            r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
        # activationType="protocol" is fully OS-handled (opens the URL
        # directly) -- no click-handler code needed on either end, whether
        # the user clicks the toast body itself (launch=) or the explicit
        # button (the <action> below).
        dashboard_url = f"http://localhost:{PORT}"
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
            creationflags=_NO_WINDOW,
        )
    except Exception as e:
        _log_bg_error("_show_native_toast", e)


def _headless_notify_worker():
    """Same running->done/error transition detection as _tray_monitor below,
    but using _show_native_toast instead of pystray's icon.notify() -- for
    --headless (the mode actually run by the watchdog) and plain CLI mode,
    neither of which have a pystray Icon event loop to call .notify() on."""
    _prev = {}
    while True:
        try:
            time.sleep(3)
            d = _own_status_payload()
            if d is None:
                continue
            agents = {a["id"]: a for a in (d.get("agents") or []) if not str(a.get("id", "")).startswith("hook_")}
            for aid, a in agents.items():
                old = _prev.get(aid, {})
                if old.get("status") == "running" and not _notification_suppressed(a.get("session_project", "")):
                    if a.get("status") == "done":
                        _show_native_toast(f"✓ {a.get('name','Agent')[:40]}", "Completed successfully",
                                           kind="done", project=a.get("session_project", ""))
                    elif a.get("status") == "error":
                        # "Agent encountered an error" told you nothing an
                        # error toast couldn't already imply from its own ✗ --
                        # the actual error_message was sitting right there on
                        # `a` unused, same gap the done/error webhook payload
                        # had (see _webhook_notify_worker).
                        _show_native_toast(f"✗ {a.get('name','Agent')[:40]}",
                                            (a.get("error_message") or "Agent encountered an error")[:180],
                                            kind="error", project=a.get("session_project", ""))
            _prev = agents
        except Exception as e:
            _log_bg_error("_headless_notify_worker", e)


def _run_app_mode():
    """System tray + pywebview native window mode."""
    import pystray, webview

    URL = f"http://127.0.0.1:{PORT}"
    _win = [None]   # mutable reference to pywebview window

    def open_window():
        if _win[0] is not None:
            try:
                _win[0].show()
                return
            except Exception:
                pass
        w = webview.create_window(
            "AOC — Agent Operations Center",
            URL,
            width=1280, height=800,
            min_size=(900, 600),
            background_color="#050815",
        )
        _win[0] = w
        webview.start(gui="edgechromium", debug=False)
        _win[0] = None   # window closed

    def on_tray_click(icon, item):
        threading.Thread(target=open_window, daemon=True).start()

    def on_quit(icon, item):
        icon.stop()
        _release_monitor_lock()  # os._exit below skips atexit -- release explicitly
        os._exit(0)

    def on_toggle_startup(icon, item):
        _toggle_startup()
        icon.update_menu()

    def startup_checked(item):
        return _is_startup_enabled()

    menu = pystray.Menu(
        pystray.MenuItem("Open Dashboard", on_tray_click, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Start with Windows", on_toggle_startup, checked=startup_checked),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit AOC", on_quit),
    )
    icon = pystray.Icon("AOC", _make_tray_icon(), "AOC — Agent Operations Center", menu)

    def _tray_monitor():
        _prev = {}
        while True:
            try:
                time.sleep(3)
                d = _own_status_payload()
                if d is None:
                    continue
                agents = {a["id"]: a for a in (d.get("agents") or []) if not str(a.get("id","")).startswith("hook_")}
                running = sum(1 for a in agents.values() if a.get("status") in ("running","waiting"))
                icon.title = f"AOC — {running} running" if running else "AOC — Agent Operations Center"
                for aid, a in agents.items():
                    old = _prev.get(aid, {})
                    if old.get("status") == "running" and not _notification_suppressed(a.get("session_project", "")):
                        if not _notify_gate("done" if a.get("status") == "done" else "error", "toast", a.get("session_project", ""))[0]:
                            pass
                        elif a.get("status") == "done":
                            try: icon.notify(f"✓ {a.get('name','Agent')[:40]}", "Completed successfully")
                            except Exception: pass
                        elif a.get("status") == "error":
                            # Same fix as _headless_notify_worker's own error
                            # branch above -- error_message was available but
                            # unused, so --app mode's tray balloon was just as
                            # content-free as --headless's toast used to be.
                            try: icon.notify(f"✗ {a.get('name','Agent')[:40]}",
                                              (a.get("error_message") or "Agent encountered an error")[:180])
                            except Exception: pass
                _prev = agents
            except Exception as e:
                _log_bg_error("_tray_monitor", e)

    threading.Thread(target=_tray_monitor, daemon=True).start()
    # Open window immediately on first launch
    threading.Thread(target=open_window, daemon=True).start()
    icon.run()


_module_loaded.set()

if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    headless = "--headless" in args
    app_mode = "--app" in args or (not headless and "pythonw" in sys.executable.lower())

    # ── single-instance guard ────────────────────────────────────────────────
    # ThreadingHTTPServer below sets allow_reuse_address=True (SO_REUSEADDR) so a
    # restart can quickly rebind past TIME_WAIT — but on Windows, SO_REUSEADDR
    # also lets a *second* process bind the same port while the first is still
    # actively listening, instead of just tolerating TIME_WAIT like on POSIX.
    # A watchdog restart racing a manual restart can silently spawn a second
    # (third, fourth...) monitor.py that coexists on the same port with its own
    # separate in-memory state — observed today: 5 stray processes accumulated
    # after repeated restarts, with requests routed unpredictably between them
    # (stale session data winning a race, a tunnel-stop call landing on a
    # process that never started the tunnel it was asked to stop).
    #
    # This used to be a urllib probe against /status: refuse to start if
    # something's already answering. That check itself raced its own
    # subject — it ran before *this* process bound its own listening
    # socket, so a second instance starting during the several-hundred-ms
    # (sometimes multi-second, see the /status-readiness poll below) window
    # between "process A passed the check" and "process A is actually
    # listening" would also see nothing answering and proceed. A PID file
    # written with O_CREAT|O_EXCL is atomic at the OS level (the create
    # itself fails if the file exists, no separate check-then-write gap to
    # race), closing that window instead of narrowing it. Stale-lock
    # detection reuses _pid_exe_name, the same technique watchdog.py's own
    # lock file (watchdog.pid) uses, so a PID recycled by an unrelated
    # process after a crash doesn't wedge every future start.
    # one lock per port: a preview (AOC_PORT) must not be refused because
    # the real AOC holds monitor.pid in the same folder
    _MONITOR_PID_FILE = os.path.join(AOC_DIR, "monitor.pid" if PORT == 5151 else f"monitor-{PORT}.pid")

    def _acquire_monitor_lock(max_attempts: int = 5) -> bool:
        for _ in range(max_attempts):
            try:
                fd = os.open(_MONITOR_PID_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w") as f:
                    f.write(str(os.getpid()))
                return True
            except FileExistsError:
                try:
                    with open(_MONITOR_PID_FILE) as f:
                        old_pid = int(f.read().strip())
                except Exception:
                    old_pid = None  # unreadable/corrupt -- treat as stale below
                if old_pid and old_pid != os.getpid() and _pid_exe_name(old_pid) in ("python.exe", "pythonw.exe"):
                    return False  # a real instance is holding the lock
                # Stale lock (holder gone, or its PID got recycled by something
                # else entirely) -- clear it and retry rather than wedging
                # every future start the way watchdog.py's own docstring warns
                # its equivalent check could.
                try:
                    os.remove(_MONITOR_PID_FILE)
                except Exception:
                    return False
        return False  # kept losing the race across every retry -- don't guess

    def _release_monitor_lock() -> None:
        try:
            with open(_MONITOR_PID_FILE) as f:
                if int(f.read().strip()) == os.getpid():
                    os.remove(_MONITOR_PID_FILE)
        except Exception:
            pass

    if not _acquire_monitor_lock():
        print(f"AOC is already running on port {PORT} — not starting a second instance.")
        sys.exit(0)
    atexit.register(_release_monitor_lock)

    # We're about to become the sole active instance — reap any tunnel child a
    # previous, now-dead instance left running (see _reap_stray_tunnel_process).
    _reap_stray_tunnel_process()

    if "--tunnel" in args:
        _start_tunnel_worker_thread()

    # Start HTTP server in background thread (all modes)
    srv_thread = threading.Thread(target=_start_server, daemon=True)
    srv_thread.start()
    threading.Thread(target=_status_stall_worker, daemon=True, name="status-stall-worker").start()
    threading.Thread(target=_shared_sweeper, daemon=True, name="shared-sweeper").start()

    # Wait for server to be ready
    import urllib.request
    for _ in range(20):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/status", timeout=1)
            break
        except Exception:
            time.sleep(0.3)

    if headless:
        # Pure headless: server runs forever, no UI
        threading.Thread(target=_register_aumid_shortcut, daemon=True).start()
        threading.Thread(target=_headless_notify_worker, daemon=True).start()
        srv_thread.join()
    elif app_mode:
        # System tray + pywebview native window
        _run_app_mode()
    else:
        # CLI mode: open browser tab
        threading.Thread(target=_register_aumid_shortcut, daemon=True).start()
        threading.Thread(target=_headless_notify_worker, daemon=True).start()
        print(f"\n  AOC — Agent Operations Center -> http://localhost:{PORT}")
        print("  Ctrl+C to stop\n")
        threading.Thread(
            target=lambda: (time.sleep(0.5), webbrowser.open(f"http://localhost:{PORT}")),
            daemon=True,
        ).start()
        srv_thread.join()
