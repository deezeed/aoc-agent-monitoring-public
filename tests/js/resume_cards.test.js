/* Today -> "Pick up where you left off" (_resumeHtml extracted from
 * monitor.py): one card per project, escaped text, Continue vs Switch when a
 * session is already open, Last conversation + edit buttons, folder tag,
 * and the section only shows up in _todayHtml when there are cards. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_fmtDurationDHM', '_lastMessageInfo', '_waitLabel', '_waitingSessions', '_ctxSaveHtml', '_ctxMeterHtml',
                 '_permAskHtml', '_replyAskHtml', '_budgetBadgeHtml', '_loopBadgeHtml', '_awayBlockHtml', '_changesBtnHtml', '_replayBtnHtml',
                 '_todayHtml', '_resumeHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
const now = 1_800_000_000;
const cards = [
  { name: 'AOC <x>', hook: 'Agent "Ops" Center', cwd: 'C:\\p\\AOC', folder: false, prompt: 'pokračuj v AOC', resume_id: 'r1',
    last: { id: 'r1', title: 'round 6', at: now - 3 * 3600 }, sessions: 4, cost: 12.5, open: [] },
  { name: 'api', hook: '', cwd: 'C:\\p\\api', folder: true, prompt: 'fix the build', resume_id: 'r2',
    last: { id: 'r2', title: '', at: now - 86400 * 2 }, sessions: 1, cost: 0.4, open: ["s'1"] },
];
const h = _resumeHtml(cards, now);
c.check('empty -> nothing', _resumeHtml([], now) === '' && _resumeHtml(null, now) === '');
c.check('two cards', (h.match(/class="rs-card/g) || []).length === 2);
c.check('name + hook escaped', h.includes('AOC &lt;x&gt;') && h.includes('Agent &quot;Ops&quot; Center'));
c.check('meta: last title, sessions, cost', h.includes('last: round 6') && h.includes('4 sessions · $12.50 in 30 days') && h.includes('1 session · $0.40'));
c.check('prompt shown', h.includes('“pokračuj v AOC”'));
c.check('Continue for closed project', h.includes('onclick="_resumeGo(0,false)"') && h.includes('▶ Continue'));
c.check('open project -> Switch, no second Continue', h.includes('is-open') && h.includes('_focusSession(') && !h.includes('_resumeGo(1,false)'));
c.check('switch id safely quoted', h.includes(`_focusSession(${jsq("s'1")})`));
c.check('Last conversation + edit on every card', h.includes('_resumeGo(0,true)') && h.includes('_resumeGo(1,true)') && h.includes('_resumeEdit(1)'));
c.check('folder tag only on folder card', (h.match(/class="rs-folder"/g) || []).length === 1);
const base = [{ sessions_list: [] }, {}, null, now, '2027-01-15', {}, 'auto'];
c.check('Today: section with cards', _todayHtml(...base, cards).includes('Pick up where you left off'));
c.check('Today: no section without cards', !_todayHtml(...base, []).includes('Pick up where you left off') && !_todayHtml(...base).includes('Pick up'));
c.finish();
