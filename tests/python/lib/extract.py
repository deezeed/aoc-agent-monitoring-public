"""
Shared helper: pull named top-level functions (and their assignment
targets, e.g. a pricing dict a function depends on) straight out of
monitor.py's source using the ast module, so tests can never silently
drift from the real shipped code. Uses ast.get_source_segment rather than
a regex, since Python's indentation-sensitive syntax extracts reliably
that way (a regex would need to know where the function's indentation
block actually ends).
"""
import ast
import os

MONITOR_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "monitor.py")
MONITOR_PATH = os.path.normpath(MONITOR_PATH)


def read_monitor_source() -> str:
    with open(MONITOR_PATH, encoding="utf-8") as f:
        return f.read()


def read_source(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def extract_functions(names, src=None, path=None) -> dict:
    """Return {name: source_text} for each top-level FunctionDef or
    module-level assignment (e.g. a dict constant) whose target name is in
    `names`. Raises if any name isn't found, so a typo'd name fails loudly
    instead of silently testing nothing. `path` selects a source file other
    than monitor.py (e.g. watchdog.py/sentinel.py); ignored if `src` is
    given directly."""
    if src is None:
        src = read_source(path) if path else read_monitor_source()
    tree = ast.parse(src)
    found = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            found[node.name] = ast.get_source_segment(src, node)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in names:
                    found[target.id] = ast.get_source_segment(src, node)
    missing = set(names) - set(found)
    if missing:
        raise ValueError(f"not found in {path or 'monitor.py'}: {sorted(missing)}")
    return found


def exec_functions(names, extra_globals=None, path=None):
    """Extract + exec the named functions/constants into a fresh namespace,
    returning that namespace dict. `extra_globals` seeds module-level state
    the extracted code references (e.g. _notify_settings, DB_FILE) before
    exec runs, so the extracted code sees exactly what it expects. `path`
    selects a source file other than monitor.py."""
    src_map = extract_functions(names, path=path)
    namespace = dict(extra_globals or {})
    for name in names:
        # watchdog.py/sentinel.py use `from __future__ import annotations`
        # (PEP 604 `X | None` style hints) -- that future import makes
        # annotations lazy strings in the real module, but extracting just
        # the function body loses it, so under Python <3.10 a bare `X |
        # None` annotation would be evaluated eagerly and raise a TypeError
        # at exec time. Re-adding it here is a no-op for monitor.py's
        # functions (which don't use that syntax) and fixes it for the rest.
        exec("from __future__ import annotations\n" + src_map[name], namespace)
    return namespace
