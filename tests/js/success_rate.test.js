/* Tests _computeSuccessRate, extracted straight from monitor.py's renderKpi.
 * Feeds off today's agents/done counts (already fetched into _kpiAnalytics
 * every 30s for the KPI bar), so the SUCCESS card needs no extra fetch of
 * its own -- this locks down the pure done/total percentage logic. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, '_computeSuccessRate'));

const c = new Checker();

c.check('50% rounds correctly', _computeSuccessRate(4, 2) === 50);
c.check('100% when done equals total', _computeSuccessRate(6, 6) === 100);
c.check('0% when nothing done yet', _computeSuccessRate(5, 0) === 0);
c.check('rounds to nearest integer (2/3 -> 67)', _computeSuccessRate(3, 2) === 67);
c.check('null todayDone treated as 0', _computeSuccessRate(4, null) === 0);
c.check('undefined todayDone treated as 0', _computeSuccessRate(4, undefined) === 0);
c.check('todayAgents === 0 -> null (not NaN/Infinity)', _computeSuccessRate(0, 0) === null);
c.check('null todayAgents -> null', _computeSuccessRate(null, 3) === null);
c.check('undefined todayAgents -> null', _computeSuccessRate(undefined, 3) === null);

c.finish();
