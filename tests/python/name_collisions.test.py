"""No top-level name is defined twice across the parts that share one
namespace (audit FIND-015 / OPP-011).

monitor.py exec()s server/NN_*.py into ONE namespace, in number order: a
function or global defined in two parts silently becomes the later one,
for every caller in every part. The page does the same with web/js/NN_*.js
(plain scripts, one global scope): a second `function x` wins without a
word -- the July bug where a shared stxt() was assumed and the page's
cards stopped rendering came from exactly this kind of shared-name
guesswork. Today there is none; this keeps it that way.

Python: function, class and assignment names at module level, compared
across parts (one part may reassign its own global). JS: top-level
function declarations and var / let / const at the start of a line,
compared everywhere (a redeclared function in the same file is a bug too).
"""
import ast, os, re, sys

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import SERVER_DIR
from lib.check import Checker

c = Checker()
WEB_JS = os.path.normpath(os.path.join(SERVER_DIR, "..", "web", "js"))
_JS_DECL = re.compile(r"^(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(|^(?:var|let|const)\s+([A-Za-z_$][\w$]*)", re.M)


def py_names(src):
    """Pure: module-level names a Python part defines."""
    out = []
    for n in ast.parse(src).body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(n.name)
        elif isinstance(n, ast.Assign):
            out += [t.id for t in n.targets if isinstance(t, ast.Name)]
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            out.append(n.target.id)
    return out


def js_names(src):
    """Pure: top-level names a page script declares."""
    return [m.group(1) or m.group(2) for m in _JS_DECL.finditer(src)]


def collisions(parts, names_of, across_only):
    """Pure: {name: [part, ...]} for names defined more than once."""
    seen = {}
    for part, src in parts:
        for n in names_of(src):
            seen.setdefault(n, []).append(part)
    return {n: ps for n, ps in seen.items() if len(set(ps) if across_only else ps) > 1}


def read(folder, ext):
    return [(f, open(os.path.join(folder, f), encoding="utf-8").read()) for f in sorted(os.listdir(folder)) if f.endswith(ext)]


# the checks themselves catch what they should
c.check("python: a function in two parts is a collision; one part reassigning its own global isn't",
        collisions([("01_a.py", "def f():\n    pass\nX = 1\nX = 2\n"), ("02_b.py", "def f():\n    return 1\n")], py_names, True)
        == {"f": ["01_a.py", "02_b.py"]})
c.check("python: a global in one part and a class of the same name in another collide",
        "Cache" in collisions([("a.py", "Cache = {}\n"), ("b.py", "class Cache:\n    pass\n")], py_names, True))
c.check("js: a function declared twice -- across files or in one -- and var vs function collide",
        set(collisions([("a.js", "function stxt(a){}\nvar _x=1;\n"), ("b.js", "function stxt(b){}\n"),
                        ("c.js", "function g(){}\nfunction g(){}\nconst _x=2;\n")], js_names, False)) == {"stxt", "g", "_x"})
c.check("js: nested functions and indented declarations are not top-level",
        js_names("function a(){\n  function inner(){}\n  var local=1;\n}\n") == ["a"])

server = read(SERVER_DIR, ".py")
web = read(WEB_JS, ".js")
c.check(f"found the parts ({len(server)} server, {len(web)} page)", len(server) >= 15 and len(web) >= 15)
py = collisions(server, py_names, True)
if py:
    print("  defined in more than one server part:", py)
c.check(f"server/*.py: no module-level name defined in two parts ({sum(len(py_names(s)) for _, s in server)} names)", not py)
js = collisions(web, js_names, False)
if js:
    print("  declared more than once in web/js:", js)
c.check(f"web/js/*.js: no top-level name declared twice ({sum(len(js_names(s)) for _, s in web)} names)", not js)
c.finish()
