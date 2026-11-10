/* ── settings ── */
const _ACCENT_VARS = {
  signal: { c:'63,208,255', c2:'30,136,200' },
  cyan:   { c:'0,196,232', c2:'0,150,200' },
  purple: { c:'140,80,255', c2:'100,60,220' },
  green:  { c:'0,220,120', c2:'0,170,90' },
  orange: { c:'255,140,40', c2:'220,100,20' },
  pink:   { c:'255,80,180', c2:'220,50,150' },
};
let _settingsOpen = false;
let _costRate = parseFloat(localStorage.getItem('aoc_cost_rate') || '9');
/* Real per-model cost (set server-side in /update from the hook's granular
   usage+model, monitor.py's _calc_cost — the same math the KPI gauge already
   trusts) when available, falling back to the flat _costRate guess for
   agents/history predating that fix. Function-hoisted so it's safe to use
   from code defined earlier in this file than _costRate's declaration. */
function _agentCost(a){
  return a.estimated_cost!=null ? a.estimated_cost : (a.tokens_used||0)/1_000_000*_costRate;
}
let _budgetLimit = parseFloat(localStorage.getItem('aoc_budget') || '0');
let _projectBudgets = (()=>{ try{ return JSON.parse(localStorage.getItem('aoc_project_budgets')||'{}'); }catch(e){ return {}; } })();
let _webhookUrl = localStorage.getItem('aoc_webhook_url') || '';
let _webhookEvents = JSON.parse(localStorage.getItem('aoc_webhook_events') || '{"done":true,"error":true,"stuck":false,"burn_spike":false,"weekly_digest":false,"waiting_nudge":false,"cost_spike":false,"budget_alert":false,"rate_limit":false}');
let _quietStart = localStorage.getItem('aoc_quiet_start') || '';
let _quietEnd = localStorage.getItem('aoc_quiet_end') || '';
let _mutedProjects = (()=>{ try{ return JSON.parse(localStorage.getItem('aoc_muted_projects')||'[]'); }catch(e){ return []; } })();
let _snitchUrl = localStorage.getItem('aoc_snitch_url') || '';
let _digestCadence = localStorage.getItem('aoc_digest_cadence') || 'weekly';
let _backupRetentionDays = parseInt(localStorage.getItem('aoc_backup_retention_days') || '14', 10) || 14;
let _agentRetentionHours = parseInt(localStorage.getItem('aoc_agent_retention_hours') || '12', 10) || 12;
async function _snitchPingNow(){
  const v=n=>document.getElementById(n);
  const url=v('st-snitch-url')?.value?.trim()||'';
  const msgEl=document.getElementById('st-snitch-status');
  if(!url){ if(msgEl){msgEl.style.color='var(--r)';msgEl.textContent='✗ no URL entered';} return; }
  if(msgEl){msgEl.style.color='var(--t3)';msgEl.textContent='…pinging';}
  try{
    const r=await fetch('/snitch_ping_now',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});
    const result=await r.json();
    if(msgEl) result.ok?(msgEl.style.color='var(--g)',msgEl.textContent='✓ ping sent'):(msgEl.style.color='var(--r)',msgEl.textContent='✗ '+(result.error||'failed'));
  }catch(e){
    if(msgEl){msgEl.style.color='var(--r)';msgEl.textContent='✗ request failed: '+e.message;}
  }
}
function _isNotifySuppressed(project){
  /* Mirrors monitor.py's _notification_suppressed exactly -- same two
     checks (explicit per-project mute, then quiet-hours window with
     overnight wraparound), kept in sync by hand since the client and the
     two server-side workers (_headless_notify_worker, _webhook_notify_worker)
     all need to agree on "should this notify right now". */
  if(project && _mutedProjects.includes(project)) return true;
  if(!_quietStart || !_quietEnd) return false;
  const now = new Date();
  const hhmm = String(now.getHours()).padStart(2,'0')+':'+String(now.getMinutes()).padStart(2,'0');
  return _quietStart <= _quietEnd ? (hhmm >= _quietStart && hhmm < _quietEnd) : (hhmm >= _quietStart || hhmm < _quietEnd);
}

