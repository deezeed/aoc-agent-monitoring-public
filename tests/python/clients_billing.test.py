"""Clients & billing (monitor.py): _sanitize_clients, _client_for (manual
override > first client with a keyword in title / project / folder /
committed repos), _client_report over a real sqlite transcript-index
schema (month split by day, commits + repos, markup, unassigned last),
_client_report_csv and the printable page (escaping, markup lines).
Round 9: hourly rate + currency per client, active time per session
counted only inside the month (local dates, a session past midnight on
the 31st is split), project subtotals, CSV time columns + UTF-8 BOM,
ASCII file names for clients with diacritics."""
import sys, os, re, json, sqlite3, tempfile, shutil, csv, io, time, calendar
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
data = tempfile.mkdtemp(prefix="aoc_cl_")
ns = exec_functions(["_load_json_file", "CLIENTS_FILE", "_CLIENT_NONE", "_sanitize_clients", "_load_clients",
                     "_save_clients", "_client_for", "_client_report", "_client_report_csv",
                     "_client_report_print_html", "_CLIENT_CURRENCY", "_BILL_STEP_H", "_billable_hours", "_csv_dec", "_client_file_slug", "_iso_to_epoch",
                     "_WORKLOG_GAP_S", "_WORKLOG_HOME_GROUP", "_worklog_active_s", "_worklog_project"],
                    {"os": os, "re": re, "json": json, "time": time, "calendar": calendar, "datetime": datetime,
                     "AOC_DATA_DIR": data})

# ── sanitize ──
san = ns["_sanitize_clients"]
cfg = san({"clients": [
    {"name": "  Omni   Social ", "match": ["OmniSocial", " omnisocial ", "", "omni-web"], "markup": "20"},
    {"name": "omni social", "match": ["dup"]},
    {"name": "-", "match": ["x"]},
    {"name": "Phantom", "match": ["phantom", "antivirus"], "markup": 9999},
    {"name": "", "match": ["y"]}, "junk"],
    "overrides": {"s9": "Phantom", "s8": "-", "s7": "Nobody", "s6": 5}})
c.check("names trimmed, duplicates / reserved / empty dropped", [x["name"] for x in cfg["clients"]] == ["Omni Social", "Phantom"])
c.check("keywords lowercased, deduped", cfg["clients"][0]["match"] == ["omnisocial", "omni-web"])
c.check("markup parsed + clamped", cfg["clients"][0]["markup"] == 20 and cfg["clients"][1]["markup"] == 500)
c.check("overrides only to known clients or '-'", cfg["overrides"] == {"s9": "Phantom", "s8": "-"})
c.check("garbage -> empty config", san(None) == {"clients": [], "overrides": {}} and san({"clients": "x"})["clients"] == [])
c.check("missing file -> empty", ns["_load_clients"]() == {"clients": [], "overrides": {}})
ns["_save_clients"](cfg)
c.check("save -> load round trip", ns["_load_clients"]() == cfg)

# ── matching ──
cf = ns["_client_for"]
c.check("title keyword", cf("a", ["Omnisocial pokračování", "marek", "C:/Users/marek"], cfg) == ("Omni Social", "keyword: omnisocial"))
c.check("repo keyword", cf("a", ["Fix things", "marek", "", "Ai Antivirus"], cfg) == ("Phantom", "keyword: antivirus"))
c.check("first client wins", cf("a", ["omnisocial vs phantom"], cfg)[0] == "Omni Social")
c.check("manual override beats keywords", cf("s9", ["omnisocial"], cfg) == ("Phantom", "manual"))
c.check("'-' = no client on purpose", cf("s8", ["omnisocial"], cfg) == ("", "manual"))
c.check("nothing matches", cf("a", ["Random chat", None, ""], cfg) == ("", ""))

# ── report over the real schema ──
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
U = lambda sid, day, cost, tok=1000, model="opus": conn.execute(
    "INSERT INTO usage VALUES (?,?,?,1,?,0,0,0,0,?,0,0,0,0)", (sid, model, day, tok, cost))
