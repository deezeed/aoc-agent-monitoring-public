/* Tests _fmtCost, extracted straight from monitor.py. Was independently
 * re-declared identically 4 times (session compare, history view, two
 * other render paths) -- consolidated into one shared function. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, '_fmtCost'));

const c = new Checker();

c.check('null -> em dash', _fmtCost(null) === '—');
c.check('undefined -> em dash', _fmtCost(undefined) === '—');
c.check('zero formats as $0.0000, not em dash', _fmtCost(0) === '$0.0000');
c.check('positive value formats to 4 decimals', _fmtCost(1.5) === '$1.5000');
c.check('rounds to 4 decimals', _fmtCost(1.23456) === '$1.2346');
c.check('string-typed number coerces correctly', _fmtCost('2.5') === '$2.5000');

c.finish();
