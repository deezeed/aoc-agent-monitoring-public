/* Orchestrate view (web/js/19_orchestration.js): the pure html builders and
 * confirm texts, extracted from the shipped dashboard; nothing is fetched. */
const fs = require('fs');
const path = require('path');
const { readMonitorSource, extractFunction, WEB_DIR } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
const part = fs.readFileSync(path.join(WEB_DIR, 'js', '19_orchestration.js'), 'utf8');
for (const v of ['_OR_STATES', '_OR_TASK_STATES', '_OR_ROLES', '_OR_ENTITY_STATES', '_OR_ATTN']) {
  const m = new RegExp(`^var ${v}=([\\s\\S]*?\\});`, 'm').exec(part);
  if (!m) throw new Error(v + ' not found');
  eval(`global.${v}=${m[1]}`);
}
for (const v of ['_OR_PLAN_FIELDS', '_OR_TASK_BUDGET']) {
  const m = new RegExp(`^var ${v}=.*;$`, 'm').exec(part);
  if (!m) throw new Error(v + ' not found');
  (0, eval)(m[0]);   // indirect eval: the var(s) on that line become globals
}
for (const f of ['escHtml', 'jsq', '_fmtDurationDHM', '_fmtDurShort', '_orChip', '_orMoney', '_orFormHtml', '_orRunsHtml',
  '_orMetricsHtml', '_orCompareHtml', '_orTaskHtml', '_orGoalHtml', '_orPickSet', '_orStatsHtml', '_orInboxHtml', '_orBillingHtml', '_orRulesHtml', '_orScheduleText', '_orWhen', '_orBudgetBar', '_orEntityCardHtml', '_orEntityDraftOf', '_orEntityEditorHtml', '_orEntitiesHtml', '_orFormDefaults', '_orTeamOf', '_orClipText', '_orAgentCardHtml', '_orAgentEditorHtml', '_orAgentsHtml', '_orAgentDraftOf', '_orDraftFrom', '_orSlug', '_orDraftAdd', '_orDraftRemove', '_orDraftMove', '_orDraftRename', '_orPlanCheck', '_orPlanEditorHtml', '_orDetailHtml', '_orHtml', '_orConfirmText',
  '_orLocalInput', '_orWhenBody', '_orWhenHtml', '_orRepeatCardHtml', '_orLaterCardHtml', '_orScheduledHtml', '_orCommitsHtml']) eval(extractFunction(src, f));
const c = new Checker();
global._orState = { pick: null };

const metrics = { cost_total: 1.2, cost_coordination: 0.3, cost_work: 0.9, coordination_share: 0.25, duration_s: 600,
  tasks: 3, done: 2, failed: 1, success_rate: 0.667, retries: 1, human_interventions: 2, avg_task_s: 95, review_rounds: 1 };
const task = (id, extra) => Object.assign({ id, role: 'developer', title: 'Build <x>', instructions: 'do it', done_criteria: 'works',
  depends_on: [], model: 'sonnet', budget_usd: 1, state: 'done', attempt: 1, feedback: '', branch: 'aoc/x', started: 1, finished: 2,
  cost_usd: 0.4, turns: 3, session_id: 'abcdef123456', output: { summary: 'built it', result: 'changed a.js', errors: [] },
  error: '', commits: 2 }, extra || {});
const view = state => ({ ok: true, run: { id: '1107-1200', created: 0, updated: 0, goal: 'Add <export>', repo: 'C:\\r\\shop', base: 'main',
  state, model: 'opus', max_parallel: 3, budget_usd: 5, plan_summary: 'two devs then qa', single_agent: 0, report: state === 'review' ? 'All good' : '',
  error: '', rounds: 1, finished: null },
  tasks: [task('api'), task('ui', { state: 'failed', commits: 0, output: null, error: 'timed out' }),
    task('qa', { role: 'qa', depends_on: ['api', 'ui'], state: 'blocked', commits: 0, output: null, error: 'input task(s) did not finish: ui' })],
  waves: [['api', 'ui'], ['qa']], events: [{ ts: 1, kind: 'approved', task_id: '', message: 'plan approved', cost_usd: 0, human: 1 }],
  metrics });

c.check('chip: label + colour class per state', _orChip('plan_ready', 'run').includes('or-c-wait') && _orChip('plan_ready', 'run').includes('needs your OK')
  && _orChip('failed', 'task').includes('or-c-err'));
c.check('form: goal, repo folder with known dirs, parallel + budget, approval note',
  ['or-goal', 'or-cwd', 'or-par', 'or-budget', 'C:\\proj\\a'].every(s => _orFormHtml([{ path: 'C:\\proj\\a' }], true).includes(s))
  && _orFormHtml([], true).includes('Nothing runs before you approve the plan'));
const runs = _orRunsHtml([{ id: 'r1', goal: 'G <1>', repo: 'C:\\x\\shop', state: 'running', tasks: 3, metrics }], 'r1');
c.check('runs list: escaped goal, repo name, progress, cost, selected', runs.includes('G &lt;1&gt;') && runs.includes('shop · 2/3 tasks · $1.20')
  && runs.includes('or-run sel'));
const mh = _orMetricsHtml(metrics);
c.check('metrics: cost split, success, retries, your actions', ['$1.20', '$0.30 · 25%', '$0.90', '67%', 'Your actions'].every(s => mh.includes(s)));
c.check('metrics: warns when coordination costs half or more', _orMetricsHtml(Object.assign({}, metrics, { coordination_share: 0.6 })).includes('single agent may be cheaper')
  && !mh.includes('single agent may be cheaper'));