U("omni1", "2026-10-02", 10.0); U("omni1", "2026-10-02", 2.5, model="haiku"); U("omni1", "2026-10-03", 1.0)
U("omni2", "2026-09-30", 7.0); U("omni2", "2026-10-01", 3.0)        # crosses the month boundary
U("ph1", "2026-10-05", 4.0)
U("chat", "2026-10-06", 0.5)
U("manual", "2026-10-07", 2.0)
U("sept", "2026-09-12", 9.0)
for row in [("omni1", "marek", "C:/Users/marek", "Omnisocial pokračování"),
            ("omni2", "marek", "C:/Users/marek", "Pokračuj v omnisocial"),
            ("ph1", "marek", "C:/Users/marek", "Build fixes"),
            ("chat", "marek", "C:/Users/marek", "Random <question>"),
            ("manual", "marek", "C:/Users/marek", "Something omnisocial-ish")]:
    conn.execute("INSERT INTO sessions_meta (session_id, project, cwd, title) VALUES (?,?,?,?)", row)
C = lambda sid, sha, cwd, day: conn.execute("INSERT INTO commits (session_id, sha, cwd, day) VALUES (?,?,?,?)", (sid, sha, cwd, day))
C("omni1", "a1", "C:/p/omnisocial-web", "2026-10-02"); C("omni1", "a2", "C:/p/omnisocial-web", "2026-10-03")
C("omni1", "tu:x", "C:/p/omnisocial-web", "2026-10-03")            # pending marker, not a commit
C("ph1", "b1", "C:\\Projekty\\Ai Antivirus", "2026-10-05")
C("omni2", "c1", "C:/p/omnisocial-web", "2026-09-30")              # September commit
cfg["overrides"] = {"manual": "Phantom"}
rep = ns["_client_report"](conn, "2026-10", cfg)
by = {g["name"]: g for g in rep["clients"]}
c.check("month total = only October days", abs(rep["total"] - (13.5 + 3.0 + 4.0 + 0.5 + 2.0)) < 1e-9)
c.check("clients in config order, unassigned last", [g["name"] for g in rep["clients"]] == ["Omni Social", "Phantom", ""])
o = by["Omni Social"]
c.check("Omni: both sessions, October part only", abs(o["cost"] - 16.5) < 1e-9 and [s["id"] for s in o["sessions"]] == ["omni1", "omni2"])
c.check("markup applied", o["billed"] == 19.8)
c.check("commits this month only, pending markers ignored", o["commits"] == 2 and o["sessions"][1]["commits"] == 0)
c.check("repos listed", o["sessions"][0]["repos"] == ["omnisocial-web"])
c.check("dates span", o["sessions"][0]["first_day"] == "2026-10-02" and o["sessions"][0]["last_day"] == "2026-10-03")
c.check("tokens summed", o["sessions"][0]["tokens"] == 3000)
p = by["Phantom"]
c.check("repo keyword (backslash path) + manual override", sorted(s["id"] for s in p["sessions"]) == ["manual", "ph1"]
        and {s["id"]: s["how"] for s in p["sessions"]} == {"ph1": "keyword: antivirus", "manual": "manual"})
c.check("unassigned keeps the rest", [s["id"] for s in by[""]["sessions"]] == ["chat"])
c.check("months available, newest first", rep["months"] == ["2026-10", "2026-09"])
c.check("empty month -> zero, no crash", ns["_client_report"](conn, "2025-01", cfg)["total"] == 0)

# ── CSV ──
rows = list(csv.reader(io.StringIO(ns["_client_report_csv"](rep)), delimiter=";"))
c.check("CSV header (time columns appended after billed_usd)", rows[0][:3] == ["month", "client", "session"]
        and rows[0][11] == "billed_usd" and rows[0][12:] == ["project", "hours", "rate", "currency", "time_amount"])
c.check("CSV: session lines + one TOTAL per client with sessions", sum(1 for r in rows if r[2] == "TOTAL") == 3)
omni_total = next(r for r in rows if r[1] == "Omni Social" and r[2] == "TOTAL")
c.check("CSV total with markup", omni_total[9] == "16,50" and omni_total[10] == "20" and omni_total[11] == "19,80")
c.check("CSV unassigned named", any(r[1] == "(unassigned)" for r in rows))
one = list(csv.reader(io.StringIO(ns["_client_report_csv"](rep, "Phantom")), delimiter=";"))
c.check("CSV for one client", {r[1] for r in one[1:]} == {"Phantom"} and len(one) == 4)

