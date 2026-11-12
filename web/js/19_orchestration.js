/* ── ORCHESTRATE: one goal, a plan of specialised agents, run as a DAG ──
   (server/17_orchestration.py) The orchestrator plans, you approve the plan,
   agents run headless in their own worktrees (parallel where they can),
   the orchestrator reviews, you approve the merge. Polls /orch/* every 3 s
   while the view is open. Its globals are `var`: boot (web/js/14) runs
   setView() before this part has run, and a `let` would throw there. */
var _orState={runs:[], sel:'', view:null, dirs:[], editing:false, draft:null, err:'', formOpen:null,
  form:null, agents:[], catalog:null, agentDraft:null, agentsOpen:false, workspaces:[], entities:[], entityDraft:null, schedules:[], formStash:null, commits:null, settings:null, stats:null, cmp:null, judging:false, rulesDraft:null};
var _orTimer=null;
var _OR_STATES={scheduled:['Scheduled','muted'], planning:['Planning','run'], plan_ready:['Plan ready — needs your OK','wait'], running:['Running','run'],
  supervising:['Reviewing results','run'], review:['Done — review & merge','wait'], merged:['Merged','done'],
  discarded:['Discarded','muted'], cancelled:['Cancelled','muted'], failed:['Failed','err']};
var _OR_TASK_STATES={pending:['waiting','muted'], running:['running','run'], done:['done','done'], failed:['failed','err'],
  blocked:['blocked','err'], skipped:['skipped','muted'], gate:['waits for your OK','wait']};
var _OR_MERGE_STATES={merged:['merged','done'], conflict:['conflict','err'], blocked:['waits on a conflict','wait'], left_out:['left out','muted']};
var _OR_ROLES={research:'Research', analyst:'Analyst', developer:'Developer', qa:'QA', manager:'Manager'};

/* Pure: a state chip. kind: 'run' | 'task'; runKind 'workspace': its results
   are accepted into the workspace, not merged (no branch is yours to merge). */
function _orChip(state, kind, runKind){
  let [label,cls]=(kind==='task'?_OR_TASK_STATES:_OR_STATES)[state]||[state,'muted'];
  if(kind!=='task'&&runKind==='workspace') label=({review:'Done — accept results', merged:'Accepted'})[state]||label;
  return `<span class="or-chip or-c-${cls}">${escHtml(label)}</span>`;
}
function _orMoney(v){ return '$'+(Number(v)||0).toFixed(2); }
/* Pure: a cost; est = part of it is an estimate (a process killed before it
   reported what it spent is charged what its output / transcript records). */
function _orCost(v, est){ return est?`~${_orMoney(v)} est.`:_orMoney(v); }
/* Pure: a long folder path -> its last `keep` parts after '…' (the full one goes in a title). */
function _orShortPath(p, keep){
  const s=String(p||''), parts=s.split(/[\\/]/).filter(Boolean), n=keep||2;
  return parts.length>n+1?'…'+(s.includes('\\')?'\\':'/')+parts.slice(-n).join(s.includes('\\')?'\\':'/'):s;
}
/* Pure: a task's merge state (after a merge stopped at a conflict) -> a chip, '' when none. */
function _orMergeChip(ms, workspace){
  const x=_OR_MERGE_STATES[ms]; if(!x) return '';
  const label=workspace&&ms==='merged'?'accepted':x[0];
  return `<span class="or-chip or-c-${x[1]}" title="Merge: ${escHtml(label)}">${escHtml(label)}</span>`;
}

/* The form's values live here (a refresh redraws the view: typed text must survive it). */
function _orFormDefaults(){ return {goal:'', cwd:'', where:'repo', workspace:'', model:'opus', par:3, budget:5, mode:'flat', teams:false, team:null,
  when:'now', at:'', rtype:'daily', rat:'08:00', rhours:24, bday:10, bmonth:100, auto:false}; }
/* Pure: a repeating run (GET /orch/schedules) as the form's values, to edit it (sch_id = which one;
   where it works stays -- shown, not editable). An empty team = the built-ins, as when it was made. */
function _orFormFromSchedule(s){
  const cfg=(s||{}).config||{}, sc=cfg.schedule||{};
  return Object.assign(_orFormDefaults(), {sch_id:String((s||{}).id||''), goal:cfg.goal||'', cwd:cfg.cwd||'',
    where:cfg.workspace?'workspace':'repo', workspace:cfg.workspace||'', model:cfg.model||'opus', par:Number(cfg.max_parallel)||3,
    budget:Number(cfg.budget_usd)||5, mode:cfg.mode||'flat', teams:!!cfg.approve_teams, team:(cfg.team||[]).length?cfg.team.slice():null,
    when:'repeat', rtype:sc.type||'daily', rat:sc.at||'08:00', rhours:Number(sc.hours)||24,
    bday:Number(cfg.budget_day)||10, bmonth:Number(cfg.budget_month)||100, auto:!!cfg.auto_approve});
}
/* Pure: what saving an edited schedule said (POST /orch/schedule_save {id, ...} -> {changed, next_due}). */
function _orScheduleSavedText(r){
  const ch=(r||{}).changed||[];
  if(!ch.length) return 'Nothing changed.';
  return 'Changed: '+ch.join(', ')+'.'+((r||{}).next_due?' Next run '+_orWhen(r.next_due)+'.':'');
}
function _orFormSet(k, v){
  const f=_orState.form; f[k]=v;
  if(k==='when'&&v==='later'&&!f.at) f.at=_orLocalInput(Date.now()+3600e3);
  if(['mode','where','when','rtype'].includes(k)) _orRender(true);
}
/* Pure: epoch ms -> a datetime-local value (local time, to the next quarter hour). */
function _orLocalInput(ms){
  const d=new Date(Math.ceil(ms/900e3)*900e3), p=n=>String(n).padStart(2,'0');
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}
/* Pure: the form's "when" -> what /orch/create or /orch/schedule_save gets on top of the run
   ({start_at} for later, {schedule, budget_day, budget_month, auto_approve} to repeat), or {error}. */