/* ── multi-machine view ──
   Client-side only, by design: each remote is just another AOC instance's
   own unmodified /status endpoint (already CORS-open, already token-gated),
   fetched and merged in this tab. Zero backend changes on either machine. */
let _remoteMachines = (()=>{ if(!window._AOC_IS_PRO) return []; try{ return JSON.parse(localStorage.getItem('aoc_remote_machines')||'[]'); }catch(e){ return []; } })();
let _remoteMachinesDraft = [];  // settings-panel working copy; committed to _remoteMachines only on Save (Cancel discards, matching every other settings field's contract)
let _localMachineName = localStorage.getItem('aoc_local_machine_name') || '';
let _remoteCache = {};  // idx -> {data, ok, lastFetch}

function _renderRemoteMachinesRows(){
  const el=document.getElementById('remote-machines-list');
  if(!el) return;
  if(!_remoteMachinesDraft.length){
    el.innerHTML='<div style="font-size:9px;color:var(--t3);font-family:var(--font2)">No machines added.</div>';
    return;
  }
  el.innerHTML=_remoteMachinesDraft.map((m,i)=>`
    <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
      <input class="settings-input" style="flex:1;min-width:90px" placeholder="Name (e.g. Laptop)" value="${escHtml(m.name||'')}" oninput="_updateRemoteMachine(${i},'name',this.value)">
      <input class="settings-input" style="flex:2;min-width:140px" placeholder="http://192.168.1.50:5151" value="${escHtml(m.url||'')}" oninput="_updateRemoteMachine(${i},'url',this.value)">
      <input class="settings-input" type="password" style="flex:1;min-width:70px" placeholder="token" value="${escHtml(m.token||'')}" oninput="_updateRemoteMachine(${i},'token',this.value)">
      <button class="adp-btn" onclick="_testRemoteMachine(${i})" style="font-size:9px;padding:3px 8px;white-space:nowrap">TEST</button>
      <button class="adp-btn" onclick="_removeRemoteMachine(${i})" style="font-size:9px;padding:3px 8px;color:var(--r)">✕</button>
      <span id="rm-test-msg-${i}" style="font-size:9px;font-family:var(--font2);width:100%"></span>
    </div>`).join('');
}
function _addRemoteMachineRow(){
  _remoteMachinesDraft.push({name:'', url:'', token:''});
  _renderRemoteMachinesRows();
}
function _removeRemoteMachine(i){
  _remoteMachinesDraft.splice(i,1);
  _renderRemoteMachinesRows();
}
function _updateRemoteMachine(i,field,val){
  if(_remoteMachinesDraft[i]) _remoteMachinesDraft[i][field]=val;
}
async function _testRemoteMachine(i){
  const m=_remoteMachinesDraft[i]; if(!m) return;
  const msgEl=document.getElementById('rm-test-msg-'+i);
  const setMsg=(color,text)=>{ if(msgEl){ msgEl.style.color=color; msgEl.textContent=text; } };
  if(!m.url||!m.url.trim()){ setMsg('var(--r)','✗ missing URL'); return; }
  setMsg('var(--t3)','…testing');
  try{
    const base=m.url.trim().replace(/\/$/,'');
    const url=base+'/status'+(m.token?('?token='+encodeURIComponent(m.token)):'');
    const r=await fetch(url,{signal:AbortSignal.timeout(4000)});
    if(r.status===401){ setMsg('var(--r)','✗ 401 — generate a token on that machine (REMOTE ACCESS)'); return; }
    if(!r.ok){ setMsg('var(--r)','✗ HTTP '+r.status); return; }
    const d=await r.json();
    setMsg('var(--g)',`✓ ${(d.sessions_list||[]).length} sessions, ${(d.agents||[]).length} agents`);
  }catch(e){
    setMsg('var(--r)','✗ unreachable (check URL/network)');
  }
}

let _accentName = localStorage.getItem('aoc_accent') || 'signal';
let _density = localStorage.getItem('aoc_density') || 'normal';

