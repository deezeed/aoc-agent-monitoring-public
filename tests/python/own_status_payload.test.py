"""The webhook, headless-toast and tray workers used to GET our own /status
over HTTP every 3 s with a 2 s timeout, so every OS-level stall logged a
timeout per worker. They now read the payload in-process through
_own_status_payload, which must return None until the module has finished
loading (the workers start ~12k lines before the /status builder exists)."""
import ast, os, sys, threading

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions, read_monitor_source, extract_functions
from lib.check import Checker

c = Checker()

loaded = threading.Event()
calls = []
payload = {"agents": [{"id": "a1", "status": "running"}]}
ns = exec_functions(["_own_status_payload"], extra_globals={
    "_module_loaded": loaded,
    "_build_status_payload": lambda: calls.append(1) or payload,
})
own = ns["_own_status_payload"]

c.check("None while the module is still loading", own() is None)
c.check("builder not called before the module is loaded", not calls)
loaded.set()
c.check("returns the builder's payload once loaded", own() is payload)

src = read_monitor_source()
fns = extract_functions(["_webhook_notify_worker", "_headless_notify_worker", "_run_app_mode"])
for name, body in fns.items():
    c.check(f"{name} no longer polls /status over HTTP", "/status" not in body or "urlopen" not in body)
    c.check(f"{name} reads the payload in-process", "_own_status_payload()" in body)

# _module_loaded.set() must run at module level after the /status builder
# (and everything above it) is defined, and before __main__ starts workers.
tree = ast.parse(src)
order = {}
for i, node in enumerate(tree.body):
    if isinstance(node, ast.FunctionDef) and node.name in ("_build_status_payload", "_build_status_payload_uncached"):
        order[node.name] = i
    elif isinstance(node, ast.Expr) and ast.get_source_segment(src, node) == "_module_loaded.set()":
        order["set"] = i
    elif isinstance(node, ast.If) and "__main__" in ast.get_source_segment(src, node.test):
        order["main"] = i
c.check("_module_loaded.set() exists at module level", "set" in order)
c.check("set after the /status builder is defined",
        order.get("set", -1) > max(order.get("_build_status_payload", 1e9), order.get("_build_status_payload_uncached", 1e9)))
c.check("set before the __main__ block", order.get("set", 1e9) < order.get("main", -1))
c.check("module-level def count after set() is zero (nothing the builder needs comes later)",
        not any(isinstance(n, ast.FunctionDef) for n in tree.body[order.get("set", 0) + 1:]))
c.finish()
