"""Tests _usage_first_sighting / _accumulate_usage, extracted straight from
monitor.py. Claude Code writes one transcript line per content block of a
response (thinking, text, tool_use...), each repeating the same usage; the
scanner used to add every line, overcounting tokens and cost ~2.4x. Each
(message id, request id) must count once, priced with that message's own
model, with 1-hour cache writes at 2x input."""
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
ns = exec_functions(["_MODEL_PRICING", "_CACHE_WRITE_1H_MULT", "_model_pricing", "_calc_cost",
                     "_usage_first_sighting", "_accumulate_usage"])
first, acc, cost = ns["_usage_first_sighting"], ns["_accumulate_usage"], ns["_calc_cost"]

usage = {"input_tokens": 10, "output_tokens": 1000, "cache_creation_input_tokens": 20000,
         "cache_read_input_tokens": 300000,
         "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 20000}}
lines = [  # one response, three content blocks -> three lines
    {"id": "msg_1", "req": "req_1", "model": "claude-opus-5-5"},
    {"id": "msg_1", "req": "req_1", "model": "claude-opus-5-5"},
    {"id": "msg_1", "req": "req_1", "model": "claude-opus-5-5"},
    {"id": "msg_2", "req": "req_2", "model": "claude-sonnet-5"},
    {"id": "msg_2", "req": "req_2", "model": "claude-sonnet-5"},
]
stats = {}
for ln in lines:
    if first(stats, ln["id"], ln["req"]):
        acc(stats, usage, ln["model"])

c.check("two responses counted, not five lines", stats["msg_count"] == 2)
c.check("tokens counted once per response",
        stats["output_tokens"] == 2000 and stats["cache_read_tokens"] == 600000 and stats["cache_write_tokens"] == 40000)
c.check("1h writes tracked", stats["cache_write_1h_tokens"] == 40000)
expected = cost(10, 1000, 20000, 300000, "claude-opus-5-5", 20000) + cost(10, 1000, 20000, 300000, "claude-sonnet-5", 20000)
c.check("each response priced with its own model", abs(stats["cost_acc"] - expected) < 1e-12)
c.check("1h writes priced at 2x input (opus-5-5: $8/M)",
        abs(cost(0, 0, 20000, 0, "claude-opus-5-5", 20000) - 20000 * 8.0 / 1e6) < 1e-12)

s2 = {}
c.check("lines without ids are always counted (older formats)", first(s2, None, None) and first(s2, None, None))
c.check("same message id, different request id = different response",
        first(s2, "m", "r1") and first(s2, "m", "r2") and not first(s2, "m", "r1"))
s3 = {}
acc(s3, {"input_tokens": 5}, "claude-sonnet-4")
c.check("missing fields default to 0", s3["output_tokens"] == 0 and s3["cache_write_1h_tokens"] == 0
        and abs(s3["cost_acc"] - 5 * 3.0 / 1e6) < 1e-15)

c.finish()
