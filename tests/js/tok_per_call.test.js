/* Tests _tokPerCall, extracted straight from monitor.py. tool_use_count
 * and tokens were both already persisted per-agent and summed into
 * COST BY MODEL / COST BY AGENT TYPE rows for their own existing stats,
 * but never divided against each other -- this is what turns "which
 * model/type burns more tokens overall" into "...per action it took". */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, '_tokPerCall'));

const c = new Checker();

c.check('divides tokens by tool_use_count', _tokPerCall({ tokens: 10000, tool_use_count: 50 }) === 200);
c.check('rounds to the nearest whole number', _tokPerCall({ tokens: 10001, tool_use_count: 3 }) === 3334);
c.check('zero tool_use_count -> null (nothing to divide by, not Infinity/NaN)', _tokPerCall({ tokens: 500, tool_use_count: 0 }) === null);
c.check('missing tool_use_count -> null', _tokPerCall({ tokens: 500 }) === null);
c.check('null tool_use_count -> null', _tokPerCall({ tokens: 500, tool_use_count: null }) === null);
c.check('missing tokens treated as 0, not NaN', _tokPerCall({ tool_use_count: 10 }) === 0);

c.finish();
