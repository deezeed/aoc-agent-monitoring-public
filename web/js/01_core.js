/* ── auth token injected by server ── */
window._AOC_TOKEN = '__AOC_TOKEN_PLACEHOLDER__';
/* ── license tier injected by server (same pattern as the token above) ── */
window._AOC_IS_PRO = __AOC_IS_PRO_PLACEHOLDER__;
/* wrapper always installed (not gated on a token existing at load time) — a
   Cloudflare tunnel can provision a token later in this same session, and
   subsequent fetches must start carrying it without a page reload */
(()=>{
  const _origFetch=window.fetch;
  window.fetch=(url,opts={})=>{
    if(window._AOC_TOKEN&&typeof url==='string'&&!url.startsWith('http')){
      opts={...opts,headers:{'X-AOC-Token':window._AOC_TOKEN,...(opts.headers||{})}};
    }
    return _origFetch(url,opts);
  };
})();

let logs = [];
let lastStatus = null;
let fcCount = 0;
let currentView = 'agents';
let statusFilter = 'all';
let sessionFilter = null;
let listMode = false;
let searchQuery = '';
let pollFails = 0;
let _connOnline = true;
let _soundMuted = false;
let _soundPrefs = { volume: 0.7, profile: 'default', events: { done: true, error: true, running: false, stuck: false, burn_spike: false, waiting_nudge: false, cost_spike: false } };
let _soundPanelOpen = false;
// First-run default follows the OS theme preference; _loadPrefs() below
// unconditionally overrides this the moment a user has ever manually
// toggled the theme, so an explicit choice always wins over the OS
// setting -- this only ever matters before that first toggle happens.
let _theme = (typeof matchMedia!=='undefined' && matchMedia('(prefers-color-scheme: light)').matches) ? 'light' : 'dark';
let _collapsedIds = new Set();
let _pinnedIds = new Set();
let _collapsedBays = new Set();  // session bay keys (machine|session_id), saved in aoc_prefs
let _prevTokens = {};
let _notifHistory = [];
let _notifUnread = 0;
let _notifOpen = false;
let _tlZoom = 1;
let _tlZoomFocus = 0.5;
const _NOTIF_MAX = 300;
const _knownAgentIds = new Set();
const _completedTaskKeys = new Set();

