/* Tests _agentToMarkdown, extracted straight from monitor.py. This
 * function was factored out of exportAgentDetail() (which used to inline
 * all of this) so exportSessionDetail() could reuse it without
 * duplicating the per-agent Markdown formatting a third time -- these
 * tests exist specifically to prove that refactor didn't change
 * exportAgentDetail's own output. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

let _costRate = 9; // _agentCost's free-variable dependency
const _lastProgressAt = {}; // _stuckSecs' free-variable dependency, kept empty so it's always a no-op here

const src = readMonitorSource();
eval(extractFunction(src, 'stxt'));
eval(extractFunction(src, '_agentCost'));
eval(extractFunction(src, 'parseTimeStr'));
eval(extractFunction(src, '_ctxPct'));
eval(extractFunction(src, '_stuckSecs'));
eval(extractFunction(src, '_agentToMarkdown'));

const c = new Checker();

const doneAgent = {
  name: 'Fix the dismissed bug',
  status: 'done',
  session_project: 'AOC',
  started_at: '2026-07-19T10:00:00',
  completed_at: '2026-07-19T10:15:00',
  tokens_used: 50000,
  model: 'claude-sonnet-4-5-20250514',
  description: 'Root-cause the dismissed flag bug',
  tasks: [
    { done: true, label: 'Investigate', completed_at: '10:05:00' },
    { done: true, label: 'Fix', completed_at: '10:14:00' },
    { done: false, label: 'Write test' },
  ],
  files_changed: [{ type: 'mod', path: 'monitor.py', lines: 42 }],
  log: ['started', 'investigating', 'fixed'],
};

// 1. default heading is the original "# AGENT: " H1 (exportAgentDetail's own shape)
const defaultMd = _agentToMarkdown(doneAgent);
c.check('default heading prefix is "# AGENT: "', defaultMd.startsWith('# AGENT: Fix the dismissed bug\n'));

// 2. custom heading prefix for embedding in a session doc
const nestedMd = _agentToMarkdown(doneAgent, '### ');
c.check('custom heading prefix is honored', nestedMd.startsWith('### Fix the dismissed bug\n'));

// 3. status/project/timestamps present
c.check('status line present', defaultMd.includes('**Status:** COMPLETE'));
c.check('project line present', defaultMd.includes('**Project:** AOC'));
c.check('started/completed lines present', defaultMd.includes('**Started:**') && defaultMd.includes('**Completed:**'));

// 4. tokens + cost line (tokens_used=50000, no estimated_cost -> falls back to tokens*rate/1e6)
c.check('tokens + fallback cost line present', defaultMd.includes('**Tokens:** 50,000') && defaultMd.includes('$0.4500'));

// 4b. model line present when the agent carries one, absent when it doesn't
c.check('model line present', defaultMd.includes('**Model:** claude-sonnet-4-5-20250514'));
c.check('bare agent (no model field) has no Model line', !_agentToMarkdown({ name: 'Bare agent', status: 'running' }).includes('**Model:**'));

// 4c. context-window-used line, mirrors the card's token_limit progress bar
c.check('no token_limit -> no Context used line', !defaultMd.includes('**Context used:**'));
const ctxAgent = _agentToMarkdown({ ...doneAgent, token_limit: 200000 });
c.check('token_limit present -> Context used line with pct and raw counts', ctxAgent.includes('**Context used:** 25% (50,000 / 200,000)'));
c.check('tokens_used but no token_limit -> no Context used line', !_agentToMarkdown({ name: 'No limit', status: 'running', tokens_used: 1000 }).includes('**Context used:**'));

// 4d. detected_via/concurrent_sessions -> "Detected via" line, mirrors the
// HOOK MISS badge already shown on the card/Compare/GRAPH tooltip/History
// Detail/agent detail panel for the same condition
c.check('detected_via not "transcript" -> no Detected via line', !defaultMd.includes('**Detected via:**'));
const hookMissAgent = _agentToMarkdown({ ...doneAgent, detected_via: 'transcript', concurrent_sessions: 3 });
c.check('detected_via "transcript" -> Detected via line with concurrent_sessions count', hookMissAgent.includes('**Detected via:** transcript fallback (hook miss) — 3 other sessions active at the time'));
const hookMissNoConcurrency = _agentToMarkdown({ ...doneAgent, detected_via: 'transcript' });
c.check('detected_via "transcript" but no concurrent_sessions -> line present without the count suffix', hookMissNoConcurrency.includes('**Detected via:** transcript fallback (hook miss)\n'));

// 4d-2. Stuck line -- mirrors the card's stuck-badge condition
// (_stuckSecs: status==='running' AND >300s since _lastProgressAt[id]).
c.check('done agent is never "stuck" regardless of _lastProgressAt', !defaultMd.includes('**Stuck:**'));
const stuckId = 'ag_stuck_test';
_lastProgressAt[stuckId] = Date.now() - 400000; // 400s ago, past the 300s threshold
c.check('running agent past the threshold -> Stuck line in minutes', _agentToMarkdown({ name: 'Stuck agent', status: 'running', id: stuckId }).includes('**Stuck:** no progress for 6m'));
_lastProgressAt[stuckId] = Date.now() - 100000; // 100s ago, under the threshold
c.check('running agent under the threshold -> no Stuck line', !_agentToMarkdown({ name: 'Not stuck yet', status: 'running', id: stuckId }).includes('**Stuck:**'));
delete _lastProgressAt[stuckId];

// 4e. parentName (3rd param) -> "Spawned by" line. Deliberately NOT
// resolved from a.parent_id inside the function itself -- the caller
// (exportAgentDetail/exportSessionDetail) resolves the id to a name from
// the full agents list, since this function stays a pure function of its
// own arguments, testable without lastStatus existing at all.
c.check('no parentName passed -> no Spawned by line', !defaultMd.includes('**Spawned by:**'));
const childMd = _agentToMarkdown(doneAgent, undefined, 'Orchestrator agent');
c.check('parentName passed -> Spawned by line with that name', childMd.includes('**Spawned by:** Orchestrator agent'));

// 4f. childNames (4th param) -> "Spawned subagents" line, the reverse
// direction of the same thread -- an orchestrator agent's own export
// should say which subagents it spawned, same as TREE's childCount badge
c.check('no childNames passed -> no Spawned subagents line', !defaultMd.includes('**Spawned subagents:**'));
c.check('empty childNames array -> no Spawned subagents line', !_agentToMarkdown(doneAgent, undefined, null, []).includes('**Spawned subagents:**'));
const orchestratorMd = _agentToMarkdown(doneAgent, undefined, null, ['Explore worker', 'Fix worker']);
c.check('childNames passed -> Spawned subagents line joins all names', orchestratorMd.includes('**Spawned subagents:** Explore worker, Fix worker'));

// 5. task checklist with correct done/undone markers
c.check('done task rendered with [x]', defaultMd.includes('- [x] Investigate'));
c.check('undone task rendered with [ ]', defaultMd.includes('- [ ] Write test'));
c.check('task section header shows correct fraction/percent', defaultMd.includes('## Tasks (2/3 — 67%)'));

// 6. files changed section
c.check('files changed section present', defaultMd.includes('## Files Changed (1)') && defaultMd.includes('MOD `monitor.py` (42L)'));

// 7. log section
c.check('log section present with all entries', defaultMd.includes('## Log (3 entries)') && defaultMd.includes('investigating'));

// 8. error agent renders an Error section; done agent does not
const errorAgent = { name: 'Broken agent', status: 'error', error_message: 'Something went wrong', tasks: [] };
const errorMd = _agentToMarkdown(errorAgent);
c.check('error agent gets an Error section', errorMd.includes('## Error') && errorMd.includes('Something went wrong'));
c.check('done agent has no Error section', !defaultMd.includes('## Error'));

// 9. an agent with no tasks/files/log omits those sections entirely
const bareAgent = { name: 'Bare agent', status: 'running' };
const bareMd = _agentToMarkdown(bareAgent);
c.check('bare agent has no Tasks section', !bareMd.includes('## Tasks'));
c.check('bare agent has no Files Changed section', !bareMd.includes('## Files Changed'));
c.check('bare agent has no Log section', !bareMd.includes('## Log'));

c.finish();
