"""The page had only <link rel="apple-touch-icon">, and the dynamic favicon
looked its link up with link[rel*='icon'], which matched that apple-touch
link. So the status-coloured canvas icon only ever replaced the iOS icon,
Chrome never showed it, and every load requested /favicon.ico -> 404.
Checks the shipped HTML and route source, so a later edit can't quietly
bring either half back."""
import os, re, sys

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import read_monitor_source
from lib.check import Checker

src = read_monitor_source()
head = src[src.index('HTML = r"""'):]
head = head[:head.index("</head>") if "</head>" in head else head.index("<style>")]

c = Checker()
c.check("page head declares a real rel=icon link", '<link rel="icon"' in head)
c.check("rel=icon comes before apple-touch-icon",
        head.index('<link rel="icon"') < head.index('rel="apple-touch-icon"'))
c.check("dynamic favicon targets rel='icon' exactly",
        "document.querySelector(\"link[rel='icon']\")" in src)
c.check("no substring rel*='icon' lookup left (would match apple-touch-icon)",
        "link[rel*='icon']" not in src)
c.check("/favicon.ico is served", re.search(r'path_no_qs in \([^)]*"/favicon\.ico"', src) is not None)
c.finish()