/* ── helpers ── */
function pad(n){ return String(n).padStart(2,'0'); }
function ts(){
  const d=new Date();
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
function parseTimeStr(s){
  if(!s) return null;
  if(s.includes('T')) return new Date(s).getTime();
  /* HH:MM:SS — handle midnight crossover: a past event can't be in the future,
     so any computed time more than a few seconds ahead (clock/render jitter) must be
     yesterday's timestamp. A large threshold here would misjudge near-24h-old events
     as "today" instead of "yesterday". */
  const [h,m,sec]=(s||'').split(':').map(Number);
  const d=new Date(); d.setHours(h||0,m||0,sec||0,0);
  const t=d.getTime();
  return t>Date.now()+5000 ? t-86400000 : t;
}

/* ── unit abbreviation helper ── */
function _deriveUnit(a){
  if(a.unit && a.unit!=='??') return a.unit;
  if(a.icon && a.icon!=='??') return a.icon;
  const words=(a.name||'').split(/[\s\-_.()]+/).filter(Boolean);
  if(words.length>=2) return (words[0][0]+words[1][0]).toUpperCase();
  if(words.length===1) return words[0].slice(0,3).toUpperCase();
  return String(a.id).slice(-3).toUpperCase();
}

/* ── log ── */
function log(msg, type='info', tag=''){
  const _t=ts();
  logs.push({t:_t, msg, type, tag});
  if(logs.length>400) logs.shift();
  _notifHistory.push({t:_t, type, msg:String(msg), tag:tag||''});
  if(_notifHistory.length>_NOTIF_MAX) _notifHistory.shift();
  if(!_notifOpen){ _notifUnread++; _updateNotifBadge(); }
  else _renderNotifPanel();
  renderLog();
}
function renderLog(){
  const el=document.getElementById('log-area');
  if(!el) return;
  if(!logs.length){
    el.innerHTML='<div style="color:var(--t3);font-family:var(--font2);font-size:10px;text-align:center;padding:30px 0;letter-spacing:.08em">// awaiting events</div>';
    return;
  }
  el.innerHTML=logs.slice().reverse().slice(0,100).map(e=>
    `<div class="le"><span class="le-t">[${e.t}]</span>${e.tag?`<span class="le-tag">${escHtml(e.tag)}</span>`:''}<span class="le-m ${escHtml(e.type)}">${escHtml(e.msg)}</span></div>`
  ).join('');
}

/* ── arc ── */
function arcDash(pct, r=16){
  const c=2*Math.PI*r;
  return {c, d:Math.min(pct/100,1)*c};
}

/* ── color by status ── */
function sCol(s){ return {running:'var(--c)',done:'var(--g)',error:'var(--r)',waiting:'var(--o)'}[s]||'var(--c)'; }
/* ── status label by status ──
   Genuinely global (unlike the identically-named function that used to live only
   inside _renderCompare(), which was invisible to every other call site). Commit
   5d42e48 deduped 4 local `const stxt={...}` objects into calls to this function
   on the assumption a shared global already existed — it didn't, so every one of
   those call sites (main card render in both grid and list mode included) has
   been throwing ReferenceError: stxt is not defined on every render since. */
function stxt(ag){ return ({running:'ACTIVE',done:'COMPLETE',error:'ERROR',waiting:'STANDBY'})[ag.status]||(ag.status||'').toUpperCase(); }

/* ── connection health ── */
function _setConnStatus(online){
  if(_connOnline===online) return;
  _connOnline=online;
  const cs=document.getElementById('conn-status');
  const cl=document.getElementById('conn-label');
  const ov=document.getElementById('reconnect-overlay');
  if(cs) cs.classList.toggle('offline',!online);
  if(cl) cl.textContent=online?'SYSTEM ONLINE':'OFFLINE';
  if(ov) ov.classList.toggle('show',!online);
}

/* ── status filter bar ── */
function renderStatusFilterBar(agents){
  const bar=document.getElementById('status-filter-bar');
  if(!bar) return;
  const counts={all:agents.length,running:0,done:0,error:0};
  agents.forEach(a=>{
    if(a.status==='running'||a.status==='waiting') counts.running++;
    else if(a.status==='done') counts.done++;
    else if(a.status==='error') counts.error++;
  });
  if(agents.length===0){ bar.style.display='none'; return; }
  bar.style.display='flex';
  const btns=[
    {f:'all',label:'ALL',cls:''},
    {f:'running',label:`RUNNING ${counts.running}`,cls:'running'},
    {f:'done',label:`DONE ${counts.done}`,cls:'done'},
    {f:'error',label:`ERROR ${counts.error}`,cls:'error'},
  ];
  const clearBtn=counts.done>0?`<button class="density-btn" onclick="clearDoneAgents()" title="Remove all completed agents [X]" style="margin-left:4px;border-color:rgba(76,194,133,.25);color:rgba(76,194,133,.65)">✕ CLEAR DONE</button>`:'';
  const collapseBtn=counts.done>0?`<button class="density-btn" onclick="collapseAllDone()" title="Collapse all done cards [D]" style="border-color:rgba(var(--c-rgb),.2);color:rgba(var(--c-rgb),.6)">⊟ COLLAPSE DONE</button>`:'';
  bar.innerHTML=btns.map(b=>
    `<button class="sfbtn ${b.cls} ${statusFilter===b.f?'active':''}" onclick="setStatusFilter('${b.f}')">${b.label}</button>`
  ).join('')+`<button class="density-btn ${listMode?'active':''}" onclick="toggleListMode()" title="Toggle compact list view [L]">${listMode?'⊟ LIST':'⊞ GRID'}</button>`+collapseBtn+clearBtn;
}
function setStatusFilter(f){
  statusFilter=f;
  _savePrefs();
  if(lastStatus) renderAgents(lastStatus);
}
function toggleListMode(){
  listMode=!listMode;
  _savePrefs();
  if(lastStatus) renderAgents(lastStatus);
}
function setSearch(val){
  searchQuery=val.toLowerCase();
  const inp=document.getElementById('search-input');
  const clr=document.getElementById('search-clear');
  if(inp && inp.value!==val) inp.value=val;
  if(clr) clr.classList.toggle('show', val.length>0);
  if(lastStatus) renderAgents(lastStatus);
}

/* ── session tab bar ── */
function renderSessionTabBar(data, agents){
  const bar=document.getElementById('session-tab-bar');
  if(!bar) return;
  const sessions=data.sessions_list||[];
  /* collect session IDs that have agents */
  const sessIds=new Set(agents.map(a=>a.session_id).filter(Boolean));
  const activeSessions=sessions.filter(s=>sessIds.has(s.id));
  if(activeSessions.length<=1){ bar.style.display='none'; return; }
  bar.style.display='flex';
  const allBtn=`<button class="stab ${sessionFilter===null?'active':''}" onclick="setSessionFilter(null)">ALL CLIs</button>`;
  const sessBtns=activeSessions.map(s=>{
    const cwdName=s.cwd?(s.cwd.replace(/\\/g,'/').split('/').filter(Boolean).pop()||''):'';
    const label=(s.display_name||s.project||cwdName||s.id.slice(-6)).slice(0,30);
    const cnt=agents.filter(a=>a.session_id===s.id).length;
    const isActive=s.session_active!==false;
    /* Remote sessions too, routed to their own machine -- same as the
       session card's ✕ and the stale-session sweep (see _dismissSession). */
    const dimBtn=!isActive?`<button onclick="event.stopPropagation();_dismissSession(${jsq(s.id)},${jsq(s.machine||'')})" title="Dismiss closed session" style="background:none;border:1px solid rgba(255,255,255,.08);border-left:none;border-radius:0 7px 7px 0;color:var(--t3);cursor:pointer;padding:3px 6px;font-size:10px;line-height:1;transition:color .2s" onmouseover="this.style.color='var(--r)'" onmouseout="this.style.color='var(--t3)'">✕</button>`:'';
    return `<span style="display:inline-flex;align-items:center;gap:0">
      <button class="stab ${sessionFilter===s.id?'active':''}" onclick="setSessionFilter(${jsq(s.id)})" title="${escHtml(s.id)}">${escHtml(label)} (${cnt})</button>${dimBtn}</span>`;
  }).join('');
  bar.innerHTML=allBtn+sessBtns;
}
function setSessionFilter(id){
  sessionFilter=id;
  if(lastStatus) renderAgents(lastStatus);
}
async function _dismissSession(id,machineName){
  try{
    await fetch(_apiUrl(_machineFor(machineName),`/session/${encodeURIComponent(id)}`),{method:'DELETE'});
  }catch(e){}
  /* immediately remove from local state so card vanishes without waiting for next poll */
  if(lastStatus){
    lastStatus.sessions_list=(lastStatus.sessions_list||[]).filter(s=>s.id!==id);
    const sessions=lastStatus.sessions||{};
    if(sessions[id]) sessions[id].dismissed=true;
    if(lastStatus.sessions_count>0) lastStatus.sessions_count--;
    renderAgents(lastStatus);
  }
}

/* ── stale-session sweep ── bulk-dismiss every closed session at once
   (local or merged-in from a Remote Machine) instead of clicking Dismiss
   on each card individually -- _dismissSession already routes each one
   to its own machine. */
function _staleSessions(sr){
  return ((sr||lastStatus||{}).sessions_list||[]).filter(s=>s.session_active===false);
}
function _updateSweepButton(sr){
  const btn=document.getElementById('btn-sweep-stale');
  const nEl=document.getElementById('sweep-stale-n');
  if(!btn) return;
  const stale=_staleSessions(sr);
  btn.style.display=stale.length>0?'':'none';
  if(nEl) nEl.textContent=stale.length>0?stale.length:'';
}
async function sweepStaleSessions(){
  const stale=_staleSessions();
  if(!stale.length) return;
  // native confirm() -- same reasoning _forceStopSession already uses:
  // impossible to accidentally click through the way a styled in-page
  // modal could be, and this is a bulk, irreversible action.
  if(!confirm(`Dismiss ${stale.length} idle session${stale.length!==1?'s':''}? This can't be undone.`)) return;
  for(const s of stale){ await _dismissSession(s.id,s.machine); }
  _updateSweepButton(lastStatus);
  log(`Swept ${stale.length} idle session${stale.length!==1?'s':''}`,'success');
}

async function _forceStopSession(id,label,machineName){
  /* Destructive -- kills a real OS process (the user's actual claude.exe),
     not just an AOC-side card. Native confirm() is a deliberate choice over
     a custom modal: it's a blocking native browser dialog, impossible to
     accidentally click through the way a styled in-page modal could be. */
  if(!confirm(`Force stop "${label}"?\n\nThis will kill the claude.exe process for this session immediately. Any unsaved work in that terminal will be lost.`)) return;
  try{
    const r=await fetch(_apiUrl(_machineFor(machineName),'/kill_session'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:id})});
    const j=await r.json();
    if(j.ok){
      log(`Force-stopped session: ${label}`,'success');
    } else {
      log(`Force stop failed: ${j.error||'unknown error'}`,'error');
    }
  }catch(e){
    log('Force stop failed: request error','error');
  }
}

