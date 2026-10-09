/* Tests _commitReportHtml (History → COMMITS), extracted from monitor.py:
 * empty/building/error states, KPIs and the share sentence, the by-repo
 * table, ledger sorting (newest / most expensive), the 50-row cap and that
 * commit messages can't inject HTML. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_commitReportHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
const cm = (sha, cost, epoch, extra = {}) => ({ sha, kind: 'committed', branch: 'master', subject: 'Commit ' + sha,
  repo: 'repo-a', cwd: 'C:/r/repo-a', ts: '', epoch, session_id: 's1', title: 'T', cost, files: 1, ins: 10, dels: 2, ...extra });
const data = (commits, extra = {}) => ({ index: { updated_at: 1 }, stats: {
  total: { commits: commits.length, cost: commits.reduce((a, x) => a + x.cost, 0), median: 1.5, ins: 120, dels: 30,
           sessions: 1, spend: 20, no_commit_spend: 4, ...extra },
  by_repo: [{ repo: 'repo-a', commits: commits.length, cost: 6, per_commit: 2, ins: 120, dels: 30, sessions: 1 }],
  commits } });

c.check('error state', _commitReportHtml({ error: 'x' }).includes('failed'));
c.check('building state', _commitReportHtml({ index: { building: true }, stats: null }).includes('Still reading'));
c.check('empty period', _commitReportHtml({ index: { updated_at: 1 }, stats: { total: { commits: 0 } } }).includes('No commits'));

const list = [cm('aaaaaaa', 1, 100), cm('bbbbbbb', 4, 300), cm('ccccccc', 1, 200)];
let h = _commitReportHtml(data(list), 'new', false);
c.check('KPIs: commits, spend, median, no-commit spend', h.includes('>3<') && h.includes('Median commit') && h.includes('No-commit spend'));
c.check('share sentence', h.includes('30% of the') && h.includes('per commit on average'));
c.check('repo table', h.includes('BY REPOSITORY') && h.includes('repo-a'));
const order = s => ['aaaaaaa', 'bbbbbbb', 'ccccccc'].sort((x, y) => s.indexOf(x) - s.indexOf(y));
c.check('newest sort keeps server order', order(h).join() === 'aaaaaaa,bbbbbbb,ccccccc');
h = _commitReportHtml(data(list), 'cost', false);
c.check('most expensive first', h.indexOf('bbbbbbb') < h.indexOf('aaaaaaa'));
c.check('cost bar relative to the max', h.includes('width:100.0%') && h.includes('width:25.0%'));
c.check('OPEN jumps into the session', h.includes('showHistoryDetail(&quot;s1&quot;)'));

const many = Array.from({ length: 60 }, (_, i) => cm(String(1000000 + i), 1, i));
h = _commitReportHtml(data(many), 'new', false);
c.check('capped at 50 rows + show all', (h.match(/class="cc-sha"/g) || []).length === 50 && h.includes('Show all 60'));
h = _commitReportHtml(data(many), 'new', true);
c.check('show all', (h.match(/class="cc-sha"/g) || []).length === 60 && !h.includes('Show all'));

h = _commitReportHtml(data([cm('ddddddd', 2, 1, { subject: '<img src=x onerror=alert(1)>', repo: '<b>r</b>', ins: null })]), 'new', false);
c.check('subject and repo escaped', !h.includes('<img') && h.includes('&lt;img') && !h.includes('<b>r</b>'));
c.check('missing line stats -> no +/- in the row', !/cc-sha[\s\S]*?\+null/.test(h));

c.finish();