const th = _orTaskHtml(task('api'));
c.check('task card: escaped title, role, model, spend, commits, output', th.includes('Build &lt;x&gt;') && th.includes('Developer · sonnet')
  && th.includes('spent $0.40') && th.includes('2 commits') && th.includes('changed a.js') && th.includes('abcdef12'));
c.check('task card: failure reason shown', _orTaskHtml(task('ui', { output: null, error: 'timed out', state: 'failed' })).includes('timed out'));

const pr = _orDetailHtml(view('plan_ready'), false);
c.check('plan ready: approve / edit / cancel, waves in order', pr.includes('Approve plan')
  && pr.includes('Edit plan') && pr.indexOf('Start · 2 in parallel') < pr.indexOf('Then') && pr.includes('up to $3.00 for the agents'));
c.check('plan ready: actions pass the id through jsq', pr.includes('_orAct(\'approve\',&quot;1107-1200&quot;)'));
const ed = _orDetailHtml(view('plan_ready'), true);
c.check('editing: one card per task with its fields, budget + parallel, no waves', (ed.match(/or-ed-task/g) || []).length === 3
  && ed.includes('value="api"') && ed.includes('aria-label="Done criterion"') && ed.includes('or-ed-budget') && !ed.includes('or-waves')
  && !ed.includes('or-plan-json'));
c.check('running: only cancel', _orDetailHtml(view('running'), false).includes('Cancel run') && !_orDetailHtml(view('running'), false).includes('Merge into'));
const rv = _orDetailHtml(view('review'), false);
c.check('review: report + merge into base + discard', rv.includes('All good') && rv.includes('Merge into main') && rv.includes('Discard'));
c.check('merged: no actions', !_orDetailHtml(view('merged'), false).includes('or-actions'));
c.check('log: your actions marked', rv.includes('or-ev human'));
c.check('whole view: form open when there are no runs', _orHtml({ runs: [], sel: '', view: null, dirs: [], editing: false, err: '', formOpen: null }).includes('or-new" open'));
c.check('confirm merge names the branches', _orConfirmText('merge', view('review')).includes('api: Build <x> (2 commit(s))')
  && !_orConfirmText('merge', view('review')).includes('ui:'));
// hierarchical
const hv = view('running');
hv.run.mode = 'hierarchical';
hv.tasks[0] = task('backend', { role: 'manager', state: 'running', commits: 0, output: null, child_id: 'R-backend-t1',
  team: { id: 'R-backend-t1', state: 'plan_ready', tasks: 3, done: 1, running: 1, error: '', plan_summary: 'p' } });
hv.metrics = Object.assign({}, metrics, { teams: 2 });
hv.waves = [['backend', 'ui'], ['qa']];
const hd = _orDetailHtml(hv, false);
c.check('manager card: team progress, its plan waiting for you, Open team', hd.includes('team: 1/3 done · 1 running')
  && hd.includes('its plan needs your OK') && hd.includes('_orSelect(&quot;R-backend-t1&quot;)') && hd.includes('or-mgr'));
c.check('hierarchical run: badge and Teams metric', hd.includes('>hierarchical<') && hd.includes('<span>Teams</span>'));
const cv = view('review');
cv.run.parent_id = 'R'; cv.run.parent_task = 'backend'; cv.parent = { id: 'R', task: 'backend', goal: 'Ship <it>' };
const cd = _orDetailHtml(cv, false);
c.check('team run: link back to the run above, no merge/discard of its own, led by the manager',
  cd.includes('Team of workstream <code>backend</code>') && cd.includes('← run R') && !cd.includes('Merge into')
  && !cd.includes('Discard') && cd.includes('· manager opus') && cd.includes('goes onto the workstream branch'));
c.check('team goal: the workstream first, the context from above folded',
  _orGoalHtml({ goal: 'Write docs\n\nDONE WHEN: ok\n\nThis is one workstream', parent_id: 'R' }).includes('<div class="or-goal">Write docs</div>')
  && _orGoalHtml({ goal: 'Write docs\n\nDONE WHEN: ok', parent_id: 'R' }).includes('<pre>DONE WHEN: ok</pre>')
  && _orGoalHtml({ goal: 'A\n\nDONE WHEN: x', parent_id: '' }).includes('DONE WHEN: x</div>'));
const tp = view('plan_ready'); tp.run.parent_id = 'R'; tp.parent = { id: 'R', task: 'b', goal: 'g' };
c.check('team plan waiting: you can approve it there', _orDetailHtml(tp, false).includes('Approve plan'));
c.check('plan editor offers the manager role only at a hierarchical top level',
  _orPlanEditorHtml(Object.assign({}, hv, { run: Object.assign({}, hv.run, { parent_id: '' }) })).includes('value="manager"')
  && !_orPlanEditorHtml(view('plan_ready')).includes('value="manager"')
  && !_orPlanEditorHtml(tp).includes('value="manager"'));

// plan editor: the draft helpers
const base = _orDraftFrom(view('plan_ready'));
c.check('draft: the plan fields only, a copy (editing it leaves the view alone)',
  Object.keys(base.tasks[0]).sort().join() === [..._OR_PLAN_FIELDS].sort().join() && base.budget === 5 && base.par === 3
  && (base.tasks[2].depends_on.push('x'), view('plan_ready').tasks[2].depends_on.length === 2));
const d0 = _orDraftFrom(view('plan_ready')).tasks;
c.check('slug like the server', _orSlug(' Build UI! ') === 'build-ui' && _orSlug('a'.repeat(40)).length === 32);
const added = _orDraftAdd(d0);
c.check('add: a free id, developer, sonnet, $1', added.length === 4 && added[3].id === 'task-4' && added[3].role === 'developer'
  && _orDraftAdd([{ id: 'task-2' }]).pop().id === 'task-3');
