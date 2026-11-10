"""Round 10 (7): the monthly report FOR THE CLIENT (History -> CLIENTS ->
Client report). Per project worked + billable time (split so the rows add
up to the invoice's quarter-hour total) and the amount, then "what was
done": the month's commit subjects (merges / WIP / fixups / tests and
"Release-note: skip" commits dropped) and session names, deduplicated per
project. No costs, tokens or model names. Slovak formatting (decimal comma,
d. m. yyyy), Slovak or English labels. Runs _client_report +
_client_month_report over the real index schema and a temp git repo."""
import sys, os, re, json, sqlite3, tempfile, subprocess, time, calendar
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions, read_monitor_source
from lib.check import Checker

c = Checker()
data = tempfile.mkdtemp(prefix="aoc_cr_")
ns = exec_functions(["_load_json_file", "CLIENTS_FILE", "_CLIENT_NONE", "_CLIENT_CURRENCY", "_sanitize_clients",
                     "_load_clients", "_save_clients", "_client_for", "_client_report", "_BILL_STEP_H", "_billable_hours",
                     "_iso_to_epoch", "_WORKLOG_GAP_S", "_WORKLOG_HOME_GROUP", "_worklog_active_s", "_worklog_project",
                     "_CR_MONTHS", "_CR_LABELS", "_CR_NOISE_RE", "_cr_lang", "_cr_num", "_cr_date", "_cr_alloc_hours",
                     "_cr_is_noise", "_cr_clean_text", "_cr_skipped_shas", "_cr_items", "_client_month_report",
                     "_client_month_report_html"],
                    {"os": os, "re": re, "json": json, "time": time, "calendar": calendar, "datetime": datetime,
                     "subprocess": subprocess, "AOC_DATA_DIR": data})

# ── pure helpers ──
num, date, alloc = ns["_cr_num"], ns["_cr_date"], ns["_cr_alloc_hours"]
c.check("decimal comma + nbsp thousands", num(1234.5) == "1\u00a0234,50" and num(0.25) == "0,25" and num(None) == "0,00")
c.check("d. m. yyyy", date("2026-11-06") == "6. 11. 2026" and date("x") == "x")
c.check("sk/cs -> sk, anything else en", ns["_cr_lang"]("sk") == "sk" and ns["_cr_lang"]("cs-CZ") == "sk"
        and ns["_cr_lang"]("en") == "en" and ns["_cr_lang"](None) == "en")
a = alloc([3600, 1800, 600], 1.75)
c.check("project hours add up to the invoiced total", round(sum(a), 2) == 1.75 and all(x * 4 == int(x * 4) for x in a))
c.check("largest share gets the most", a[0] >= a[1] >= a[2])
c.check("0 hours / no time -> zeros", alloc([10, 20], 0) == [0.0, 0.0] and alloc([0, 0], 1.0) == [0.0, 0.0])
c.check("one project gets everything", alloc([42], 0.25) == [0.25])
noise = ns["_cr_is_noise"]
c.check("merge / WIP / fixup / tests are noise", all(noise(s) for s in [
    "Merge round 9 (8): invoice", "Merge branch 'x'", "WIP", "wip: half", "[WIP] thing", "fixup! Add", "squash! x",
    "Tests: limit_stops loads helpers", "test: add case", "tests(ci): flaky", "", None]))
c.check("real work is not", not any(noise(s) for s in ["Invoice export: quarter hours", "Testimonials page", "Merger logos"]))
clean = ns["_cr_clean_text"]
c.check("commands / markup / caveats dropped", clean("/clear") == "" and clean("<command-name>x") == ""
        and clean("Caveat: the messages below") == "" and clean("   ") == "")
c.check("long text cut with an ellipsis", len(clean("x" * 300)) == 140 and clean("x" * 300).endswith("…"))

# ── a real repo: one commit marked Release-note: skip ──
repo = tempfile.mkdtemp(prefix="aoc_cr_repo_")
git = lambda *a: subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True)
git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
shas = []
for msg in ["Landing page: pricing section", "Internal refactor\n\nRelease-note: skip", "Checkout: card payments",
            # not a git trailer (a paragraph follows), still meant as skip
            "Split the server\n\nRelease-note: skip\n\nCo-Authored-By: X <x@x>",
            "Docs: mention release notes\n\nWe skip nothing here. Release-note: skip is described in README"]:
    open(os.path.join(repo, "f.txt"), "a").write(msg + "\n")
    git("add", "f.txt"); git("commit", "-qm", msg)
    shas.append(git("rev-parse", "HEAD").stdout.strip())
