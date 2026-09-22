/* Tests _isCostSpike and _projectAvgCosts, extracted straight from
 * monitor.py -- the JS-side mirror of _is_cost_spike/_project_avg_costs
 * in Python. A code-quality audit found cost_spike was the one new
 * feature missing its client-side (browser-tab) detection entirely,
 * breaking the "hand-synced Python+JS pair" pattern every sibling event
 * (burn_spike, waiting_nudge) already follows -- these two functions and
 * their wiring into diffStatusWithRemove close that gap. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, '_isCostSpike'));
eval(extractFunction(src, '_projectAvgCosts'));

const c = new Checker();

// ── _isCostSpike ──
c.check('well above multiplier and floor -> spike', _isCostSpike(10.0, 2.0) === true);
c.check('exactly at the multiplier boundary -> not a spike', _isCostSpike(6.0, 2.0) === false);
c.check('just above the multiplier boundary -> spike', _isCostSpike(6.01, 2.0) === true);
c.check('high ratio but below the absolute floor -> not a spike', _isCostSpike(0.5, 0.01) === false);
c.check('no historical average -> never a spike', _isCostSpike(100.0, 0) === false);
c.check('session cost below the average -> not a spike', _isCostSpike(1.0, 2.0) === false);
c.check('custom floor/multiplier honored', _isCostSpike(3.0, 1.0, 5.0, 2.0) === false);
c.check('custom floor/multiplier: passes once both satisfied', _isCostSpike(6.0, 1.0, 5.0, 2.0) === true);

// ── _projectAvgCosts ──
const rows = [
  { project: 'AOC', cost: 100.0, sessions: 20 },
  { project: 'PHANTOM AI', cost: 30.0, sessions: 10 },
  { project: 'NoSessions', cost: 5.0, sessions: 0 },
  { project: '', cost: 9.0, sessions: 3 },
  { cost: 4.0, sessions: 2 },
];
const avgs = _projectAvgCosts(rows);
c.check('AOC average computed correctly (100/20=5)', avgs['AOC'] === 5.0);
c.check('PHANTOM AI average computed correctly (30/10=3)', avgs['PHANTOM AI'] === 3.0);
c.check('zero-session row excluded', !('NoSessions' in avgs));
c.check('empty-string project excluded', !('' in avgs));
c.check('row missing project key excluded, does not crash', Object.keys(avgs).length === 2);
c.check('empty input -> empty object', Object.keys(_projectAvgCosts([])).length === 0);
c.check('null input -> empty object, does not crash', Object.keys(_projectAvgCosts(null)).length === 0);

c.finish();