function _orWhenBody(f, nowMs){
  if(f.when==='later'){
    const t=new Date(String(f.at||'')).getTime();
    if(!f.at||isNaN(t)) return {error:'pick a start time'};
    if(t<=nowMs) return {error:'pick a start time in the future'};
    return {start_at:Math.round(t/1000)};
  }
  if(f.when==='repeat'){
    const schedule=f.rtype==='every'?{type:'every', hours:+f.rhours}:{type:f.rtype, at:String(f.rat||'')};
    return {schedule, budget_day:+f.bday, budget_month:+f.bmonth, auto_approve:!!f.auto};
  }
  return {};
}
/* Pure: the form's "when" row: now / later (a start time) / repeat (a clock + money caps). */
function _orWhenHtml(f){
  const opt=(cur,list)=>list.map(([v,l])=>`<option value="${escHtml(v)}"${String(cur)===String(v)?' selected':''}>${escHtml(l)}</option>`).join('');
  const later=f.when==='later'?`<input id="or-at" class="settings-input" type="datetime-local" value="${escHtml(f.at||'')}" oninput="_orFormSet('at',this.value)" aria-label="Start time">`:'';
  const rep=f.when!=='repeat'?'':`<select class="settings-input" aria-label="Repeat" onchange="_orFormSet('rtype',this.value)">${opt(f.rtype,[['daily','daily at'],['weekdays','on weekdays at'],['every','every N hours']])}</select>
      ${f.rtype==='every'?`<input class="settings-input" type="number" min="0.5" max="168" step="0.5" value="${Number(f.rhours)||24}" style="width:70px" aria-label="Hours" oninput="_orFormSet('rhours',+this.value)"> <span class="or-note">hours</span>`
        :`<input class="settings-input" type="time" value="${escHtml(f.rat||'08:00')}" aria-label="Time" oninput="_orFormSet('rat',this.value)">`}`;
  const caps=f.when!=='repeat'?'':`<div class="ln-row"><label class="ln-lbl" for="or-bday">$ per day</label><input id="or-bday" class="settings-input" type="number" min="0.5" max="200" step="1" value="${Number(f.bday)||10}" style="width:80px" oninput="_orFormSet('bday',+this.value)">
      <label class="ln-lbl" for="or-bmonth">$ per month</label><input id="or-bmonth" class="settings-input" type="number" min="1" max="2000" step="5" value="${Number(f.bmonth)||100}" style="width:90px" oninput="_orFormSet('bmonth',+this.value)">
      <label class="ln-chk" title="Otherwise each run's plan waits for your OK (Inbox)"><input type="checkbox"${f.auto?' checked':''} onchange="_orFormSet('auto',this.checked)"> approve its plans itself (within the run budget)</label></div>
    <span class="or-note">A run is skipped while the previous one still works or waits for your merge, or when it would cross a cap. Missed while AOC was off: it runs once when AOC starts.</span>`;
  return `<div class="ln-row"><label class="ln-lbl" for="or-when">When</label>
      <select id="or-when" class="settings-input" onchange="_orFormSet('when',this.value)">${opt(f.when||'now',f.sch_id?[['repeat','Repeat']]:[['now','Now'],['later','Later — at a time'],['repeat','Repeat — on a schedule']])}</select>${later}${rep}</div>${caps}`;
}
/* Pure: the run's team as picked (default: the built-in agents). */
function _orTeamOf(f, agents){
  const ids=(agents||[]).map(a=>a.id);
  return (f.team||(agents||[]).filter(a=>a.builtin).map(a=>a.id)).filter(id=>ids.includes(id));
}
/* Pure: the "New orchestration" form. dirs = /launch/dirs, f = its values, agents = the library. */
function _orFormHtml(dirs, open, f, agents, workspaces){
  f=f||_orFormDefaults();
  const team=_orTeamOf(f, agents);
  const sel=(k,opts)=>opts.map(([v,l])=>`<option value="${escHtml(String(v))}"${String(f[k])===String(v)?' selected':''}>${escHtml(String(l))}</option>`).join('');
  const picker=(agents||[]).map(a=>`<label class="ln-chk or-team-pick" title="${escHtml(_orClipText(a.brief,200))}"><input type="checkbox"${team.includes(a.id)?' checked':''} onchange="_orTeamToggle(${jsq(a.id)},this.checked)"> ${escHtml(a.name)}${(a.mcp||[]).length?' <span class="or-chip or-c-muted">MCP</span>':''}</label>`).join('');
  const ed=!!f.sch_id;
  const whereRo=f.where==='workspace'?'workspace '+f.workspace:f.cwd;
  return `<details class="td-card or-new${ed?' or-new-ed':''}" id="or-new"${open?' open':''}><summary onclick="_orState.formOpen=!this.parentNode.open"><h3 style="display:inline-flex">${ed?'Edit schedule':'New orchestration'}</h3></summary>
    <form onsubmit="event.preventDefault();_orCreate(this)">
      <label class="ln-lbl" for="or-goal">Goal</label>
      <textarea id="or-goal" class="settings-input" rows="4" required minlength="10" placeholder="What should the team achieve? e.g. Add CSV export to the reports page, with tests" oninput="_orFormSet('goal',this.value)">${escHtml(f.goal)}</textarea>
      ${ed?`<div class="ln-row"><span class="ln-lbl">Where</span><code class="or-ro">${escHtml(whereRo)}</code></div>
      <span class="or-note">Where it works stays: its runs' history is about that ${f.where==='workspace'?'workspace':'repo'}. For another one, make a new schedule.</span>`
      :`<div class="ln-row"><label class="ln-lbl" for="or-where">Where</label>
        <select id="or-where" class="settings-input" onchange="_orFormSet('where',this.value)" title="A workspace is a folder AOC keeps for work that isn't code: reports, research, store operations through connected services">${sel('where',[['repo','A code repository (git)'],['workspace','A workspace — not code (reports, research, services)']])}</select></div>
      ${f.where==='workspace'?`<label class="ln-lbl" for="or-ws">Workspace</label>
      <input id="or-ws" class="settings-input" list="or-wss" placeholder="e.g. eshop-ops (new or existing)" autocomplete="off" required value="${escHtml(f.workspace)}" oninput="_orFormSet('workspace',this.value)">
      <datalist id="or-wss">${(workspaces||[]).map(w=>`<option value="${escHtml(w.name)}"></option>`).join('')}</datalist>
      <span class="or-note">Kept in git by AOC under %LOCALAPPDATA%\\AOC\\workspaces — every file the agents write is versioned; you accept the results at the end.</span>`
      :`<label class="ln-lbl" for="or-cwd">Repository folder</label>
      <input id="or-cwd" class="settings-input" list="or-dirs" placeholder="C:\\path\\to\\repo" autocomplete="off" required value="${escHtml(f.cwd)}" oninput="_orFormSet('cwd',this.value)">
      <datalist id="or-dirs">${(dirs||[]).map(d=>`<option value="${escHtml(d.path)}"></option>`).join('')}</datalist>`}`}
      <div class="ln-row">
        <label class="ln-lbl" for="or-model">Orchestrator</label>
        <select id="or-model" class="settings-input" title="Plans and reviews; the agents' models are chosen per task in the plan" onchange="_orFormSet('model',this.value)">${sel('model',[['opus','Opus'],['sonnet','Sonnet']])}</select>
        <label class="ln-lbl" for="or-par">Parallel agents</label>
        <select id="or-par" class="settings-input" onchange="_orFormSet('par',+this.value)">${sel('par',[[1,'1'],[2,'2'],[3,'3'],[4,'4']])}</select>
        <label class="ln-lbl" for="or-budget">Run budget $</label>
        <input id="or-budget" class="settings-input" type="number" min="0.5" max="50" step="0.5" value="${Number(f.budget)||5}" style="width:80px" oninput="_orFormSet('budget',+this.value)">
      </div>
      <div class="ln-row">
        <label class="ln-lbl" for="or-mode">Structure</label>
        <select id="or-mode" class="settings-input" onchange="_orFormSet('mode',this.value)"
          title="Hierarchical: the orchestrator splits a large goal into workstreams; each has a manager that plans and runs its own team">${sel('mode',[['flat','One team'],['hierarchical','Hierarchical — managers with their own teams']])}</select>
        ${f.mode==='hierarchical'?`<label class="ln-chk" id="or-teams-row" title="Otherwise a team's plan is approved by your approval of the run's plan, within its manager's budget">
          <input type="checkbox" id="or-teams"${f.teams?' checked':''} onchange="_orFormSet('teams',this.checked)"> also approve each team's plan</label>`:''}
      </div>
      ${picker?`<div class="or-team-row"><span class="ln-lbl">Team</span>${picker}${team.length?'':'<span class="or-errs">pick at least one agent</span>'}</div>`:''}
      ${_orWhenHtml(f)}
      <div class="ln-row"><span class="or-note">${f.when==='repeat'&&f.auto?'Each run approves its own plan.':'Nothing runs before you approve the plan.'} Agents work in their own git worktrees off the repo's last commit; you approve the merge at the end. Agents are billed like any <code>claude -p</code> run; agents with MCP services run on your Claude subscription.</span>
        <span class="ln-err" id="or-err"></span>${ed?'<button type="button" class="adp-btn" onclick="_orScheduleEditCancel()">Cancel</button>':''}<button type="submit" class="adp-btn ln-go"${picker&&!team.length?' disabled':''}>${ed?'Save changes':f.when==='later'?'Schedule it':f.when==='repeat'?'Save the schedule':'Plan it'}</button></div>
    </form></details>`;
}
function _orClipText(s, n){ s=String(s||''); return s.length>n?s.slice(0,n)+'…':s; }
function _orTeamToggle(id, on){
  const f=_orState.form, team=_orTeamOf(f, _orState.agents).filter(x=>x!==id);
  f.team=on?team.concat([id]):team;
  _orRender(true);
}

/* ── Agent library: the built-ins + your own (GET /orch/agents, /orch/catalog) ── */
/* Pure: one agent's card. */
function _orAgentCardHtml(a){
  const chips=(xs,cls)=>(xs||[]).map(x=>`<span class="or-chip ${cls}">${escHtml(x)}</span>`).join(' ');
  return `<div class="or-agent">
    <div class="or-agent-h"><b>${escHtml(a.name)}</b> <code>${escHtml(a.id)}</code>${a.builtin?' <span class="or-chip or-c-muted">built-in</span>':''}${a.sensitive?' <span class="or-chip or-c-wait">asks first</span>':''}</div>
    <div class="or-agent-brief">${escHtml(_orClipText(a.brief,220))}</div>
    <div class="or-agent-chips">${chips(a.tools,'or-c-muted')} ${chips(a.mcp,'or-c-run')} ${chips(a.skills,'or-c-done')}</div>
    <div class="or-agent-f">${escHtml(a.model)} · ${_orMoney(a.budget_usd)} default${(a.mcp||[]).length?' · runs on your subscription (MCP)':''}
      <span style="flex:1"></span>${a.builtin?'':`<button class="adp-btn" onclick="_orAgentEdit(${jsq(a.id)})">Edit</button>`}
      <button class="adp-btn" onclick="_orAgentEdit(${jsq(a.id)},true)" title="A new agent starting from this one">Duplicate</button>
      ${a.builtin?'':`<button class="adp-btn" onclick="_orAgentDelete(${jsq(a.id)})">Delete</button>`}</div></div>`;
}
/* Pure: the agent editor. d = the draft, cat = /orch/catalog. */
function _orAgentEditorHtml(d, cat){
  cat=cat||{tools:[],mcp:[],skills:[],models:['haiku','sonnet','opus']};
  const box=(field,name,on,label,title)=>`<label class="ln-chk"${title?` title="${escHtml(title)}"`:''}><input type="checkbox"${on?' checked':''} onchange="_orAgentToggle(${jsq(field)},${jsq(name)},this.checked)"> ${escHtml(label||name)}</label>`;
  const mcpNames=(cat.mcp||[]).map(m=>m.name);
  const on=m=>(d.mcp||[]).includes(m.name);
  const offline=(cat.mcp||[]).filter(m=>!m.connected&&!on(m));
  const mcp=(cat.mcp||[]).filter(m=>m.connected||on(m)).map(m=>box('mcp',m.name,on(m),m.name+(m.connected?'':' (needs sign-in)'),m.connected?'':'Not connected: sign in to it in Claude Code first'))
    .concat((d.mcp||[]).filter(x=>!mcpNames.includes(x)).map(x=>box('mcp',x,true,x+' (not found)'))).join('')
    +(offline.length?`<details class="or-ag-off" data-k="ag-off"><summary>${offline.length} not connected</summary>${offline.map(m=>box('mcp',m.name,false,m.name,'Not connected: sign in to it in Claude Code first')).join('')}</details>`:'');
  const q=String(d._skillQ||'').toLowerCase();
  const skills=(cat.skills||[]).filter(s=>(d.skills||[]).includes(s.name)||!q||s.name.toLowerCase().includes(q)||String(s.description||'').toLowerCase().includes(q))
    .slice(0,60).map(s=>box('skills',s.name,(d.skills||[]).includes(s.name),s.name,s.description)).join('');
  return `<div class="or-agent-ed">
    <div class="ln-row"><span class="or-ag-f"><label class="ln-lbl" for="or-ag-name">Name</label><input id="or-ag-name" class="settings-input or-ag-name" value="${escHtml(d.name||'')}" oninput="_orAgentSet('name',this.value)"></span>
      <span class="or-ag-f"><label class="ln-lbl" for="or-ag-id">Id</label><input id="or-ag-id" class="settings-input or-ed-id" value="${escHtml(d.id||'')}" placeholder="from the name" oninput="_orAgentSet('id',this.value)"></span>
      <span class="or-ag-f"><label class="ln-lbl" for="or-ag-model">Model</label><select id="or-ag-model" class="settings-input" onchange="_orAgentSet('model',this.value)">${(cat.models||['haiku','sonnet','opus']).map(m=>`<option${m===d.model?' selected':''}>${m}</option>`).join('')}</select></span>
      <span class="or-ag-f"><label class="ln-lbl" for="or-ag-b">Default $</label><input id="or-ag-b" class="settings-input or-ed-b" type="number" min="0.1" max="5" step="0.1" value="${Number(d.budget_usd)||1}" oninput="_orAgentSet('budget_usd',+this.value)"></span></div>
    <label class="ln-lbl" for="or-ag-brief">Role (its system prompt): who it is, what it does, how it reports</label>
    <textarea id="or-ag-brief" class="settings-input" rows="5" oninput="_orAgentSet('brief',this.value)">${escHtml(d.brief||'')}</textarea>
    <div class="or-ag-sec"><span class="ln-lbl">Tools</span>${(cat.tools||[]).map(t=>box('tools',t,(d.tools||[]).includes(t))).join('')}</div>
    <div class="or-ag-sec"><span class="ln-lbl">MCP services</span>${mcp||`<span class="or-note">${cat.scanning?'Looking for your MCP servers… (claude mcp list)':'No MCP servers found'}</span>`}</div>
    <div class="or-ag-sec"><span class="ln-lbl">Skills</span><input class="settings-input or-ag-q" placeholder="filter ${(cat.skills||[]).length} skills" value="${escHtml(d._skillQ||'')}" oninput="_orAgentSet('_skillQ',this.value,true)">${skills||'<span class="or-note">none found</span>'}</div>
    <label class="ln-chk"><input type="checkbox"${d.sensitive?' checked':''} onchange="_orAgentSet('sensitive',this.checked)"> Sensitive — each of its tasks waits for my OK before it starts</label>
    ${(d.mcp||[]).length?'<div class="or-note">With MCP services it runs on your Claude subscription (claude.ai login, plan limits apply) — the connectors don\'t load with an API key.</div>':''}
    <div class="ln-row"><span class="ln-err" id="or-ag-err"></span><span style="flex:1"></span><button class="adp-btn" onclick="_orAgentCancel()">Cancel</button><button class="adp-btn ln-go" onclick="_orAgentSave()">Save agent</button></div></div>`;
}
/* Pure: the library section. */
function _orAgentsHtml(agents, draft, cat, open){
  const mine=(agents||[]).filter(a=>!a.builtin).length;
  return `<details class="td-card or-agents"${open?' open':''}><summary onclick="_orState.agentsOpen=!this.parentNode.open"><h3 style="display:inline-flex">Agents · ${(agents||[]).length}${mine?` (${mine} yours)`:''}</h3></summary>
    ${draft?_orAgentEditorHtml(draft, cat):`<div class="ln-row"><span class="or-note">Your own agents: a role, tools, MCP services and skills. Pick them into a run's team.</span><span style="flex:1"></span><button class="adp-btn" onclick="_orAgentEdit('')">+ New agent</button></div>`}
    <div class="or-agent-grid">${(agents||[]).map(_orAgentCardHtml).join('')}</div></details>`;
}

/* Pure: the list of runs, the selected one marked. */
function _orRunsHtml(runs, sel){
  if(!(runs||[]).length) return '';
  /* a team's run is selected -> its run above stays marked */
  return `<div class="td-card"><h3>Runs</h3>${runs.map(r=>`<button class="or-run${r.id===sel||String(sel).startsWith(r.id+'-')?' sel':''}" onclick="_orSelect(${jsq(r.id)})">
      <span class="or-run-chips">${_orChip(r.state,'run',r.kind)}${r.mode==='hierarchical'?'<span class="or-chip or-c-muted">teams</span>':''}${r.baseline_of?'<span class="or-chip or-c-muted" title="The same goal as run '+escHtml(r.baseline_of)+', done by one agent">1 agent</span>':''}${r.schedule_id?'<span class="or-chip or-c-muted" title="Started by a repeating schedule">repeat</span>':''}${r.needs_you?'<span class="or-dot" title="Needs you"></span>':''}</span><span class="or-run-goal">${escHtml(r.goal)}</span>
      <span class="or-run-meta">${escHtml((r.repo||'').split(/[\\/]/).filter(Boolean).pop()||'')} · ${r.state==='scheduled'?'starts '+escHtml(_orWhen(r.start_at)):`${r.metrics.done}/${r.tasks} tasks · ${_orCost(r.metrics.cost_total,r.metrics.cost_est)}`}</span></button>`).join('')}</div>`;
}

/* Pure: the run's numbers (PDF ch. 9: coordination must not cost more than the work). */
function _orMetricsHtml(m){
  const share=Math.round((m.coordination_share||0)*100);
  const cells=[['Cost',_orCost(m.cost_total,m.cost_est)],['Coordination',`${_orMoney(m.cost_coordination)} · ${share}%`],['Agents',_orMoney(m.cost_work)],
    ['Time',_fmtDurationDHM(m.duration_s||0)],['Tasks',`${m.done}/${m.tasks}${m.failed?` · ${m.failed} failed`:''}`],
    ['Success',m.success_rate==null?'—':Math.round(m.success_rate*100)+'%'],['Retries',String(m.retries)],
    ['Your actions',String(m.human_interventions)],['Avg task',m.avg_task_s==null?'—':_fmtDurShort(Math.round(m.avg_task_s))]]
    .concat(m.teams?[['Teams',String(m.teams)]]:[]);
  return `<div class="or-metrics">${cells.map(([k,v])=>`<div><b>${escHtml(v)}</b><span>${k}</span></div>`).join('')}</div>
    ${share>=50&&m.cost_total>0.5?'<div class="or-warn">Coordination cost half of the run or more — for a goal like this, a single agent may be cheaper.</div>':''}`;
}

/* Pure: the team's run next to its one-agent version (GET /orch/compare). */
function _orCompareHtml(c, judging){
  const T=c.team, S=c.solo, both=T.state==='review'&&S.state==='review';
  const diff=d=>d?`${d.files} file${d.files===1?'':'s'} · +${d.insertions} −${d.deletions}`:'—';
  const tasks=x=>`${x.tasks}`+(x.success==null?'':` · ${Math.round(x.success*100)}% ok`);
  const rows=[['State',_orChip(T.state,'run'),_orChip(S.state,'run')],
    ['Cost',_orMoney(T.cost),_orMoney(S.cost)],
    ['Coordination',_orMoney(T.coordination),_orMoney(S.coordination)],
    ['Time',escHtml(_fmtDurationDHM(T.seconds||0)),escHtml(_fmtDurationDHM(S.seconds||0))],
    ['Tasks',escHtml(tasks(T)),escHtml(tasks(S))],
    ['Changes',escHtml(diff(T.diff)),escHtml(diff(S.diff))]];
  const ratio=both&&S.cost>0&&T.cost>0?T.cost/S.cost:null;
  const v=c.verdict;
  const said={team:"The team's result is better",solo:"The single agent's result is better",tie:'About the same'};
  const verdict=v?`<div class="or-verdict or-v-${escHtml(v.better)}"><b>${escHtml(said[v.better]||v.better)}</b>${v.cost?` <i>${_orMoney(v.cost)}</i>`:''}
      <div>${escHtml(v.reasons||'')}</div>${v.risks?`<div class="or-note">Check before merging: ${escHtml(v.risks)}</div>`:''}</div>`:'';
  const ask=both?`<button class="adp-btn" onclick="_orJudge(${jsq(T.id)})"${judging?' disabled':''} title="Claude (Sonnet) reads both reports and diffs and says which is better -- a few cents">${judging?'Comparing…':v?'Ask again':'Ask Claude which is better'}</button>`
    :'<span class="or-note">When both are done you can ask Claude which result is better.</span>';
  return `<div class="or-cmp"><h3>Team vs one agent</h3>
    <table class="or-cmp-t"><tr><th></th><th><button class="adp-btn" onclick="_orSelect(${jsq(T.id)})">Team</button></th><th><button class="adp-btn" onclick="_orSelect(${jsq(S.id)})">One agent</button></th></tr>
      ${rows.map(([k,a,b])=>`<tr><td>${k}</td><td>${a}</td><td>${b}</td></tr>`).join('')}</table>
    ${ratio?`<div class="or-note">The team cost ${ratio>=1?ratio.toFixed(1)+'× as much as':Math.round((1-ratio)*100)+'% less than'} the single agent.</div>`:''}
    ${verdict}<div class="or-actions">${ask}<span class="or-note">Merging one of them drops the other.</span></div>
    <details class="or-log" data-k="cmp-rep"><summary>Both reports</summary><b>Team</b><pre>${escHtml(T.report||'(none yet)')}</pre><b>One agent</b><pre>${escHtml(S.report||'(none yet)')}</pre></details></div>`;
}

/* Pure: one task card. */
function _orTaskHtml(t, runId, pick, workspace){
  const o=t.output||{};
  const facts=[(t.agent&&t.agent.name)||_OR_ROLES[t.role]||t.role, t.model, `budget ${_orMoney(t.budget_usd)}`]
    .concat(t.cost_usd?[`spent ${_orCost(t.cost_usd,t.cost_est)}`]:[]).concat(t.attempt>1?[`attempt ${t.attempt}`]:[])
    .concat(t.commits&&t.branch?[`${t.commits} commit${t.commits>1?'s':''}`]:[]);
  const deps=(t.depends_on||[]).length?`<div class="or-deps">after ${t.depends_on.map(d=>`<code>${escHtml(d)}</code>`).join(' ')}</div>`:'';
  const body=o.summary||t.error?`<details class="or-out" data-k="o:${escHtml(t.id)}"><summary>${escHtml(o.summary||t.error)}</summary>
      ${o.result?`<pre>${escHtml(o.result)}</pre>`:''}
      ${(o.errors||[]).length?`<div class="or-errs">${o.errors.map(e=>`<div>✗ ${escHtml(e)}</div>`).join('')}</div>`:''}
      ${t.error&&o.summary?`<div class="or-errs"><div>✗ ${escHtml(t.error)}</div></div>`:''}
      ${t.session_id?`<div class="or-sid">session <code>${escHtml(t.session_id.slice(0,8))}</code></div>`:''}</details>`:'';
  const sid=t.session_id||t.live_session;
  const watch=sid&&t.role!=='manager'?`<button class="adp-btn or-watch" onclick="openReplay(${jsq(sid)},'',${jsq(t.title)})" title="${t.state==='running'?'What the agent is doing right now (a snapshot)':'Every step the agent took'}">${t.state==='running'?'👁 Watch':'Replay'}</button>`:'';
  /* the agent's own Claude Code session, once the transcript scanner has put it in History */
  const hist=sid&&t.in_history&&t.role!=='manager'?`<button class="adp-btn or-watch" onclick="_jumpToSessionHistory(${jsq(sid)})" title="This agent's session in History: cost, tokens, files">History</button>`:'';
  const tm=t.team;
  const team=tm?`<div class="or-team">${_orChip(tm.state,'run')} <span>team: ${tm.done}/${tm.tasks} done${tm.running?` · ${tm.running} running`:''}</span>
      ${tm.state==='plan_ready'?'<b class="or-team-ok">its plan needs your OK</b>':''}
      <button class="adp-btn" onclick="_orSelect(${jsq(tm.id)})" title="The manager's own run: its plan, agents and review">Open team</button></div>`:'';
  const gate=t.state==='gate'&&runId?`<div class="or-gate"><span>${escHtml((t.agent&&t.agent.name)||t.role)} is a sensitive agent — start this task?</span>
      <button class="adp-btn ln-go" onclick="_orGate(${jsq(runId)},${jsq(t.id)},true)">Allow</button><button class="adp-btn" onclick="_orGate(${jsq(runId)},${jsq(t.id)},false)">Skip</button></div>`:'';
  const keep=pick&&t.state==='done'&&t.commits&&t.branch?`<label class="ln-chk or-keep" title="Merge / accept this task's work"><input type="checkbox"${pick.has(t.id)?' checked':''} onchange="_orPick(${jsq(t.id)},this.checked)"> keep</label>`:'';
  return `<div class="or-task or-t-${escHtml(t.state)}${t.role==='manager'?' or-mgr':''}">${keep}
    <div class="or-task-h"><code>${escHtml(t.id)}</code> <span class="or-task-chips">${_orMergeChip(t.merge_state, workspace)}${_orChip(t.state,'task')}</span></div>
    <div class="or-task-title">${escHtml(t.title)}</div>
    <div class="or-task-facts">${facts.map(escHtml).join(' · ')}</div>${deps}
    <details class="or-instr" data-k="i:${escHtml(t.id)}"><summary>Instructions</summary><pre>${escHtml(t.instructions)}</pre><div class="or-crit"><b>Done when:</b> ${escHtml(t.done_criteria)}</div>
      ${t.feedback?`<div class="or-crit"><b>Feedback:</b> ${escHtml(t.feedback)}</div>`:''}</details>
    ${gate}${team}${body}${watch}${hist}</div>`;
}

/* ── Plan editor: one card per task, fields instead of JSON ──
   The draft lives in _orState.draft (the 3 s refresh redraws from it, so
   nothing typed is lost); text fields write into it as you type, structural
   changes (add / remove / move / dependencies / role) redraw. The server
   validates the saved plan again (_orch_validate_tasks). */
var _OR_PLAN_FIELDS=['id','role','title','instructions','done_criteria','depends_on','model','budget_usd'];
var _OR_TASK_BUDGET=[0.1,5], _OR_MANAGER_BUDGET=[0.5,20];

/* Pure: a run's tasks -> an editable draft. */
function _orDraftFrom(v){
  return {runId:v.run.id, json:false, budget:Number(v.run.budget_usd)||5, par:Number(v.run.max_parallel)||3,
    tasks:v.tasks.map(t=>{ const o={}; _OR_PLAN_FIELDS.forEach(k=>{ o[k]=k==='depends_on'?(t[k]||[]).slice():t[k]; }); return o; })};
}
/* Pure: what the server would turn an id into -- diacritics become plain letters
   ("Výskum" -> "vyskum"), not dashes, like _orch_ascii there. */
function _orSlug(s){ const m={'ß':'ss','æ':'ae','œ':'oe','ø':'o','ł':'l','đ':'d','ð':'d','þ':'th','ı':'i'};
  return String(s||'').trim().toLowerCase().replace(/[ßæœøłđðþı]/g,ch=>m[ch]).normalize('NFKD').replace(/[\u0300-\u036f]/g,'')
    .replace(/[^a-z0-9-]+/g,'-').replace(/^-+|-+$/g,'').slice(0,32); }
/* Pure: a new task appended (a free id, a developer by default). */
function _orDraftAdd(tasks){
  let n=tasks.length+1; while(tasks.some(t=>t.id==='task-'+n)) n++;
  return tasks.concat([{id:'task-'+n, role:'developer', title:'', instructions:'', done_criteria:'', depends_on:[], model:'sonnet', budget_usd:1}]);
}
/* Pure: task i removed, and from everyone's dependencies. */
function _orDraftRemove(tasks, i){
  const gone=tasks[i]&&tasks[i].id;
  return tasks.filter((_,j)=>j!==i).map(t=>Object.assign({},t,{depends_on:(t.depends_on||[]).filter(d=>d!==gone)}));
}
/* Pure: task i moved up (-1) or down (+1). */
function _orDraftMove(tasks, i, dir){
  const j=i+dir; if(j<0||j>=tasks.length) return tasks;
  const out=tasks.slice(); [out[i],out[j]]=[out[j],out[i]]; return out;
}
/* Pure: task i renamed; the tasks that depend on it follow. */
function _orDraftRename(tasks, i, id){
  const old=tasks[i].id;
  return tasks.map((t,j)=>Object.assign({},t,{id:j===i?id:t.id,
    depends_on:(t.depends_on||[]).map(d=>d===old?id:d)}));
}
/* Pure: the problems the server would refuse, per task (index -> [messages])
   plus the plan's own; and the budgets' sum against 85% of the run's. */
function _orPlanCheck(tasks, allowManager, runBudget){
  const per={}, add=(i,m)=>{ (per[i]=per[i]||[]).push(m); };
  const plan=[];
  if(!tasks.length) plan.push('the plan has no tasks');
  const ids=tasks.map(t=>_orSlug(t.id));
  tasks.forEach((t,i)=>{
    if(!ids[i]) add(i,'needs an id (a-z, 0-9, -)');
    else if(ids.indexOf(ids[i])!==i) add(i,`two tasks are called '${ids[i]}'`);
    if(!String(t.title||'').trim()) add(i,'needs a title');
    if(!String(t.instructions||'').trim()) add(i,'needs instructions');
    if(!String(t.done_criteria||'').trim()) add(i,'needs a done criterion');
    if(t.role==='manager'&&!allowManager) add(i,'a manager needs a hierarchical run');
    (t.depends_on||[]).forEach(d=>{ if(!ids.includes(d)||d===ids[i]) add(i,`depends on unknown task '${d}'`); });
  });
  /* cycle: repeatedly take tasks whose dependencies are all taken */
  const done=new Set(); let moved=true;
  while(moved){ moved=false; tasks.forEach((t,i)=>{ if(!done.has(ids[i])&&(t.depends_on||[]).every(d=>done.has(d)||!ids.includes(d))){ done.add(ids[i]); moved=true; } }); }
  if(done.size<new Set(ids).size) plan.push('the dependencies go in a circle');
  const total=tasks.reduce((s,t)=>s+(Number(t.budget_usd)||0),0);
  return {per, plan, total, over:total>runBudget*0.85+1e-9, ok:!plan.length&&!Object.keys(per).length};
}

/* Pure: the editor. allowManager = a hierarchical run's top level. */
function _orPlanEditorHtml(v, d){
  d=d||_orDraftFrom(v);
  const allowManager=v.run.mode==='hierarchical'&&!v.run.parent_id;
  const roles=((v.run.team||[]).length?v.run.team.slice():['research','analyst','developer','qa']).concat(allowManager?['manager']:[]);
  const chk=_orPlanCheck(d.tasks, allowManager, d.budget);
  const sel=(i,f,opts,cur,labels)=>`<select class="settings-input" onchange="_orDraftSet(${i},${jsq(f)},this.value,true)">${opts.map((o,k)=>`<option value="${escHtml(o)}"${o===cur?' selected':''}>${escHtml(labels?labels[k]:o)}</option>`).join('')}</select>`;
  const head=`<div class="ln-row or-ed-run"><label class="ln-lbl" for="or-ed-budget">Run budget $</label><input id="or-ed-budget" class="settings-input" type="number" min="0.5" max="50" step="0.5" value="${d.budget}" style="width:80px" oninput="_orDraftRun('budget',this.value)">
      <label class="ln-lbl" for="or-ed-par">Parallel agents</label><input id="or-ed-par" class="settings-input" type="number" min="1" max="4" value="${d.par}" style="width:60px" oninput="_orDraftRun('par',this.value)">
      <span class="or-ed-sum${chk.over?' bad':''}" id="or-ed-sum">tasks ${_orMoney(chk.total)} of ${_orMoney(d.budget*0.85)} available to them</span></div>`;
  const foot=`<div class="ln-row">${chk.plan.map(m=>`<span class="or-errs">✗ ${escHtml(m)}</span>`).join(' ')}<span style="flex:1"></span>
      <button class="adp-btn" onclick="_orDraftJson(${d.json?'false':'true'})" title="${d.json?'Back to one card per task':'Edit the plan as JSON'}">${d.json?'Form':'JSON'}</button>
      <button class="adp-btn" onclick="_orEdit(false)">Cancel</button>
      <button class="adp-btn ln-go" onclick="_orSavePlan(${jsq(v.run.id)})"${d.json||chk.ok?'':' disabled title="Fix the marked fields first"'}>Save plan</button></div>`;
  if(d.json) return `<div class="or-edit">${head}<div class="or-note">Fields: ${_OR_PLAN_FIELDS.join(', ')}. Roles: ${roles.join(' · ')}.</div>
    <textarea id="or-plan-json" class="settings-input" rows="18" spellcheck="false">${escHtml(JSON.stringify(d.tasks,null,2))}</textarea>${foot}</div>`;
  const ids=d.tasks.map(t=>_orSlug(t.id));
  const cards=d.tasks.map((t,i)=>{
    const lim=t.role==='manager'?_OR_MANAGER_BUDGET:_OR_TASK_BUDGET;
    const deps=ids.filter((x,j)=>j!==i&&x).map(x=>`<label class="ln-chk"><input type="checkbox"${(t.depends_on||[]).includes(x)?' checked':''} onchange="_orDraftDep(${i},${jsq(x)},this.checked)"> ${escHtml(x)}</label>`).join('');
    const errs=(chk.per[i]||[]).map(m=>`<div>✗ ${escHtml(m)}</div>`).join('');
    return `<div class="or-ed-task${errs?' bad':''}">
      <div class="or-ed-row"><input class="settings-input or-ed-id" value="${escHtml(t.id)}" aria-label="Task id" title="Task id (a-z, 0-9, -)" onchange="_orDraftId(${i},this.value)">
        ${sel(i,'role',roles,t.role,roles.map(r=>_OR_ROLES[r]||r))}${sel(i,'model',['haiku','sonnet','opus'],t.model)}
        <label class="ln-lbl">$</label><input class="settings-input or-ed-b" type="number" min="${lim[0]}" max="${lim[1]}" step="0.1" value="${Number(t.budget_usd)||0}" aria-label="Budget" title="${t.role==='manager'?'For its whole team':'For this agent'} (${lim[0]}–${lim[1]})" oninput="_orDraftSet(${i},'budget_usd',this.value)">
        <span class="or-ed-ops"><button class="adp-btn" onclick="_orDraftOp('move',${i},-1)"${i?'':' disabled'} title="Move up">↑</button><button class="adp-btn" onclick="_orDraftOp('move',${i},1)"${i<d.tasks.length-1?'':' disabled'} title="Move down">↓</button><button class="adp-btn" onclick="_orDraftOp('remove',${i})" title="Remove this task">✕</button></span></div>
      <input class="settings-input" value="${escHtml(t.title)}" placeholder="Title" aria-label="Title" oninput="_orDraftSet(${i},'title',this.value)">
      <textarea class="settings-input" rows="3" placeholder="${t.role==='manager'?'The workstream: what its team delivers, where, constraints':'Exactly what to do, with the inputs it needs'}" aria-label="Instructions" oninput="_orDraftSet(${i},'instructions',this.value)">${escHtml(t.instructions)}</textarea>
      <input class="settings-input" value="${escHtml(t.done_criteria)}" placeholder="Done when… (checkable)" aria-label="Done criterion" oninput="_orDraftSet(${i},'done_criteria',this.value)">
      ${deps?`<div class="or-ed-deps"><span class="or-note">Runs after:</span>${deps}</div>`:''}
      ${errs?`<div class="or-errs">${errs}</div>`:''}</div>`;
  }).join('');
  return `<div class="or-edit">${head}${cards}<div class="ln-row"><button class="adp-btn" onclick="_orDraftOp('add')">+ Add task</button></div>${foot}</div>`;
}

/* Pure: the run's goal. A team's goal is its workstream first, then the
   context the run above gave it (done criterion, overall goal, inputs) --
   that part folded away. */
function _orGoalHtml(r){
  const g=String(r.goal||''), i=r.parent_id?g.indexOf('\n\nDONE WHEN:'):-1;
  if(i<0) return `<div class="or-goal">${escHtml(g)}</div>`;
  return `<div class="or-goal">${escHtml(g.slice(0,i))}</div>
    <details class="or-instr" data-k="ctx"><summary>Done criterion, overall goal and inputs from the run above</summary><pre>${escHtml(g.slice(i+2))}</pre></details>`;
}

/* Pure: the commits a run's agents made (GET /orch/commits, loaded when
   opened). c = {run, commits, base} or null (not loaded); each links to the
   session that made it when History has it. */
function _orCommitsHtml(c, v){
  const r=v.run, n=v.tasks.reduce((s,t)=>s+(Number(t.commits)||0),0);
  const sess={};
  v.tasks.forEach(t=>{ const sid=t.session_id||t.live_session; if(sid&&t.in_history) sess[t.id]=sid; });
  const rows=(c&&c.commits||[]).map(x=>`<tr><td class="cc-sha" title="${escHtml(x.sha)}">${escHtml(String(x.sha).slice(0,7))}</td>
      <td class="cache-name cc-subj">${escHtml(x.subject||'(no message)')}</td><td>${x.task?`<code>${escHtml(x.task)}</code>`:''}</td>
      <td>${sess[x.task]?`<button class="adp-btn conv-btn" onclick="_jumpToSessionHistory(${jsq(sess[x.task])})" title="The agent's session that made it, in History">Session</button>`:''}</td></tr>`).join('');
  const body=!c?'<span class="or-note">Loading…</span>':rows?`<table class="cache-table or-commits-t">${rows}</table>`
    :'<span class="or-note">No commits found — their branches may be gone.</span>';
  return `<details class="or-log" data-k="commits:${escHtml(r.id)}"><summary onclick="_orCommitsToggle(this,${jsq(r.id)})">Commits (${n})${r.state==='merged'?` · merged into ${escHtml(r.base)}`:''}</summary>${body}</details>`;
}
function _orCommitsToggle(sum, id){
  if(!sum.parentNode.open) fetch('/orch/commits?id='+encodeURIComponent(id)).then(r=>r.json()).then(d=>{ _orState.commits=Object.assign({}, d, {run:id}); _orRender(true); }).catch(()=>{});
}

/* Pure: the selected run. */
/* Pure: a run in review whose merge stopped at a conflict -> {merged,
   conflict, blocked, untried, open} task ids (open = not merged, not left
   out); null when no merge has been tried (or the run is past review). */
function _orMergeStatus(v){
  const r=(v||{}).run||{};
  if(r.state!=='review') return null;
  const ts=((v||{}).tasks||[]).filter(t=>t.state==='done'&&t.commits&&t.branch);
  const by=s=>ts.filter(t=>(t.merge_state||'')===s).map(t=>t.id);
  const out={merged:by('merged'), conflict:by('conflict'), blocked:by('blocked'), untried:by('')};
  if(!out.merged.length&&!out.conflict.length&&!out.blocked.length) return null;
  out.open=out.conflict.concat(out.blocked, out.untried);
  return out;
}
/* Pure: the "merge stopped at a conflict" box: per task where it stands and
   how to go on -- resolve it yourself and try again, merge the rest, or
   finish with what is merged (found in dogfooding: the run just sat in review). */
function _orMergeHtml(v){
  const ms=_orMergeStatus(v); if(!ms) return '';
  const r=v.run, id=jsq(r.id), ws=r.kind==='workspace';
  const rows=v.tasks.filter(t=>t.merge_state&&t.merge_state!=='left_out'||ms.untried.includes(t.id)).map(t=>`<div class="or-mg-row"><code>${escHtml(t.id)}</code>
      ${_orMergeChip(t.merge_state, ws)||'<span class="or-chip or-c-muted">not tried</span>'}<span class="or-note">${escHtml(t.title)}</span>
      ${t.merge_state==='conflict'?`<div class="or-note or-mg-how">Resolve it yourself: in <code title="${escHtml(r.repo)}">${escHtml(_orShortPath(r.repo))}</code> run <code>git merge ${escHtml(t.branch)}</code>, fix the files, commit — then <b>Try again</b>.</div>`:''}</div>`).join('');
  return `<div class="or-mergebox"><div><b>The merge stopped at a conflict</b> <span class="or-note">· ${ms.merged.length} of ${ms.merged.length+ms.open.length} ${ws?'accepted':'merged into '+escHtml(r.base)} so far</span></div>
    ${rows}
    <div class="or-actions">
      <button class="adp-btn ln-go" onclick="_orMergeGo(${id},'retry')" title="Merge what is left again -- a branch you merged yourself counts as merged">Try again</button>
      ${ms.untried.length?`<button class="adp-btn" onclick="_orMergeGo(${id},'rest')" title="Merge every branch that goes in cleanly; the conflicting ones and the tasks built on them wait">Merge the rest</button>`:''}
      ${ms.merged.length?`<button class="adp-btn" onclick="_orMergeGo(${id},'finish')" title="End the run with what is merged; the rest is dropped">Finish with what's merged</button>`
        :`<button class="adp-btn" onclick="_orAct('discard',${id})" title="Delete the branches and everything the agents did">Discard</button>`}</div></div>`;
}

function _orDetailHtml(v, editing, cmp, judging, commits){
  if(!v||!v.run) return '';
  const r=v.run, id=jsq(r.id);
  const planned=v.tasks.reduce((s,t)=>s+(Number(t.budget_usd)||0),0);
  const mstat=_orMergeStatus(v);
  let actions='';
  if(r.state==='scheduled') actions=`<button class="adp-btn ln-go" onclick="_orLaterAct('start',${id})" title="Start planning now instead of at its time">Start now</button>
      <button class="adp-btn" onclick="_orLaterAct('cancel',${id})">Cancel</button>`;
  else if(r.state==='plan_ready'&&editing) actions='<span class="or-note">Save or cancel your changes to approve the plan.</span>';
  else if(r.state==='plan_ready') actions=`<button class="adp-btn ln-go" onclick="_orAct('approve',${id})" title="Start the agents">Approve plan</button>
      <button class="adp-btn" onclick="_orEdit(true)">Edit plan</button><button class="adp-btn" onclick="_orAct('cancel',${id})">Cancel</button>`;
  else if(['planning','running','supervising'].includes(r.state)) actions=`<button class="adp-btn" onclick="_orAct('cancel',${id})" title="Stop every agent and remove their worktrees">Cancel run</button>`;
  else if(r.parent_id) actions='';   // a team's finished work is taken over by the run above
  else if(mstat) actions='';   // a merge stopped at a conflict: _orMergeHtml says how to go on
  else if(r.state==='review'&&r.kind==='workspace') actions=`<button class="adp-btn ln-go" onclick="_orAct('merge',${id})" title="Keep the files the agents wrote in the workspace">Accept results</button>
      <button class="adp-btn" onclick="_orAct('discard',${id})" title="Drop what the agents wrote">Discard</button>`;
  else if(r.state==='review') actions=`<button class="adp-btn ln-go" onclick="_orAct('merge',${id})" title="Merge the developers' and workstreams' branches into ${escHtml(r.base)}">Merge into ${escHtml(r.base)}</button>
      <button class="adp-btn" onclick="_orAct('discard',${id})" title="Delete the branches and everything the agents did">Discard</button>`;
  else if(r.state==='failed') actions=`<button class="adp-btn" onclick="_orAct('discard',${id})">Clean up</button>`;
  if(!r.parent_id&&!r.baseline_of&&!v.baseline&&['running','supervising','review'].includes(r.state))
    actions+=` <button class="adp-btn" onclick="_orBaseline(${id})" title="Run the same goal with one developer next to the team -- no plan, no review -- and compare cost, time and result. Up to ${_orMoney(Math.min(5, Math.min(r.budget_usd, 20)*0.85))} more.">Compare with one agent</button>`;
  const p=v.parent;
  const solo=r.baseline_of?`<div class="or-parent">One-agent version of <button class="adp-btn" onclick="_orSelect(${jsq(r.baseline_of)})">← run ${escHtml(r.baseline_of)}</button>
      <span class="or-note">· same goal, one developer, no plan and no review</span></div>`:'';
  const parent=p?`<div class="or-parent">Team of workstream <code>${escHtml(p.task)}</code> in <button class="adp-btn" onclick="_orSelect(${jsq(p.id)})" title="${escHtml(p.goal)}">← run ${escHtml(p.id)}</button>
      ${r.state==='review'?'<span class="or-note">· its work goes onto the workstream branch automatically</span>':''}</div>`:'';
  const waves=(v.waves||[]).map((w,i)=>`<div class="or-wave"><div class="or-wave-h">${i===0?'Start':'Then'}${w.length>1?` · ${w.length} in parallel`:''}</div>
      ${w.map(tid=>v.tasks.find(t=>t.id===tid)).filter(Boolean).map(t=>_orTaskHtml(t, r.id, r.state==='review'&&!r.parent_id&&!mstat?_orPickSet(v):null, r.kind==='workspace')).join('')}</div>`).join('');
  const evs=(v.events||[]).slice().reverse().slice(0,40).map(e=>`<div class="or-ev${e.human?' human':''}"><span>${new Date(e.ts*1000).toLocaleTimeString()}</span>
      <b>${escHtml(e.kind.replace(/_/g,' '))}</b>${e.task_id?` <code>${escHtml(e.task_id)}</code>`:''} ${escHtml(e.message||'')}${e.user?` <i>— ${escHtml(e.user)}</i>`:''}${e.cost_usd?` <i>${_orCost(e.cost_usd,e.cost_est)}</i>`:''}</div>`).join('');
  const lead=r.parent_id?'manager':'orchestrator';
  return `<div class="td-card or-detail">${parent}${solo}
    <div class="or-head">${_orChip(r.state,'run',r.kind)}${r.mode==='hierarchical'?'<span class="or-chip or-c-muted">hierarchical</span>':''}<span class="or-repo"><span class="or-path" title="${escHtml(r.repo)}">${escHtml(_orShortPath(r.repo))}</span> · ${escHtml(r.base)} · ${lead} ${escHtml(r.model)} · budget ${_orMoney(r.budget_usd)} · ${r.max_parallel} parallel</span></div>
    ${_orGoalHtml(r)}
    ${r.plan_summary?`<div class="or-plan"><b>Plan:</b> ${escHtml(r.plan_summary)}${r.single_agent?' <span class="or-chip or-c-done">one agent is enough</span>':''}
      ${r.state==='plan_ready'?` <span class="or-note">· ${v.tasks.length} task(s), up to ${_orMoney(planned)} for the agents</span>`:''}</div>`:''}
    ${r.state==='plan_ready'&&v.estimate?`<div class="or-note or-est">Likely: ~${_orMoney(v.estimate.cost)} and ~${_fmtDurationDHM(v.estimate.seconds)}
      ${v.estimate.from_history?`(from your past tasks of the same agents and models${v.estimate.guessed?`; ${v.estimate.guessed} task(s) without history guessed at half their budget`:''})`:'(no history yet: half of each budget, 3 min per task)'}</div>`:''}
    ${r.state==='scheduled'?`<div class="or-note">Starts ${escHtml(_orWhen(r.start_at))} — the ${lead} plans it then, on the repo as it is at that time; you approve the plan.</div>`:''}
    ${r.state==='planning'?`<div class="or-note">The ${lead} is reading the repo and writing the plan…</div>`:''}
    ${r.state==='supervising'?`<div class="or-note">The ${lead} is reviewing the results…</div>`:''}
    ${r.state==='plan_ready'&&planned>r.budget_usd*0.85+1e-9?`<div class="or-warn">The tasks' budgets (${_orMoney(planned)}) are over 85% of the run's ${_orMoney(r.budget_usd)} — tasks that would go over it are skipped. Raise the run budget or lower theirs.</div>`:''}
    ${r.error?`<div class="or-warn">${escHtml(r.error)}</div>`:''}
    ${mstat&&!r.parent_id?_orMergeHtml(v):''}
    ${actions?`<div class="or-actions">${actions}</div>`:''}
    ${_orMetricsHtml(v.metrics)}
    ${cmp&&cmp.baseline&&(cmp.team.id===r.id||cmp.solo.id===r.id)?_orCompareHtml(cmp, judging):''}
    ${r.report?`<div class="or-report"><h3>Report</h3><pre>${escHtml(r.report)}</pre></div>`:''}
    ${['review','merged','failed'].includes(r.state)&&v.tasks.some(t=>t.commits)?_orCommitsHtml(commits&&commits.run===r.id?commits:null, v):''}
    ${editing&&r.state==='plan_ready'?_orPlanEditorHtml(v, editing===true?null:editing):`<div class="or-waves">${waves}</div>`}
    ${evs?`<details class="or-log" data-k="log"><summary>Log (${v.events.length})</summary>${evs}</details>`:''}
  </div>`;
}

/* ── Entities: autonomous teams with a mission (GET /orch/entities) ── */
var _OR_ENTITY_STATES={idle:['Idle','muted'], scouting:['Looking for work','run'], running:['Working','run'],
  waiting_you:['Needs you','wait'], waiting:['Waiting','wait'], paused:['Paused','muted']};
/* Pure: when it acts, in words. */
function _orScheduleText(cfg){
  const s=(cfg||{}).schedule||{};
  const base=s.type==='every'?`every ${s.hours} h`:s.type==='daily'?`daily at ${s.at}`:s.type==='weekdays'?`on weekdays at ${s.at}`:'when you say';
  return base+((cfg||{}).loop?` · again ${cfg.cooldown_min} min after each run`:'');
}
function _orWhen(epoch){
  if(!epoch) return '—';
  const d=new Date(epoch*1000), now=new Date();
  return (d.toDateString()===now.toDateString()?'':d.toLocaleDateString()+' ')+d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
}
/* Pure: a budget bar. */
function _orBudgetBar(label, spent, limit){
  const pct=Math.min(100, Math.round((Number(spent)||0)/(Number(limit)||1)*100));
  return `<div class="or-bud"><span>${escHtml(label)} ${_orMoney(spent)} / ${_orMoney(limit)}</span><i style="width:${pct}%"${pct>=90?' class="hi"':''}></i></div>`;
}
/* Pure: one entity's card. */
function _orEntityCardHtml(e, agents){
  const cfg=e.config, id=jsq(e.id), [label,cls]=_OR_ENTITY_STATES[e.state]||[e.state,'muted'];
  const names=(cfg.team||[]).map(a=>((agents||[]).find(x=>x.id===a)||{}).name||a);
  const where=cfg.where==='workspace'?`workspace ${cfg.workspace}`:String(cfg.cwd||'').split(/[\\/]/).filter(Boolean).pop()||cfg.cwd;
  const runs=(e.runs||[]).slice(0,5).map(r=>`<button class="or-ent-run" onclick="_orSelect(${jsq(r.id)})">${_orChip(r.state,'run')} ${escHtml(r.goal)}</button>`).join('');
  const mem=(e.memory||[]).map(m=>`<div class="or-ev"><span>${_orWhen(m.ts)}</span><b>${escHtml(m.outcome)}</b> ${escHtml(m.goal)} <i>${_orMoney(m.cost)}</i>${m.summary?` — ${escHtml(m.summary)}`:''}</div>`).join('');
  const evs=(e.events||[]).slice().reverse().slice(0,30).map(x=>`<div class="or-ev"><span>${_orWhen(x.ts)}</span><b>${escHtml(x.kind.replace(/_/g,' '))}</b> ${escHtml(x.message||'')}${x.user?` <i>— ${escHtml(x.user)}</i>`:''}${x.cost_usd?` <i>${_orMoney(x.cost_usd)}</i>`:''}</div>`).join('');
  return `<div class="or-ent or-ent-${escHtml(e.state)}">
    <div class="or-ent-h"><span class="or-chip or-c-${cls}">${escHtml(label)}</span><b>${escHtml(cfg.name)}</b>
      <span class="or-note">${escHtml(where)} · ${escHtml(_orScheduleText(cfg))}</span></div>
    <div class="or-ent-mission">${escHtml(_orClipText(cfg.mission,260))}</div>
    <div class="or-ent-team">${names.map(n=>`<span class="or-chip or-c-muted">${escHtml(n)}</span>`).join(' ')}</div>
    <div class="or-ent-buds">${_orBudgetBar('today',e.spend_day,cfg.budget_day)}${_orBudgetBar('month',e.spend_month,cfg.budget_month)}<span class="or-note">≤ ${_orMoney(cfg.budget_run)} per run</span></div>
    ${e.note?`<div class="${e.state==='waiting'?'or-warn':'or-note'}">${escHtml(e.note)}</div>`:''}
    ${!e.paused&&e.next_due&&['idle','waiting'].includes(e.state)?`<div class="or-note">Next look: ${_orWhen(e.next_due)}</div>`:''}
    ${e.state==='waiting_you'?'<div class="or-warn">A run waits for your merge / accept below.</div>':''}
    <div class="or-actions">
      <button class="adp-btn ln-go" onclick="_orEntityAct('run',${id})"${['scouting','running','waiting_you'].includes(e.state)?' disabled':''} title="Let it look for work now (even when paused)">Run now</button>
      <button class="adp-btn" onclick="_orEntityAct('pause',${id},${e.paused?'false':'true'})">${e.paused?'Resume':'Pause'}</button>
      <button class="adp-btn" onclick="_orEntityEdit(${id})">Edit</button>
      <button class="adp-btn" onclick="_orEntityAct('delete',${id})">Delete</button></div>
    ${runs?`<div class="or-ent-runs">${runs}</div>`:''}
    <details class="or-log" data-k="em:${escHtml(e.id)}"><summary>Memory (${(e.memory||[]).length})${(e.memory||[]).length?` <button class="adp-btn" onclick="event.preventDefault();_orEntityAct('forget',${id})">Forget</button>`:''}</summary>${mem||'<span class="or-note">Nothing yet — each finished run is remembered here.</span>'}</details>
    <details class="or-log" data-k="el:${escHtml(e.id)}"><summary>Log</summary>${evs}</details></div>`;
}
/* Pure: a new / existing entity as an editable draft. */
function _orEntityDraftOf(e){
  if(!e) return {old_id:'', name:'', mission:'', where:'workspace', cwd:'', workspace:'', team:null, model:'opus', mode:'flat',
    max_parallel:2, budget_run:3, budget_day:10, budget_month:100, schedule:{type:'manual'}, loop:false, cooldown_min:10};
  return Object.assign(JSON.parse(JSON.stringify(e.config)), {old_id:e.id});
}
/* Pure: the entity editor. */
function _orEntityEditorHtml(d, agents, workspaces){
  const team=_orTeamOf({team:d.team}, agents), s=d.schedule||{type:'manual'};
  const num=(k,lbl,min,max,step,w)=>`<label class="ln-lbl">${lbl}</label><input class="settings-input" type="number" min="${min}" max="${max}" step="${step}" value="${Number(d[k])||0}" style="width:${w||80}px" oninput="_orEntitySet(${jsq(k)},+this.value)">`;
  const opt=(cur,list)=>list.map(([v,l])=>`<option value="${escHtml(v)}"${String(cur)===String(v)?' selected':''}>${escHtml(l)}</option>`).join('');
  return `<div class="or-agent-ed or-ent-ed">
    <div class="ln-row"><label class="ln-lbl" for="or-en-name">Name</label><input id="or-en-name" class="settings-input or-ag-name" value="${escHtml(d.name||'')}" oninput="_orEntitySet('name',this.value)">
      <label class="ln-lbl">Orchestrator</label><select class="settings-input" onchange="_orEntitySet('model',this.value)">${opt(d.model,[['opus','Opus'],['sonnet','Sonnet'],['haiku','Haiku']])}</select>
      <label class="ln-lbl">Structure</label><select class="settings-input" onchange="_orEntitySet('mode',this.value)">${opt(d.mode,[['flat','One team'],['hierarchical','Hierarchical']])}</select>
      ${num('max_parallel','Parallel',1,4,1,60)}</div>
    <label class="ln-lbl" for="or-en-mission">Mission — what it is for, what good looks like, what it must never do</label>
    <textarea id="or-en-mission" class="settings-input" rows="5" oninput="_orEntitySet('mission',this.value)">${escHtml(d.mission||'')}</textarea>
    <div class="ln-row"><label class="ln-lbl">Works in</label><select class="settings-input" onchange="_orEntitySet('where',this.value,true)">${opt(d.where,[['workspace','A workspace (not code)'],['repo','A code repository']])}</select>
      ${d.where==='workspace'?`<input class="settings-input or-en-cwd" list="or-en-wss" placeholder="workspace name" value="${escHtml(d.workspace||'')}" oninput="_orEntitySet('workspace',this.value)"><datalist id="or-en-wss">${(workspaces||[]).map(w=>`<option value="${escHtml(w.name)}"></option>`).join('')}</datalist>`
        :`<input class="settings-input or-en-cwd" placeholder="C:\\path\\to\\repo" value="${escHtml(d.cwd||'')}" oninput="_orEntitySet('cwd',this.value)">`}</div>
    <div class="or-team-row"><span class="ln-lbl">Team</span>${(agents||[]).map(a=>`<label class="ln-chk or-team-pick"><input type="checkbox"${team.includes(a.id)?' checked':''} onchange="_orEntityTeam(${jsq(a.id)},this.checked)"> ${escHtml(a.name)}</label>`).join('')}</div>
    <div class="ln-row">${num('budget_run','$ per run',0.5,20,0.5)}${num('budget_day','$ per day',0.5,200,1)}${num('budget_month','$ per month',1,2000,5,90)}</div>
    <div class="ln-row"><label class="ln-lbl">Acts</label><select class="settings-input" onchange="_orEntitySched('type',this.value)">${opt(s.type,[['manual','When I say (Run now)'],['every','Every N hours'],['daily','Daily at'],['weekdays','On weekdays at']])}</select>
      ${s.type==='every'?`<input class="settings-input" type="number" min="0.5" max="168" step="0.5" value="${Number(s.hours)||24}" style="width:70px" oninput="_orEntitySched('hours',+this.value)"> <span class="or-note">hours</span>`:''}
      ${s.type==='daily'||s.type==='weekdays'?`<input class="settings-input" type="time" value="${escHtml(s.at||'08:00')}" oninput="_orEntitySched('at',this.value)">`:''}
      <label class="ln-chk"><input type="checkbox"${d.loop?' checked':''} onchange="_orEntitySet('loop',this.checked,true)"> and again after each run</label>
      ${d.loop?num('cooldown_min','min later',1,1440,1,70):''}</div>
    <div class="or-note">It approves its own plans within the per-run budget; merging / accepting results always waits for you. New entities start paused.</div>
    <div class="ln-row"><span class="ln-err" id="or-en-err"></span><span style="flex:1"></span><button class="adp-btn" onclick="_orEntityCancel()">Cancel</button><button class="adp-btn ln-go" onclick="_orEntitySave()">Save entity</button></div></div>`;
}
/* Pure: the entities section. */
function _orEntitiesHtml(entities, draft, agents, workspaces){
  return `<div class="td-card or-ents"><div class="ln-row"><h3 style="margin:0">Entities · ${(entities||[]).length}</h3>
      <span class="or-note">Autonomous teams with a mission: they find their next task, run it, remember the outcome.</span><span style="flex:1"></span>
      ${draft?'':'<button class="adp-btn" onclick="_orEntityEdit(\'\')">+ New entity</button>'}</div>
    ${draft?_orEntityEditorHtml(draft, agents, workspaces):''}
    <div class="or-ent-grid">${(entities||[]).map(e=>_orEntityCardHtml(e, agents)).join('')}</div></div>`;
}

/* ── Scheduled: runs for later + repeating runs (GET /orch/schedules) ── */
/* Pure: one line of a schedule's log -- a skip or a failed start reads as a warning,
   a started run links to it, an edit says what changed. */
var _OR_SCHEDULE_EVS={fired:'started', skipped:'skipped', failed:'could not start', edited:'edited', created:'created', paused:'paused', resumed:'resumed'};
function _orScheduleEvHtml(x){
  const warn=x.kind==='skipped'||x.kind==='failed';
  const run=x.kind==='fired'&&x.run_id?` <button class="or-ent-run or-ev-run" onclick="_orSelect(${jsq(x.run_id)})">run ${escHtml(x.run_id)}</button>`:'';
  return `<div class="or-ev${warn?' warn':''}"><span>${_orWhen(x.ts)}</span><b>${escHtml(_OR_SCHEDULE_EVS[x.kind]||String(x.kind||'').replace(/_/g,' '))}</b> ${escHtml(x.message||'')}${run}${x.user?` <i>— ${escHtml(x.user)}</i>`:''}</div>`;
}
/* Pure: one repeating run's card. */
function _orRepeatCardHtml(s){
  const cfg=s.config||{}, id=jsq(s.id);
  const where=cfg.workspace?`workspace ${cfg.workspace}`:String(cfg.cwd||'').split(/[\\/]/).filter(Boolean).pop()||cfg.cwd||'';
  const last=(s.runs||[])[0];
  const evs=(s.events||[]).slice().reverse().slice(0,20).map(_orScheduleEvHtml).join('');
  return `<div class="or-ent or-sch${s.paused?' or-sch-paused':''}">
    <div class="or-ent-h"><span class="or-chip or-c-${s.paused?'muted':'run'}">${s.paused?'Paused':'Repeats'}</span><b>${escHtml(_orScheduleText(cfg))}</b>
      <span class="or-note">${escHtml(where)} · ≤ ${_orMoney(cfg.budget_usd)} per run${cfg.auto_approve?' · approves its plans':''}</span></div>
    <div class="or-ent-mission">${escHtml(_orClipText(cfg.goal,260))}</div>
    <div class="or-ent-buds">${_orBudgetBar('today',s.spend_day,cfg.budget_day)}${_orBudgetBar('month',s.spend_month,cfg.budget_month)}</div>
    <div class="or-note">${s.paused?'Paused — nothing starts until you resume it.':`Next run: ${_orWhen(s.next_due)}`}</div>
    ${s.last_result?`<div class="${/^(skipped|could not)/.test(s.last_result)?'or-warn':'or-note'}">Last: ${escHtml(s.last_result)}</div>`:''}
    <div class="or-actions">
      ${last?`<button class="or-ent-run" onclick="_orSelect(${jsq(last.id)})">${_orChip(last.state,'run')} last run ${escHtml(last.id)}</button>`:''}<span style="flex:1"></span>
      <button class="adp-btn ln-go" onclick="_orScheduleRun(${id})" title="One run now, behind the same guards (no overlap, nothing waiting for your merge, caps); its clock doesn't move${s.paused?' -- also while paused':''}">Run now</button>
      <button class="adp-btn" onclick="_orScheduleEdit(${id})">Edit</button>
      <button class="adp-btn" onclick="_orScheduleAct('pause',${id},${s.paused?'false':'true'})">${s.paused?'Resume':'Pause'}</button>
      <button class="adp-btn" onclick="_orScheduleAct('delete',${id})">Delete</button></div>
    ${evs?`<details class="or-log" data-k="sl:${escHtml(s.id)}"><summary>Log</summary>${evs}</details>`:''}</div>`;
}
/* Pure: one run for later. */
function _orLaterCardHtml(r){
  const id=jsq(r.id);
  return `<div class="or-ent or-sch">
    <div class="or-ent-h"><span class="or-chip or-c-wait">Later</span><b>${escHtml(_orWhen(r.start_at))}</b>
      <span class="or-note">${escHtml((r.repo||'').split(/[\\/]/).filter(Boolean).pop()||'')}</span></div>
    <div class="or-ent-mission">${escHtml(_orClipText(r.goal,260))}</div>
    <div class="or-actions"><button class="or-ent-run" onclick="_orSelect(${id})">Open</button><span style="flex:1"></span>
      <button class="adp-btn ln-go" onclick="_orLaterAct('start',${id})" title="Start planning now instead of at its time">Start now</button>
      <button class="adp-btn" onclick="_orLaterAct('cancel',${id})">Cancel</button></div></div>`;
}
/* Pure: the Scheduled section ('' when nothing is scheduled). */
function _orScheduledHtml(runs, schedules){
  const later=(runs||[]).filter(r=>r.state==='scheduled').sort((a,b)=>(a.start_at||0)-(b.start_at||0));
  if(!later.length&&!(schedules||[]).length) return '';
  return `<div class="td-card or-ents"><div class="ln-row"><h3 style="margin:0">Scheduled · ${later.length+(schedules||[]).length}</h3>
      <span class="or-note">Runs for later and repeating runs. New ones: the form below, When.</span></div>
    <div class="or-ent-grid">${later.map(_orLaterCardHtml).join('')}${(schedules||[]).map(_orRepeatCardHtml).join('')}</div></div>`;
}

/* Pure: which account pays: the API key or the Claude subscription. */
function _orBillingHtml(set){
  if(!set) return '';
  const opt=(v,l)=>`<option value="${v}"${set.billing===v?' selected':''}>${l}</option>`;
  return `<label class="or-billing" title="API key: ANTHROPIC_API_KEY pays per token. Subscription: your claude.ai login, plan limits apply (entities wait at 90 %). Agents with MCP services always use the subscription.">Runs on
    <select class="settings-input" onchange="_orBilling(this.value)">${opt('api','API key')}${opt('subscription','Claude subscription')}</select></label>
    ${set.billing==='api'&&!set.api_key_set?'<span class="or-warn">no ANTHROPIC_API_KEY set -- Claude Code uses your login anyway</span>':''}`;
}

/* Pure: your house rules -- every agent, the planner and the review get them. draft = unsaved text (null = none). */
function _orRulesHtml(set, draft){
  if(!set) return '';
  const saved=set.rules||'', text=draft==null?saved:draft;
  const n=saved.split('\n').filter(l=>l.trim()).length;
  return `<details class="td-card or-rules" data-k="rules"><summary><h3 style="display:inline-flex">House rules${saved?` · ${n} line${n===1?'':'s'}`:''}</h3>
      <span class="or-note">${saved?'every agent, the planner and the review follow them':'none yet — rules every agent should follow'}</span></summary>
    <textarea id="or-rules" class="settings-input" rows="6" maxlength="4000" placeholder="e.g.&#10;- Never commit secrets or .env files&#10;- Run the tests before you say you are done&#10;- No new dependencies without saying why" oninput="_orRulesSet(this.value)">${escHtml(text)}</textarea>
    <div class="ln-row"><span class="or-note" id="or-rules-n">${text.length} / 4000</span><span style="flex:1"></span>
      <button class="adp-btn ln-go" onclick="_orRulesSave()"${draft==null||draft===saved?' disabled':''}>Save rules</button></div>
    <div class="or-note">Agents already working keep what they started with; new tasks, plans and reviews get the saved rules.</div></details>`;
}

/* Pure: the whole view. */
function _orHtml(st){
  return `<div class="or-top"><h2 class="or-h">Orchestrate</h2><span class="or-note">A goal → a plan of specialised agents → parallel work → review → your merge.</span>
      <span style="flex:1"></span>${_orBillingHtml(st.settings)}</div>
    ${st.err?`<div class="or-warn">${escHtml(st.err)}</div>`:''}
    ${_orWorkersHtml(st.workers, st.hooksHint)}
    ${_orEntitiesHtml(st.entities, st.entityDraft, st.agents, st.workspaces)}
    ${_orScheduledHtml(st.runs, st.schedules)}
    ${_orFormHtml(st.dirs, st.formOpen==null?!st.runs.length:st.formOpen, st.form, st.agents, st.workspaces)}
    ${_orAgentsHtml(st.agents, st.agentDraft, st.catalog, st.agentsOpen||!!st.agentDraft)}
    ${_orRulesHtml(st.settings, st.rulesDraft)}
    ${_orStatsHtml(st.stats, false)}
    <div class="or-grid">${_orRunsHtml(st.runs, st.sel)}${_orDetailHtml(st.view, st.editing?(st.draft||true):false, st.cmp, st.judging, st.commits)}</div>`;
}

function _orRender(force){
  const el=document.getElementById('orch-area');
  if(!el||currentView!=='orch') return;
  /* don't swap the DOM under a form you're typing in */
  const a=document.activeElement;
  if(!force&&a&&el.contains(a)&&['TEXTAREA','INPUT','SELECT'].includes(a.tagName)) return;
  const html=_orHtml(_orState);
  if(el.innerHTML===html) return;
  /* keep what you opened (form, instructions, outputs, log) open across refreshes */
  const was={};
  el.querySelectorAll('details[data-k]').forEach(d=>{ was[d.dataset.k]=d.open; });
  el.innerHTML=html;
  el.querySelectorAll('details[data-k]').forEach(d=>{ if(d.dataset.k in was) d.open=was[d.dataset.k]; });
}

async function _orRefresh(){
  try{
    const lst=await fetch('/orch/runs').then(r=>r.json());
    const runs=lst.runs||[];
    _orState.runs=runs;
    _orState.hooksHint=lst.hooks_hint||'';
    _orState.workers=(typeof lastStatus!=='undefined'&&lastStatus&&lastStatus.orch_workers)||[];
    await _orLoadEntities();
    try{ _orState.schedules=(await fetch('/orch/schedules').then(r=>r.json())).schedules||[]; }catch(e){}
    if(!_orState.sel) _orState.sel=(runs[0]||{}).id||'';
    /* a team's run isn't in the list (only runs you started are) -- keep it while it exists */
    let v=_orState.sel?await fetch('/orch/run?id='+encodeURIComponent(_orState.sel)).then(r=>r.json()):null;
    if((!v||!v.ok)&&runs.length&&_orState.sel!==runs[0].id){
      _orState.sel=runs[0].id;
      v=await fetch('/orch/run?id='+encodeURIComponent(_orState.sel)).then(r=>r.json());
    }
    _orState.view=v&&v.ok?v:null;
    const pair=_orState.view&&(_orState.view.baseline?_orState.view.run.id:_orState.view.run.baseline_of);
    _orState.cmp=pair?await fetch('/orch/compare?id='+encodeURIComponent(pair)).then(r=>r.json()).catch(()=>null):null;
    _orState.err='';
  }catch(e){ _orState.err='Could not load orchestration runs: '+e.message; }
  _orRender();
}

function renderOrchestrate(){
  if(!_orState) return;   // boot (web/js/14) calls setView before this part has run -- see the end of the file
  if(_orTimer){ clearInterval(_orTimer); _orTimer=null; }
  if(currentView!=='orch') return;
  if(!_orState.form) _orState.form=_orFormDefaults();
  _orRender(true);
  if(!_orState.dirs.length) fetch('/launch/dirs').then(r=>r.json()).then(d=>{ _orState.dirs=d.dirs||[]; _orRender(); }).catch(()=>{});
  _orLoadAgents();
  fetch('/orch/settings').then(r=>r.json()).then(d=>{ _orState.settings=d; _orRender(); }).catch(()=>{});
  fetch('/orch/workspaces').then(r=>r.json()).then(d=>{ _orState.workspaces=d.workspaces||[]; _orRender(); }).catch(()=>{});
  _orRefresh();
  _orTimer=setInterval(()=>{ if(currentView!=='orch'){ clearInterval(_orTimer); _orTimer=null; return; } _orRefresh(); }, 3000);
}

function _orSelect(id){ _orState.sel=id; _orState.editing=false; _orState.draft=null; _orRefresh(); }
function _orEdit(on){
  _orState.editing=!!on;
  _orState.draft=on&&_orState.view?_orDraftFrom(_orState.view):null;
  _orRender(true);
}
/* the draft's sum + Save button, live while you type (no redraw: focus stays) */
function _orDraftLive(){
  const d=_orState.draft, v=_orState.view; if(!d||!v) return;
  const chk=_orPlanCheck(d.tasks, v.run.mode==='hierarchical'&&!v.run.parent_id, d.budget);
  const sum=document.getElementById('or-ed-sum');
  if(sum){ sum.textContent=`tasks ${_orMoney(chk.total)} of ${_orMoney(d.budget*0.85)} available to them`; sum.classList.toggle('bad',chk.over); }
  const save=document.querySelector('#orch-area .or-edit .ln-go');
  if(save&&!d.json) save.disabled=!chk.ok;
}
function _orDraftSet(i, field, value, redraw){
  const d=_orState.draft; if(!d||!d.tasks[i]) return;
  d.tasks[i][field]=field==='budget_usd'?(parseFloat(value)||0):value;
  if(field==='role'){   // a manager's budget range differs
    const lim=value==='manager'?_OR_MANAGER_BUDGET:_OR_TASK_BUDGET;
    d.tasks[i].budget_usd=Math.min(Math.max(Number(d.tasks[i].budget_usd)||lim[0],lim[0]),lim[1]);
  }
  if(redraw) _orRender(true); else _orDraftLive();
}
function _orDraftRun(k, value){ if(_orState.draft){ _orState.draft[k]=parseFloat(value)||0; _orDraftLive(); } }
function _orDraftId(i, value){ const d=_orState.draft; if(!d) return; d.tasks=_orDraftRename(d.tasks, i, _orSlug(value)); _orRender(true); }
function _orDraftDep(i, dep, on){
  const t=_orState.draft&&_orState.draft.tasks[i]; if(!t) return;
  t.depends_on=(t.depends_on||[]).filter(x=>x!==dep).concat(on?[dep]:[]);
  _orRender(true);
}
function _orDraftOp(op, i, dir){
  const d=_orState.draft; if(!d) return;
  d.tasks=op==='add'?_orDraftAdd(d.tasks):op==='remove'?_orDraftRemove(d.tasks,i):_orDraftMove(d.tasks,i,dir);
  _orRender(true);
}
/* switch the editor between cards and JSON (JSON that doesn't parse stays put) */
function _orDraftJson(on){
  const d=_orState.draft; if(!d) return;
  if(!on){
    try{ const t=JSON.parse(document.getElementById('or-plan-json').value); if(!Array.isArray(t)) throw new Error('expected a list of tasks'); d.tasks=t; }
    catch(e){ showToast('error','Plan','Not valid JSON: '+e.message); return; }
  }
  d.json=!!on; _orRender(true);
}

async function _orPost(path, body){
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  try{ return await r.json(); }catch(e){ return {ok:false, error:'HTTP '+r.status}; }
}

async function _orCreate(form){
  const err=form.querySelector('#or-err'), f=_orState.form;
  const when=_orWhenBody(f, Date.now());
  if(when.error){ err.textContent=when.error; return; }
  const body=Object.assign({goal:String(f.goal||'').trim(), model:f.model, max_parallel:+f.par,
    cwd:f.where==='workspace'?'':String(f.cwd||'').trim(), workspace:f.where==='workspace'?String(f.workspace||'').trim():'',
    budget_usd:+f.budget, mode:f.mode, approve_teams:f.mode==='hierarchical'&&!!f.teams, team:_orTeamOf(f, _orState.agents)}, when);
  const repeat=f.when==='repeat';
  if(f.sch_id){   // an existing schedule edited: same id, where it works stays (the server keeps it)
    err.textContent='Saving…';
    const r=await _orPost('/orch/schedule_save', Object.assign(body, {id:f.sch_id}));
    if(!r.ok){ err.textContent=r.error||'failed'; return; }
    err.textContent='';
    _orScheduleEditCancel();
    showToast((r.changed||[]).length?'success':'info','Schedule',_orScheduleSavedText(r));
    await _orRefresh(); _orRender(true);
    return;
  }
  err.textContent=repeat?'Saving the schedule…':when.start_at?'Scheduling…':'Starting the orchestrator…';
  const r=await _orPost(repeat?'/orch/schedule_save':'/orch/create', body);
  if(!r.ok){ err.textContent=r.error||'failed'; return; }
  err.textContent='';
  _orState.form=Object.assign(_orFormDefaults(), {cwd:f.cwd, where:f.where, workspace:f.workspace, model:f.model, par:f.par, budget:f.budget, mode:f.mode, team:f.team});
  _orState.formOpen=false;
  if(repeat) showToast('success','Scheduled',`First run ${_orWhen(r.next_due)} — see Scheduled.`);
  else{
    _orState.sel=r.id; _orState.editing=false; _orState.draft=null;
    showToast('info','Orchestration', when.start_at?`It starts ${_orWhen(when.start_at)} — you approve the plan before anything runs.`
      :'The orchestrator is planning — you approve the plan before anything runs.');
  }
  await _orRefresh(); _orRender(true);
}

/* Pure: what a confirm() says before a run-changing action ('' = no confirm). */
function _orConfirmText(action, v){
  const r=(v||{}).run||{};
  const devs=((v||{}).tasks||[]).filter(t=>t.state==='done'&&t.commits);
  const other=(v||{}).baseline||r.baseline_of;
  const drop=other?`\n\nThe other version (run ${other}) is stopped and its work dropped.`:'';
  if(action==='merge'&&r.kind==='workspace') return `Keep the files from ${devs.length} task(s) in the workspace ${String(r.repo||'').split(/[\\/]/).pop()}?\n\n`+devs.map(t=>`• ${t.id}: ${t.title}`).join('\n')+drop;
  if(action==='merge') return `Merge ${devs.length} developer branch(es) into ${r.base} in ${r.repo}?\n\n`+devs.map(t=>`• ${t.id}: ${t.title} (${t.commits} commit(s))`).join('\n')+drop;
  const stays=other?`\n\nThe other version (run ${other}) stays -- merge it or discard it on its own.`:'';
  if(action==='discard') return 'Delete every branch and worktree of this run? What the agents did is lost.'+stays;
  if(action==='cancel') return 'Stop every agent of this run and remove their worktrees?'+stays;
  return '';
}

async function _orAct(action, id){
  const v=_orState.view;
  const all=v?(v.tasks||[]).filter(t=>t.state==='done'&&t.commits&&t.branch).map(t=>t.id):[];
  const pick=v&&action==='merge'?[..._orPickSet(v)]:null;
  const subset=pick&&pick.length<all.length;
  const q=subset?`Keep only ${pick.join(', ')} and drop the work of ${all.filter(i=>!pick.includes(i)).join(', ')}?`:_orConfirmText(action, v);
  if(q&&!confirm(q)) return;
  const r=await _orPost('/orch/'+action, subset?{id, only:pick}:{id});
  if(!r.ok) showToast('error','Orchestration',r.error||'failed');
  else if(action==='merge') showToast('success','Merged',`${(r.merged||[]).length} branch(es) merged`);
  _orState.editing=false; _orState.draft=null;
  await _orRefresh(); _orRender(true);
}

/* Pure: the confirm text of going on after a merge stopped at a conflict
   (how: 'retry' | 'rest' | 'finish'). */
function _orMergeGoText(how, v){
  const ms=_orMergeStatus(v)||{merged:[], open:[]}, r=(v||{}).run||{};
  const into=r.kind==='workspace'?'the workspace':r.base;
  if(how==='rest') return `Merge every other branch that goes into ${into} cleanly? The conflicting ones and the tasks built on them wait.`;
  if(how==='finish') return `Finish with the ${ms.merged.length} merged task(s) (${ms.merged.join(', ')})?`
    +(ms.open.length?`\n\nThe rest is dropped and its branches deleted: ${ms.open.join(', ')}.`:'');
  return '';
}
async function _orMergeGo(id, how){
  const q=_orMergeGoText(how, _orState.view);
  if(q&&!confirm(q)) return;
  const r=await _orPost('/orch/merge', how==='rest'?{id, rest:true}:how==='finish'?{id, finish:true}:{id});
  if(!r.ok) showToast('error','Orchestration',r.error||'failed');
  else showToast('success','Merged',`${(r.merged||[]).length} branch(es) merged`+((r.dropped||[]).length?`, ${r.dropped.length} left out`:''));
  await _orRefresh(); _orRender(true);
}

async function _orSavePlan(id){
  const d=_orState.draft; if(!d) return;
  let tasks=d.tasks;
  if(d.json){
    try{ tasks=JSON.parse(document.getElementById('or-plan-json').value); }
    catch(e){ showToast('error','Plan','Not valid JSON: '+e.message); return; }
  }
  const r=await _orPost('/orch/plan', {id, tasks, budget_usd:d.budget, max_parallel:d.par});
  if(!r.ok){ showToast('error','Plan',r.error||'failed'); return; }
  _orState.editing=false; _orState.draft=null;
  await _orRefresh(); _orRender(true);
}

async function _orBilling(v){
  const r=await _orPost('/orch/settings', {billing:v});
  if(!r.ok){ showToast('error','Orchestrate',r.error||'failed'); return; }
  _orState.settings=r;
  showToast('info','Orchestrate', v==='subscription'?'Runs now use your Claude subscription (plan limits apply).':'Runs now use the API key.');
  _orRender(true);
}

/* typing in the house rules: no redraw (focus stays), just the counter and Save */
function _orRulesSet(v){
  _orState.rulesDraft=v;
  const n=document.getElementById('or-rules-n'); if(n) n.textContent=`${(v||'').length} / 4000`;
  const b=document.querySelector('#orch-area .or-rules .ln-go'); if(b) b.disabled=v===((_orState.settings||{}).rules||'');
}
async function _orRulesSave(){
  const r=await _orPost('/orch/settings', {rules:_orState.rulesDraft||''});
  if(!r.ok){ showToast('error','House rules',r.error||'failed'); return; }
  _orState.settings=r; _orState.rulesDraft=null;
  showToast('success','House rules', r.rules?'Saved — new tasks, plans and reviews follow them.':'Cleared.');
  _orRender(true);
}

/* Pure: the Inbox block for what orchestration waits on you for (sr.orch_attention). */
var _OR_ATTN={plan:['Plan ready','Approve the plan to start the agents'], team_plan:['Team plan ready','A manager planned its team'],
  gate:['Sensitive task','Waits for your OK before it starts'], review:['Results ready','Review, then merge or accept']};
function _orInboxHtml(items){
  if(!(items||[]).length) return '';
  const row=i=>{
    const [what,hint]=_OR_ATTN[i.kind]||[i.kind,''];
    const run=jsq(i.run_id);
    const btns=i.kind==='gate'?`<button class="adp-btn ln-go" onclick="_orGate(${run},${jsq(i.task_id)},true)">Allow</button><button class="adp-btn" onclick="_orGate(${run},${jsq(i.task_id)},false)">Skip</button>`
      :(i.kind==='plan'||i.kind==='team_plan')?`<button class="adp-btn ln-go" onclick="_orInboxApprove(${run})">Approve</button>`:'';
    return `<div class="or-ib"><div class="or-ib-h"><span class="or-chip or-c-wait">${escHtml(what)}</span><b>${escHtml(i.title)}</b></div>
      <div class="or-note">${escHtml(hint)}${i.detail?` · ${escHtml(i.detail)}`:''}${i.entity?` · entity ${escHtml(i.entity)}`:''}</div>
      <div class="or-actions">${btns}<button class="adp-btn" onclick="_orOpenRun(${run})">Open</button></div></div>`;
  };
  return `<div class="td-card or-inbox"><h3>Orchestration · ${items.length}</h3>${items.map(row).join('')}</div>`;
}
/* Pure: an Orchestrate worker's run / task, from its tag or (hooks from
   before the tag) its worktree slot "orch-<MMDD-HHMMSS run>-<task>-<attempt>". */
function _orWorkerRef(w){
  w=w||{};
  let run=String(w.run||''), task=String(w.task||'');
  const m=!run&&/^orch-(\d{4}-\d{6})-(.+)-(\d+)$/.exec(String(w.slot||''));
  if(m){ run=m[1]; task=m[2]; }
  return {run, task, entity:run.startsWith('entity-')?run.slice(7):''};
}
/* Pure: the Inbox block for a held dangerous command of one of
   Orchestrate's own agents (/status orch_holds). Its run is not a session
   of yours, but the danger guard holds the command until you decide. */
function _orHoldsHtml(holds){
  if(!(holds||[]).length) return '';
  const row=q=>{
    const ref=_orWorkerRef(q);
    const who=ref.entity?`entity ${ref.entity} · scout`:[ref.run?'run '+ref.run:'', ref.task?'task '+ref.task:''].filter(Boolean).join(' · ')||'an Orchestrate agent';
    const call=d=>`_permDecide('',${jsq(q.id)},'${d}')`;
    const open=ref.run&&!ref.entity?`<button class="adp-btn" onclick="_orOpenRun(${jsq(ref.run)})">Open run</button>`:'';
    return `<div class="or-ib"><div class="or-ib-h"><span class="or-chip or-c-wait">${q.danger?'Dangerous command':'Needs your OK'}</span><b>${escHtml(who)}</b></div>
      <div class="perm-sum">${escHtml(q.summary||'')}</div>
      <div class="or-note">${q.danger?escHtml(q.danger):''}${q.age_s?`${q.danger?' · ':''}held ${escHtml(_fmtDurShort(q.age_s))}`:''}</div>
      ${q.detail?`<details class="perm-detail"><summary>Details</summary><pre>${escHtml(q.detail)}</pre></details>`:''}
      <div class="or-actions"><button class="perm-btn allow" onclick="${call('allow')}">${q.danger?'Run it':'Allow'}</button>
        <button class="perm-btn deny" onclick="${call('deny')}">Deny…</button>${open}</div></div>`;
  };
  return `<div class="td-card or-inbox or-holds"><h3>Orchestrate agents · ${holds.length} held</h3>${holds.map(row).join('')}</div>`;
}
/* Pure: the Orchestrate view's line about its own running agents (/status
   orch_workers -- they are kept out of your sessions) and outdated hooks. */
function _orWorkersHtml(workers, hint){
  const live=(workers||[]).filter(w=>w&&w.session_active!==false);
  const cost=live.reduce((t,w)=>t+(Number(w.estimated_cost)||0),0);
  const runs=new Set(live.map(w=>_orWorkerRef(w).run).filter(Boolean)).size;
  const line=live.length?`<div class="or-note or-workers">${live.length} agent${live.length===1?'':'s'} working${runs?` in ${runs} run${runs===1?'':'s'}`:''} · ${escHtml(_orMoney(cost))} so far · not listed as your sessions</div>`:'';
  return line+(hint?`<div class="or-warn">${escHtml(hint)} <button class="adp-btn" onclick="setView('diag')">Health</button></div>`:'');
}
function _orOpenRun(id){ _orState.sel=id; _orState.editing=false; _orState.draft=null; setView('orch'); }
async function _orGate(runId, taskId, allow){
  if(!allow&&!confirm('Skip this task? The tasks after it are skipped too.')) return;
  const r=await _orPost('/orch/gate', {id:runId, task:taskId, allow:!!allow});
  if(!r.ok) showToast('error','Orchestration',r.error||'failed');
  if(currentView==='orch'){ await _orRefresh(); _orRender(true); }
}
async function _orInboxApprove(runId){
  const r=await _orPost('/orch/approve', {id:runId});
  if(!r.ok) showToast('error','Orchestration',r.error||'failed');
  else showToast('success','Plan approved','The agents start in a few seconds.');
}

/* Pure: the stats card (GET /orch/stats, loaded when opened). */
function _orStatsHtml(st, open){
  const pct=x=>x==null?'—':Math.round(x*100)+'%';
  const table=(rows,first)=>`<table class="cache-table or-stats-t"><tr><th>${first}</th><th>tasks</th><th>success</th><th>avg $</th><th>avg time</th><th>retries</th></tr>
    ${rows.map(r=>`<tr><td>${escHtml(r.name)}</td><td>${r.n}</td><td>${pct(r.success)}</td><td>${_orMoney(r.avg_cost)}</td><td>${r.avg_s==null?'—':_fmtDurShort(Math.round(r.avg_s))}</td><td>${r.retries}</td></tr>`).join('')}</table>`;
  const body=!st?'<span class="or-note">Loading…</span>':!st.runs?'<span class="or-note">No runs yet.</span>':`
    <div class="or-metrics">${[['Runs',String(st.runs)],['Kept',pct(st.kept_rate)],['Cost',_orMoney(st.cost)],['Avg run',_orMoney(st.avg_cost)],
      ['Coordination',pct(st.coordination_share)],['Avg time',st.avg_s==null?'—':_fmtDurationDHM(st.avg_s)]].map(([k,v])=>`<div><b>${escHtml(v)}</b><span>${k}</span></div>`).join('')}</div>
    <table class="cache-table or-stats-t"><tr><th>kind of run</th><th>runs</th><th>kept</th><th>dropped</th><th>avg $</th></tr>
      ${(st.by_kind||[]).map(k=>`<tr><td>${escHtml(k.name)}</td><td>${k.runs}</td><td>${k.kept}</td><td>${k.dropped}</td><td>${_orMoney(k.avg_cost)}</td></tr>`).join('')}</table>
    ${table(st.by_agent||[],'agent')}${table(st.by_model||[],'model')}
    <div class="or-note">Kept = merged / accepted by you out of the runs you decided on. A kind of run, agent or model that costs more without being kept more is a candidate to drop (PDF ch. 9).</div>`;
  return `<details class="td-card or-statsc" data-k="stats"${open?' open':''}><summary onclick="_orStatsToggle(this)"><h3 style="display:inline-flex">Stats · last ${st&&st.days||90} days</h3></summary>${body}</details>`;
}
function _orStatsToggle(sum){
  if(!sum.parentNode.open) fetch('/orch/stats').then(r=>r.json()).then(d=>{ _orState.stats=d; _orRender(true); }).catch(()=>{});
}

/* The same goal with one agent, next to the team (POST /orch/baseline). */
async function _orBaseline(id){
  if(!confirm('Run the same goal with one developer next to the team -- no plan, no review -- to compare cost, time and result?\n\nIt costs that agent\'s budget on top of the team\'s.')) return;
  const r=await _orPost('/orch/baseline', {id});
  if(!r.ok){ showToast('error','Orchestration',r.error||'failed'); return; }
  showToast('info','Comparing','One agent works on the same goal: run '+r.id);
  _orRefresh();
}
/* Claude compares both results (POST /orch/judge -- takes up to a few minutes). */
async function _orJudge(id){
  _orState.judging=true; _orRender(true);
  try{
    const r=await _orPost('/orch/judge', {id});
    if(!r.ok) showToast('error','Compare',r.error||'failed');
  }finally{ _orState.judging=false; }
  _orRefresh();
}

/* What you keep of a finished run (all by default); a subset merges only those. */
function _orPickSet(v){
  const all=(v.tasks||[]).filter(t=>t.state==='done'&&t.commits&&t.branch).map(t=>t.id);
  const p=_orState.pick;
  return new Set(p&&p.run===v.run.id?p.ids.filter(i=>all.includes(i)):all);
}
function _orPick(tid, on){
  const v=_orState.view; if(!v) return;
  const ids=[..._orPickSet(v)].filter(i=>i!==tid).concat(on?[tid]:[]);
  _orState.pick={run:v.run.id, ids};
  _orRender(true);
}

/* ── entity actions ── */
async function _orLoadEntities(){
  try{ _orState.entities=(await fetch('/orch/entities').then(r=>r.json())).entities||[]; }catch(e){}
}
function _orEntityEdit(id){
  const e=(_orState.entities||[]).find(x=>x.id===id);
  _orState.entityDraft=_orEntityDraftOf(id?e:null);
  _orRender(true);
}
function _orEntitySet(k, v, redraw){ const d=_orState.entityDraft; if(!d) return; d[k]=v; if(redraw) _orRender(true); }
function _orEntitySched(k, v){
  const d=_orState.entityDraft; if(!d) return;
  d.schedule=Object.assign({}, d.schedule||{}, {[k]:v});
  if(k==='type'){
    if(v==='every'&&!d.schedule.hours) d.schedule.hours=24;
    if((v==='daily'||v==='weekdays')&&!d.schedule.at) d.schedule.at='08:00';
    _orRender(true);
  }
}
function _orEntityTeam(id, on){
  const d=_orState.entityDraft; if(!d) return;
  const team=_orTeamOf({team:d.team}, _orState.agents).filter(x=>x!==id);
  d.team=on?team.concat([id]):team;
  _orRender(true);
}
function _orEntityCancel(){ _orState.entityDraft=null; _orRender(true); }
async function _orEntitySave(){
  const d=_orState.entityDraft; if(!d) return;
  const body=Object.assign({}, d, {team:_orTeamOf({team:d.team}, _orState.agents)});
  const r=await _orPost('/orch/entity_save', body);
  if(!r.ok){ const e=document.getElementById('or-en-err'); if(e) e.textContent=r.error||'failed'; return; }
  _orState.entityDraft=null;
  showToast('success','Entity saved',`${r.entity.name}${d.old_id?'':' — starts paused: Resume or Run now'}`);
  await _orLoadEntities(); _orRender(true);
}
async function _orEntityAct(action, id, paused){
  if(action==='delete'&&!confirm(`Delete the entity '${id}'? Its runs stay; its memory and log go.`)) return;
  if(action==='forget'&&!confirm('Clear what this entity remembers about its past runs?')) return;
  const r=await _orPost('/orch/entity_'+action, {id, paused});
  if(!r.ok) showToast('error','Entity',r.error||'failed');
  else if(action==='run') showToast('info','Entity','It will look for work within a few seconds.');
  await _orLoadEntities(); _orRender(true);
}

/* ── scheduled actions ── */
async function _orLaterAct(action, id){
  if(action==='cancel'&&!confirm('Cancel this run for later? It never starts.')) return;
  const r=await _orPost('/orch/'+action, {id});
  if(!r.ok){ showToast('error','Scheduled',r.error||'failed'); return; }
  if(action==='start'){ _orState.sel=id; showToast('info','Orchestration','The orchestrator is planning — you approve the plan before anything runs.'); }
  await _orRefresh(); _orRender(true);
}
/* Run now: one run of the schedule, its clock stays. Refused -> why (a run still going or waiting for
   your merge, a cap, plan limits); started -> that run opens below. */
async function _orScheduleRun(id){
  const r=await _orPost('/orch/schedule_run', {id});
  if(!r.ok) showToast('warning','Schedule',r.error||'failed',9000);
  else{
    _orState.sel=r.id; _orState.editing=false; _orState.draft=null;
    showToast('info','Schedule',`Started run ${r.id} — it's open below.`);
  }
  await _orRefresh(); _orRender(true);
  if(r.ok){ const el=document.querySelector('#orch-area .or-grid'); if(el&&el.scrollIntoView) el.scrollIntoView({behavior:'smooth', block:'start'}); }
}
/* Edit: the form above the agents, filled from the schedule (what you were typing into it waits until you're done) */
function _orScheduleEdit(id){
  const s=(_orState.schedules||[]).find(x=>x.id===id); if(!s) return;
  if(!(_orState.form||{}).sch_id) _orState.formStash=_orState.form;
  _orState.form=_orFormFromSchedule(s); _orState.formOpen=true;
  _orRender(true);
  const el=document.getElementById('or-new'); if(el&&el.scrollIntoView) el.scrollIntoView({behavior:'smooth', block:'start'});
}
function _orScheduleEditCancel(){
  _orState.form=_orState.formStash||_orFormDefaults(); _orState.formStash=null; _orState.formOpen=false;
  _orRender(true);
}
async function _orScheduleAct(action, id, paused){
  if(action==='delete'&&!confirm('Delete this schedule? The runs it started stay.')) return;
  const r=await _orPost('/orch/schedule_'+action, {id, paused});
  if(!r.ok) showToast('error','Scheduled',r.error||'failed');
  await _orRefresh(); _orRender(true);
}

