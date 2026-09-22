/* Tests _computeBurnRates and _isBurnSpike, extracted straight from
 * monitor.py. This is the JS mirror of _compute_burn_rates/_is_burn_spike
 * (tests/python/burn_spike.test.py) -- their own comment says "kept in
 * sync by hand... rather than sharing code across a Python/JS boundary
 * that doesn't otherwise exist in this app", which is exactly the kind of
 * duplication that can silently drift. Had zero coverage of its own
 * before this (auto_dismiss.test.js only ever stubs both functions out
 * to isolate something else). Mirrors the Python test's cases 1:1 so a
 * numeric drift between the two implementations would show up as a
 * mismatched expected value here. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, '_computeBurnRates'));
eval(extractFunction(src, '_isBurnSpike'));

const c = new Checker();

// ── _computeBurnRates ──

// 1. fewer than 2 samples -> no verdict possible yet
c.check('single sample -> null', _computeBurnRates([[0, 100]], 100) === null);
c.check('empty history -> null', _computeBurnRates([], 100) === null);

// 2. not enough total observed history yet (< minHistoryS) -> null
const shortHist = [[0, 0], [60, 1000]]; // only 60s observed, default min is 300s
c.check('insufficient observed history -> null', _computeBurnRates(shortHist, 60) === null);

// 3. steady burn rate over a long enough window: recent ~= avg
const steady = [];
for (let t = 0; t <= 600; t += 30) steady.push([t, t * 100]); // 100 tok/s = 6000 tok/min, dead steady
const rates = _computeBurnRates(steady, 600);
c.check('steady rate returns a [recent, avg] pair', rates !== null);
if (rates) {
  const [recentRate, avgRate] = rates;
  c.check('steady rate: recent ~= avg (within 1%)', Math.abs(recentRate - avgRate) / avgRate < 0.01);
  c.check('steady rate: ~6000 tok/min', Math.abs(avgRate - 6000) < 50);
}

// 4. genuine spike: slow for a long time, then a burst in the last 3 min
const now = 900;
let history = [];
for (let t = 0; t <= 600; t += 30) history.push([t, t * 10]); // slow 10 tok/s = 600 tok/min baseline
for (let t = 720; t <= 900; t += 30) history.push([t, 6000 + (t - 720) * 200]); // ~12000 tok/min burst
const spikeRates = _computeBurnRates(history, now);
c.check('burst history returns a pair', spikeRates !== null);
if (spikeRates) {
  const [recentRate, avgRate] = spikeRates;
  c.check('burst: recent rate is much higher than the long-run average', recentRate > avgRate * 2);
}

// ── _isBurnSpike ──

c.check('recent >> avg and above floor -> spike', _isBurnSpike(20000, 1000) === true);
c.check('recent only slightly above avg -> not a spike (ratio too low)', _isBurnSpike(1500, 1000) === false);
c.check('high ratio but below the absolute floor -> not a spike (low-volume noise guard)', _isBurnSpike(100, 10) === false);
c.check('avgRate is zero -> never a spike (nothing to compare against)', _isBurnSpike(50000, 0) === false);
c.check('custom floor/multiplier are honored', _isBurnSpike(3000, 500, 2000, 2) === true);

c.finish();