function _applyAccent(name){
  const v=_ACCENT_VARS[name];
  if(!v) return;
  const r=document.documentElement.style;
  r.setProperty('--c', `rgba(${v.c},1)`);
  r.setProperty('--c2', `rgba(${v.c2},1)`);
  r.setProperty('--c-rgb', v.c);
  r.setProperty('--c2-rgb', v.c2);
  document.querySelectorAll('.accent-swatch').forEach(s=>s.classList.toggle('active', s.dataset.accent===name));
}
function _applyDensity(d){
  const cards = document.getElementById('agents');
  if(cards){
    cards.style.gap = d==='compact'?'6px':d==='comfortable'?'18px':'10px';
    cards.classList.toggle('density-compact', d==='compact');
    cards.classList.toggle('density-comfortable', d==='comfortable');
  }
}
function setAccent(name){ _accentName=name; _applyAccent(name); }

/* ── auth panel helpers ── */
async function _authRefresh(){
  try{
    const d=await fetch('/auth/info').then(r=>r.json());
    const lbl=document.getElementById('auth-status-lbl');
    const urlRow=document.getElementById('auth-url-row');
    const urlInput=document.getElementById('auth-url-input');
    const tokenDisplay=document.getElementById('auth-token-display');
    const genBtn=document.getElementById('auth-gen-btn');
    const delBtn=document.getElementById('auth-del-btn');
    const note=document.getElementById('auth-note');
    if(!lbl) return;
    if(d.enabled){
      lbl.textContent=`Remote access ENABLED — ${d.ip}:5151`;
      lbl.style.color='var(--g)';
      urlRow.style.display='flex';
      if(urlInput) urlInput.value=d.url||'';
      if(tokenDisplay) tokenDisplay.textContent=d.token||'';
      if(genBtn) genBtn.textContent='↻ REGENERATE';
      if(delBtn) delBtn.style.display='inline-flex';
      if(note) note.textContent='Share the URL above with any device on your local network.';
    } else {
      lbl.textContent='Remote access DISABLED — localhost only';
      lbl.style.color='var(--t3)';
      if(urlRow) urlRow.style.display='none';
      if(genBtn) genBtn.textContent='⚡ GENERATE TOKEN';
      if(delBtn) delBtn.style.display='none';
      if(note) note.textContent='Generate a token to enable access from phone or another PC on the same network. Restart the monitor after first enable.';
    }
  }catch(e){}
}
async function _authGenerate(){
  try{
    await fetch('/auth/token',{method:'POST'});
    await _authRefresh();
    showToast('info','Network access token generated — restart monitor to apply bind change');
  }catch(e){ showToast('error','Failed to generate token'); }
}
async function _authDisable(){
  if(!confirm('Disable remote access? Anyone with the URL will lose access.')) return;
  try{
    await fetch('/auth/token',{method:'DELETE'});
    await _authRefresh();
    showToast('info','Remote access disabled');
  }catch(e){}
}
function _authCopyUrl(){
  const el=document.getElementById('auth-url-input');
  if(!el) return;
  navigator.clipboard.writeText(el.value).then(()=>showToast('info','URL copied to clipboard')).catch(()=>{ el.select(); document.execCommand('copy'); showToast('info','URL copied'); });
}

function _copyResumeCmd(id, cwd){
  const cmd=(cwd?'cd "'+cwd+'"; ':'')+'claude --resume '+id;
  navigator.clipboard.writeText(cmd).then(()=>showToast('info','Resume command copied',cmd)).catch(()=>{
    const ta=document.createElement('textarea');
    ta.value=cmd; ta.style.position='fixed'; ta.style.opacity='0';
    document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
    showToast('info','Resume command copied',cmd);
  });
}

/* Bundles a failed agent's project/task/error/log-tail into one paste-ready
   block -- the "copy" button on an error card only ever copied the bare
   error_message, which usually isn't enough context to share/debug without
   also pasting the project and what the agent was doing. Pure/testable:
   takes the agent object directly, no DOM/clipboard access inside. */
