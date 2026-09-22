"""Tests _compute_burn_rates and _is_burn_spike, extracted straight from
monitor.py. This is the token-burn anomaly detector -- a live in-memory
rate check (no persisted baseline, per the user's chosen approach): a
session's recent tokens/min rate compared against its own session-long
average, with an absolute floor so a low-volume session's noisy ratio
doesn't false-positive."""
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ns = exec_functions(["_compute_burn_rates", "_is_burn_spike"])
_compute_burn_rates = ns["_compute_burn_rates"]
_is_burn_spike = ns["_is_burn_spike"]

c = Checker()

# ── _compute_burn_rates ──

# 1. fewer than 2 samples -> no verdict possible yet
c.check("single sample -> None", _compute_burn_rates([(0, 100)], 100) is None)
c.check("empty history -> None", _compute_burn_rates([], 100) is None)

# 2. not enough total observed history yet (< min_history_s) -> None,
# avoids false positives on brand-new sessions
short_hist = [(0, 0), (60, 1000)]  # only 60s observed, default min is 300s
c.check("insufficient observed history -> None", _compute_burn_rates(short_hist, 60) is None)

# 3. steady burn rate over a long enough window: recent ~= avg
steady = [(t, t * 100) for t in range(0, 601, 30)]  # 100 tok/s = 6000 tok/min, dead steady
rates = _compute_burn_rates(steady, 600)
c.check("steady rate returns a (recent, avg) tuple", rates is not None)
if rates:
    recent_rate, avg_rate = rates
    c.check("steady rate: recent ~= avg (within 1%)", abs(recent_rate - avg_rate) / avg_rate < 0.01)
    c.check("steady rate: ~6000 tok/min", abs(avg_rate - 6000) < 50)

# 4. genuine spike: slow for a long time, then a burst in the last 3 min
now = 900
history = [(t, t * 10) for t in range(0, 601, 30)]  # slow 10 tok/s = 600 tok/min average baseline
# then a burst: last 3 min (720-900) gains a lot more
history += [(t, 6000 + (t - 720) * 200) for t in range(720, 901, 30)]  # ~12000 tok/min in the burst window
spike_rates = _compute_burn_rates(history, now)
c.check("burst history returns a tuple", spike_rates is not None)
if spike_rates:
    recent_rate, avg_rate = spike_rates
    c.check("burst: recent rate is much higher than the long-run average", recent_rate > avg_rate * 2)

# ── _is_burn_spike ──

c.check("recent >> avg and above floor -> spike", _is_burn_spike(recent_rate=20000, avg_rate=1000) is True)
c.check("recent only slightly above avg -> not a spike (ratio too low)", _is_burn_spike(recent_rate=1500, avg_rate=1000) is False)
c.check("high ratio but below the absolute floor -> not a spike (low-volume noise guard)",
        _is_burn_spike(recent_rate=100, avg_rate=10) is False)
c.check("avg_rate is zero -> never a spike (nothing to compare against)",
        _is_burn_spike(recent_rate=50000, avg_rate=0) is False)
c.check("custom floor/multiplier are honored",
        _is_burn_spike(recent_rate=3000, avg_rate=500, min_floor=2000, multiplier=2) is True)

c.finish()