# ── printable page ──
h = ns["_client_report_print_html"](rep, "Omni Social")
c.check("print: title + month name", "<h1>Omni Social</h1>" in h and "October 2026" in h)
c.check("print: markup + total lines", "Markup 20 %" in h and "$19.80" in h and "$16.50" in h)
c.check("print: date range shown", "2026-10-02 – 2026-10-03" in h)
h = ns["_client_report_print_html"](rep, "")
c.check("print: unassigned, titles escaped", "Unassigned sessions" in h and "Random &lt;question&gt;" in h and "<question>" not in h)
c.check("print: unknown client", "No client named" in ns["_client_report_print_html"](rep, "<x>") and "&lt;x&gt;" in ns["_client_report_print_html"](rep, "<x>"))

# ── round 9: rate, currency, active time, projects, BOM ──
cfg9 = san({"clients": [{"name": "Čajovňa Ľubo", "match": ["cajovna"], "rate": "45.5", "currency": "eur"},
                        {"name": "US Co", "match": ["usco"], "rate": 100, "currency": "USD", "markup": 10},
                        {"name": "Bad", "match": ["bad"], "rate": -5, "currency": "euro"},
                        {"name": "Nan", "match": ["nan"], "rate": "nan"}]})
by9 = {x["name"]: x for x in cfg9["clients"]}
c.check("rate parsed, currency upper-cased", by9["Čajovňa Ľubo"]["rate"] == 45.5 and by9["Čajovňa Ľubo"]["currency"] == "EUR")
c.check("bad rate -> 0, bad currency -> EUR default", by9["Bad"]["rate"] == 0 and by9["Bad"]["currency"] == "EUR"
        and by9["Nan"]["rate"] == 0)
c.check("old config without rate -> rate 0, EUR", cfg["clients"][0]["rate"] == 0 and cfg["clients"][0]["currency"] == "EUR")


def local_iso(y, mo, d, h, mi):
    """UTC ISO timestamp (as transcripts store it) of a LOCAL wall-clock time."""
    ep = time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ep)), ep


conn9 = sqlite3.connect(":memory:")
for (sql,) in conn.execute("SELECT sql FROM sqlite_master WHERE type='table'").fetchall():
    conn9.execute(sql)
U9 = lambda sid, day, cost: conn9.execute("INSERT INTO usage VALUES (?,?,?,1,1000,0,0,0,0,?,0,0,0,0)", (sid, "opus", day, cost))
M9 = lambda sid, *t: conn9.execute("INSERT INTO msgs VALUES (?,?,?,?)", (sid, local_iso(*t)[0], "user", "x"))
# a session over midnight 31 Oct -> 1 Nov: 23:50, 23:55 | 00:02, 00:05
U9("tea1", "2026-10-31", 6.0); U9("tea1", "2026-11-01", 2.0)
for t in [(2026, 10, 31, 23, 50), (2026, 10, 31, 23, 55), (2026, 11, 1, 0, 2), (2026, 11, 1, 0, 5)]:
    M9("tea1", *t)
# a second project for the same client: a 2 h pause counts as the gap only, a commit 4 min after the last message extends the work
U9("tea2", "2026-10-10", 1.0)
for t in [(2026, 10, 10, 9, 0), (2026, 10, 10, 9, 20), (2026, 10, 10, 11, 20), (2026, 10, 10, 11, 30)]:
    M9("tea2", *t)
conn9.execute("INSERT INTO commits (session_id, sha, cwd, day, epoch) VALUES (?,?,?,?,?)",
              ("tea2", "d1", "C:/p/cajovna-eshop", "2026-10-10", local_iso(2026, 10, 10, 11, 34)[1]))
U9("us1", "2026-10-12", 10.0)
for t in [(2026, 10, 12, 14, 0), (2026, 10, 12, 14, 30)]:
    M9("us1", *t)
for row in [("tea1", "home", "C:/Users/marek", "Cajovna menu"), ("tea2", "x", "C:/p/whatever", "cajovna eshop"),
            ("us1", "usco", "C:/p/usco-api", "usco api")]:
    conn9.execute("INSERT INTO sessions_meta (session_id, project, cwd, title) VALUES (?,?,?,?)", row)
