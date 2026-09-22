/* Tests _computeMonthProjection, extracted straight from monitor.py.
 * This is the run-rate cost forecast now shared by renderHistory's
 * analytics view and the Cost/Budget settings pane (previously inlined
 * separately in renderHistory only). Since the function reads the real
 * wall clock (new Date()) rather than taking "now" as a parameter, this
 * test derives its expected values the same way the function itself
 * does, rather than mocking Date. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, '_computeMonthProjection'));
eval(extractFunction(src, '_perProjectBudgetSummary'));

const c = new Checker();

const now = new Date();
const monthPrefix = now.toISOString().slice(0, 7);
const daysElapsed = now.getDate();
const daysInMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
const daysRemaining = daysInMonth - daysElapsed;

// 1. empty by_day -> zero cost so far, zero projection
const empty = _computeMonthProjection([]);
c.check('empty by_day -> costSoFar is 0', empty.costSoFar === 0);
c.check('empty by_day -> projectedMonthEnd is 0', empty.projectedMonthEnd === 0);

// 2. rows entirely from a different month are excluded
const otherMonth = [{ date: '2020-01-15', cost: 999 }];
c.check('rows from another month excluded from costSoFar', _computeMonthProjection(otherMonth).costSoFar === 0);

// 3. rows from THIS month are summed; other-month rows ignored
const thisMonthDate = `${monthPrefix}-01`;
const mixed = [
  { date: thisMonthDate, cost: 10 },
  { date: thisMonthDate, cost: 5 },
  { date: '1999-01-01', cost: 1000 },
];
const result = _computeMonthProjection(mixed);
c.check('this-month rows summed correctly, other-month row excluded', result.costSoFar === 15);

// 4. projection formula: costSoFar + (costSoFar/daysElapsed)*daysRemaining
const expectedProjection = 15 + (daysElapsed > 0 ? 15 / daysElapsed : 0) * daysRemaining;
c.check('projectedMonthEnd matches the run-rate formula', Math.abs(result.projectedMonthEnd - expectedProjection) < 1e-9);

// 5. a row with no cost field doesn't crash (treated as 0)
const noCostField = [{ date: thisMonthDate }];
c.check('row with missing cost field treated as 0, no crash', _computeMonthProjection(noCostField).costSoFar === 0);

// ── _perProjectBudgetSummary ──────────────────────────────────────────
// Reuses _computeMonthProjection per-project, so it inherits the same
// real-wall-clock dependency -- derive expected values the same way.
const byDayProject = [
  { date: thisMonthDate, project: 'AOC', cost: 10 },
  { date: thisMonthDate, project: 'AOC', cost: 5 },
  { date: thisMonthDate, project: 'PHANTOM AI', cost: 40 },
  { date: '1999-01-01', project: 'AOC', cost: 999 }, // other month, excluded
];
const budgets = { 'AOC': 20, 'PHANTOM AI': 10 };
const summary = _perProjectBudgetSummary(budgets, byDayProject);
c.check('one entry per configured project', summary.length === 2);
const aoc = summary.find(r => r.project === 'AOC');
const phantom = summary.find(r => r.project === 'PHANTOM AI');
c.check('AOC costSoFar sums only its own this-month rows', aoc.costSoFar === 15);
c.check('AOC pctUsed computed against its own budget', Math.abs(aoc.pctUsed - (15 / 20 * 100)) < 1e-9);
c.check('PHANTOM AI costSoFar isolated from AOC rows', phantom.costSoFar === 40);
c.check('PHANTOM AI over its budget shows pctUsed > 100', phantom.pctUsed > 100);
c.check('empty project_budgets -> empty array', _perProjectBudgetSummary({}, byDayProject).length === 0);
c.check('null project_budgets -> empty array, no crash', _perProjectBudgetSummary(null, byDayProject).length === 0);
c.check('project with a budget but no matching rows -> costSoFar 0, not a crash',
  _perProjectBudgetSummary({ 'Ghost Project': 5 }, byDayProject)[0].costSoFar === 0);
c.check('budget of 0 -> pctUsed is 0, not Infinity/NaN',
  _perProjectBudgetSummary({ 'AOC': 0 }, byDayProject)[0].pctUsed === 0);

c.finish();