function _buildErrorContext(agent){
  if(!agent) return '';
  const lines=[];
  lines.push(`Agent: ${agent.name||'(unnamed)'}`);
  if(agent.session_project) lines.push(`Project: ${agent.session_project}`);
  lines.push(`Status: ${agent.status||'unknown'}`);
  if(agent.description) lines.push(`Task: ${agent.description}`);
  const tasks=agent.tasks||[];
  if(tasks.length){
    const done=tasks.filter(t=>t.done).length;
    lines.push(`Subtasks: ${done}/${tasks.length} done`);
  }
  if(agent.error_message) lines.push(`Error: ${agent.error_message}`);
  const log=(agent.log||[]).slice(-10);
  if(log.length){
    lines.push('--- Log tail (last '+log.length+') ---');
    log.forEach(e=>lines.push(String(e)));
  }
  return lines.join('\n');
}
function _copyErrorContext(id){
  const agent=(lastStatus&&lastStatus.agents||[]).find(a=>a.id===id);
  const text=_buildErrorContext(agent);
  if(!text) return;
  navigator.clipboard.writeText(text).then(()=>showToast('info','Error context copied')).catch(()=>{
    const ta=document.createElement('textarea');
    ta.value=text; ta.style.position='fixed'; ta.style.opacity='0';
    document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
    showToast('info','Error context copied');
  });
}