/* ── agent library actions ── */
async function _orLoadAgents(){
  try{ _orState.agents=(await fetch('/orch/agents').then(r=>r.json())).agents||[]; _orRender(); }catch(e){}
}
/* the catalog (MCP servers, skills) -- polled while `claude mcp list` runs in the background */
async function _orLoadCatalog(fresh){
  try{
    _orState.catalog=await fetch('/orch/catalog'+(fresh?'?fresh=1':'')).then(r=>r.json());
    _orRender();
    if(_orState.catalog.scanning&&_orState.agentDraft) setTimeout(()=>_orLoadCatalog(false), 4000);
  }catch(e){}
}
/* Pure: a new draft -- empty, an agent to edit, or a copy of one (dup). */
function _orAgentDraftOf(a, dup){
  if(!a) return {old_id:'', id:'', name:'', brief:'', tools:['Read','Grep','Glob'], model:'sonnet', budget_usd:1, mcp:[], skills:[], sensitive:false};
  const d=JSON.parse(JSON.stringify(a));
  delete d.builtin;
  if(dup){ d.old_id=''; d.id=a.id+'-copy'; d.name=a.name+' (copy)'; } else d.old_id=a.id;
  return d;
}
function _orAgentEdit(id, dup){
  const a=(_orState.agents||[]).find(x=>x.id===id);
  _orState.agentDraft=_orAgentDraftOf(id?a:null, dup);
  _orState.agentsOpen=true;
  _orRender(true);
  _orLoadCatalog(false);
}
function _orAgentSet(k, v, redraw){
  const d=_orState.agentDraft; if(!d) return;
  d[k]=v;
  if(redraw){ _orRender(true); const q=document.querySelector('#orch-area .or-ag-q'); if(q){ q.focus(); q.setSelectionRange(q.value.length,q.value.length); } }
}
function _orAgentToggle(field, name, on){
  const d=_orState.agentDraft; if(!d) return;
  d[field]=(d[field]||[]).filter(x=>x!==name).concat(on?[name]:[]);
  if(field==='mcp') _orRender(true);   // the subscription note
}
function _orAgentCancel(){ _orState.agentDraft=null; _orRender(true); }
async function _orAgentSave(){
  const d=_orState.agentDraft; if(!d) return;
  const body=Object.assign({}, d); delete body._skillQ;
  const r=await _orPost('/orch/agent_save', body);
  if(!r.ok){ const e=document.getElementById('or-ag-err'); if(e) e.textContent=r.error||'failed'; return; }
  _orState.agentDraft=null;
  showToast('success','Agent saved',r.agent.name);
  await _orLoadAgents();
}
async function _orAgentDelete(id){
  if(!confirm(`Delete the agent '${id}'? Runs already planned keep their copy of it.`)) return;
  const r=await _orPost('/orch/agent_delete', {id});
  if(!r.ok) showToast('error','Agents',r.error||'failed');
  await _orLoadAgents();
}

/* opened at load (the last view you used): draw it now that this part has run */
if(currentView==='orch') renderOrchestrate();
