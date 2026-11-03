/* Stranded worktrees (monitor.py): the diff-stat line, the cards (reason,
 * kind, escaping, Commit WIP disabled when clean / detached, Discard
 * disabled when locked), and that Inbox + Today show them -- Inbox counts
 * them and isn't "All clear" with only stranded work. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(src.match(/const _INBOX_KINDS=\{[\s\S]*?\};/)[0].replace(/^const /, 'var '));
for (const f of ['escHtml', 'jsq', '_fmtDurationDHM', '_lastMessageInfo', '_waitLabel', '_waitingSessions', '_ctxMeterHtml',
                 '_permAskHtml', '_replyAskHtml', '_budgetBadgeHtml', '_loopBadgeHtml', '_awayBlockHtml', '_changesBtnHtml', '_replayBtnHtml',
                 '_inboxItems', '_inboxHtml', '_todayHtml', '_wtMergeBlock', '_wtHtml', '_wtStrandedStat', '_wtStrandedHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
const now = 1_800_000_000;
const W = o => Object.assign({ id: 'abc123', repo: 'C:\\r\\AOC', repo_name: 'AOC', path: 'C:\\r\\AOC\\.claude\\worktrees\\agent-1',
  name: 'agent-1', branch: 'worktree-agent-1', kind: 'agent', base: 'main', exists: true, dirty: 2, ahead: 1, files: 3, add: 10, del: 4,
  wip_add: 5, wip_del: 0, last_subject: 'Half <done>', last_activity: now - 7200, detached: false, locked: false,
  reason: '2 uncommitted files + 1 unmerged commit, untouched for 2 h' }, o);

c.check('stat: commits + uncommitted', _wtStrandedStat(W()) === '1 commit (+10 −4 in 3 files) · 2 uncommitted files (+5 −0 in tracked files)');
c.check('stat: only untracked files', _wtStrandedStat(W({ ahead: 0, dirty: 1, wip_add: 0, wip_del: 0 })) === '1 uncommitted file');
c.check('stat: nothing', _wtStrandedStat(W({ ahead: 0, dirty: 0 })) === '');

let h = _wtStrandedHtml([W()], now);
c.check('card: repo, branch, kind, age, reason', h.includes('AOC · worktree-agent-1') && h.includes('Agent worktree · 2h')
  && h.includes('untouched for 2 h'));
c.check('card: subject escaped', h.includes('Half &lt;done&gt;') && !h.includes('Half <done>'));
c.check('card: three actions wired to the id', ['commit', 'relaunch', 'discard'].every(k => h.includes(`_wtStrandedAct(${jsq('abc123')},'${k}')`)));
c.check('card: all enabled normally', !/disabled/.test(h));
c.check('commit disabled when clean', /Commit WIP<\/button>/.test(h) && /disabled title="Nothing uncommitted"/.test(_wtStrandedHtml([W({ dirty: 0 })], now)));
c.check('commit disabled on detached HEAD', /disabled title="Detached HEAD/.test(_wtStrandedHtml([W({ detached: true, branch: '' })], now)));
c.check('discard disabled when locked', /disabled title="git has it locked/.test(_wtStrandedHtml([W({ locked: true })], now)));
c.check('kind labels', _wtStrandedHtml([W({ kind: 'aoc' })], now).includes('Parallel session')
  && _wtStrandedHtml([W({ kind: 'other' })], now).includes('>Worktree'));
c.check('nothing -> empty', _wtStrandedHtml([], now) === '' && _wtStrandedHtml(null, now) === '');
c.check('evil id escaped in onclick', _wtStrandedHtml([W({ id: "x');alert(1);//" })], now).includes(`_wtStrandedAct(${jsq("x');alert(1);//")},'commit')`));

// Inbox
h = _inboxHtml([], now, [W()]);
c.check('inbox: only stranded -> counted, not "All clear"', h.includes('1 thing needs you') && !h.includes('All clear') && h.includes('wts-item'));
c.check('inbox: no ib-list without session items', !h.includes('class="ib-list"'));
const items = _inboxItems({ sessions_list: [{ id: 's', display_name: 's', session_active: true, waiting_on_you: true, waiting_secs: 60, last_message: 'Done.' }] }, now);
h = _inboxHtml(items, now, [W(), W({ id: 'b' })]);
c.check('inbox: sessions first, then stranded; total count', h.includes('3 things need you') && h.indexOf('ib-list') < h.indexOf('wts-list'));
c.check('inbox: still works without the 3rd arg', _inboxHtml([], now).includes('All clear'));

// Today
h = _todayHtml({ sessions_list: [] }, {}, null, now, '2027-01-15', {}, 'auto', [], [], [W()]);
c.check('today: Stranded work section with count', h.includes('Stranded work <span class="td-count">1</span>') && h.includes('wts-item'));
c.check('today: no section without stranded', !_todayHtml({ sessions_list: [] }, {}, null, now, '2027-01-15', {}, 'auto', [], [], []).includes('Stranded work'));

c.finish();
