"""monitor.py (30k lines) was split: the server code lives in server/NN_*.py
and monitor.py only exec()s those parts, in number order, into its own
namespace -- the same single set of globals as when it was one file.
Checks the loader (_server_parts) on temp dirs, that the real parts load
into one namespace with every name a later part needs, and that every place
shipping monitor.py ships server/ too."""
import ast, os, re, sys, tempfile

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions, read_source, server_parts, MONITOR_PATH, SERVER_DIR
from lib.check import Checker

c = Checker()
ROOT = os.path.dirname(MONITOR_PATH)
ns = exec_functions(["_server_parts"], {"os": os})
parts_of = ns["_server_parts"]


def tmp_parts(*names):
    d = tempfile.mkdtemp()
    for n in names:
        open(os.path.join(d, n), "w").write("x = 1\n")
    return d


def raises(d):
    try:
        parts_of(d)
        return False
    except RuntimeError:
        return True


d = tmp_parts("02_b.py", "01_a.py", "03_c.py", "notes.txt")
c.check("parts in number order, non-.py ignored",
        [os.path.basename(p) for p in parts_of(d)] == ["01_a.py", "02_b.py", "03_c.py"])
c.check("a gap (01, 03) fails startup", raises(tmp_parts("01_a.py", "03_c.py")))
c.check("not starting at 01 fails", raises(tmp_parts("02_b.py")))
c.check("an unnumbered .py fails", raises(tmp_parts("01_a.py", "helper.py")))
c.check("a duplicate number fails", raises(tmp_parts("01_a.py", "01_b.py")))
c.check("an empty server/ fails", raises(tmp_parts()))

# the real parts
real = parts_of(SERVER_DIR)
c.check("real server/ loads in order and matches the test helper", real == server_parts() and len(real) >= 10)
mon = read_source(MONITOR_PATH)
c.check("monitor.py is just the loader now (< 60 lines)", mon.count("\n") < 60)
c.check("monitor.py keeps its utf-8 coding line", mon.startswith("# -*- coding: utf-8 -*-"))
c.check("parts are compiled with their own file name (real tracebacks)",
        'compile(_f.read(), _part, "exec")' in mon and "exec(_code)" in mon)

defined, problems = set(), []
for p in real:
    src = read_source(p)
    tree = ast.parse(src)  # each part parses on its own
    c.check(f"{os.path.basename(p)} starts with the part header", src.startswith("# AOC server, part "))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        defined.add(n.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                defined.add((a.asname or a.name).split(".")[0])
c.check("the HTTP handler and the entry point are in the parts", {"Handler", "_start_server", "_build_status_payload"} <= defined)
last = read_source(real[-1])
c.check("the __main__ block is in the last part", 'if __name__ == "__main__":' in last)
c.check("no other part has a __main__ block",
        all('if __name__ == "__main__":' not in read_source(p) for p in real[:-1]))
c.check("no part uses `from __future__` (exec'd mid-namespace it would be a SyntaxError later)",
        all("from __future__" not in read_source(p) for p in real))

# everywhere monitor.py ships, server/ ships with it
def text(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8-sig") as f:
        return f.read()
c.check("installer build copies server/", re.search(r'Copy-Item[^\n]*"server"', text("installer/build.ps1")) is not None)
c.check("public export includes server", '"server",' in text("export_public_release.ps1"))
c.check("release notes count server/ commits", '"server"' in text("installer/publish_release.ps1"))
c.finish()
