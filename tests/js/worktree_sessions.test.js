/* Parallel worktrees (monitor.py): the New session form's "Where" choice
 * (this PC only), the Start button label, why Merge is disabled, the Today
 * panel (groups, session state, facts, escaping) and the Today section. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_fmtDurationDHM', '_lastMessageInfo', '_waitLabel', '_waitingSessions', '_ctxSaveHtml', '_ctxMeterHtml',
                 '_permAskHtml', '_replyAskHtml', '_budgetBadgeHtml', '_loopBadgeHtml', '_awayBlockHtml', '_changesBtnHtml', '_replayBtnHtml',
                 '_todayHtml', '_launchFormHtml', '_launchGoLabel', '_wtMergeBlock', '_wtHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
let h = _launchFormHtml([], [{ name: '', label: 'This PC' }], '');
c.check('form: Where select on this PC, 0-4', h.includes('id="ln-wt"') && ['value="0"', 'value="1"', 'value="2"', 'value="3"', 'value="4"'].every(x => h.includes(x)));
c.check('form: no worktrees on a remote machine', !_launchFormHtml([], [{ name: '', label: 'This PC' }, { name: 'lap', label: 'lap' }], 'lap').includes('id="ln-wt"'));
c.check('label', _launchGoLabel('now', 0) === 'Start session' && _launchGoLabel('now', 1) === 'Start in a worktree'
  && _launchGoLabel('now', 3) === 'Start 3 sessions' && _launchGoLabel('at', 2) === 'Add to queue');

const W = o => Object.assign({ id: 'g-1', group: 'g', n: 1, of: 2, repo: 'C:\\r\\AOC', repo_name: 'AOC', base: 'main', branch: 'aoc/g-1',
  path: 'C:\\r\\AOC\\.claude\\worktrees\\aoc-g-1', prompt: 'do <it>', model: 'sonnet', created: 1000, exists: true,
  dirty: 0, ahead: 2, behind: 0, files: 3, add: 10, del: 4, last_subject: 'Fix "x"', sessions: [] }, o);
c.check('merge block: ok', _wtMergeBlock(W()) === '');
c.check('merge block: open session', /still open/.test(_wtMergeBlock(W({ sessions: [{ id: 's', active: true }] }))));
c.check('merge block: closed session fine', _wtMergeBlock(W({ sessions: [{ id: 's', active: false }] })) === '');
c.check('merge block: dirty / no commits / gone', /2 uncommitted files/.test(_wtMergeBlock(W({ dirty: 2 })))
  && /No commits/.test(_wtMergeBlock(W({ ahead: 0 }))) && /gone/.test(_wtMergeBlock(W({ exists: false }))));

c.check('panel: empty -> nothing', _wtHtml([], 2000) === '' && _wtHtml(null, 2000) === '');
const items = [W(), W({ id: 'g-2', n: 2, branch: 'aoc/g-2', ahead: 0, files: 0, add: 0, del: 0, last_subject: '', dirty: 1,
  sessions: [{ id: 'sx', active: true, waiting: true, cost: 1.25 }] }),
  W({ id: 'h-1', group: 'h', branch: 'aoc/h-1', repo_name: 'Omni', exists: false })];
h = _wtHtml(items, 1000 + 7200);
c.check('panel: one block per group', (h.match(/class="wt-group"/g) || []).length === 2 && (h.match(/class="wt-row"/g) || []).length === 3);
c.check('panel: escaped prompt/subject', h.includes('“do &lt;it&gt;”') && h.includes('Fix &quot;x&quot;') && !h.includes('<it>'));
c.check('panel: facts', h.includes('2 commits') && h.includes('+10</span> <span class="wt-del">−4</span> in 3 files') && h.includes('1 uncommitted') && h.includes('$1.25'));
c.check('panel: session states', h.includes('Waiting on you') && h.includes('starting…') && h.includes('folder gone'));
c.check('panel: switch to open session', h.includes(`_focusSession(${jsq('sx')})`));
c.check('panel: merge disabled with reason, enabled for clean', /onclick="_wtAct\(&quot;g-2&quot;,'merge'\)" disabled title="A session is still open/.test(h)
  && /_wtAct\(&quot;g-1&quot;,'merge'\)" title="Merge into main/.test(h));
c.check('panel: discard + diff buttons', h.includes(`_wtAct(${jsq('h-1')},'discard')`) && h.includes(`_wtDiff(${jsq('g-1')},${jsq('aoc/g-1')})`));

const base = [{ sessions_list: [] }, {}, null, 1_800_000_000, '2027-01-15', {}, 'auto', []];
c.check('Today: section with worktrees', _todayHtml(...base, items).includes('Parallel worktrees <span class="td-count">3</span>'));
c.check('Today: no section without', !_todayHtml(...base, []).includes('Parallel worktrees') && !_todayHtml(...base).includes('Parallel worktrees'));
c.finish();
