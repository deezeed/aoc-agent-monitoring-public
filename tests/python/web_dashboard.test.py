"""The dashboard moved out of monitor.py (a 13k-line HTML string) into
web/index.html, web/app.css and web/app.js; _build_dashboard_html inlines
the two back into one page at startup, so what the browser gets is the same
self-contained page as before (the token-gated "/" is the only request, no
sub-resources). Checks the loader on the real shipped files and that every
place that ships monitor.py also ships web/."""
import os, re, sys, tempfile

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions, read_monitor_source, read_source, WEB_DIR
from lib.check import Checker

c = Checker()
ROOT = os.path.dirname(WEB_DIR)
ns = exec_functions(["_build_dashboard_html", "_read_web", "_web_js_parts", "_read_web_js"], {"os": os, "re": re, "WEB_DIR": WEB_DIR})
build, read, read_js, js_parts = ns["_build_dashboard_html"], ns["_read_web"], ns["_read_web_js"], ns["_web_js_parts"]

page = build(read("index.html"), read("app.css"), read_js())
c.check("a whole page: doctype ... </html>", page.startswith("<!DOCTYPE html>") and page.endswith("</html>"))
c.check("css and js inlined, no external app.css/app.js left",
        "app.css" not in page and 'src="app.js"' not in page and page.count("<style>") == 1)
c.check("the server's placeholders survive (token, Pro flag)",
        "__AOC_TOKEN_PLACEHOLDER__" in page and "__AOC_IS_PRO_PLACEHOLDER__" in page)
c.check("style before /head, script inside body",
        page.index("<style>") < page.index("</head>") < page.index("<body>") < page.index("<script>\n") < page.index("</body>"))
css, js = read("app.css"), read_js()
c.check("app.css is pure css (no <style> wrapper)", "<style" not in css and "</style>" not in css)
c.check("app.js is pure js (no <script> wrapper)", not js.lstrip().startswith("<script") and "</script>" not in js)

mini = build('<head>\n<link rel="stylesheet" href="app.css">\n</head>\n<script src="app.js"></script>\n</html>\n', "a{}\n", "x();\n")
c.check("exact inlining", mini == "<head>\n<style>\na{}\n</style>\n</head>\n<script>\nx();\n</script>\n</html>")
for label, idx in (("missing app.css link", '<script src="app.js"></script>\n'),
                   ("missing app.js script", '<link rel="stylesheet" href="app.css">\n'),
                   ("app.js twice", '<link rel="stylesheet" href="app.css">\n<script src="app.js"></script>\n' * 2)):
    try:
        build(idx, "", "")
        c.check(f"{label} -> fails loudly", False)
    except RuntimeError:
        c.check(f"{label} -> fails loudly", True)

# the script is web/js/NN_*.js joined in number order, nothing added between parts
parts = js_parts(os.path.join(WEB_DIR, "js"))
c.check("web/js parts load 01..N in order", [os.path.basename(p)[:2] for p in parts] == [f"{i:02d}" for i in range(1, len(parts) + 1)])
c.check("script = the parts joined as they are", js == "".join(read_source(p) for p in parts))
c.check("no web/app.js left beside web/js/", not os.path.exists(os.path.join(WEB_DIR, "app.js")))
for label, names in (("a gap in the numbers", ["01_a.js", "03_c.js"]),
                     ("an unnumbered part", ["01_a.js", "b.js"]),
                     ("no parts at all", [])):
    d = tempfile.mkdtemp()
    for n in names:
        open(os.path.join(d, n), "w").write("x;\n")
    try:
        js_parts(d)
        c.check(f"web/js with {label} -> startup fails", False)
    except RuntimeError:
        c.check(f"web/js with {label} -> startup fails", True)

src = read_monitor_source()
c.check("monitor.py builds HTML from web/ at startup",
        'HTML = _build_dashboard_html(_read_web("index.html"), _read_web("app.css"), _read_web_js())' in src)
# the only page left in the server is the small invite page (server/18_users.py _join_page)
_jp = src.index("def _join_page(")
_rest = src[:_jp] + src[src.index('</html>"""', _jp):]
c.check("no dashboard string left in monitor.py (the invite page aside)", "<!DOCTYPE html>" not in _rest)

# everywhere monitor.py ships, web/ ships with it
def text(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()
c.check("installer build copies web/", re.search(r'Copy-Item[^\n]*"web"', text("installer/build.ps1")) is not None)
c.check("public export includes web", '"web",' in text("export_public_release.ps1"))
c.check("release notes count web/ commits", '"web"' in text("installer/publish_release.ps1"))
c.finish()
