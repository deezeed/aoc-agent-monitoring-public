"""Clients & billing (monitor.py): _sanitize_clients, _client_for (manual
override > first client with a keyword in title / project / folder /
committed repos), _client_report over a real sqlite transcript-index
schema (month split by day, commits + repos, markup, unassigned last),
_client_report_csv and the printable page (escaping, markup lines)."""
import sys, os, json, sqlite3, tempfile, shutil, csv, io
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
data = tempfile.mkdtemp(prefix="aoc_cl_")
ns = exec_functions(["_load_json_file", "CLIENTS_FILE", "_CLIENT_NONE", "_sanitize_clients", "_load_clients",
                     "_save_clients", "_client_for", "_client_report", "_client_report_csv",
                     "_client_report_print_html"],
                    {"os": os, "json": json, "datetime": datetime, "AOC_DATA_DIR": data})

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
rows = list(csv.reader(io.StringIO(ns["_client_report_csv"](rep))))
c.check("CSV header", rows[0][:3] == ["month", "client", "session"] and rows[0][-1] == "billed_usd")
c.check("CSV: session lines + one TOTAL per client with sessions", sum(1 for r in rows if r[2] == "TOTAL") == 3)
omni_total = next(r for r in rows if r[1] == "Omni Social" and r[2] == "TOTAL")
c.check("CSV total with markup", omni_total[9] == "16.50" and omni_total[10] == "20" and omni_total[11] == "19.80")
c.check("CSV unassigned named", any(r[1] == "(unassigned)" for r in rows))
one = list(csv.reader(io.StringIO(ns["_client_report_csv"](rep, "Phantom"))))
c.check("CSV for one client", {r[1] for r in one[1:]} == {"Phantom"} and len(one) == 4)

# ── printable page ──
h = ns["_client_report_print_html"](rep, "Omni Social")
c.check("print: title + month name", "<h1>Omni Social</h1>" in h and "October 2026" in h)
c.check("print: markup + total lines", "Markup 20 %" in h and "$19.80" in h and "$16.50" in h)
c.check("print: date range shown", "2026-10-02 – 2026-10-03" in h)
h = ns["_client_report_print_html"](rep, "")
c.check("print: unassigned, titles escaped", "Unassigned sessions" in h and "Random &lt;question&gt;" in h and "<question>" not in h)
c.check("print: unknown client", "No client named" in ns["_client_report_print_html"](rep, "<x>") and "&lt;x&gt;" in ns["_client_report_print_html"](rep, "<x>"))

shutil.rmtree(data, ignore_errors=True)
c.finish()