oct9 = {g["name"]: g for g in ns["_client_report"](conn9, "2026-10", cfg9, "C:/Users/marek")["clients"]}
nov9 = {g["name"]: g for g in ns["_client_report"](conn9, "2026-11", cfg9, "C:/Users/marek")["clients"]}
tea_o = {s["id"]: s for s in oct9["Čajovňa Ľubo"]["sessions"]}
# every gap counts up to 10 min: 9:00-9:20 -> 10, 9:20-11:20 -> 10, 11:20-11:30 -> 10, 11:30-11:34 commit -> 4
TEA2 = (10 + 10 + 10 + 4) * 60
c.check("midnight session: October gets only its October minutes", tea_o["tea1"]["active_s"] == 300)
c.check("midnight session: November gets the rest", nov9["Čajovňa Ľubo"]["sessions"][0]["active_s"] == 180)
c.check("a pause over the gap counts as the gap; the commit extends the work", tea_o["tea2"]["active_s"] == TEA2)
g9 = oct9["Čajovňa Ľubo"]
c.check("client hours + time amount at its rate", g9["active_s"] == 300 + TEA2 and g9["hours"] == 0.75
        and g9["time_amount"] == round(0.75 * 45.5, 2) and g9["currency"] == "EUR")
c.check("projects: committed repo first, home folder grouped last",
        [p["name"] for p in g9["projects"]] == ["cajovna-eshop", "Other (home folder)"]
        and g9["projects"][1]["active_s"] == 300 and g9["projects"][0]["sessions"] == 1)
c.check("session carries its work project", tea_o["tea2"]["work_project"] == "cajovna-eshop")
c.check("client without time -> 0 h, 0 amount", oct9["Bad"]["active_s"] == 0 and oct9["Bad"]["time_amount"] == 0)

raw = ns["_client_report_csv"]({"month": "2026-10", "clients": list(oct9.values())}, "Čajovňa Ľubo", bom=True)
c.check("CSV with BOM for Excel, diacritics intact", raw.startswith("\ufeffmonth;client;") and "Čajovňa Ľubo" in raw)
c.check("CSV rows end in CRLF, no blank lines", "\r\n" in raw and "\r\r" not in raw and "\n\n" not in raw.replace("\r\n", "\n"))
r9 = list(csv.reader(io.StringIO(raw.lstrip("\ufeff")), delimiter=";"))
tot = next(r for r in r9 if r[2] == "TOTAL")
c.check("CSV total: hours, rate, currency, time amount", tot[13] == "0,75" and tot[14] == "45,5"
        and tot[15] == "EUR" and tot[16] == f"{round(0.75 * 45.5, 2):.2f}".replace(".", ","))
row2 = next(r for r in r9 if r[3] == "tea2")
c.check("CSV session: project + hours", row2[12] == "cajovna-eshop" and row2[13] == f"{TEA2 / 3600:.2f}".replace(".", ","))
c.check("no BOM unless asked", not ns["_client_report_csv"](rep).startswith("\ufeff"))

bh = ns["_billable_hours"]
c.check("billable hours: a started quarter hour counts, exact quarters stay", bh(0) == 0 and bh(None) == 0 and bh(1) == 0.25
        and bh(900) == 0.25 and bh(901) == 0.5 and bh(3600) == 1.0 and bh(2340) == 0.75)
c.check("CSV decimal comma", ns["_csv_dec"](12.5) == "12,50" and ns["_csv_dec"](45.5, "g") == "45,5"
        and ns["_csv_dec"](20, "g") == "20")

slug = ns["_client_file_slug"]
c.check("file slug drops diacritics, keeps letters", slug("Čajovňa Ľubo s.r.o.") == "Cajovna-Lubo-s.r.o" and slug(None) == "all"
        and slug("日本") == "client")

h9 = ns["_client_report_print_html"]({"month": "2026-10", "clients": list(oct9.values())}, "Čajovňa Ľubo")
c.check("print: time column as h:mm, locale-free", "<th class=n>Time</th>" in h9 and ">0:34<" in h9 and ">0:05<" in h9
        and "(0:39, rounded up to 0.25 h)" in h9 and ">0.75 h<" in h9)
c.check("print: project subtotals when 2+ projects", "<h2>By project</h2>" in h9 and "cajovna-eshop" in h9)
c.check("print: work time in the client's currency, no USD grand total", "Work time" in h9
        and f"{round(0.75 * 45.5, 2):,.2f} EUR" in h9 and "45.50 EUR/h" in h9 and "Total due" not in h9)
hu = ns["_client_report_print_html"]({"month": "2026-10", "clients": list(oct9.values())}, "US Co")
c.check("print: USD rate -> one total due (AI billed + time)", "Total due" in hu and f"{11.0 + 0.25 * 100:,.2f} USD" in hu
        and "<h2>By project</h2>" not in hu)
c.check("print: no rate -> no work time block", "Work time" not in ns["_client_report_print_html"](rep, "Omni Social"))

shutil.rmtree(data, ignore_errors=True)
c.finish()
