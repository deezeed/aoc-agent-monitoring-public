"""Tests _calc_cost (and its _model_pricing/_MODEL_PRICING dependencies),
extracted straight from monitor.py."""
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ns = exec_functions(["_MODEL_PRICING", "_CACHE_WRITE_1H_MULT", "_model_pricing", "_calc_cost"])
_calc_cost = ns["_calc_cost"]
_model_pricing = ns["_model_pricing"]

c = Checker()

# claude-sonnet-4 pricing per monitor.py: (3.0, 15.0, 3.75, 0.30) $/1M
cost = _calc_cost(1_000_000, 0, 0, 0, "claude-sonnet-4")
c.check("input tokens priced correctly (sonnet-4, 1M in)", abs(cost - 3.0) < 1e-9)

cost = _calc_cost(0, 1_000_000, 0, 0, "claude-sonnet-4")
c.check("output tokens priced correctly (sonnet-4, 1M out)", abs(cost - 15.0) < 1e-9)

cost = _calc_cost(0, 0, 1_000_000, 0, "claude-sonnet-4")
c.check("cache-write tokens priced correctly", abs(cost - 3.75) < 1e-9)

cost = _calc_cost(0, 0, 0, 1_000_000, "claude-sonnet-4")
c.check("cache-read tokens priced correctly", abs(cost - 0.30) < 1e-9)

cost = _calc_cost(500_000, 500_000, 0, 0, "claude-opus-4")
c.check("opus-4 pricing is distinct from sonnet (more expensive)", abs(cost - (15.0 * 0.5 + 75.0 * 0.5)) < 1e-9)

cost_unknown = _calc_cost(1_000_000, 0, 0, 0, "some-future-model-nobody-added-yet")
c.check("unknown model falls back to sonnet-4 default pricing", abs(cost_unknown - 3.0) < 1e-9)

pricing = _model_pricing("claude-sonnet-4-5-20260101")
c.check("prefix match works for versioned model strings", pricing == (3.0, 15.0, 3.75, 0.30))

c.check("zero usage -> zero cost", _calc_cost(0, 0, 0, 0, "claude-sonnet-4") == 0.0)

# current models: specific prefixes must win over their family
c.check("opus-5-5 is $4/$20, not opus-5's $5",
        _model_pricing("claude-opus-5-5") == (4.0, 20.0, 5.0, 0.20))
c.check("opus-5 $5/$25", _model_pricing("claude-opus-5") == (5.0, 25.0, 6.25, 0.50))
c.check("opus-4-5..4-8 at $5, not opus-4's $15",
        all(_model_pricing(f"claude-opus-4-{v}")[0] == 5.0 for v in (5, 6, 7, 8)))
c.check("opus-4-1 / opus-4 dated stay $15",
        _model_pricing("claude-opus-4-1")[0] == 15.0 and _model_pricing("claude-opus-4-20250514")[0] == 15.0)
c.check("sonnet-5 $2/$10", _model_pricing("claude-sonnet-5") == (2.0, 10.0, 2.5, 0.20))
c.check("fable-5-1 cache read $0.25 vs fable-5 $1",
        _model_pricing("claude-fable-5-1")[3] == 0.25 and _model_pricing("claude-fable-5")[3] == 1.0)
c.check("haiku-4-5 $1/$5", _model_pricing("claude-haiku-4-5-20251001")[:2] == (1.0, 5.0))

# 1-hour cache writes cost 2x input instead of 1.25x
c.check("1h cache write = 2x input", abs(_calc_cost(0, 0, 1_000_000, 0, "claude-opus-5-5", 1_000_000) - 8.0) < 1e-9)
c.check("mixed 5m/1h writes", abs(_calc_cost(0, 0, 1_000_000, 0, "claude-opus-5-5", 250_000) - (0.75 * 5.0 + 0.25 * 8.0)) < 1e-9)
c.check("1h share can't exceed total writes", abs(_calc_cost(0, 0, 100, 0, "claude-opus-5-5", 10**9) - 100 * 8.0 / 1e6) < 1e-12)

c.finish()