skip = ns["_cr_skipped_shas"]
c.check("Release-note: skip commits found by git, also when Co-Authored-By follows",
        skip(repo, shas) == {shas[1], shas[3]})
c.check("a SHA the repo doesn't have (rebased away) doesn't hide the others",
        skip(repo, ["deadbeefdeadbeef"] + shas[:3]) == {shas[1]})
c.check("short SHAs (what the index stores) come back as given", skip(repo, [s[:7] for s in shas]) == {shas[1][:7], shas[3][:7]})
shas = shas[:3]
c.check("non-SHA input never reaches git", skip(repo, ["--all", "HEAD"]) == set())
c.check("missing repo / no shas -> nothing skipped", skip(os.path.join(repo, "gone"), shas) == set() and skip(repo, []) == set())
c.check("git failing -> nothing skipped", skip(repo, shas, run=lambda *a, **k: (_ for _ in ()).throw(OSError())) == set())

# ── over the index schema ──
conn = sqlite3.connect(":memory:")
conn.executescript("""
CREATE TABLE usage (session_id TEXT, model TEXT, day TEXT, n INT, inp INT, out INT, cw INT, cw1h INT, cr INT, cost REAL,
                    cold_n INT, cold_cw INT, cold_cw1h INT, cold_idle_n INT);
CREATE TABLE sessions_meta (session_id TEXT PRIMARY KEY, project TEXT, cwd TEXT, title TEXT, first_ts TEXT, last_ts TEXT,
                            last_call_epoch REAL, run_cost REAL);
CREATE TABLE commits (session_id TEXT, sha TEXT, kind TEXT, branch TEXT, subject TEXT, cwd TEXT, ts TEXT, epoch REAL,
                      end_epoch REAL, day TEXT, cost_at REAL, files INT, ins INT, dels INT);
CREATE TABLE msgs (session_id TEXT, ts TEXT, role TEXT, text TEXT);
""")
ep = lambda day, h, m=0: time.mktime(time.strptime(f"{day} {h:02d}:{m:02d}", "%Y-%m-%d %H:%M"))
iso = lambda e: datetime.utcfromtimestamp(e).strftime("%Y-%m-%dT%H:%M:%S.000Z")
for sid, day, cost, model in [("s1", "2026-10-02", 12.5, "claude-opus-4-5"), ("s2", "2026-10-09", 3.25, "haiku"),
                              ("s3", "2026-10-15", 1.0, "opus"), ("other", "2026-10-03", 9.0, "opus")]:
    conn.execute("INSERT INTO usage VALUES (?,?,?,1,4321,0,0,0,0,?,0,0,0,0)", (sid, model, day, cost))
for row in [("s1", "acme", repo, "Acme shop pokračovanie"), ("s2", "acme", repo, "Acme"),
            ("s3", "marek", "C:/Users/marek", ""), ("other", "x", "C:/x", "Something else")]:
    conn.execute("INSERT INTO sessions_meta (session_id, project, cwd, title) VALUES (?,?,?,?)", row)
CM = lambda sid, sha, subj, day, h: conn.execute(
    "INSERT INTO commits (session_id, sha, subject, cwd, day, epoch) VALUES (?,?,?,?,?,?)", (sid, sha, subj, repo, day, ep(day, h)))
CM("s1", shas[0], "Landing page: pricing section", "2026-10-02", 11)
CM("s1", shas[1], "Internal refactor", "2026-10-02", 12)
CM("s2", shas[2], "Checkout: card payments", "2026-10-09", 10)
CM("s2", "m1", "Merge branch 'feature'", "2026-10-09", 11)
CM("s2", "dup", "checkout: card  payments.", "2026-10-09", 12)       # same thing again
CM("s1", "sep", "September work", "2026-09-28", 10)                  # other month
CM("s1", "tu:1", "pending", "2026-10-02", 13)
M = lambda sid, day, h, m, role="user", text="hi": conn.execute("INSERT INTO msgs VALUES (?,?,?,?)", (sid, iso(ep(day, h, m)), role, text))
for mi in range(0, 60, 5):                       # s1: 55 min of work
    M("s1", "2026-10-02", 10, mi, "assistant")
for mi in range(0, 25, 5):                       # s2: 20 min
    M("s2", "2026-10-09", 9, mi, "assistant")
