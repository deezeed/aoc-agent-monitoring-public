/* Tests _topProjectsToday, extracted straight from monitor.py. Feeds off
 * by_day_project (an /analytics field renderKpi already fetches every 30s
 * for the KPI bar), so the "top projects today" widget needs no fetch of
 * its own -- this locks down the pure filter/sort/slice logic. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, '_topProjectsToday'));

const c = new Checker();

const byDayProject = [
  { date: '2026-07-20', project: 'AOC', cost: 12.5 },
  { date: '2026-07-20', project: 'PHANTOM AI', cost: 40.0 },
  { date: '2026-07-20', project: 'Omnisocial', cost: 3.2 },
  { date: '2026-07-20', project: 'Tiny', cost: 0 },
  { date: '2026-07-19', project: 'Yesterday Project', cost: 999.0 },
];

const top = _topProjectsToday(byDayProject, '2026-07-20');
c.check('returns exactly 3 (default topN)', top.length === 3);
c.check('sorted descending by cost, highest first', top[0].project === 'PHANTOM AI' && top[0].cost === 40.0);
c.check('second place is AOC', top[1].project === 'AOC' && top[1].cost === 12.5);
c.check('third place is Omnisocial', top[2].project === 'Omnisocial' && top[2].cost === 3.2);
c.check('rows from other dates are excluded', !top.some(r => r.project === 'Yesterday Project'));
c.check('zero-cost rows are excluded', !top.some(r => r.project === 'Tiny'));

const top1 = _topProjectsToday(byDayProject, '2026-07-20', 1);
c.check('custom topN=1 returns exactly 1', top1.length === 1);
c.check('custom topN=1 is still the highest-cost project', top1[0].project === 'PHANTOM AI');

c.check('no matching date -> empty array', _topProjectsToday(byDayProject, '2099-01-01').length === 0);
c.check('empty input -> empty array', _topProjectsToday([], '2026-07-20').length === 0);
c.check('null input -> empty array, does not crash', _topProjectsToday(null, '2026-07-20').length === 0);

const missingProject = [{ date: '2026-07-20', project: '', cost: 5 }, { date: '2026-07-20', cost: 6 }];
const topMissing = _topProjectsToday(missingProject, '2026-07-20');
c.check('missing/empty project falls back to "(none)" label', topMissing.every(r => r.project === '(none)'));

c.finish();
