/* Background agents (monitor.py, GET /agents/background): _bgDead,
 * _bgStrandedLeft, the agent cards (_bgAgentsHtml: state tag, reason,
 * worktree facts, escaping, Relaunch only for dead/failed -- through the
 * stranded-worktree buttons when its worktree is stranded), the Inbox
 * (dead ones counted, a stranded worktree not shown twice) and Today
 * (a "Background agents" section before "Parallel worktrees", dead first). */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(src.match(/const _BG_PROTECTED=\[[^\]]*\];/)[0].replace(/^const /, 'var '));
eval(src.match(/const _INBOX_KINDS=\{[\s\S]*?\};/)[0].replace(/^const /, 'var '));
for (const f of ['escHtml', 'jsq', '_fmtDurationDHM', '_lastMessageInfo', '_waitLabel', '_waitingSessions', '_ctxMeterHtml',
                 '_permAskHtml', '_replyAskHtml', '_budgetBadgeHtml', '_loopBadgeHtml', '_awayBlockHtml', '_changesBtnHtml', '_replayBtnHtml',
                 '_inboxItems', '_inboxHtml', '_todayHtml', '_wtMergeBlock', '_wtHtml', '_wtStrandedStat', '_wtStrandedBtns', '_wtStrandedHtml',
                 '_bgDead', '_bgStrandedLeft', '_bgCanCheckpoint', '_bgCheckpointBtns', '_bgAgentsHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
const now = 1_800_000_000;
const A = o => Object.assign({ id: 'ag_1', name: 'Fix the <parser>', state: 'working', agent_type: 'fork', session_name: 'AOC',
  last_activity: now - 120, reason: '', cwd: 'C:\\r\\AOC', tokens_used: 52000,
  worktree: { path: 'C:\\r\\AOC\\.claude\\worktrees\\agent-1', repo_name: 'AOC', branch: 'worktree-agent-1', dirty: 2, ahead: 1,
              last_commit_at: now - 3600, last_commit_subject: 'Half <done>', stranded_id: null } }, o);
const W = o => Object.assign({ id: 'wt9', repo: 'C:\\r\\AOC', repo_name: 'AOC', path: 'C:\\r\\AOC\\.claude\\worktrees\\agent-1',
  name: 'agent-1', branch: 'worktree-agent-1', kind: 'agent', exists: true, dirty: 2, ahead: 1, files: 3, add: 10, del: 4,
  last_activity: now - 7200, detached: false, locked: false, reason: '2 uncommitted files, untouched for 2 h' }, o);

// pure helpers
const all = [A(), A({ id: 'd', state: 'dead' }), A({ id: 'f', state: 'failed' }), A({ id: 'q', state: 'quiet' }), A({ id: 'ok', state: 'finished' })];
c.check('_bgDead: dead + failed only', _bgDead(all).map(a => a.id).join() === 'd,f');
c.check('_bgDead: null-safe', _bgDead(null).length === 0);
const rows = [A({ state: 'dead', worktree: { ...A().worktree, stranded_id: 'wt9' } })];
c.check('_bgStrandedLeft: drops the stranded worktree an agent row already shows',
  _bgStrandedLeft(rows, [W(), W({ id: 'other' })]).map(w => w.id).join() === 'other');
c.check('_bgStrandedLeft: rows without worktree keep everything', _bgStrandedLeft([A({ worktree: null })], [W()]).length === 1);
c.check('_bgStrandedLeft: null-safe', _bgStrandedLeft(null, null).length === 0);

// cards
let h = _bgAgentsHtml([A()], now, []);
c.check('card: state tag + class', h.includes('bga-working') && h.includes('>Working<'));
c.check('card: name escaped', h.includes('Fix the &lt;parser&gt;') && !h.includes('<parser>'));
c.check('card: type, session, activity', h.includes('fork · AOC · active 2m ago'));
c.check('card: worktree facts', h.includes('AOC · worktree-agent-1') && h.includes('2 files uncommitted') && h.includes('1 commit not merged')
  && h.includes('Half &lt;done&gt;'));
c.check('card: tokens', h.includes('52k tokens'));
c.check('card: no relaunch while working', !h.includes('Relaunch'));
c.check('card: no relaunch when quiet / finished', !_bgAgentsHtml([A({ state: 'quiet' }), A({ state: 'finished' })], now, []).includes('Relaunch'));
c.check('card: unknown state shown as quiet', _bgAgentsHtml([A({ state: '<x>' })], now, []).includes('bga-quiet') && !_bgAgentsHtml([A({ state: '<x>' })], now, []).includes('<x>'));
c.check('card: removed worktree', _bgAgentsHtml([A({ worktree: null, worktree_path: 'C:\\gone' })], now, []).includes('worktree removed'));
const clean = { ...A().worktree, dirty: 0 };
h = _bgAgentsHtml([A({ id: "x');alert(1);//", state: 'dead', reason: 'Plan limit hit <5h>', worktree: clean })], now, []);
c.check('dead: reason with warning, escaped', h.includes('bga-dead') && h.includes('⚠ Plan limit hit &lt;5h&gt;'));
c.check('dead: own Relaunch button, id escaped', h.includes(`_bgAgentRelaunch(${jsq("x');alert(1);//")})`));
h = _bgAgentsHtml([A({ state: 'failed', reason: 'boom', worktree: clean })], now, []);
c.check('failed: Relaunch too, no warning sign', h.includes('_bgAgentRelaunch(') && h.includes('boom') && !h.includes('⚠'));
h = _bgAgentsHtml([A({ state: 'dead', worktree: { ...clean, stranded_id: 'wt9' } })], now, [W({ dirty: 0 })]);
c.check('dead + stranded worktree: the stranded buttons instead', h.includes(`_wtStrandedAct(${jsq('wt9')},'relaunch')`)
  && h.includes('Commit WIP') && !h.includes('_bgAgentRelaunch('));
// checkpoint: a dead agent with uncommitted files on its own branch
c.check('checkpointable: dead + dirty + own branch', _bgCanCheckpoint(A({ state: 'dead' })) && _bgCanCheckpoint(A({ state: 'failed' })));
c.check('not while working / quiet / finished', !_bgCanCheckpoint(A()) && !_bgCanCheckpoint(A({ state: 'quiet' })) && !_bgCanCheckpoint(A({ state: 'finished' })));
c.check('not without uncommitted files or worktree', !_bgCanCheckpoint(A({ state: 'dead', worktree: clean })) && !_bgCanCheckpoint(A({ state: 'dead', worktree: null })));
c.check('never on a detached HEAD', !_bgCanCheckpoint(A({ state: 'dead', worktree: { ...A().worktree, detached: true } }))
  && !_bgCanCheckpoint(A({ state: 'dead', worktree: { ...A().worktree, branch: '' } })));
c.check('never on master/main or the base branch', ['master', 'main'].every(b => !_bgCanCheckpoint(A({ state: 'dead', worktree: { ...A().worktree, branch: b } })))
  && !_bgCanCheckpoint(A({ state: 'dead', worktree: { ...A().worktree, branch: 'feat', base: 'feat' } })));
h = _bgAgentsHtml([A({ id: "y');x(//", state: 'dead', reason: 'plan limit' })], now, []);
c.check('dead + dirty: Checkpoint + relaunch and Checkpoint buttons, id escaped', h.includes(`_bgAgentCheckpoint(${jsq("y');x(//")},true)`)
  && h.includes(`_bgAgentCheckpoint(${jsq("y');x(//")},false)`) && h.includes('>Checkpoint + relaunch<') && h.includes('>Checkpoint<'));
c.check('dead + dirty: card says N files uncommitted', h.includes('2 files uncommitted'));
c.check('dead + dirty: no plain Relaunch (the checkpoint one replaces it)', !h.includes('_bgAgentRelaunch('));
c.check('dead + dirty, not stranded: no Discard', !h.includes('wt-discard'));
h = _bgAgentsHtml(rows, now, [W()]);
c.check('dead + dirty + stranded: checkpoint buttons + the worktree Discard, no Commit WIP twice',
  h.includes('_bgAgentCheckpoint(') && h.includes(`_wtStrandedAct(${jsq('wt9')},'discard')`) && !h.includes('Commit WIP'));
c.check('checkpoint tooltip names the branch', h.includes('WIP commit on worktree-agent-1'));
h = _inboxHtml([], now, [], _bgDead([A({ id: 'dd', state: 'dead' })]));
c.check('inbox: a dead dirty agent offers the checkpoint', h.includes('_bgAgentCheckpoint(') && h.includes('1 thing needs you'));
c.check('nothing -> empty', _bgAgentsHtml([], now, []) === '' && _bgAgentsHtml(null, now) === '');

// Inbox
h = _inboxHtml([], now, [], _bgDead(all));
c.check('inbox: dead agents counted, not "All clear"', h.includes('2 things need you') && !h.includes('All clear') && h.includes('bga-dead'));
h = _inboxHtml([], now, [W(), W({ id: 'other' })], _bgDead(rows));
c.check('inbox: stranded worktree of a dead agent not shown twice', h.includes('2 things need you')
  && (h.match(/data-wts="/g) || []).length === 1 && h.includes('data-wts="other"'));
c.check('inbox: still works without the 4th arg', _inboxHtml([], now, []).includes('All clear'));

// Today
const today = (wt, st, bg) => _todayHtml({ sessions_list: [] }, {}, null, now, '2027-01-15', {}, 'auto', [], wt, st, bg);
const wtItem = { id: 'p1', repo_name: 'AOC', branch: 'feat', path: 'C:\\r\\AOC\\.claude\\worktrees\\p1', dirty: 0, ahead: 0, files: 0 };
h = today([wtItem], [], all);
c.check('today: Background agents section with count + dead count', h.includes('Background agents <span class="td-count">5</span>')
  && h.includes('2 dead</span>'));
c.check('today: before Parallel worktrees', h.indexOf('Background agents') > 0 && h.indexOf('Background agents') < h.indexOf('Parallel worktrees'));
const order = [...h.matchAll(/data-bga="([^"]+)"/g)].map(m => m[1]);
c.check('today: dead/failed first, then quiet, working, finished', order.join() === 'd,f,q,ag_1,ok');
c.check('today: no dead badge when none dead', !today([], [], [A()]).includes(' dead</span>'));
c.check('today: no section without agents', !today([], [], []).includes('Background agents') && !today([], []).includes('Background agents'));
h = today([], [W(), W({ id: 'other' })], rows);
c.check('today: stranded worktree of a dead agent not in Stranded work again', h.includes('Stranded work <span class="td-count">1</span>')
  && h.includes('data-wts="other"') && !h.includes('data-wts="wt9"'));
c.check('today: stranded list untouched without agents', today([], [W()], []).includes('Stranded work <span class="td-count">1</span>'));

c.finish();
