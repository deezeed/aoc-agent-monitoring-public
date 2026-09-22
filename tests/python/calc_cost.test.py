"""Tests _calc_cost (and its _model_pricing/_MODEL_PRICING dependencies),
extracted straight from monitor.py."""
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ns = exec_functions(["_MODEL_PRICING", "_model_pricing", "_calc_cost"])
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

c.finish()
