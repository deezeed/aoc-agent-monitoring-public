"""Model advice (monitor.py): _advice_kind (what work a call did),
_advice_scan_file over transcript lines (each API call once, period
filter, subagent files), _model_label, and _advice_report's tips:
exploration on Opus, subagents on Opus, the honest main-model saving,
monthly scaling and the 'already lean' case."""
import sys, os, json, re, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
ns = exec_functions(["_MODEL_PRICING", "_CACHE_WRITE_1H_MULT", "_model_pricing", "_calc_cost", "_ADVICE_READ_ONLY",
                     "_ADVICE_EDIT", "_ADVICE_SHELL", "_ADVICE_CHEAP", "_advice_kind", "_advice_scan_file",
                     "_model_label", "_advice_report"], {"json": json, "re": re})
k = ns["_advice_kind"]
c.check("kinds", [k(x) for x in ([], ["Read", "Grep"], ["Read", "Edit"], ["Bash", "Read"], ["Agent", "Bash"], ["mcp__x"])]
        == ["text", "explore", "edit", "shell", "delegate", "other"])
lbl = ns["_model_label"]
c.check("labels", [lbl(m) for m in ("claude-opus-5-5", "claude-sonnet-5", "claude-haiku-4-5-20251001", "claude-opus-4-20250514", "x", "")]
        == ["Opus 5.5", "Sonnet 5", "Haiku 4.5", "Opus 4", "x", "?"])

# ── scanning ──
tmp = tempfile.mkdtemp(prefix="aoc_adv_")
main = os.path.join(tmp, "s.jsonl")
os.makedirs(os.path.join(tmp, "s", "subagents"))
sub = os.path.join(tmp, "s", "subagents", "agent-1.jsonl")
U = {"input_tokens": 1000, "output_tokens": 1000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 1_000_000}


def call(mid, model, tools, ts="2026-10-05T10:00:00.000Z", text=True):
    lines = []
    if text:
        lines.append({"type": "assistant", "timestamp": ts, "requestId": "r" + mid,
                      "message": {"id": mid, "model": model, "usage": U, "content": [{"type": "text", "text": "hi"}]}})
    for i, t in enumerate(tools):  # one line per content block, usage repeated (Claude Code does that)
        lines.append({"type": "assistant", "timestamp": ts, "requestId": "r" + mid,
                      "message": {"id": mid, "model": model, "usage": U, "content": [{"type": "tool_use", "id": f"{mid}{i}", "name": t, "input": {}}]}})
    return lines


with open(main, "w", encoding="utf-8") as f:
    for o in (call("m1", "claude-opus-5-5", ["Read", "Grep"]) + call("m2", "claude-opus-5-5", ["Edit", "Bash"])
              + call("m3", "claude-opus-5-5", [], ts="2026-08-01T00:00:00Z")   # before the period
              + call("m4", "<synthetic>", []) + [{"type": "user", "message": {"content": "x"}}]):
        f.write(json.dumps(o) + "\n")
    f.write("{broken\n")
with open(sub, "w", encoding="utf-8") as f:
    for o in call("a1", "claude-opus-5-5", ["Grep"]):
        f.write(json.dumps(o) + "\n")
agg = {}
ns["_advice_scan_file"](main, "2026-10-01T00:00:00", agg)
ns["_advice_scan_file"](sub, "2026-10-01T00:00:00", agg)
c.check("each call counted once despite one line per block", agg[("main", "claude-opus-5-5", "explore")][0] == 1)
c.check("edit wins over shell", ("main", "claude-opus-5-5", "edit") in agg and ("main", "claude-opus-5-5", "shell") not in agg)
c.check("old calls and <synthetic> skipped", ("main", "claude-opus-5-5", "text") not in agg and len(agg) == 3)
c.check("subagent file recognized", agg[("subagent", "claude-opus-5-5", "explore")][0] == 1)
cost = agg[("main", "claude-opus-5-5", "explore")]
c.check("priced: opus = 1000*4 + 1000*20 + 1M*0.20 per M", abs(cost[1] - (4000 + 20000 + 200000) / 1e6) < 1e-9)
c.check("re-priced on sonnet and haiku", abs(cost[2] - (2000 + 10000 + 200000) / 1e6) < 1e-9 and abs(cost[3] - (1000 + 5000 + 100000) / 1e6) < 1e-9)

# ── report & tips ──
big = {("main", "claude-opus-5-5", "explore"): [500, 60.0, 45.0, 22.0],
       ("main", "claude-opus-5-5", "shell"): [3000, 300.0, 240.0, 120.0],
       ("subagent", "claude-opus-5-5", "explore"): [100, 10.0, 7.0, 3.5],
       ("main", "claude-sonnet-5", "edit"): [900, 100.0, 100.0, 50.0]}
r = ns["_advice_report"](big, 30)
c.check("total + by model shares", r["total"] == 470.0 and r["by_model"][0]["label"] == "Opus 5.5" and r["by_model"][0]["share"] == 78.7)
ids = [t["id"] for t in r["tips"]]
c.check("three tips, biggest saving first", ids == ["main", "explore", "subagents"])
t = {x["id"]: x for x in r["tips"]}
c.check("explore saving = opus - haiku", t["explore"]["saving_month"] == 38.0)
c.check("subagent saving = opus - sonnet", t["subagents"]["saving_month"] == 3.0)
c.check("main: honest %, without the explore calls (tips add up)", t["main"]["saving_month"] == 60.0 and "saves 20% here" in t["main"]["title"])
r7 = ns["_advice_report"](big, 7)
c.check("7 days scaled to a month", abs({x["id"]: x for x in r7["tips"]}["explore"]["saving_month"] - round(38.0 * 30 / 7, 2)) < 0.01)
lean = ns["_advice_report"]({("main", "claude-sonnet-5", "edit"): [10, 50.0, 50.0, 25.0]}, 30)
c.check("only Sonnet -> 'already lean'", [x["id"] for x in lean["tips"]] == ["fine"])
c.check("tiny Opus spend -> no nagging", ns["_advice_report"]({("main", "claude-opus-5-5", "explore"): [1, 1.0, 0.8, 0.4]}, 30)["tips"][0]["id"] == "fine")
c.check("nothing -> zero, no tips", ns["_advice_report"]({}, 30)["total"] == 0 and ns["_advice_report"]({}, 30)["tips"] == [])

shutil.rmtree(tmp, ignore_errors=True)
c.finish()
