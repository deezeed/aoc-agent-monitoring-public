# -*- coding: utf-8 -*-
# ^ Not optional: without it Python 3.9 checks UTF-8 in 512-byte chunks when
# running this file as a script, and a multi-byte character (this file
# used to embed the whole dashboard; the docstring still has one) straddling a chunk boundary on a long line fails
# startup with a bogus "Non-UTF-8 code" SyntaxError. py_compile/import don't
# hit that path, so only an actual `python monitor.py` shows it.
"""
AOC — Agent Operations Center
spusti: python monitor.py  ->  http://localhost:5151
"""


def _server_parts(server_dir: str) -> list:
    """server/NN_name.py in load order. Raises unless they are numbered
    01..N without a gap, so a missing or misnamed part fails startup instead
    of starting half an AOC (a later part would hit NameErrors at runtime)."""
    import re
    names = sorted(n for n in os.listdir(server_dir) if n.endswith(".py"))
    nums = [int(m.group(1)) if (m := re.match(r"(\d\d)_\w+\.py$", n)) else 0 for n in names]
    if not names or nums != list(range(1, len(names) + 1)):
        raise RuntimeError(f"server/ must hold 01_*.py .. NN_*.py with no gap, found {names}")
    return [os.path.join(server_dir, n) for n in names]


# The server lives in server/NN_*.py (split out of this file, which had grown
# to 30k lines). Each part runs here, in order, in this module's namespace --
# exactly as when it was one file: one set of globals, tracebacks name the
# real part file, the last part ends with the __main__ block.
import os
for _part in _server_parts(os.path.join(os.path.dirname(os.path.abspath(__file__)), "server")):
    with open(_part, encoding="utf-8") as _f:
        _code = compile(_f.read(), _part, "exec")
    exec(_code)