/* ── view toggle ── */
let _panelCollapsed = false;
/* ── agent compare ── */
let _compareIds=[];
let _cmpOpen=false;
function toggleCompare(id,event){
  if(event) event.stopPropagation();
  const idx=_compareIds.indexOf(id);
  if(idx>=0){ _compareIds.splice(idx,1); }
  else { if(_compareIds.length>=2) _compareIds.shift(); _compareIds.push(id); }
  document.querySelectorAll('.card-cmp-btn').forEach(btn=>{
    btn.classList.toggle('selected',_compareIds.includes(btn.dataset.id));
  });
  if(_compareIds.length===2){ openCompare(); }
  else if(_compareIds.length===1){ log('⊞ Select one more agent to compare','info'); }
  else { closeCompare(); }
}
function openCompare(){
  _cmpOpen=true;
  const ov=document.getElementById('cmp-overlay');
  if(ov) ov.classList.add('open');
  _renderCompare();
}
function closeCompare(){
  _cmpOpen=false;
  const ov=document.getElementById('cmp-overlay');
  if(ov) ov.classList.remove('open');
}
function _renderCompare(){
  const body=document.getElementById('cmp-body');
  if(!body||!lastStatus||!_cmpOpen) return;
  const agents=_compareIds.map(id=>(lastStatus.agents||[]).find(a=>a.id===id)).filter(Boolean);
  if(agents.length<2){ body.innerHTML='<div style="grid-column:1/-1;text-align:center;padding:60px;color:var(--t3);font-family:var(--font2);font-size:11px;letter-spacing:.1em">// select 2 agents to compare</div>'; return; }
  const [a,b]=agents;
  function dur(ag){ if(!ag.started_at) return null; const s=parseTimeStr(ag.started_at); let e=Date.now(); if(ag.status!=='running'&&ag.completed_at) e=parseTimeStr(ag.completed_at); return Math.max(0,Math.floor((e-s)/1000)); }
  const fmtDur=_fmtDurShort;
  function pct(ag){ const t=(ag.tasks||[]).length; return t>0?Math.round((ag.tasks||[]).filter(t=>t.done).length/t*100):0; }
  function col(ag){ return ({running:'var(--c)',done:'var(--g)',error:'var(--r)',waiting:'var(--o)'})[ag.status]||'var(--t3)'; }
  function renderCol(ag,other){
    const c=col(ag); const dA=dur(ag); const dB=dur(other);
    const tA=ag.tokens_used||0; const tB=other.tokens_used||0;
    const pA=pct(ag); const pB=pct(other);
    const tasks=ag.tasks||[]; const done=tasks.filter(t=>t.done).length;
    const files=ag.files_changed||[];
    const logs=(ag.log||[]).slice(-8).reverse();
    const durBetter=dA!==null&&dB!==null&&dA<=dB;
    const tokBetter=tA>0&&tB>0&&tA<=tB;
    const pctBetter=pA>=pB;
    const cost=tA?_agentCost(ag).toFixed(4):null;
    // Parent-only, deliberately: unlike the other 4 parent_id surfaces (detail
    // panel / Markdown export / History Detail / GRAPH-TREE tooltip), Compare
    // does not also show a "spawned N subagents" row here. Compare is a
    // peer-vs-peer tool and comparing an orchestrator against its own
    // subagent isn't a typical use case, so the reverse direction was judged
    // lower-value and left out rather than added just for symmetry (see
    // a0e55b7). The one-directional "Parent" row below stays for consistency
    // with Model/Type as a plain informational (no better/worse) stat.
    const parentAg=_resolveParentAgent(ag);
    return `<div class="cmp-col">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
        <div class="unit-badge" style="border-color:${c};color:${c};flex-shrink:0">${escHtml(_deriveUnit(ag))}</div>
        <div style="flex:1;min-width:0">
          <div style="font-size:13px;font-weight:700;color:${c};overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(ag.name)}</div>
          ${ag.description?`<div style="font-size:10px;color:var(--t3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-top:1px">${escHtml(ag.description)}</div>`:''}
        </div>
        ${ag.detected_via==='transcript'?`<span class="hookmiss-badge" style="flex-shrink:0" title="${escHtml(_hookMissTitle(ag))}">⚠ HOOK MISS</span>`:''}
        ${_stuckSecs(ag)>300?`<span class="stuck-badge" style="flex-shrink:0;margin-top:0">⚠ STUCK</span>`:''}
        <div class="status-pill ${escHtml(ag.status)}" style="font-size:9px;padding:2px 7px;flex-shrink:0"><span class="sdot"></span>${escHtml(stxt(ag))}</div>
      </div>
      <div style="height:3px;background:rgba(255,255,255,.05);border-radius:999px;margin:8px 0;overflow:hidden">
        <div style="height:100%;width:${pA}%;background:${c};border-radius:999px;transition:width .6s ease;box-shadow:0 0 4px ${c}55"></div>
      </div>
      <div class="cmp-sec">STATS</div>
      ${(ag.model||other.model)?`<div class="cmp-stat-row"><span class="cmp-stat-lbl">Model</span><span class="cmp-stat-val" style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(ag.model||'')}">${ag.model?escHtml(ag.model):'—'}</span></div>`:''}
      ${(ag.subagent_type||other.subagent_type)?`<div class="cmp-stat-row"><span class="cmp-stat-lbl">Type</span><span class="cmp-stat-val">${ag.subagent_type?escHtml(ag.subagent_type):'—'}</span></div>`:''}
      ${(ag.parent_id||other.parent_id)?`<div class="cmp-stat-row"><span class="cmp-stat-lbl">Parent</span><span class="cmp-stat-val" style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(parentAg?.name||'')}">${parentAg?escHtml(parentAg.name):'—'}</span></div>`:''}
      ${(ag.tool_use_count!=null||other.tool_use_count!=null)?`<div class="cmp-stat-row"><span class="cmp-stat-lbl">Tool calls</span><span class="cmp-stat-val ${ag.tool_use_count!=null&&other.tool_use_count!=null?(ag.tool_use_count<=other.tool_use_count?'better':'worse'):''}">${ag.tool_use_count!=null?ag.tool_use_count:'—'}</span></div>`:''}
      <div class="cmp-stat-row"><span class="cmp-stat-lbl">Duration</span><span class="cmp-stat-val ${dA!==null&&dB!==null?(durBetter?'better':'worse'):''}">${fmtDur(dA)}</span></div>
      <div class="cmp-stat-row"><span class="cmp-stat-lbl">Tokens</span><span class="cmp-stat-val ${tA>0&&tB>0?(tokBetter?'better':'worse'):''}">${tA>0?Number(tA).toLocaleString():'—'}</span></div>
      <div class="cmp-stat-row"><span class="cmp-stat-lbl">Est. cost</span><span class="cmp-stat-val ${tA>0&&tB>0?(tokBetter?'better':'worse'):''}">${cost?'$'+cost:'—'}</span></div>
      <div class="cmp-stat-row"><span class="cmp-stat-lbl">Progress</span><span class="cmp-stat-val ${pctBetter?'better':''}">${done}/${tasks.length} tasks (${pA}%)</span></div>
      <div class="cmp-stat-row"><span class="cmp-stat-lbl">Files</span><span class="cmp-stat-val">${files.length}</span></div>
      ${tasks.length?`<div class="cmp-sec">TASKS</div>${tasks.map(t=>`<div class="cmp-task-row ${t.done?'done':'pend'}"><span style="font-family:var(--font2);flex-shrink:0">${t.done?'✓':'·'}</span><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1">${escHtml(t.label||'')}</span>${t.completed_at?`<span style="font-size:9px;color:var(--t3);font-family:var(--font2);flex-shrink:0">${t.completed_at}</span>`:''}</div>`).join('')}`:''}
      ${files.length?`<div class="cmp-sec">FILES (${files.length})</div>${files.slice(0,12).map(f=>`<div class="fe ${f.type==='new'?'new':'changed'}" style="padding:3px 7px;margin-bottom:2px;border-radius:5px"><span class="fe-badge">${f.type==='new'?'NEW':'MOD'}</span><span class="fe-name" style="font-size:10px">${escHtml(f.path.split('/').pop())}</span></div>`).join('')}${files.length>12?`<div style="font-size:10px;color:var(--t3);padding:3px 0">+${files.length-12} more</div>`:''}`:''}
      ${logs.length?`<div class="cmp-sec">RECENT LOG</div>${logs.map(e=>`<div class="adp-log-line" style="font-size:10px">${escHtml(String(e))}</div>`).join('')}`:''}
    </div>`;
  }
  body.innerHTML=renderCol(a,b)+renderCol(b,a);
}

/* ── session compare ── mirrors the agent-compare pattern immediately
   above (_compareIds/toggleCompare/openCompare/closeCompare/_renderCompare)
   at the session level instead of the agent level. */
let _sessCompareIds=[];
let _sessCmpOpen=false;
function toggleSessionCompare(id,event){
  if(event) event.stopPropagation();
  const idx=_sessCompareIds.indexOf(id);
  if(idx>=0){ _sessCompareIds.splice(idx,1); }
  else { if(_sessCompareIds.length>=2) _sessCompareIds.shift(); _sessCompareIds.push(id); }
  if(lastStatus) renderAgents(lastStatus);
  if(_sessCompareIds.length===2){ openSessionCompare(); }
  else if(_sessCompareIds.length===1){ log('⊞ Select one more session to compare','info'); }
  else { closeSessionCompare(); }
}
function openSessionCompare(){
  _sessCmpOpen=true;
  const ov=document.getElementById('sess-cmp-overlay');
  if(ov) ov.classList.add('open');
  _renderSessionCompare();
}
function closeSessionCompare(){
  _sessCmpOpen=false;
  const ov=document.getElementById('sess-cmp-overlay');
  if(ov) ov.classList.remove('open');
}
function _renderSessionCompare(){
  const body=document.getElementById('sess-cmp-body');
  if(!body||!lastStatus||!_sessCmpOpen) return;
  const sessions=_sessCompareIds.map(id=>(lastStatus.sessions_list||[]).find(s=>s.id===id)).filter(Boolean);
  if(sessions.length<2){ body.innerHTML='<div style="grid-column:1/-1;text-align:center;padding:60px;color:var(--t3);font-family:var(--font2);font-size:11px;letter-spacing:.1em">// select 2 sessions to compare</div>'; return; }
  const [a,b]=sessions;
  function agentsOf(s){ return (lastStatus.agents||[]).filter(ag=>ag.session_id===s.id); }
  function dur(s){
    if(!s.first_ts||!s.last_ts) return null;
    const ms=new Date(s.last_ts)-new Date(s.first_ts);
    return isNaN(ms)||ms<=0?null:Math.floor(ms/1000);
  }
  const fmtDur=_fmtDurShort;
  const fmtCost=_fmtCost;
  function fmtTok(t){ return t?Number(t).toLocaleString():'—'; }
  function taskStats(s){
    const ags=agentsOf(s);
    const tasks=ags.flatMap(ag=>ag.tasks||[]);
    return {done:tasks.filter(t=>t.done).length,total:tasks.length};
  }
  function col(s){ return s.session_active!==false?'var(--c)':'var(--t3)'; }
  function renderCol(s,other){
    const c=col(s);
    const dA=dur(s),dB=dur(other);
    const tA=(s.input_tokens||0)+(s.output_tokens||0),tB=(other.input_tokens||0)+(other.output_tokens||0);
    const cA=s.estimated_cost||0,cB=other.estimated_cost||0;
    const aA=agentsOf(s).length,aB=agentsOf(other).length;
    const tsA=taskStats(s),tsB=taskStats(other);
    const pctA=tsA.total>0?Math.round(tsA.done/tsA.total*100):0;
    const pctB=tsB.total>0?Math.round(tsB.done/tsB.total*100):0;
    const durBetter=dA!==null&&dB!==null&&dA<=dB;
    const costBetter=cA>0&&cB>0&&cA<=cB;
    const tokBetter=tA>0&&tB>0&&tA<=tB;
    const label=escHtml(s.display_name||s.project||s.id.slice(-6));
    // isActive/waitingOnYou/waitingDurStr mirror the session card's own
    // status pill exactly (renderAgents' statsRow) -- Compare's pill was
    // hardcoded to ACTIVE/CLOSED only, so two sessions being compared
    // could never show which one is actually WAITING on you right now.
    const isActive=s.session_active!==false;
    const waitingOnYou=isActive&&!!s.waiting_on_you;
    const waitingDurStr=(waitingOnYou&&(s.waiting_secs||0)>=60)?' · '+_fmtDurationDHM(s.waiting_secs):'';
    return `<div class="cmp-col">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
        <div class="unit-badge" style="border-color:${c};color:${c};flex-shrink:0">CLI</div>
        <div style="flex:1;min-width:0">
          <div style="font-size:13px;font-weight:700;color:${c};overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${label}</div>
          ${s.git_branch?`<div style="font-size:10px;color:var(--t3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-top:1px">⎇ ${s.pr_url?`<a href="${escHtml(s.pr_url)}" target="_blank" onclick="event.stopPropagation()" style="color:inherit;text-decoration:underline;text-decoration-style:dotted" title="Open PR on GitHub">${escHtml(s.git_branch)}</a>`:escHtml(s.git_branch)}</div>`:''}
        </div>
        <div class="status-pill ${isActive?(waitingOnYou?'waiting'+(_waitLabel(s).blocked?' blocked':''):'running'):''}" style="font-size:9px;padding:2px 7px;flex-shrink:0" ${waitingOnYou?`title="${escHtml(_waitLabel(s).tip)}"`:''}><span class="sdot"></span>${isActive?(waitingOnYou?_waitLabel(s).word+waitingDurStr:'ACTIVE'):'CLOSED'}</div>
      </div>
      <div class="cmp-sec">STATS</div>
      <div class="cmp-stat-row"><span class="cmp-stat-lbl">Duration</span><span class="cmp-stat-val ${dA!==null&&dB!==null?(durBetter?'better':'worse'):''}">${fmtDur(dA)}</span></div>
      <div class="cmp-stat-row"><span class="cmp-stat-lbl">Tokens</span><span class="cmp-stat-val ${tA>0&&tB>0?(tokBetter?'better':'worse'):''}"${tA>0?` title="${escHtml(_tokBreakdownStr(s)+(s.msg_count?' · '+s.msg_count+' msg':''))}"`:''}>${fmtTok(tA)}</span></div>
      <div class="cmp-stat-row"><span class="cmp-stat-lbl">Est. cost</span><span class="cmp-stat-val ${cA>0&&cB>0?(costBetter?'better':'worse'):''}">${fmtCost(cA)}</span></div>
      <div class="cmp-stat-row"><span class="cmp-stat-lbl">Agents</span><span class="cmp-stat-val">${aA}</span></div>
      <div class="cmp-stat-row"><span class="cmp-stat-lbl">Tasks</span><span class="cmp-stat-val ${pctA>=pctB?'better':''}">${tsA.done}/${tsA.total} (${pctA}%)</span></div>
      ${(s.cc_version||other.cc_version)?`<div class="cmp-stat-row"><span class="cmp-stat-lbl">CC Version</span><span class="cmp-stat-val">${s.cc_version?'v'+escHtml(s.cc_version):'—'}</span></div>`:''}
      ${(()=>{
        // Distinct models across this session's own agents -- a session
        // can mix models (e.g. an orchestrator agent on one, subagents on
        // another), so this is a set, not a single value like the
        // per-agent Model row Agent Compare shows.
        const models=[...new Set(agentsOf(s).map(ag=>ag.model).filter(Boolean))];
        return models.length?`<div class="cmp-stat-row"><span class="cmp-stat-lbl">Models</span><span class="cmp-stat-val" style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(models.join(', '))}">${escHtml(models.join(', '))}</span></div>`:'';
      })()}
      ${s.note?`<div class="cmp-sec">NOTE</div><div class="adp-log-line" style="font-size:10px">${escHtml(s.note)}</div>`:''}
    </div>`;
  }
  body.innerHTML=renderCol(a,b)+renderCol(b,a);
}

let _rightTab='log';
function setRightTab(tab){
  _rightTab=tab;
  ['log','files','audit','errors'].forEach(t=>{
    const btn=document.getElementById('rpt-'+t);
    if(btn){ btn.classList.toggle('active',t===tab); btn.setAttribute('aria-selected',t===tab); }
  });
  document.getElementById('log-area').style.display    = tab==='log'   ? '' : 'none';
  document.getElementById('rp-files').style.display   = tab==='files' ? 'flex' : 'none';
  document.getElementById('rp-audit').style.display   = tab==='audit' ? 'flex' : 'none';
  document.getElementById('rp-errors-wrap').style.display  = tab==='errors' ? 'flex' : 'none';
  if(tab==='files' && lastStatus) _renderRpFiles(lastStatus);
  if(tab==='audit') _renderRpAudit();
  if(tab==='errors') _renderRpErrors();
}
/* the count badge on a right-panel tab (empty = hidden) */
function _rpTabCount(tab, n, bad){
  const b=document.querySelector('#rpt-'+tab+' .rp-n');
  if(!b) return;
  b.textContent=n?String(n):'';
  b.classList.toggle('bad', !!bad&&n>0);
}
/* Pure: FILES tab html -- the files each open session edited (edited_count,
   opens the session's changes) and then the files subagents reported. */
function _rpFilesParts(data){
  const sessions=((data&&data.sessions_list)||[]).filter(s=>s&&s.session_active!==false&&s.edited_count>0)
    .sort((a,b)=>b.edited_count-a.edited_count);
  const agents=((data&&data.agents)||[]).filter(a=>!String(a.id||'').startsWith('hook_'));
  const seen=new Set(), files=[];
  agents.forEach(a=>(a.files_changed||[]).forEach(f=>{ if(f&&f.path&&!seen.has(f.path)){ seen.add(f.path); files.push({...f,agent:a.unit||a.name||''}); } }));
  const nameOf=s=>s.display_name||s.project||String(s.id||'').slice(0,8);
  const count=sessions.reduce((n,s)=>n+s.edited_count,0)+files.length;
  if(!count) return {count, html:'<div class="rp-empty">No file changes yet.<small>Files an open session or an agent edits show up here.</small></div>'};
  let html='';
  if(sessions.length) html+=`<div class="rp-sec"><span>Sessions</span><span>${sessions.length}</span></div>`+sessions.map(s=>
    `<button class="rp-srow" onclick="openSessionChanges(${jsq(s.id)},${jsq(s.machine||'')},${jsq(nameOf(s))})" title="Uncommitted changes in the files this session edited">
      <span class="rp-sdot"></span><span class="rp-sname">${escHtml(nameOf(s))}</span><span class="rp-scount">${s.edited_count} file${s.edited_count===1?'':'s'}</span><span class="rp-chev">›</span></button>`).join('');
  if(files.length) html+=`<div class="rp-sec"><span>Agents</span><span>${files.length}</span></div>`+files.map(f=>
    `<div class="fe ${f.type==='new'?'new':'changed'}" onclick="showDiff(${jsq(f.path)});event.stopPropagation()" title="${escHtml(f.path)}">
      <span class="fe-badge">${f.type==='new'?'NEW':'MOD'}</span>
      <span class="fe-name">${escHtml(f.path.split(/[\\/]/).pop())}</span>
      ${f.lines?`<span class="fe-lines">${escHtml(f.lines)}L</span>`:''}
      <span class="fe-agent">${escHtml(f.agent)}</span></div>`).join('');
  return {count, html};
}
const _rpPainted=new WeakMap();
function _renderRpFiles(data){
  const el=document.getElementById('rp-files');
  const p=_rpFilesParts(data);
  _rpTabCount('files', p.count);
  if(!el || _rightTab!=='files' || _rpPainted.get(el)===p.html) return;
  el.innerHTML=p.html; _rpPainted.set(el,p.html);
}
/* Pure: AUDIT tab html from the audit log lines ("ts | EVENT | details"),
   newest first; POLL heartbeats are only counted, they carry no event. */
function _rpAuditParts(lines){
  const rows=[]; let polls=0;
  (lines||[]).slice().reverse().forEach(l=>{
    const parts=String(l).split('|').map(x=>x.trim());
    const ev=parts[1]||'';
    if(ev==='POLL'){ polls++; return; }
    const ts=parts[0]||'', rest=parts.slice(2).filter(x=>x&&!/=$/.test(x)).join(' · ');
    rows.push(`<div class="rp-arow ev-${ev.replace(/\W/g,'')}" title="${escHtml(ts)}"><span class="rp-at">${escHtml(ts.slice(11,19)||ts)}</span>
      <div class="rp-am"><b>${escHtml(ev.replace(/_/g,' ')||'EVENT')}</b>${rest?`<small>${escHtml(rest)}</small>`:''}</div></div>`);
  });
  const foot=polls?`<div class="rp-foot">${polls} heartbeat poll${polls===1?'':'s'} hidden</div>`:'';
  return {count:rows.length, html:(rows.join('')||'<div class="rp-empty">No events in this session yet.<small>Session start, resets and agent events land here.</small></div>')+foot};
}
let _auditLastRender=0;
async function _renderRpAudit(){
  const el=document.getElementById('rp-audit');
  if(!el || _rightTab!=='audit') return;
  const now=Date.now();
  if(now-_auditLastRender<5000) return;
  _auditLastRender=now;
  try{
    const d=await fetch('/auditlog').then(r=>r.json());
    const p=_rpAuditParts(d.lines);
    _rpTabCount('audit', d.current?p.count:0);
    if(_rpPainted.get(el)!==p.html){ el.innerHTML=p.html; _rpPainted.set(el,p.html); }
  }catch(e){}
}
/* /errors already returns session_id per error (needed for the join
   against sessions), but nothing ever read it -- there was no way to
   jump from a past error straight to that session's full History
   context. */
async function _jumpToSessionHistory(sid){
  if(!sid) return;
  await setView('history');
  await showHistoryDetail(sid);
}
async function _selectTagFilter(tag){
  if(!tag) return;
  _histTab='sessions';
  await renderHistory();
  setHistSearch(tag);
}
let _errorsLastRender=0;
let _errorsCache=[];
let _errorSearchQuery='';
let _errorTypeFilter='all';
/* Errors have no structured category in the DB (error_msg is whatever
   string the agent/hook happened to report) -- classify by matching the
   handful of shapes that actually recur in production (worktree/git setup
   failures, the user declining a permission prompt, upstream network
   timeouts, "model temporarily unavailable", and raw tracebacks), falling
   back to 'other' rather than inventing a bucket per one-off message. */
const _ERROR_TYPE_LABELS={worktree:'WORKTREE/GIT',rejected:'USER REJECTED',unavailable:'MODEL UNAVAILABLE',network:'NETWORK/TIMEOUT',exception:'EXCEPTION',other:'OTHER'};
function _classifyError(msg){
  const m=msg||'';
  if(/worktree/i.test(m)) return 'worktree';
  if(/doesn't want to proceed|was rejected/i.test(m)) return 'rejected';
  if(/temporarily unavailable/i.test(m)) return 'unavailable';
  if(/timeout|connection refused|connection dropped|econnreset|upstream/i.test(m)) return 'network';
  if(/traceback \(most recent call last\)/i.test(m)) return 'exception';
  return 'other';
}
function _filterErrors(errs,query,type){
  const q=(query||'').trim().toLowerCase();
  return errs.filter(e=>{
    if(type && type!=='all' && _classifyError(e.error_msg)!==type) return false;
    if(!q) return true;
    return (e.error_msg||'').toLowerCase().includes(q)
      || (e.name||'').toLowerCase().includes(q)
      || (e.project||'').toLowerCase().includes(q);
  });
}
function _renderErrorTypeBar(){
  const bar=document.getElementById('rp-errors-types');
  if(!bar) return;
  if(!_errorsCache.length){ bar.style.display='none'; return; }
  const counts={};
  _errorsCache.forEach(e=>{ const t=_classifyError(e.error_msg); counts[t]=(counts[t]||0)+1; });
  if(_errorTypeFilter!=='all' && !counts[_errorTypeFilter]) _errorTypeFilter='all';
  const types=Object.keys(counts).sort((a,b)=>counts[b]-counts[a]);
  bar.style.display='flex';
  const btn=(f,label)=>`<button class="sfbtn error ${_errorTypeFilter===f?'active':''}" style="padding:3px 8px;font-size:9px" onclick="_setErrorTypeFilter('${f}')">${label}</button>`;
  bar.innerHTML=btn('all',`ALL ${_errorsCache.length}`)
    +types.map(t=>btn(t,`${_ERROR_TYPE_LABELS[t]||t.toUpperCase()} ${counts[t]}`)).join('');
}
function _setErrorTypeFilter(type){
  _errorTypeFilter=type;
  _renderErrorTypeBar();
  _renderErrorsList();
}
function _renderErrorsList(){
  const el=document.getElementById('rp-errors');
  if(!el) return;
  const errs=_filterErrors(_errorsCache,_errorSearchQuery,_errorTypeFilter);
  if(!_errorsCache.length){
    el.innerHTML='<div class="rp-empty">No errors recorded.<small>Failed agents and tool errors show up here.</small></div>';
    return;
  }
  if(!errs.length){
    el.innerHTML='<div class="rp-empty">No errors match your filters.</div>';
    return;
  }
  el.innerHTML=_groupErrors(errs).map(g=>{
    const e=g[0], names=[...new Set(g.map(x=>x.name||x.agent_id||'agent'))];
    const who=names[0]+(names.length>1?` +${names.length-1} more`:'');
    return `<div class="rp-err" onclick="_jumpToSessionHistory(${jsq(e.session_id)})" title="Open the latest of these sessions in History">
      <div class="rp-err-h"><b>${escHtml(who)}</b>${g.length>1?`<span class="rp-err-n">×${g.length}</span>`:''}
        ${g.some(x=>x.detected_via==='transcript')?`<span class="hookmiss-badge" title="${escHtml(_hookMissTitle(e))}">⚠ HOOK MISS</span>`:''}</div>
      <div class="rp-err-meta">${escHtml([e.model, e.project, `${e.date||''} ${e.completed_at||''}`.trim()].filter(Boolean).join(' · '))}${g.length>1?' · latest':''}</div>
      <div class="rp-err-msg">${escHtml(e.error_msg||'')}</div>
      ${(e.error_msg||'').length>160||(e.error_msg||'').split('\n').length>4?`<button class="rp-err-more" onclick="event.stopPropagation();this.parentNode.classList.toggle('open');this.textContent=this.parentNode.classList.contains('open')?'less':'more'">more</button>`:''}
    </div>`;
  }).join('');
}
/* Pure: errors with the same message grouped, keeping the list's order
   (newest first) -- one model outage logged ten times is one card, ×10. */
function _groupErrors(errs){
  const by=new Map();
  (errs||[]).forEach(e=>{ const k=(e.error_msg||'').trim(); if(!by.has(k)) by.set(k,[]); by.get(k).push(e); });
  return [...by.values()];
}
function _setErrorSearch(val){
  _errorSearchQuery=val.toLowerCase();
  const inp=document.getElementById('rp-errors-search-input');
  const clr=document.getElementById('rp-errors-search-clear');
  if(inp && inp.value!==val) inp.value=val;
  if(clr) clr.classList.toggle('show', val.length>0);
  _renderErrorsList();
}
async function _renderRpErrors(){
  const el=document.getElementById('rp-errors');
  if(!el || _rightTab!=='errors') return;
  const now=Date.now();
  if(now-_errorsLastRender<5000) return;
  _errorsLastRender=now;
  try{
    _errorsCache=await fetch('/errors').then(r=>r.json());
    _rpTabCount('errors', _errorsCache.length, true);
    _renderErrorTypeBar();
    _renderErrorsList();
  }catch(e){}
}
function toggleRightPanel(){
  _panelCollapsed=!_panelCollapsed;
  document.querySelector('.root').classList.toggle('panel-collapsed',_panelCollapsed);
  const btn=document.getElementById('panel-toggle-btn');
  if(btn) btn.textContent=_panelCollapsed?'▶':'◀';
  _savePrefs();
}
