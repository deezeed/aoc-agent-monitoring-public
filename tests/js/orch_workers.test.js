/* Orchestrate's own agents in the dashboard (web/js/19 + the Inbox in 03):
 * _orWorkerRef (run / task from the tag or the worktree slot), _orHoldsHtml
 * (a worker's held dangerous command as an Orchestrate item: escaped,
 * Run it / Deny through the permission decide flow, Open run only when the
 * run is known), _orWorkersHtml (the compact "N agents working" line and
 * the hooks-outdated hint) and the Inbox counting those holds. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(src.match(/const _INBOX_KINDS=\{[\s\S]*?\};/)[0].replace(/^const /, 'var '));
for (const f of ['escHtml', 'jsq', '_fmtDurShort', '_fmtDurationDHM', '_orMoney', '_orWorkerRef', '_orHoldsHtml', '_orWorkersHtml',
                 '_lastMessageInfo', '_waitLabel', '_permAskHtml', '_replyAskHtml', '_budgetBadgeHtml', '_loopBadgeHtml', '_ctxMeterHtml',
                 '_inboxItems', '_inboxHtml', '_bgStrandedLeft', '_bgAgentsHtml'])
  eval(extractFunction(src, f));
const c = new Checker();

// run / task of a worker
let r = _orWorkerRef({ run: '1112-172316', task: 'api' });
c.check('ref: from the tag', r.run === '1112-172316' && r.task === 'api' && r.entity === '');
r = _orWorkerRef({ slot: 'orch-1112-172316-fix-the-api-3' });
c.check('ref: from the worktree slot (old hooks)', r.run === '1112-172316' && r.task === 'fix-the-api');
r = _orWorkerRef({ run: 'entity-shop', task: 'scout' });
c.check('ref: an entity scout', r.entity === 'shop' && r.run === 'entity-shop');
c.check('ref: nothing known', JSON.stringify(_orWorkerRef(null)) === JSON.stringify({ run: '', task: '', entity: '' })
  && _orWorkerRef({ slot: 'weird' }).run === '');

// held commands
const hold = { id: "p'1", summary: 'Bash: rm -rf <dist>', detail: 'AOC holds this because: <x>', danger: 'recursive delete',
               age_s: 125, session_id: 'w1', run: '1112-172316', task: 'api', slot: '' };
let h = _orHoldsHtml([hold]);
c.check('hold: an Orchestrate block with the run and task', h.includes('Orchestrate agents · 1 held')
  && h.includes('run 1112-172316 · task api') && h.includes('Dangerous command'));
c.check('hold: escaped', h.includes('rm -rf &lt;dist&gt;') && h.includes('because: &lt;x&gt;') && !h.includes('<dist>'));
c.check('hold: Run it / Deny through the permission decide flow (id quoted safely)',
  h.includes(`_permDecide('',${jsq("p'1")},'allow')`) && h.includes(`_permDecide('',${jsq("p'1")},'deny')`) && h.includes('>Run it<'));
c.check('hold: no "answer in terminal" -- a worker has none', !h.includes("'terminal'"));
c.check('hold: Open run links to the run', h.includes(`_orOpenRun(${jsq('1112-172316')})`) && h.includes('held 2m'));
h = _orHoldsHtml([{ id: 'p2', summary: 'Bash: git push -f', danger: 'force push', slot: 'orch-0101-101010-docs-2' }]);
c.check('hold: only a slot -> run / task recovered, still linked', h.includes('run 0101-101010 · task docs') && h.includes('_orOpenRun'));
h = _orHoldsHtml([{ id: 'p3', summary: 'Bash: rm x', run: 'entity-shop', task: 'scout' }]);
c.check('hold: an entity scout -> named, no run to open', h.includes('entity shop · scout') && !h.includes('_orOpenRun') && h.includes('Needs your OK'));
h = _orHoldsHtml([{ id: 'p4', summary: 'Bash: rm y' }]);
c.check('hold: nothing known -> generic name, no link', h.includes('an Orchestrate agent') && !h.includes('_orOpenRun'));
c.check('hold: none -> empty', _orHoldsHtml([]) === '' && _orHoldsHtml(null) === '');

// the Orchestrate view's line
const W = o => Object.assign({ id: 'w', run: 'R1', task: 't', session_active: true, estimated_cost: 0.25 }, o);
h = _orWorkersHtml([W(), W({ id: 'w2', run: 'R2', estimated_cost: 0.5 }), W({ id: 'w3', session_active: false })], '');
c.check('workers: live ones counted, runs and cost', h.includes('2 agents working in 2 runs') && h.includes('$0.75'));
c.check('workers: says they are not your sessions', h.includes('not listed as your sessions'));
c.check('workers: none, no hint -> nothing', _orWorkersHtml([], '') === '' && _orWorkersHtml(null) === '');
h = _orWorkersHtml([], 'Hooks outdated -- reinstall <now>');
c.check('workers: the hooks hint, escaped, with a way to Health', h.includes('reinstall &lt;now&gt;') && h.includes("setView('diag')"));

// the Inbox counts and shows them
const now = 1_800_000_000;
h = _inboxHtml([], now, [], [], [], [hold]);
c.check('inbox: a held worker command is not "All clear"', h.includes('1 thing needs you') && h.includes('Orchestrate agents · 1 held'));
c.check('inbox: still works without the holds', _inboxHtml([], now, [], [], []).includes('All clear'));
const sr = { sessions_list: [{ id: 'me', session_active: true, waiting_on_you: true, waiting_secs: 60, last_message: 'Done.' }],
             orch_holds: [hold] };
h = _inboxHtml(_inboxItems(sr, now), now, [], [], [], sr.orch_holds);
c.check('inbox: your session and the hold side by side, hold first',
  h.includes('2 things need you') && h.indexOf('Orchestrate agents') < h.indexOf('Your turn'));
c.finish();
