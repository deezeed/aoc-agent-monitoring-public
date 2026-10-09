/* Tests _computeWeekOverWeekCost, extracted straight from monitor.py: the
 * header's "7d cost ▲/▼ N% vs prior wk". Windows are calendar days (today
 * and the 6 before vs the 7 before that), not row positions -- by_day only
 * has rows for days with sessions, so 7 rows used to span up to a month
 * (and read "▼ 100 %" after a few quiet days). Same rule as the server's
 * weekly digest (_week_windows). */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, '_computeWeekOverWeekCost'));
eval(extractFunction(src, '_localDateStr'));

const c = new Checker();
const T = '2026-07-25';

const byDay = [
  { date: '2026-07-25', cost: 10 }, // today
  { date: '2026-07-24', cost: 5 },
  { date: '2026-07-19', cost: 5 },  // 6 days ago -> still this week
  { date: '2026-07-18', cost: 4 },  // 7 days ago -> previous week
  { date: '2026-07-14', cost: 4 },
  { date: '2026-07-12', cost: 12 }, // 13 days ago -> previous week
  { date: '2026-07-11', cost: 999 },// 14 days ago -> neither
];
const r = _computeWeekOverWeekCost(byDay, T);
c.check('this week = today and the 6 days before (10+5+5)', r.cost === 20);
c.check('previous week = days 7..13 back (4+4+12)', r.prevCost === 20);
c.check('equal weeks -> 0 %', r.pctChange === 0);

const sparse = _computeWeekOverWeekCost([{ date: '2026-07-25', cost: 30 }, { date: '2026-07-16', cost: 10 }, { date: '2026-06-01', cost: 500 }], T);
c.check('sparse days: a month-old row is in neither week', sparse.cost === 30 && sparse.prevCost === 10 && sparse.pctChange === 200);
c.check('cheaper week -> negative %', _computeWeekOverWeekCost([{ date: T, cost: 5 }, { date: '2026-07-15', cost: 20 }], T).pctChange === -75);
c.check('no previous week -> null', _computeWeekOverWeekCost([{ date: T, cost: 10 }], T).pctChange === null);
c.check('future-dated rows ignored', _computeWeekOverWeekCost([{ date: '2026-07-26', cost: 10 }], T).cost === 0);
c.check('empty / null input -> zeros, null %', (() => {
  const a = _computeWeekOverWeekCost([], T), b = _computeWeekOverWeekCost(null, T);
  return a.cost === 0 && a.prevCost === 0 && a.pctChange === null && b.cost === 0 && b.pctChange === null;
})());
c.check('rows without a date or cost do not break it', _computeWeekOverWeekCost([{ cost: 7 }, { date: T }, { date: T, cost: 3 }], T).cost === 3);
c.check('no today -> nothing counted', _computeWeekOverWeekCost(byDay, '').cost === 0);
c.check('_localDateStr pads month and day', _localDateStr(new Date(2026, 0, 5)) === '2026-01-05');

c.finish();
