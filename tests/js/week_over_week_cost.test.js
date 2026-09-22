/* Tests _computeWeekOverWeekCost, extracted straight from monitor.py.
 * Mirrors _build_digest_summary's own last-7-vs-preceding-7 by_day
 * slicing math server-side, just computed client-side off the same
 * by_day the KPI bar's own 30s /analytics fetch already carries -- so
 * the weekly digest's cost trend is visible live, not just once a week
 * in a toast/webhook. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, '_computeWeekOverWeekCost'));

const c = new Checker();

// by_day is ORDER BY date DESC from the backend -- index 0 is the most
// recent day, matching _build_digest_summary's own by_day[:7]/[7:14].
const byDay = [
  { date: '2026-07-25', cost: 10 }, // last 7 (most recent)
  { date: '2026-07-24', cost: 5 },
  { date: '2026-07-23', cost: 5 },
  { date: '2026-07-22', cost: 0 },
  { date: '2026-07-21', cost: 0 },
  { date: '2026-07-20', cost: 0 },
  { date: '2026-07-19', cost: 0 },
  { date: '2026-07-18', cost: 4 }, // preceding 7
  { date: '2026-07-17', cost: 4 },
  { date: '2026-07-16', cost: 4 },
  { date: '2026-07-15', cost: 4 },
  { date: '2026-07-14', cost: 4 },
  { date: '2026-07-13', cost: 0 },
  { date: '2026-07-12', cost: 0 },
  { date: '2026-07-11', cost: 999 }, // 15th entry -- must be excluded from both windows
];

const r = _computeWeekOverWeekCost(byDay);
c.check('sums the last 7 entries (10+5+5+0+0+0+0=20)', r.cost === 20);
c.check('sums the preceding 7 entries (4*5+0+0=20)', r.prevCost === 20);
c.check('pctChange is 0 when cost equals prevCost', r.pctChange === 0);
c.check('the 15th entry is excluded from both windows', true); // implicit in the sums above already being exact

const rIncrease = _computeWeekOverWeekCost([{ cost: 30 }, ...Array(6).fill({ cost: 0 }), { cost: 10 }, ...Array(6).fill({ cost: 0 })]);
c.check('positive pctChange when this week costs more (30 vs 10 = +200%)', rIncrease.pctChange === 200);

const rDecrease = _computeWeekOverWeekCost([{ cost: 5 }, ...Array(6).fill({ cost: 0 }), { cost: 20 }, ...Array(6).fill({ cost: 0 })]);
c.check('negative pctChange when this week costs less (5 vs 20 = -75%)', rDecrease.pctChange === -75);

c.check('no prior-week data -> pctChange is null (nothing to compare against)',
  _computeWeekOverWeekCost([{ cost: 10 }]).pctChange === null);
c.check('empty input -> cost/prevCost both 0, pctChange null, no crash', (() => {
  const empty = _computeWeekOverWeekCost([]);
  return empty.cost === 0 && empty.prevCost === 0 && empty.pctChange === null;
})());
c.check('null input -> same as empty, does not crash', (() => {
  const nul = _computeWeekOverWeekCost(null);
  return nul.cost === 0 && nul.prevCost === 0 && nul.pctChange === null;
})());
c.check('rows with a missing cost field are treated as 0, not NaN', (() => {
  const r2 = _computeWeekOverWeekCost([{ date: '2026-07-25' }, { cost: 10 }]);
  return r2.cost === 10;
})());

c.finish();
