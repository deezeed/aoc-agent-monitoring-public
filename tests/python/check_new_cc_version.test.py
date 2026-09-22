"""Tests _check_new_cc_version, extracted straight from monitor.py. This
is the early-warning signal for a Claude Code update silently changing
the transcript/hook format the transcript-scanner fallback depends on
(undocumented internals) -- fires a toast the first time a session's
cc_version is seen, never again for that same version. Zero prior test
coverage."""
import sys, os, threading

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker


def make_ns(known=None):
    toasts = []
    saved = []
    ns = exec_functions(["_check_new_cc_version"], {
        "_known_cc_versions": set(known or []),
        "_known_cc_versions_lock": threading.Lock(),
        "_save_known_cc_versions": lambda versions: saved.append(set(versions)),
        "_show_native_toast": lambda title, msg: toasts.append((title, msg)),
    })
    return ns, toasts, saved


c = Checker()

# 1. A genuinely new version fires a toast and persists the updated set
ns, toasts, saved = make_ns()
ns["_check_new_cc_version"]("2.1.0")
c.check("new version fires exactly one toast", len(toasts) == 1)
c.check("toast mentions the new version", "2.1.0" in toasts[0][1])
c.check("_save_known_cc_versions called with the version added", saved and "2.1.0" in saved[-1])

# 2. The same version seen again never refires (the whole point --
# "once per version", not once per session)
ns2, toasts2, saved2 = make_ns(known=["2.1.0"])
ns2["_check_new_cc_version"]("2.1.0")
c.check("already-known version does not fire a toast", len(toasts2) == 0)
c.check("already-known version does not re-persist the set", len(saved2) == 0)

# 3. A second, different new version fires again independently
ns3, toasts3, saved3 = make_ns(known=["2.1.0"])
ns3["_check_new_cc_version"]("2.2.0")
c.check("a different new version fires its own toast", len(toasts3) == 1 and "2.2.0" in toasts3[0][1])

# 4. Empty/falsy version is a no-op -- never crashes, never toasts
ns4, toasts4, saved4 = make_ns()
ns4["_check_new_cc_version"]("")
ns4["_check_new_cc_version"](None)
c.check("empty/None version never fires a toast", len(toasts4) == 0)
c.check("empty/None version never persists", len(saved4) == 0)

# 5. A toast failure (e.g. _show_native_toast not yet defined at
# module-load time, per the real function's own docstring on this) must
# not propagate -- the version is still recorded even if the toast itself
# blows up.
def _raising_toast(title, msg):
    raise RuntimeError("not defined yet")

toasts5 = []
saved5 = []
ns5 = exec_functions(["_check_new_cc_version"], {
    "_known_cc_versions": set(),
    "_known_cc_versions_lock": threading.Lock(),
    "_save_known_cc_versions": lambda versions: saved5.append(set(versions)),
    "_show_native_toast": _raising_toast,
})
raised = False
try:
    ns5["_check_new_cc_version"]("3.0.0")
except Exception:
    raised = True
c.check("a failing _show_native_toast does not propagate out of _check_new_cc_version", not raised)
c.check("the version is still persisted even when the toast itself fails", saved5 and "3.0.0" in saved5[-1])

c.finish()