const removed = _orDraftRemove(d0, 0);
c.check('remove: gone from the dependencies of the others', removed.length === 2 && removed[1].depends_on.join() === 'ui');
c.check('move: up/down, not past the ends', _orDraftMove(d0, 1, -1).map(t => t.id).join() === 'ui,api,qa'
  && _orDraftMove(d0, 0, -1) === d0 && _orDraftMove(d0, 2, 1) === d0);
c.check('rename: dependents follow', _orDraftRename(d0, 0, 'backend')[2].depends_on.join() === 'backend,ui' && d0[2].depends_on[0] === 'api');
const ok = _orPlanCheck(d0, false, 5);
c.check('check: a good plan passes, sum of budgets', ok.ok && ok.total === 3 && !ok.over);
const bad = _orPlanCheck([Object.assign({}, d0[0], { title: ' ' }), Object.assign({}, d0[1], { id: 'api' }),
  Object.assign({}, d0[2], { depends_on: ['nope'] })], false, 5);
c.check('check: empty field, duplicate id, unknown dependency -- per task', !bad.ok && bad.per[0][0] === 'needs a title'
  && bad.per[1][0].includes("two tasks are called 'api'") && bad.per[2][0].includes("unknown task 'nope'"));
const cyc = _orPlanCheck([Object.assign({}, d0[0], { depends_on: ['ui'] }), Object.assign({}, d0[1], { depends_on: ['api'] })], false, 5);
c.check('check: a circle is a problem of the whole plan', !cyc.ok && cyc.plan.includes('the dependencies go in a circle'));
c.check('check: manager only where allowed', !_orPlanCheck([Object.assign({}, d0[0], { role: 'manager' })], false, 5).ok
  && _orPlanCheck([Object.assign({}, d0[0], { role: 'manager' })], true, 5).ok);
