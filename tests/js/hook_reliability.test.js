/* Tests _formatHookReliability, extracted straight from monitor.py.
 * Surfaces /analytics' hook_miss_concurrency (previously only ever shown
 * buried inside HISTORY -> ANALYTICS) as one line in the INFRA settings
 * tab -- whether the PreToolUse hook drops more under concurrent load. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, '_formatHookReliability'));

const c = new Checker();

c.check('no data at all -> placeholder text', _formatHookReliability({}) === 'No data yet.');
c.check('null input -> placeholder text, does not crash', _formatHookReliability(null) === 'No data yet.');
c.check('undefined input -> placeholder text', _formatHookReliability(undefined) === 'No data yet.');

const full = {
  hook_miss: { avg_concurrent_sessions: 2.4, n: 10 },
  normal: { avg_concurrent_sessions: 1.1, n: 90 },
};
const out = _formatHookReliability(full);
c.check('includes the miss percentage (10/100 = 10.0%)', out.includes('10.0% hook misses (10/100)'));
c.check('includes the concurrency comparison', out.includes('avg concurrency: 2.4 (miss) vs 1.1 (normal)'));

const missOnly = { hook_miss: { avg_concurrent_sessions: 3.0, n: 5 } };
const outMissOnly = _formatHookReliability(missOnly);
c.check('miss-only data still reports a percentage (5/5 = 100%)', outMissOnly.includes('100.0% hook misses (5/5)'));
c.check('miss-only data has no concurrency comparison (needs both sides)', !outMissOnly.includes('avg concurrency'));

const zeroN = { hook_miss: { avg_concurrent_sessions: null, n: 0 }, normal: { avg_concurrent_sessions: null, n: 0 } };
c.check('both n=0 -> placeholder text (nothing to report)', _formatHookReliability(zeroN) === 'No data yet.');

const nullConcurrency = { hook_miss: { avg_concurrent_sessions: null, n: 3 }, normal: { avg_concurrent_sessions: null, n: 7 } };
const outNullConc = _formatHookReliability(nullConcurrency);
c.check('percentage still shown when concurrency values are null', outNullConc.includes('30.0% hook misses (3/10)'));
c.check('no concurrency comparison appended when either value is null', !outNullConc.includes('avg concurrency'));

c.finish();