function setSettingsTab(name){
  document.querySelectorAll('.settings-pane').forEach(el=>el.style.display=el.dataset.pane===name?'':'none');
  document.querySelectorAll('.settings-tab').forEach(el=>{ el.classList.toggle('active',el.dataset.pane===name); el.setAttribute('aria-selected',el.dataset.pane===name); });
  if(name==='notify') _ncLoad();
  if(name==='people'&&typeof _peLoad==='function') _peLoad();
}
function openSettings(){
  _settingsOpen=true;
  document.getElementById('settings-overlay').classList.add('open');
  setSettingsTab('cost');  // always reset to the first tab -- simpler than persisting last-used tab
  _authRefresh();
  _renderLicenseUI();
  _raLoad();
  _dgLoad();
  _csLoad();
  _capsLoad();
  _pushLoad();
  _ncLoad();
  /* populate inputs */
  const v=n=>document.getElementById(n);
  if(v('st-cost-rate')) v('st-cost-rate').value=_costRate;
  if(v('st-budget'))    v('st-budget').value=_budgetLimit||'';
  if(v('st-project-budgets')) v('st-project-budgets').value=Object.keys(_projectBudgets).length?JSON.stringify(_projectBudgets,null,2):'';
  if(v('st-webhook'))   v('st-webhook').value=_webhookUrl;
  if(v('st-wh-done'))   v('st-wh-done').checked=!!_webhookEvents.done;
  if(v('st-wh-error'))  v('st-wh-error').checked=!!_webhookEvents.error;
  if(v('st-wh-stuck'))  v('st-wh-stuck').checked=!!_webhookEvents.stuck;
  if(v('st-wh-burn'))   v('st-wh-burn').checked=!!_webhookEvents.burn_spike;
  if(v('st-wh-digest')) v('st-wh-digest').checked=!!_webhookEvents.weekly_digest;
  if(v('st-wh-waiting')) v('st-wh-waiting').checked=!!_webhookEvents.waiting_nudge;
  if(v('st-wh-cost')) v('st-wh-cost').checked=!!_webhookEvents.cost_spike;
  if(v('st-wh-budget')) v('st-wh-budget').checked=!!_webhookEvents.budget_alert;
  if(v('st-wh-ratelimit')) v('st-wh-ratelimit').checked=!!_webhookEvents.rate_limit;
  if(v('st-quiet-start')) v('st-quiet-start').value=_quietStart;
  if(v('st-quiet-end'))   v('st-quiet-end').value=_quietEnd;
  if(v('st-muted-projects')) v('st-muted-projects').value=_mutedProjects.join(', ');
  if(v('st-snitch-url'))  v('st-snitch-url').value=_snitchUrl;
  if(v('st-digest-cadence')) v('st-digest-cadence').value=_digestCadence;
  if(v('st-backup-retention')) v('st-backup-retention').value=_backupRetentionDays;
  if(v('st-agent-retention')) v('st-agent-retention').value=_agentRetentionHours;
  if(v('st-local-machine-name')) v('st-local-machine-name').value=_localMachineName;
  _remoteMachinesDraft=_remoteMachines.map(m=>({...m}));  // working copy so Cancel discards edits, matching every other settings field
  _renderRemoteMachinesRows();
  if(v('st-density'))   v('st-density').value=_density;
  _applyAccent(_accentName);
  /* Sync webhook config from the server (source of truth for the background
     delivery worker) -- best-effort, falls back to whatever localStorage
     already populated above if this fails or the panel closes first. */
  fetch('/webhook_settings').then(r=>r.json()).then(cfg=>{
    if(!_settingsOpen) return;
    if(cfg && typeof cfg.url==='string'){
      _webhookUrl=cfg.url; _webhookEvents=cfg.events||_webhookEvents;
      if(v('st-webhook'))  v('st-webhook').value=_webhookUrl;
      if(v('st-wh-done'))  v('st-wh-done').checked=!!_webhookEvents.done;
      if(v('st-wh-error')) v('st-wh-error').checked=!!_webhookEvents.error;
      if(v('st-wh-stuck')) v('st-wh-stuck').checked=!!_webhookEvents.stuck;
      if(v('st-wh-burn'))  v('st-wh-burn').checked=!!_webhookEvents.burn_spike;
      if(v('st-wh-digest')) v('st-wh-digest').checked=!!_webhookEvents.weekly_digest;
      if(v('st-wh-waiting')) v('st-wh-waiting').checked=!!_webhookEvents.waiting_nudge;
      if(v('st-wh-cost')) v('st-wh-cost').checked=!!_webhookEvents.cost_spike;
      if(v('st-wh-budget')) v('st-wh-budget').checked=!!_webhookEvents.budget_alert;
      if(v('st-wh-ratelimit')) v('st-wh-ratelimit').checked=!!_webhookEvents.rate_limit;
    }
  }).catch(()=>{});
  /* Same idea for quiet hours / muted projects -- server is the source of
     truth for the two headless workers, localStorage above is just the
     fallback if this fetch loses the race with the panel closing. */
  fetch('/notify_settings').then(r=>r.json()).then(cfg=>{
    if(!_settingsOpen) return;
    if(cfg){
      _quietStart=cfg.quiet_start||''; _quietEnd=cfg.quiet_end||''; _mutedProjects=cfg.muted_projects||[]; _snitchUrl=cfg.snitch_url||''; _digestCadence=cfg.digest_cadence||'weekly'; _backupRetentionDays=cfg.backup_retention_days||14; _agentRetentionHours=cfg.agent_retention_hours||12;
      _projectBudgets=cfg.project_budgets||{};
      if(v('st-project-budgets')) v('st-project-budgets').value=Object.keys(_projectBudgets).length?JSON.stringify(_projectBudgets,null,2):'';
      _renderProjectBudgetSummary((_kpiAnalytics||{}).by_day_project);
      if(v('st-quiet-start')) v('st-quiet-start').value=_quietStart;
      if(v('st-quiet-end'))   v('st-quiet-end').value=_quietEnd;
      if(v('st-muted-projects')) v('st-muted-projects').value=_mutedProjects.join(', ');
      if(v('st-snitch-url'))  v('st-snitch-url').value=_snitchUrl;
      if(v('st-digest-cadence')) v('st-digest-cadence').value=_digestCadence;
      if(v('st-backup-retention')) v('st-backup-retention').value=_backupRetentionDays;
      if(v('st-agent-retention')) v('st-agent-retention').value=_agentRetentionHours;
    }
  }).catch(()=>{});
  /* Projected month-end spend -- reuses _computeMonthProjection (the same
     run-rate math the History/Analytics view already computes), just
     fetched fresh here since Settings has its own lifecycle from that
     view. Best-effort like the syncs above: silently leaves the em-dash
     placeholder if this fails or the panel closes first. */
  fetch('/analytics').then(r=>r.json()).then(a=>{
    if(!_settingsOpen) return;
    const el=v('st-projected-spend');
    if(!el) return;
    const {costSoFar,projectedMonthEnd}=_computeMonthProjection(a.by_day||[]);
    const fmt=c=>'$'+(+c).toFixed(2);
    el.textContent=costSoFar>0?`${fmt(costSoFar)} so far → projected ${fmt(projectedMonthEnd)}`:'—';
    const hrEl=v('infra-hook-reliability');
    if(hrEl) hrEl.textContent=_formatHookReliability(a.hook_miss_concurrency);
    _renderProjectBudgetSummary(a.by_day_project);
  }).catch(()=>{});
  /* Backup summary for the retention setting above -- same /backups list
     the DIAG view's restore picker already uses, just totalled here
     instead of rendered per-row. */
  fetch('/backups').then(r=>r.json()).then(list=>{
    if(!_settingsOpen) return;
    const el=v('infra-backup-summary');
    if(!el) return;
    if(!list.length){ el.textContent='No backups yet.'; return; }
    const totalBytes=list.reduce((s,b)=>s+(b.size_bytes||0),0);
    const totalMb=(totalBytes/1e6).toFixed(1);
    const corruptCount=list.filter(b=>b.corrupt).length;
    el.textContent=`${list.length} backup${list.length!==1?'s':''} · ${totalMb} MB total`+(corruptCount?` · ${corruptCount} flagged corrupt`:'');
  }).catch(()=>{});
  /* Agent-retention summary: how many currently-tracked agents are already
     done/error and therefore subject to the auto-clear worker -- reuses
     lastStatus rather than a fresh fetch since it's already kept live. */
  (()=>{
    const el=v('infra-agent-retention-summary');
    if(!el) return;
    const agents=(lastStatus&&lastStatus.agents)||[];
    if(!agents.length){ el.textContent='No tracked agents right now.'; return; }
    const finished=agents.filter(a=>a.status==='done'||a.status==='error').length;
    el.textContent=`${agents.length} tracked agent${agents.length!==1?'s':''} · ${finished} done/error (auto-cleared after ${_agentRetentionHours}h)`;
  })();
}
function closeSettings(){
  _settingsOpen=false;
  document.getElementById('settings-overlay').classList.remove('open');
}
async function saveSettings(){
  const v=n=>document.getElementById(n);
  _costRate = parseFloat(v('st-cost-rate')?.value) || 9;
  _budgetLimit = parseFloat(v('st-budget')?.value) || 0;
  const pbRaw = v('st-project-budgets')?.value?.trim() || '';
  if(pbRaw){
    try{
      const parsed = JSON.parse(pbRaw);
      if(parsed && typeof parsed==='object' && !Array.isArray(parsed)){
        _projectBudgets = parsed;
      } else {
        log('Per-project budgets: must be a JSON object, ignoring','error');
      }
    }catch(e){
      log('Per-project budgets: invalid JSON, ignoring (kept previous value)','error');
    }
  } else {
    _projectBudgets = {};
  }
  _webhookUrl = v('st-webhook')?.value?.trim() || '';
  _webhookEvents = {
    done:  v('st-wh-done')?.checked ?? true,
    error: v('st-wh-error')?.checked ?? true,
    stuck: v('st-wh-stuck')?.checked ?? false,
    burn_spike: v('st-wh-burn')?.checked ?? false,
    weekly_digest: v('st-wh-digest')?.checked ?? false,
    waiting_nudge: v('st-wh-waiting')?.checked ?? false,
    cost_spike: v('st-wh-cost')?.checked ?? false,
    budget_alert: v('st-wh-budget')?.checked ?? false,
    rate_limit: v('st-wh-ratelimit')?.checked ?? false,
  };
  _quietStart = v('st-quiet-start')?.value || '';
  _quietEnd = v('st-quiet-end')?.value || '';
  _mutedProjects = (v('st-muted-projects')?.value || '').split(',').map(s=>s.trim()).filter(Boolean);
  _snitchUrl = v('st-snitch-url')?.value?.trim() || '';
  _digestCadence = v('st-digest-cadence')?.value || 'weekly';
  _backupRetentionDays = Math.max(1, Math.min(365, parseInt(v('st-backup-retention')?.value, 10) || 14));
  _agentRetentionHours = Math.max(1, Math.min(23, parseInt(v('st-agent-retention')?.value, 10) || 12));
  _localMachineName = v('st-local-machine-name')?.value?.trim() || '';
  _remoteMachines = _remoteMachinesDraft
    .filter(m=>m.url&&m.url.trim())
    .map(m=>({name:(m.name||'').trim(), url:m.url.trim().replace(/\/$/,''), token:(m.token||'').trim()}));
  _remoteCache = {};  // config just changed -- drop stale cached data, next poll tick repopulates
  _density = v('st-density')?.value || 'normal';
  /* persist */
  localStorage.setItem('aoc_cost_rate', _costRate);
  localStorage.setItem('aoc_budget', _budgetLimit);
  localStorage.setItem('aoc_project_budgets', JSON.stringify(_projectBudgets));
  localStorage.setItem('aoc_webhook_url', _webhookUrl);
  localStorage.setItem('aoc_webhook_events', JSON.stringify(_webhookEvents));
  localStorage.setItem('aoc_quiet_start', _quietStart);
  localStorage.setItem('aoc_quiet_end', _quietEnd);
  localStorage.setItem('aoc_muted_projects', JSON.stringify(_mutedProjects));
  localStorage.setItem('aoc_snitch_url', _snitchUrl);
  localStorage.setItem('aoc_digest_cadence', _digestCadence);
  localStorage.setItem('aoc_backup_retention_days', _backupRetentionDays);
  localStorage.setItem('aoc_agent_retention_hours', _agentRetentionHours);
  localStorage.setItem('aoc_local_machine_name', _localMachineName);
  localStorage.setItem('aoc_remote_machines', JSON.stringify(_remoteMachines));
  /* Also persist server-side so _webhook_notify_worker can deliver events
     even when no browser tab is open at all -- localStorage above only
     helps the JS-side _fireWebhook, which needs an open tab to run.
     Awaited (not fire-and-forget) and checked for .ok specifically so a
     failure here -- server briefly unreachable, a validation error -- is
     surfaced instead of silently claiming "Settings saved" while the
     headless webhook/budget/quiet-hours delivery this exists for is left
     running on stale server-side config with zero indication anything
     went wrong. */
  let serverSyncOk=true;
  try{
    const [whRes,ntRes]=await Promise.all([
      fetch('/webhook_settings',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({url:_webhookUrl, events:_webhookEvents})}),
      fetch('/notify_settings',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({quiet_start:_quietStart, quiet_end:_quietEnd, muted_projects:_mutedProjects, snitch_url:_snitchUrl, digest_cadence:_digestCadence, backup_retention_days:_backupRetentionDays, agent_retention_hours:_agentRetentionHours, project_budgets:_projectBudgets})}),
    ]);
    serverSyncOk=whRes.ok&&ntRes.ok;
  }catch(e){ serverSyncOk=false; }
  localStorage.setItem('aoc_accent', _accentName);
  localStorage.setItem('aoc_density', _density);
  _applyAccent(_accentName);
  _applyDensity(_density);
  _renderProjectBudgetSummary((_kpiAnalytics||{}).by_day_project);
  const msg=document.getElementById('st-saved-msg');
  if(msg){ msg.style.display='block'; setTimeout(()=>msg.style.display='none',2000); }
  if(serverSyncOk){
    log('Settings saved','info');
  } else {
    log('Settings saved locally, but syncing to the server failed -- webhook/budget/quiet-hours delivery may be running on stale config until this succeeds (try Save again)','error');
  }
}