c.check('check: over 85% of the run budget flagged', _orPlanCheck(d0, false, 3).over);
c.check('check: no tasks', _orPlanCheck([], false, 5).plan.includes('the plan has no tasks'));
const be = _orPlanEditorHtml(view('plan_ready'), { runId: 'r', json: false, budget: 5, par: 3, tasks: bad.ok ? [] : [Object.assign({}, d0[0], { title: '' })] });
c.check('editor: a broken task is marked, Save disabled', be.includes('or-ed-task bad') && be.includes('needs a title') && /ln-go"[^>]*disabled/.test(be));
c.check('editor: dependencies as checkboxes of the other tasks, checked where set',
  /<input type="checkbox" checked[^>]*_orDraftDep\(2,&quot;api&quot;/.test(ed) && !/_orDraftDep\(0,&quot;api&quot;/.test(ed));
c.check('editor: move/remove/add, the JSON switch', ed.includes("_orDraftOp('move',1,-1)") && ed.includes("_orDraftOp('remove',2)")
  && ed.includes('+ Add task') && ed.includes('_orDraftJson(true)'));
const ej = _orPlanEditorHtml(view('plan_ready'), Object.assign(_orDraftFrom(view('plan_ready')), { json: true }));
c.check('editor in JSON mode: the textarea, back to the form', ej.includes('or-plan-json') && ej.includes('&quot;done_criteria&quot;') && ej.includes('_orDraftJson(false)'));
c.check('editor: budget sum against what the tasks may take', ed.includes('tasks $3.00 of $4.25 available to them'));
c.check('editing: no Approve until saved or cancelled', ed.includes('Save or cancel your changes') && !ed.includes('Approve plan'));
const rl = _orRunsHtml([{ id: 'R', goal: 'g', repo: 'x', state: 'running', mode: 'hierarchical', needs_you: true, tasks: 2, metrics }], 'R-backend-t1');
c.check('runs list: teams badge, needs-you dot, run above stays marked while its team is open',
  rl.includes('>teams<') && rl.includes('or-dot') && rl.includes('or-run sel'));
c.check('form: structure picker; the per-team approval only for hierarchical', _orFormHtml([], true).includes('value="hierarchical"')
  && !_orFormHtml([], true).includes('or-teams') && _orFormHtml([], true, Object.assign(_orFormDefaults(), { mode: 'hierarchical' })).includes('id="or-teams"'));

// agent library UI
const lib = [{ id: 'developer', name: 'Developer', brief: 'b', tools: ['Read', 'Edit'], model: 'sonnet', budget_usd: 1, mcp: [], skills: [], sensitive: false, builtin: true },
  { id: 'qa', name: 'QA', brief: 'b', tools: ['Read'], model: 'sonnet', budget_usd: 1, mcp: [], skills: [], sensitive: false, builtin: true },
  { id: 'shop', name: 'Shop <Analyst>', brief: 'Reads the shop', tools: ['Read'], model: 'haiku', budget_usd: 0.5, mcp: ['claude.ai Shopify'], skills: ['xlsx'], sensitive: true, builtin: false }];
const kept = _orFormHtml([], true, Object.assign(_orFormDefaults(), { goal: 'Ship <it>', cwd: 'C:\\r', par: 2, budget: 7 }), lib);
c.check('form keeps typed values (a refresh redraws it)', kept.includes('>Ship &lt;it&gt;</textarea>') && kept.includes('value="C:\\r"')
  && kept.includes('<option value="2" selected>') && kept.includes('value="7"'));
c.check('team picker: built-ins checked by default, MCP agents marked', /checked[^>]*_orTeamToggle\(&quot;developer&quot;/.test(kept)
  && !/checked[^>]*_orTeamToggle\(&quot;shop&quot;/.test(kept) && kept.includes('Shop &lt;Analyst&gt; <span class="or-chip or-c-muted">MCP</span>'));
c.check('team: the picked ids that still exist', _orTeamOf({ team: ['shop', 'gone'] }, lib).join() === 'shop' && _orTeamOf({ team: null }, lib).join() === 'developer,qa');
const empty = _orFormHtml([], true, Object.assign(_orFormDefaults(), { team: [] }), lib);
c.check('no agent picked -> warned, Plan it disabled', empty.includes('pick at least one agent') && /ln-go"\s+disabled/.test(empty));
const card = _orAgentCardHtml(lib[2]);
c.check('agent card: escaped name, tools / MCP / skills, asks first, subscription note, edit + delete',
  card.includes('Shop &lt;Analyst&gt;') && card.includes('claude.ai Shopify') && card.includes('xlsx') && card.includes('asks first')
  && card.includes('runs on your subscription') && card.includes('_orAgentEdit(&quot;shop&quot;)') && card.includes('_orAgentDelete'));
c.check('built-in card: duplicate only', _orAgentCardHtml(lib[0]).includes('Duplicate') && !_orAgentCardHtml(lib[0]).includes('_orAgentDelete')
  && !_orAgentCardHtml(lib[0]).includes('_orAgentEdit(&quot;developer&quot;)"'));
const cat = { tools: ['Read', 'Edit'], models: ['haiku', 'sonnet', 'opus'], scanning: false,
  mcp: [{ name: 'plugin:design:slack', connected: false }, { name: 'claude.ai Shopify', connected: true }],
  skills: [{ name: 'xlsx', description: 'Spreadsheets' }, { name: 'pdf', description: 'PDF files' }] };
const edh = _orAgentEditorHtml(_orAgentDraftOf(lib[2]), cat);
c.check('agent editor: fields, connected MCP first, not-connected marked, skills, sensitive, subscription note',
  edh.includes('value="Shop &lt;Analyst&gt;"') && edh.indexOf('claude.ai Shopify') < edh.indexOf('plugin:design:slack')
  && edh.includes('1 not connected</summary>') && /checked[^>]*_orAgentToggle\(&quot;skills&quot;,&quot;xlsx&quot;/.test(edh)
  && edh.includes('waits for my OK') && edh.includes('runs on your Claude subscription'));
c.check('agent editor: skill filter', !_orAgentEditorHtml(Object.assign(_orAgentDraftOf(null), { _skillQ: 'spread' }), cat).includes('&quot;pdf&quot;'));
c.check('drafts: new / edit keeps its id / duplicate gets a new one', _orAgentDraftOf(null).old_id === '' && _orAgentDraftOf(lib[2]).old_id === 'shop'
  && _orAgentDraftOf(lib[2], true).id === 'shop-copy' && _orAgentDraftOf(lib[2], true).old_id === '' && !('builtin' in _orAgentDraftOf(lib[0])));
c.check('library: count, yours, + New agent; the editor replaces the button', _orAgentsHtml(lib, null, cat, true).includes('Agents · 3 (1 yours)')
  && _orAgentsHtml(lib, null, cat, true).includes('+ New agent') && !_orAgentsHtml(lib, _orAgentDraftOf(null), cat, true).includes('+ New agent'));
c.check('task card shows the agent it runs as', _orTaskHtml(task('x', { role: 'shop', agent: lib[2] })).includes('Shop &lt;Analyst&gt; · sonnet'));
c.check('plan over the budget: warned', _orDetailHtml(Object.assign({}, view('plan_ready'), { run: Object.assign({}, view('plan_ready').run, { budget_usd: 2 }) }), false).includes('over 85%')
  && !_orDetailHtml(view('plan_ready'), false).includes('over 85%'));
c.check('confirm discard / cancel / approve', _orConfirmText('discard', view('review')).includes('lost')
  && _orConfirmText('cancel', view('running')).includes('Stop every agent') && _orConfirmText('approve', view('plan_ready')) === '');
c.check('whole view: form closed once there are runs, unless you opened it', !_orHtml({ runs: [{ id: 'r', goal: 'g', repo: 'x', state: 'merged', tasks: 1, metrics }], sel: 'r', view: null, dirs: [], editing: false, err: '', formOpen: null }).includes('or-new" open')
  && _orHtml({ runs: [{ id: 'r', goal: 'g', repo: 'x', state: 'merged', tasks: 1, metrics }], sel: 'r', view: null, dirs: [], editing: false, err: '', formOpen: true }).includes('or-new" open'));
// workspaces
const wsForm = _orFormHtml([], true, Object.assign(_orFormDefaults(), { where: 'workspace', workspace: 'eshop-ops' }), lib, [{ name: 'eshop-ops' }, { name: 'research' }]);
c.check('form: a workspace instead of a repo folder, known workspaces offered', wsForm.includes('id="or-ws"') && wsForm.includes('value="eshop-ops"')
  && wsForm.includes('<option value="research">') && !wsForm.includes('id="or-cwd"'));
c.check('form: repo by default', _orFormHtml([], true, _orFormDefaults(), lib, []).includes('id="or-cwd"'));
const wv = view('review'); wv.run.kind = 'workspace'; wv.run.repo = 'C:\\AOC\\workspaces\\eshop-ops';
const wd = _orDetailHtml(wv, false);
c.check('workspace run: Accept results instead of Merge', wd.includes('Accept results') && !wd.includes('Merge into'));
c.check('workspace accept confirm names the workspace and the tasks', _orConfirmText('merge', wv).includes('workspace eshop-ops') && _orConfirmText('merge', wv).includes('api: Build <x>'));
// entities
const ent = { id: 'store-watch', state: 'waiting', paused: false, note: "waiting: today's budget: $9.80 of $10.00 spent", run_now: false,
  next_due: 1794100000, last_scout: 1794000000, spend_day: 9.8, spend_month: 30,
  config: { id: 'store-watch', name: 'Store <Watch>', mission: 'Keep the store healthy', where: 'workspace', workspace: 'store-watch', cwd: '',
    team: ['developer', 'shop'], model: 'opus', mode: 'flat', max_parallel: 2, budget_run: 2, budget_day: 10, budget_month: 100,
    schedule: { type: 'daily', at: '08:00' }, loop: true, cooldown_min: 30 },
  memory: [{ run_id: 'r1', ts: 1794000000, goal: 'Weekly report', outcome: 'dropped by you', cost: 0.4, summary: 'Wrote it.' }],
  runs: [{ id: 'r1', goal: 'Weekly <report>', state: 'discarded', created: 1 }],
  events: [{ ts: 1794000000, kind: 'run_started', message: 'Weekly report', cost_usd: 0.05, run_id: 'r1' }] };
c.check('schedule in words', _orScheduleText(ent.config) === 'daily at 08:00 · again 30 min after each run'
  && _orScheduleText({ schedule: { type: 'every', hours: 6 } }) === 'every 6 h' && _orScheduleText({ schedule: { type: 'manual' } }) === 'when you say');
c.check('budget bar: spent of limit, high when 90 %+', _orBudgetBar('today', 9.8, 10).includes('$9.80 / $10.00') && _orBudgetBar('today', 9.8, 10).includes('class="hi"')
  && !_orBudgetBar('m', 1, 10).includes('class="hi"'));
const ec2 = _orEntityCardHtml(ent, lib);
c.check('entity card: escaped name, state, where, team names, budgets, why it waits, memory, runs, controls',
  ec2.includes('Store &lt;Watch&gt;') && ec2.includes('>Waiting<') && ec2.includes('workspace store-watch') && ec2.includes('Shop &lt;Analyst&gt;')
  && ec2.includes('$9.80 / $10.00') && ec2.includes("today&#39;s budget") && ec2.includes('dropped by you') && ec2.includes('Weekly &lt;report&gt;')
  && ec2.includes('_orEntityAct(\'run\',&quot;store-watch&quot;)') && ec2.includes('>Pause<') && ec2.includes('Next look'));
c.check('entity card: runs open their run', ec2.includes('_orSelect(&quot;r1&quot;)'));
const busy = _orEntityCardHtml(Object.assign({}, ent, { state: 'waiting_you', paused: true }), lib);
c.check('entity card: needs you -> says so, Run now off; paused -> Resume', busy.includes('waits for your merge') && /Run now/.test(busy)
  && /ln-go"[^>]*disabled/.test(busy) && busy.includes('>Resume<'));
const nd = _orEntityDraftOf(null);
c.check('new entity draft: workspace, manual, sane budgets', nd.where === 'workspace' && nd.schedule.type === 'manual' && nd.budget_run <= nd.budget_day);
c.check('edit draft keeps its id', _orEntityDraftOf(ent).old_id === 'store-watch' && _orEntityDraftOf(ent).mission === 'Keep the store healthy');
const eed = _orEntityEditorHtml(_orEntityDraftOf(ent), lib, [{ name: 'store-watch' }]);
c.check('entity editor: mission, workspace offered, team checked, budgets, daily time, loop minutes, starts-paused note',
  eed.includes('Keep the store healthy</textarea>') && eed.includes('<option value="store-watch">') && /checked[^>]*_orEntityTeam\(&quot;shop&quot;/.test(eed)
  && eed.includes('$ per month') && eed.includes('type="time" value="08:00"') && eed.includes('min later') && eed.includes('start paused'));
c.check('entity editor: a repo entity asks for the folder', _orEntityEditorHtml(Object.assign(_orEntityDraftOf(null), { where: 'repo' }), lib, []).includes('or-en-cwd'));
c.check('entity editor: the repo example shows single backslashes; the workspace field gets the wide input', _orEntityEditorHtml(Object.assign(_orEntityDraftOf(null), { where: 'repo' }), lib, []).includes('placeholder="C:\\path\\to\\repo"')
  && /class="settings-input or-en-cwd" list="or-en-wss"/.test(_orEntityEditorHtml(_orEntityDraftOf(null), lib, [])));
c.check('entities section: count, + New entity (or the editor)', _orEntitiesHtml([ent], null, lib, []).includes('Entities · 1')
  && _orEntitiesHtml([ent], null, lib, []).includes('+ New entity') && !_orEntitiesHtml([], nd, lib, []).includes('+ New entity'));
c.check('billing picker: current choice, no-key note only when it matters',
  _orBillingHtml({ billing: 'subscription', api_key_set: true }).includes('<option value="subscription" selected>')
  && _orBillingHtml({ billing: 'api', api_key_set: false }).includes('no ANTHROPIC_API_KEY') && _orBillingHtml(null) === '');
// phase D: gates, Inbox, stats
const gt = _orTaskHtml(task('post', { state: 'gate', agent: { name: 'Shop Poster' }, output: null, commits: 0 }), 'R1');
c.check('gate: the task asks, Allow / Skip for that run + task', gt.includes('Shop Poster is a sensitive agent')
  && gt.includes('_orGate(&quot;R1&quot;,&quot;post&quot;,true)') && gt.includes('_orGate(&quot;R1&quot;,&quot;post&quot;,false)')
  && gt.includes('waits for your OK'));
c.check('no gate buttons on other tasks', !_orTaskHtml(task('a'), 'R1').includes('_orGate'));
const ib = _orInboxHtml([
  { key: 'plan:R1', kind: 'plan', run_id: 'R1', task_id: '', title: 'Add <export>', detail: '', entity: '' },
  { key: 'gate:R2:post', kind: 'gate', run_id: 'R2', task_id: 'post', title: 'Post to shop', detail: 'Shop Poster', entity: 'store' },
  { key: 'review:R3', kind: 'review', run_id: 'R3', task_id: '', title: 'Report', detail: '', entity: '' }]);
c.check('Inbox: a card per item, escaped, the right buttons', ib.includes('Orchestration · 3') && ib.includes('Add &lt;export&gt;')
  && ib.includes('_orInboxApprove(&quot;R1&quot;)') && ib.includes('_orGate(&quot;R2&quot;,&quot;post&quot;,true)')
  && ib.includes('entity store') && ib.includes('_orOpenRun(&quot;R3&quot;)') && !ib.includes('_orInboxApprove(&quot;R3&quot;)'));
c.check('Inbox: nothing waiting -> no block', _orInboxHtml([]) === '' && _orInboxHtml(undefined) === '');
const sth = _orStatsHtml({ days: 90, runs: 3, merged: 2, discarded: 1, kept_rate: 0.667, cost: 4.5, avg_cost: 1.5, coordination_share: 0.244, avg_s: 150,
  by_kind: [{ name: 'entity', runs: 1, kept: 1, dropped: 0, avg_cost: 0.5 }],
  by_agent: [{ name: 'Dev <x>', n: 2, success: 1, avg_cost: 0.4, avg_s: 60, retries: 1 }], by_model: [{ name: 'sonnet', n: 3, success: 0.667, avg_cost: 0.9, avg_s: null, retries: 1 }] }, true);
c.check('stats card: totals, kept, per kind / agent / model', sth.includes('Stats · last 90 days') && sth.includes('67%') && sth.includes('$4.50')
  && sth.includes('<td>entity</td>') && sth.includes('Dev &lt;x&gt;') && sth.includes('<td>sonnet</td>'));
c.check('stats card: empty / loading', _orStatsHtml({ runs: 0 }, true).includes('No runs yet') && _orStatsHtml(null, true).includes('Loading'));
c.check('task card: Watch while running (live session), Replay when done, nothing for a manager',
  _orTaskHtml(task('a', { state: 'running', session_id: '', live_session: 'live-1', output: null })).includes('openReplay(&quot;live-1&quot;')
  && _orTaskHtml(task('a', { state: 'running', session_id: '', live_session: 'live-1', output: null })).includes('Watch')
  && _orTaskHtml(task('a')).includes('>Replay<') && !_orTaskHtml(task('m', { role: 'manager' })).includes('openReplay')
  && !_orTaskHtml(task('a', { state: 'pending', session_id: '', output: null })).includes('openReplay'));
const pe = Object.assign(view('plan_ready'), { estimate: { cost: 1.5, seconds: 270, from_history: 2, guessed: 1 } });
c.check('plan ready: the likely cost and time, and what it is based on', _orDetailHtml(pe, false).includes('Likely: ~$1.50')
  && _orDetailHtml(pe, false).includes('1 task(s) without history') && !_orDetailHtml(view('running'), false).includes('Likely:'));
// partial merge: keep only some tasks
const rv2 = view('review');
rv2.tasks = [task('api'), task('ui', { depends_on: ['api'] }), task('docs', { commits: 0, branch: '' })];
c.check('review: a keep box on each task with work, all kept by default', (_orDetailHtml(rv2, false).match(/or-keep/g) || []).length === 2
  && _orDetailHtml(rv2, false).includes('checked onchange="_orPick(&quot;api&quot;'));
_orState.pick = { run: rv2.run.id, ids: ['api'] };
c.check('your pick is shown', _orDetailHtml(rv2, false).includes('<input type="checkbox" onchange="_orPick(&quot;ui&quot;'));
_orState.pick = null;
c.check('no keep boxes outside review', !_orDetailHtml(view('running'), false).includes('or-keep'));
// the same goal with one agent
c.check('team run in progress / review: Compare with one agent; not once it has one, not for the solo run or before approval',
  _orDetailHtml(view('running'), false).includes('_orBaseline(') && _orDetailHtml(view('review'), false).includes('Compare with one agent')
  && !_orDetailHtml(Object.assign(view('review'), { baseline: '1107-1200-solo' }), false).includes('_orBaseline(')
  && !_orDetailHtml(view('plan_ready'), false).includes('_orBaseline('));
const sv = view('review'); sv.run.id = '1107-1200-solo'; sv.run.baseline_of = '1107-1200';
c.check('the solo run links back to the team', _orDetailHtml(sv, false).includes('One-agent version of')
  && _orDetailHtml(sv, false).includes('_orSelect(&quot;1107-1200&quot;)') && !_orDetailHtml(sv, false).includes('_orBaseline('));
const side = (id, state, cost, extra) => Object.assign({ id, state, cost, coordination: 0, seconds: 300, tasks: 1, success: 1,
  diff: { files: 1, insertions: 3, deletions: 0 }, report: 'did <it>' }, extra || {});
const cmp = { ok: true, baseline: '1107-1200-solo', team: side('1107-1200', 'review', 1.2, { coordination: 0.4, tasks: 3, diff: { files: 2, insertions: 10, deletions: 1 } }),
  solo: side('1107-1200-solo', 'review', 0.4), verdict: null };
const ch = _orCompareHtml(cmp, false);
c.check('compare: both columns, cost ratio, diffs, reports escaped, ask Claude when both are done', ch.includes('Team vs one agent')
  && ch.includes('$1.20') && ch.includes('$0.40') && ch.includes('3.0× as much as') && ch.includes('2 files · +10 −1')
  && ch.includes('did &lt;it&gt;') && ch.includes('_orJudge(&quot;1107-1200&quot;)'));
c.check('compare: no judge while one still runs; judging disables the button',
  !_orCompareHtml(Object.assign({}, cmp, { solo: side('1107-1200-solo', 'running', 0.1, { diff: null }) }), false).includes('_orJudge(')
  && _orCompareHtml(cmp, true).includes('Comparing…'));
const cvd = _orCompareHtml(Object.assign({}, cmp, { verdict: { better: 'solo', reasons: 'Simpler <b>', risks: 'line endings', cost: 0.05 } }), false);
c.check('compare: the verdict, escaped, with what to check', cvd.includes('single agent') && cvd.includes('result is better') && cvd.includes('Simpler &lt;b&gt;')
  && cvd.includes('Check before merging: line endings') && cvd.includes('Ask again'));
c.check('the panel shows on both runs of the pair, not elsewhere', _orDetailHtml(view('review'), false, cmp).includes('Team vs one agent')
  && _orDetailHtml(sv, false, cmp).includes('Team vs one agent') && !_orDetailHtml(view('review'), false, Object.assign({}, cmp, { team: side('other', 'review', 1) })).includes('Team vs one agent'));
c.check('merge confirm: says the other version is dropped', _orConfirmText('merge', Object.assign(view('review'), { baseline: '1107-1200-solo' })).includes('run 1107-1200-solo) is stopped')
  && _orConfirmText('merge', sv).includes('run 1107-1200) is stopped') && !_orConfirmText('merge', view('review')).includes('other version'));
c.check('runs list: the solo run is marked', _orRunsHtml([{ id: 'x-solo', state: 'review', goal: 'g', repo: 'r', tasks: 1, baseline_of: 'x', metrics }], '').includes('1 agent'));
// house rules
const rh0 = _orRulesHtml({ billing: 'api', rules: '' }, null);
c.check('rules card: empty -> example in the placeholder, Save off', rh0.includes('House rules') && rh0.includes('none yet') && rh0.includes('placeholder="e.g.')
  && rh0.includes('disabled') && rh0.includes('0 / 4000'));
const rh1 = _orRulesHtml({ billing: 'api', rules: '- a <b>\n\n- c' }, null);
c.check('rules card: saved rules counted (lines), escaped', rh1.includes('House rules · 2 lines') && rh1.includes('- a &lt;b&gt;') && rh1.includes('disabled'));
c.check('rules card: an unsaved change -> Save on', !_orRulesHtml({ rules: 'a' }, 'ab').includes(' disabled') && _orRulesHtml({ rules: 'a' }, 'ab').includes('2 / 4000'));
c.check('rules card: no settings yet -> nothing', _orRulesHtml(null, null) === '');
c.check('cancel / discard confirm: the other version stays', _orConfirmText('cancel', Object.assign(view('running'), { baseline: '1107-1200-solo' })).includes('run 1107-1200-solo) stays')
  && _orConfirmText('discard', sv).includes('run 1107-1200) stays') && !_orConfirmText('discard', view('review')).includes('stays'));

// ── when: now / later / repeat ──
const nowMs = new Date(2026, 10, 11, 9, 7).getTime();
c.check('later: the start time for the input is local and rounded up to a quarter hour',
  _orLocalInput(nowMs) === '2026-11-11T09:15' && _orLocalInput(new Date(2026, 11, 31, 23, 50).getTime()) === '2027-01-01T00:00');
c.check('when now: nothing added to the run', JSON.stringify(_orWhenBody(Object.assign(_orFormDefaults(), {}), nowMs)) === '{}');
const lb = _orWhenBody(Object.assign(_orFormDefaults(), { when: 'later', at: '2026-11-11T10:30' }), nowMs);
c.check('later: start_at in epoch seconds of that local time', lb.start_at === new Date(2026, 10, 11, 10, 30).getTime() / 1000 && !lb.error);
c.check('later: a past or missing time refused', _orWhenBody(Object.assign(_orFormDefaults(), { when: 'later', at: '2026-11-11T09:00' }), nowMs).error === 'pick a start time in the future'
  && _orWhenBody(Object.assign(_orFormDefaults(), { when: 'later', at: '' }), nowMs).error === 'pick a start time');
const rb = _orWhenBody(Object.assign(_orFormDefaults(), { when: 'repeat', rtype: 'weekdays', rat: '07:45', bday: 12, bmonth: 150, auto: true }), nowMs);
c.check('repeat: the clock, the caps and whether it approves its own plans',
  JSON.stringify(rb) === JSON.stringify({ schedule: { type: 'weekdays', at: '07:45' }, budget_day: 12, budget_month: 150, auto_approve: true })
  && JSON.stringify(_orWhenBody(Object.assign(_orFormDefaults(), { when: 'repeat', rtype: 'every', rhours: 6 }), nowMs).schedule) === '{"type":"every","hours":6}');
const fNow = _orFormHtml([], true, _orFormDefaults(), [], []);
const fLater = _orFormHtml([], true, Object.assign(_orFormDefaults(), { when: 'later', at: '2026-11-11T10:30' }), [], []);
const fRep = _orFormHtml([], true, Object.assign(_orFormDefaults(), { when: 'repeat', rtype: 'every', auto: true }), [], []);
c.check('form: When row; Plan it now, a start time later, a clock + caps to repeat',
  fNow.includes('or-when') && fNow.includes('>Plan it<') && !fNow.includes('datetime-local')
  && fLater.includes('type="datetime-local"') && fLater.includes('value="2026-11-11T10:30"') && fLater.includes('>Schedule it<')
  && fRep.includes('or-bday') && fRep.includes('or-bmonth') && fRep.includes('hours') && fRep.includes('>Save the schedule<')
  && fRep.includes('Each run approves its own plan') && fNow.includes('Nothing runs before you approve the plan'));
c.check('schedule in words: weekdays', _orScheduleText({ schedule: { type: 'weekdays', at: '07:45' } }) === 'on weekdays at 07:45');
const sch = { id: 's1111-090000', paused: false, next_due: 1e9, last_result: 'skipped: its last run (r1) is still running',
  spend_day: 2, spend_month: 9, created: 1, config: { goal: 'Nightly <deps> check', cwd: 'C:\\r\\shop', workspace: '', budget_usd: 3,
    budget_day: 10, budget_month: 100, schedule: { type: 'daily', at: '02:00' }, auto_approve: true },
  runs: [{ id: 'r1', goal: 'g', state: 'running', created: 1 }], events: [{ ts: 1, kind: 'skipped', message: 'its <last> run', user: '' }] };
const sc = _orRepeatCardHtml(sch);
c.check('repeat card: clock, escaped goal, folder, caps, next run, last result as a warning, last run, pause / delete, log',
  sc.includes('daily at 02:00') && sc.includes('Nightly &lt;deps&gt; check') && sc.includes('shop') && sc.includes('today $2.00 / $10.00')
  && sc.includes('Next run:') && sc.includes('or-warn">Last: skipped') && sc.includes('last run r1') && sc.includes('approves its plans')
  && sc.includes('_orScheduleAct(\'pause\',&quot;s1111-090000&quot;,true)') && sc.includes('>Delete<') && sc.includes('its &lt;last&gt; run'));
const scp = _orRepeatCardHtml(Object.assign({}, sch, { paused: true, last_result: 'started run r2', runs: [] }));
c.check('repeat card paused: Resume, no next run, a plain last result', scp.includes('>Resume<') && scp.includes('_orScheduleAct(\'pause\',&quot;s1111-090000&quot;,false)')
  && !scp.includes('Next run:') && scp.includes('or-note">Last: started run r2'));
const later = { id: '1111-0900', goal: 'Upgrade <x>', repo: 'C:\\r\\shop', state: 'scheduled', start_at: 2e9, tasks: 0, metrics };
const sh = _orScheduledHtml([later, { id: 'r', goal: 'g', repo: 'x', state: 'merged', tasks: 1, metrics }], [sch]);
c.check('Scheduled: runs for later (start now / cancel) and repeating runs; nothing scheduled -> no card',
  sh.includes('Scheduled · 2') && sh.includes('Upgrade &lt;x&gt;') && sh.includes('_orLaterAct(\'start\',&quot;1111-0900&quot;)')
  && sh.includes('_orLaterAct(\'cancel\',&quot;1111-0900&quot;)') && sh.includes('Nightly') && _orScheduledHtml([], []) === ''
  && _orScheduledHtml([{ state: 'merged' }], []) === '');
c.check('runs list: a run for later shows its start time; a repeat run is marked', _orRunsHtml([later], '').includes('starts ')
  && _orRunsHtml([Object.assign({}, later, { state: 'running', schedule_id: 's1' })], '').includes('>repeat<'));
const schedView = Object.assign(view('running'), { tasks: [] });
schedView.run = Object.assign({}, schedView.run, { state: 'scheduled', start_at: 2e9, error: 'waiting: this repo has <another> one' });
const sd = _orDetailHtml(schedView, false);
c.check('a run for later: start now / cancel, when it starts, why it waits (escaped)', sd.includes('Start now') && sd.includes('_orLaterAct(\'cancel\'')
  && sd.includes('Starts ') && sd.includes('waiting: this repo has &lt;another&gt; one') && !sd.includes('Cancel run'));
c.check('whole view: the Scheduled card above the form', _orHtml({ runs: [later], sel: '', view: null, dirs: [], editing: false, err: '', formOpen: null, schedules: [] }).includes('Scheduled · 1'));

// ── links: History session, commits ──
c.check('task card: a History link only when History has the session',
  _orTaskHtml(task('api', { in_history: true })).includes('_jumpToSessionHistory(&quot;abcdef123456&quot;)')
  && !_orTaskHtml(task('api')).includes('_jumpToSessionHistory')
  && !_orTaskHtml(task('m', { role: 'manager', in_history: true })).includes('_jumpToSessionHistory'));
const mv = view('merged');
mv.tasks[0].in_history = true;
const cm0 = _orDetailHtml(mv, false, null, false, null);
c.check('run commits: offered once there are commits, loaded when opened', cm0.includes('Commits (2) · merged into main') && cm0.includes('Loading…')
  && !_orDetailHtml(view('running'), false).includes('Commits ('));
const cm = _orCommitsHtml({ run: mv.run.id, commits: [{ sha: 'abcdef1234567890', subject: 'Add <api>', task: 'api', when: 1 },
  { sha: '1234567aaaa', subject: '', task: 'ui', when: 2 }] }, mv);
c.check('run commits: short sha, escaped subject, task, the session link when History has it',
  cm.includes('>abcdef1<') && cm.includes('Add &lt;api&gt;') && cm.includes('<code>api</code>') && (cm.match(/_jumpToSessionHistory/g) || []).length === 1
  && cm.includes('(no message)'));
c.check('run commits: none found said so', _orCommitsHtml({ run: mv.run.id, commits: [] }, mv).includes('No commits found'));
c.finish();