M("s3", "2026-10-15", 8, 0, "user", "<command-name>/clear</command-name>")
M("s3", "2026-10-15", 8, 1, "user", "Pomôž mi s DNS pre acme.sk")
M("s3", "2026-10-15", 8, 6, "assistant")
cfg = ns["_sanitize_clients"]({"clients": [{"name": "Acme s.r.o.", "match": ["acme"], "rate": 40, "currency": "eur",
                                            "note": "  Zmluva č. 12/2026  \r\n  Kontakt:   Jana  "}],
                               "overrides": {"s3": "Acme s.r.o."}})
c.check("note kept, trimmed, line breaks kept", cfg["clients"][0]["note"] == "Zmluva č. 12/2026\nKontakt: Jana")
c.check("note capped at 2000", len(ns["_sanitize_clients"]({"clients": [{"name": "A", "note": "x" * 5000}]})["clients"][0]["note"]) == 2000)
c.check("no note -> ''", ns["_sanitize_clients"]({"clients": [{"name": "A"}]})["clients"][0]["note"] == "")

rep = ns["_client_report"](conn, "2026-10", cfg, "C:/Users/marek")
g = next(x for x in rep["clients"] if x["name"] == "Acme s.r.o.")
d = ns["_client_month_report"](conn, rep, "Acme s.r.o.", cfg["clients"][0]["note"])
c.check("unknown client -> None", ns["_client_month_report"](conn, rep, "Nobody") is None)
names = [p["name"] for p in d["projects"]]
c.check("projects: the repo + the home-folder group", os.path.basename(repo) in names and "Other (home folder)" in names)
c.check("billable total = the invoice's (rounded up once for the month)", d["total"]["hours"] == g["hours"])
c.check("amount = hours x rate", abs(d["total"]["amount"] - g["hours"] * 40) < 1e-9)
c.check("currency upper-cased", d["currency"] == "EUR")
items = {p["name"]: [i["text"] for i in p["items"]] for p in d["projects"]}
work = items[os.path.basename(repo)]
c.check("commits of the month, oldest first", work[:2] == ["Landing page: pricing section", "Checkout: card payments"])
c.check("Release-note: skip / merge / other-month / pending dropped",
        not any(t in work for t in ["Internal refactor", "Merge branch 'feature'", "September work", "pending"]))
c.check("near-duplicate subject only once", sum(1 for t in work if t.lower().startswith("checkout")) == 1)
c.check("session names added after the commits; one equal to the project name skipped",
        "Acme shop pokračovanie" in work and "Acme" not in work)
c.check("unnamed session -> its first real prompt (not the /clear)", items["Other (home folder)"] == ["Pomôž mi s DNS pre acme.sk"])

# ── the page ──
html = ns["_client_month_report_html"]
sk = html(d, "sk", today="2026-11-06")
en = html(d, "en", today="2026-11-06")
c.check("Slovak labels + month", "Výkaz práce · október 2026" in sk and "Čo sa urobilo" in sk and 'lang="sk"' in sk)
c.check("English labels + month", "Work report · October 2026" in en and "What was done" in en)
c.check("Slovak dates", "Obdobie: 1. 10. 2026 – 31. 10. 2026" in sk and "Vygenerované: 6. 11. 2026" in sk and "2. 10. 2026" in sk)
c.check("decimal comma in hours and amount", re.search(r"\d+,\d\d h", sk) and re.search(r"\d+,\d\d EUR", sk))
c.check("home-folder group shown as Other / Ostatné", "Ostatné" in sk and "Other (home folder)" not in sk and ">Other<" in en)
c.check("client note on the page, escaped", "Zmluva č. 12/2026" in sk and "<div class=note>" in sk)
for bad in ["$", "USD", "token", "4321", "opus", "haiku", "claude", "Claude", "cost", "Cost", "AI"]:
    c.check(f"nothing internal on the page: {bad!r}", bad not in en.replace("Checkout", "").replace("Acme shop pokračovanie", ""))
c.check("no rate -> no amount column", "Amount" not in html({**d, "rate": 0}, "en"))
c.check("escaping", "&lt;b&gt;" in html({**d, "client": "<b>x</b>"}, "en"))
empty = html({**d, "projects": [], "total": {"active_s": 0, "hours": 0, "amount": 0}}, "sk")
c.check("empty month says so", "nie je zaznamenaná žiadna práca" in empty)
c.check("printable: print button hidden in print", "@media print{button{display:none}" in en)

# ── route ──
src = read_monitor_source()
c.check("route /clients/report is dispatched", '"/clients/print", "/clients/report")' in src)
c.check("route passes the client's note and lang", "_client_month_report(conn, report, client or \"\", note)" in src
        and "_client_month_report_html(month_report, lang)" in src)
c.finish()
