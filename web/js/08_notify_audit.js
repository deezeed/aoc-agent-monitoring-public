/* ── Notification center (Settings -> Notify): every alert type x channel,
   from GET /notify_center. Webhook toggles also mirror into _webhookEvents
   and the hidden st-wh-* boxes, so a later saveSettings() keeps them. */
let _ncData=null;
const _NC_WH_IDS={done:'st-wh-done',error:'st-wh-error',stuck:'st-wh-stuck',burn_spike:'st-wh-burn',weekly_digest:'st-wh-digest',
  waiting_nudge:'st-wh-waiting',cost_spike:'st-wh-cost',budget_alert:'st-wh-budget',rate_limit:'st-wh-ratelimit'};
const _NC_ICONS={toast:'🖥',push:'📱',webhook:'🔗'};
/* Pure: the toggle table. d = /notify_center payload. */
function _ncHtml(d){
  const types=(d&&d.types)||[];
  if(!types.length) return '<div class="nc-l" style="grid-column:1/-1">No notification types.</div>';
  let h='<div class="nc-h" style="text-align:left">ALERT</div><div class="nc-h">PC</div><div class="nc-h">PHONE</div><div class="nc-h">WEBHOOK</div><div class="nc-h"></div>';
  let group='';
  for(const t of types){
    if(t.group!==group){ group=t.group; h+=`<div class="nc-g">${escHtml(String(group).toUpperCase())}</div>`; }
    h+=`<div class="nc-l">${escHtml(t.label)}${t.quiet===false?'<small title="Sent even in quiet hours and for muted projects">· always</small>':''}</div>`;
    for(const ch of ['toast','push','webhook']){
      const on=(t.channels||{})[ch];
      if(on===null||on===undefined){ h+='<div class="nc-c nc-na" title="Not sent this way">–</div>'; continue; }
      const off=ch==='webhook'&&!d.webhook_url;
      h+=`<div class="nc-c"><input type="checkbox" aria-label="${escHtml(t.label)}: ${ch}"${on?' checked':''}${off?' disabled title="Set a webhook URL below first"':''}`
        +` onchange="_ncSet(${jsq(t.key)},${jsq(ch)},this.checked,this)"></div>`;
    }
    h+=`<div class="nc-c"><button class="adp-btn" style="font-size:9px;padding:2px 6px" title="Send a sample through the channels that are on" onclick="_ncTest(${jsq(t.key)})">Test</button></div>`;
  }
  return h;
}
/* Pure: the one-line status above the table. */
function _ncStatusHtml(d){
  if(!d) return '';
  const q=d.quiet_start&&d.quiet_end
    ?`Quiet hours ${escHtml(d.quiet_start)}–${escHtml(d.quiet_end)}`+(d.quiet_now?' — <b style="color:var(--o)">on now</b>, alerts held except the “always” ones':'')
    :'No quiet hours (set them under Notifications below)';
  const m=(d.muted_projects||[]).length?` · ${d.muted_projects.length} muted project${d.muted_projects.length===1?'':'s'}`:'';
  const p=` · ${d.push_devices||0} phone${d.push_devices===1?'':'s'} for push`;
  const w=d.webhook_url?' · webhook set':' · no webhook URL';
  return q+m+p+w+'.';
}
/* Pure: recent log, newest first (the server already orders it). */
function _ncLogHtml(log, nowS){
  const rows=(log||[]).slice(0,40);
  if(!rows.length) return '<div style="color:var(--t3);font-size:10px;padding:4px 0">Nothing sent yet.</div>';
  const ago=t=>{ const s=Math.max(0,Math.floor(nowS-t)); return s<60?'just now':s<3600?Math.floor(s/60)+'m ago':s<86400?Math.floor(s/3600)+'h ago':Math.floor(s/86400)+'d ago'; };
  return rows.map(e=>`<div class="nc-e"><span class="nc-t">${ago(e.t||0)}</span><span title="${escHtml(String(e.channel||''))}">${_NC_ICONS[e.channel]||'•'}</span>`
    +`<span class="nc-m">${e.test?'<span>[test]</span> ':''}${escHtml(String(e.title||'').replace(/^\[test\] /,''))}${e.body?' <span>— '+escHtml(String(e.body))+'</span>':''}</span></div>`).join('');
}
function _ncRender(){
  const t=document.getElementById('nc-table'); if(!t||!_ncData) return;
  t.innerHTML=_ncHtml(_ncData);
  document.getElementById('nc-status').innerHTML=_ncStatusHtml(_ncData);
  document.getElementById('nc-log').innerHTML=_ncLogHtml(_ncData.log, Date.now()/1000);
}
async function _ncLoad(){
  if(!document.getElementById('nc-table')) return;
  try{ _ncData=await (await fetch('/notify_center')).json(); }
  catch(e){ document.getElementById('nc-status').textContent='Could not reach AOC.'; return; }
  _ncRender();
}
async function _ncSet(kind, channel, on, box){
  try{
    const d=await (await fetch('/notify_center/set',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind,channel,on})})).json();
    if(!d.ok){ if(box) box.checked=!on; showToast('warning','Not changed',d.error||''); return; }
    _ncData=d;
    if(channel==='webhook'){
      _webhookEvents[kind]=!!on;
      const el=_NC_WH_IDS[kind]&&document.getElementById(_NC_WH_IDS[kind]); if(el) el.checked=!!on;
    }
    _ncRender();
  }catch(e){ if(box) box.checked=!on; showToast('warning','Not saved','Could not reach AOC.'); }
}
async function _ncTest(kind){
  try{
    const d=await (await fetch('/notify_center/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind})})).json();
    if(!d.ok){ showToast('warning','Not sent',d.error||''); return; }
    const sk=Object.entries(d.skipped||{}).map(([c,w])=>`${c}: ${w}`).join(', ');
    showToast(d.sent.length?'done':'warning', d.sent.length?'Test sent':'Nothing sent',
      (d.sent.length?'Via '+d.sent.join(', ')+'.':'')+(sk?' Skipped — '+sk+'.':''));
    _ncLoad();
  }catch(e){ showToast('warning','Not sent','Could not reach AOC.'); }
}
async function _ncClear(){
  try{ await fetch('/notify_center/clear',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}); }catch(e){}
  _ncLoad();
}
async function _capsLoad(){
  try{
    const d=await (await fetch('/budget_caps')).json();
    const a=document.getElementById('st-cap-session'), b=document.getElementById('st-cap-day'), t=document.getElementById('st-cap-today');
    if(a) a.value=d.session_usd||'';
    if(b) b.value=d.day_usd||'';
    if(t) t.textContent=d.day_usd?`Today so far: $${(d.today_spend||0).toFixed(2)}${d.day?' — cap reached':''}`:'';
  }catch(e){}
}
async function _capsSave(){
  const val=id=>{ const v=parseFloat(document.getElementById(id).value); return isFinite(v)&&v>0?v:null; };
  try{
    await fetch('/budget_caps',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_usd:val('st-cap-session'),day_usd:val('st-cap-day')})});
    _capsLoad();
  }catch(e){ showToast('warning','Not saved','Could not reach AOC.'); }
}
function _loopBadgeHtml(s){
  const l=s&&s.loop;
  if(!l||!l.text) return '';
  const tip=l.text+'. Your next message or a successful commit clears this. If it keeps going, check its terminal or force-stop it.';
  return `<div class="loop-flag" title="${escHtml(tip)}"><span class="loop-lbl">⟳ Looks stuck</span><span class="loop-text">${escHtml(l.text)}</span></div>`;
}
function _ctxMeterHtml(s, nowSec){
  const c=s&&s.context;
  if(!c||typeof c.pct!=='number'||!c.window) return '';
  const fmtTok=n=>n>=1e6?(n/1e6).toFixed(n%1e6?1:0)+'M':n>=1e3?Math.round(n/1e3)+'k':String(n);
  const pct=Math.max(0,Math.min(100,c.pct));
  const lvl=c.level==='high'||c.level==='warn'?c.level:'ok';
  const hint=lvl==='high'?' · compact soon':'';
  let cache='';
  if(s.session_active!==false&&s.waiting_on_you&&c.cache_expires_at){
    const left=c.cache_expires_at-nowSec;
    const usd=n=>typeof n==='number'?'$'+n.toFixed(2):'';
    const re=c.recache_tokens?` (~${fmtTok(c.recache_tokens)} tok re-cached${c.recache_usd!=null?', '+usd(c.recache_usd):''})`:'';
    const warmTip=c.warm_usd!=null?` Replying while warm costs ~${usd(c.warm_usd)} to read it.`:'';
    const soon=left>0&&left<=(c.cache_ttl==='5m'?120:600);
    cache=left>0
      ?`<span class="ctx-cache${soon?' soon':''}" title="Prompt cache (${escHtml(c.cache_ttl||'')}) expires ${new Date(c.cache_expires_at*1000).toLocaleTimeString()}. Reply before then to keep it warm${escHtml(re)}.${escHtml(warmTip)}">cache warm · ${escHtml(_fmtDurationDHM(Math.ceil(left/60)*60))} left${soon&&c.recache_usd!=null?' · '+escHtml(usd(c.recache_usd))+' if cold':''}</span>`
      :`<span class="ctx-cache cold" title="The prompt cache expired: the next message writes the whole context to cache again${escHtml(re)}.">cache cold${c.recache_usd!=null?' · next msg ~'+escHtml(usd(c.recache_usd)):''}</span>`;
  }
  const title=`Context window: ${(c.tokens||0).toLocaleString()} of ${c.window.toLocaleString()} tokens (${pct.toFixed(1)} %). Claude Code auto-compacts close to the limit.`;
  return `<div class="ctx-meter ctx-${lvl}" title="${escHtml(title)}"><span class="ctx-lbl">CTX</span><span class="ctx-bar"><span style="width:${pct.toFixed(1)}%"></span></span><span class="ctx-val">${Math.round(pct)}% · ${fmtTok(c.tokens||0)}/${fmtTok(c.window)}${hint}</span>${cache}</div>`+_ctxSaveHtml(s);
}
/* Pure: the strip above the views listing sessions waiting > 10 min. */
function _waitingStripHtml(list){
  if(!list.length) return '';
  const items=list.slice(0,4).map(s=>{
    const name=s.display_name||s.project||(s.cwd||'').split(/[\\/]/).filter(Boolean).pop()||s.id.slice(0,8);
    const info=_lastMessageInfo(s.last_message, 140);
    const wl=_waitLabel(s);
    const text=wl.blocked&&s.waiting_message?s.waiting_message:(info?info.text:'');
    const focus=s._isLocal===false?'':` onclick="_focusSession(${jsq(s.id)})"`;
    return `<button class="woy-item${wl.blocked?' blocked':''}"${focus||` onclick="setView('cli')"`} title="${escHtml((focus?'Switch to its terminal. ':'')+(wl.blocked?wl.tip:(info?info.full.slice(0,1500):'Open the CLI view')))}">
      <span class="woy-name">${escHtml(name)}</span><span class="woy-wait">${escHtml(_fmtDurationDHM(s.waiting_secs||0))}</span>
      ${text?`<span class="woy-text">${wl.icon?wl.icon+' ':(info&&info.asked?'❓ ':'')}${escHtml(text)}</span>`:''}</button>`;
  }).join('');
  const more=list.length>4?`<span class="woy-more">+${list.length-4} more</span>`:'';
  return `<span class="woy-head">⏳ ${list.length} WAITING ON YOU</span>${items}${more}`;
}
function _waitingStripUpdate(sr){
  const el=document.getElementById('waiting-strip');
  if(!el) return;
  const html=_waitingStripHtml(_waitingSessions(sr&&sr.sessions_list, 600));
  if(el.innerHTML!==html) el.innerHTML=html;
  el.style.display=html?'flex':'none';
}

async function _focusSession(id){
  try{
    const r=await fetch('/focus_session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:id})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok||!d.ok) showToast('warning','Terminal not found',d.error==='terminal window not found'
      ?'Could not find the window running this session.':'Could not switch to the terminal.');
  }catch(e){ showToast('warning','Terminal not found','Could not switch to the terminal.'); }
}

function _sessionBayHtml(bay, stripsHtml){
  const s=bay.session||{};
  const cwdBase=s.cwd?(s.cwd.replace(/\\/g,'/').split('/').filter(Boolean).pop()||''):'';
  const title=s.display_name||s.project||bay.project||cwdBase||(bay.sessionId?'CLI session':'Unattached agents');
  const active=bay.session ? s.session_active!==false : null;
  const waiting=active&&!!s.waiting_on_you;
  const state=active===null?''
    : waiting?`<span class="bay-state waiting${_waitLabel(s).blocked?' blocked':''}" title="${escHtml(_waitLabel(s).tip)}">${_waitLabel(s).bay}${(s.waiting_secs||0)>=60?' · '+_fmtDurationDHM(s.waiting_secs):''}</span>`
    : active?'<span class="bay-state active">Working</span>'
    : '<span class="bay-state">Closed</span>';
  const tok=(s.input_tokens||0)+(s.output_tokens||0);
  const fmtTok=n=>n>=1e6?(n/1e6).toFixed(1)+'M':n>=1e3?(n/1e3).toFixed(n>=1e5?0:1)+'k':String(n);
  const model=s.model?_ccModelName(s.model):'';
  const meta=[
    bay.isRemote&&bay.machine?'⌘ '+bay.machine:null,
    model||null,
    s.git_branch&&s.git_branch!=='HEAD'?'⎇ '+s.git_branch:null,
    s.msg_count?s.msg_count+' msg':null,
    tok?fmtTok(tok)+' tok':null,
    s.estimated_cost?'$'+Number(s.estimated_cost).toFixed(2):null,
  ].filter(Boolean).map(x=>`<span>${escHtml(x)}</span>`).join('');
  const n={run:0,err:0,done:0};
  bay.agents.forEach(a=>{ if(a.status==='error') n.err++; else if(a.status==='done') n.done++; else n.run++; });
  const counts=[n.run?`<b class="bc-run">${n.run} active</b>`:'', n.err?`<b class="bc-err">${n.err} failed</b>`:'', n.done?`<b class="bc-done">${n.done} done</b>`:''].filter(Boolean).join('');
  const collapsed=_collapsedBays.has(bay.key);
  const dismiss=active===false?`<button class="bay-btn" onclick="_dismissSession(${jsq(bay.sessionId)},${jsq(bay.machine)})" title="Dismiss closed session">Dismiss</button>`
    : active&&!bay.isRemote?`<button class="bay-btn" onclick="_focusSession(${jsq(bay.sessionId)})" title="Switch to this session's terminal window">Terminal</button>`:'';
  return `<section class="sbay${waiting?' waiting':''}${active===false?' closed':''}${collapsed?' collapsed':''}" data-bay="${escHtml(bay.key)}">
    <header class="sbay-head">
      <div class="sbay-title"><h2 title="${escHtml(s.cwd||title)}">${escHtml(title)}</h2><span class="sbay-meta">${meta}</span></div>
      <div class="sbay-right"><span class="sbay-counts">${counts}</span>${state}${dismiss}
        <button class="bay-btn" onclick="toggleBay(${jsq(bay.key)})" aria-expanded="${!collapsed}" title="${collapsed?'Show':'Hide'} this session's agents">${collapsed?'▼':'▲'}</button></div>
    </header>
    ${active?_budgetBadgeHtml(s)+_loopBadgeHtml(s)+_ctxMeterHtml(s, Date.now()/1000):''}
    ${waiting?_woyQuoteHtml(s):''}
    ${collapsed?'':`<div class="sbay-slots">${stripsHtml}</div>`}
  </section>`;
}

function renderAgents(data){
  const mainArea=document.getElementById('main-area');
  let running=0, done=0, errors=0, totalT=0, doneT=0;

  /* filter out internal hook entries written by claude hooks */
  const agents=(data.agents||[]).filter(a=>!String(a.id||'').startsWith('hook_'));

  const cardsArea = document.getElementById('cards-area');

  /* idle state — only when no CLIs are connected at all */
  if(!data.session_active && agents.length===0 && (data.sessions_count||0)===0){
    if(!cardsArea.querySelector('.idle')){
      cardsArea.innerHTML=`
      <div class="idle">
        <div class="idle-hex">
          <svg viewBox="0 0 48 48">
            <polygon points="24,4 44,14 44,34 24,44 4,34 4,14" stroke-width=".8"/>
            <circle cx="24" cy="24" r="10" stroke-width=".8"/>
            <line x1="24" y1="4" x2="24" y2="44" stroke-width=".5"/>
            <line x1="4" y1="14" x2="44" y2="14" stroke-width=".5"/>
            <line x1="4" y1="34" x2="44" y2="34" stroke-width=".5"/>
          </svg>
        </div>
        <div class="idle-title">AOC ONLINE</div>
        <div class="idle-sub">Awaiting mission orders — no active agents</div>
      </div>`;
    }
    document.getElementById('s-run').textContent='00';
    document.getElementById('s-done').textContent='00';
    const _eEl=document.getElementById('s-errors'); if(_eEl){ _eEl.textContent='—'; _eEl.style.color='var(--t3)'; }
    document.getElementById('s-tasks').textContent='—';
    document.getElementById('s-files').textContent=pad(fcCount);
    return;
  }

  /* ensure agents grid exists — remove idle placeholder without wiping filter bars */
  const _idle=cardsArea.querySelector('.idle');
  if(_idle) _idle.remove();
  if(!document.getElementById('agents')){
    if(!document.getElementById('search-wrap')){
      const _sw=document.createElement('div');
      _sw.id='search-wrap'; _sw.className='search-wrap'; _sw.style.display='none';
      _sw.innerHTML=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg><input class="search-input" id="search-input" type="text" placeholder="Search agents..." oninput="setSearch(this.value)"><button class="search-clear" id="search-clear" onclick="setSearch('')">✕</button>`;
      cardsArea.insertBefore(_sw,cardsArea.firstChild);
    }
    if(!document.getElementById('session-tab-bar')){
      const _stb=document.createElement('div');
      _stb.id='session-tab-bar'; _stb.style.cssText='display:none;gap:4px;padding:0 0 6px;flex-wrap:wrap';
      const _swRef=document.getElementById('search-wrap');
      _swRef?_swRef.after(_stb):cardsArea.insertBefore(_stb,cardsArea.firstChild);
    }
    if(!document.getElementById('status-filter-bar')){
      const _sfb=document.createElement('div');
      _sfb.id='status-filter-bar'; _sfb.style.cssText='display:none;gap:6px;padding:0 0 8px;flex-wrap:wrap';
      const _ref=document.getElementById('session-tab-bar');
      _ref?_ref.after(_sfb):cardsArea.insertBefore(_sfb,cardsArea.firstChild);
    }
    const _ag=document.createElement('div');
    _ag.className='agents'; _ag.id='agents';
    cardsArea.appendChild(_ag);
  }

  const agentsEl=document.getElementById('agents');
  if(!agentsEl) return;

  /* show search bar + session/status filters only in the AGENTS view */
  const sw=document.getElementById('search-wrap');
  if(sw) sw.style.display=(currentView==='agents'&&agents.length>0)?'flex':'none';
  if(currentView==='cli'){
    const stb=document.getElementById('session-tab-bar'); if(stb) stb.style.display='none';
    const sfb=document.getElementById('status-filter-bar'); if(sfb) sfb.style.display='none';
  }

  if(currentView==='agents') renderSessionTabBar(data, agents);
  const sessionAgents = sessionFilter===null ? agents : agents.filter(a=>a.session_id===sessionFilter);
  renderStatusFilterBar(sessionAgents);
  const statusFiltered = statusFilter==='all' ? sessionAgents : sessionAgents.filter(a=>{
    if(statusFilter==='running') return a.status==='running'||a.status==='waiting';
    return a.status===statusFilter;
  });
  const _filtered = searchQuery
    ? statusFiltered.filter(a=>(a.name||'').toLowerCase().includes(searchQuery)||(a.description||'').toLowerCase().includes(searchQuery)||(a.session_project||'').toLowerCase().includes(searchQuery))
    : statusFiltered;
  const _sPri={running:0,waiting:1,error:2,done:3};
  const visibleAgents=[..._filtered].sort((a,b)=>{
    const pa=_pinnedIds.has(a.id)?-1:_sPri[a.status]??4;
    const pb=_pinnedIds.has(b.id)?-1:_sPri[b.status]??4;
    if(pa!==pb) return pa-pb;
    /* within same status: running → newest first; done → oldest first */
    const ta=parseTimeStr(a.started_at)||0, tb=parseTimeStr(b.started_at)||0;
    return a.status==='done' ? ta-tb : tb-ta;
  });

  agentsEl.classList.toggle('list-mode', listMode);
  /* re-applied every render since #agents.innerHTML rebuild below only
     replaces children, not this container — but idle-state resets do
     recreate #agents itself (see "ensure agents grid exists" above),
     so the density class can't just be set once at init/save time. */
  _applyDensity(_density);

  if(listMode){
    const _rowById=new Map(visibleAgents.map(a=>{
      const dt=(a.tasks||[]).filter(t=>t.done).length;
      const tt=(a.tasks||[]).length;
      const pct=tt>0?Math.round(dt/tt*100):0;
      if(a.status==='running'||a.status==='waiting') running++;
      if(a.status==='done') done++;
      if(a.status==='error') errors++;
      totalT+=tt; doneT+=dt;
      let elapsedStr='';
      if(a.started_at){
        const [_eh,_em,_es]=(a.started_at||'').split(':').map(Number);
        const startMs=new Date().setHours(_eh||0,_em||0,_es||0,0);
        let endMs=Date.now();
        if(a.status!=='running'&&a.completed_at){
          const [_ch,_cm,_cs]=(a.completed_at||'').split(':').map(Number);
          endMs=new Date().setHours(_ch||0,_cm||0,_cs||0,0);
        }
        const secs=Math.max(0,Math.floor((endMs-startMs)/1000));
        elapsedStr=secs<60?`${secs}s`:`${Math.floor(secs/60)}m ${secs%60}s`;
      }
      const col=sCol(a.status);
      const tokStr=a.tokens_used?`<span style="font-size:9px;font-family:var(--font2);color:rgba(76,194,133,.7);margin-left:4px">$${_agentCost(a).toFixed(4)}</span>`:'';
      const pinClass=_pinnedIds.has(a.id)?'style="border-left-color:rgba(224,161,58,.6)"':'';
      return [a, `<div class="agent-row ${escHtml(a.status)}" ${pinClass}>
        <div class="ar-unit">${escHtml(_deriveUnit(a))}</div>
        ${a.detected_via==='transcript'?`<span style="color:rgba(224,161,58,.95);font-size:10px;flex-shrink:0" title="${escHtml(_hookMissTitle(a))}">⚠</span>`:''}
        <div class="ar-name" title="${escHtml(a.description||a.name)}">${escHtml(a.name)}</div>
        ${tokStr}
        <div class="ar-pill ${escHtml(a.status)}">${escHtml(stxt(a))}</div>
        <div class="ar-prog"><div class="ar-prog-fill" style="width:${pct}%;background:${col}"></div></div>
        <div class="ar-elapsed">${elapsedStr||'—'}</div>
        <button class="ar-dismiss" onclick="dismissAgent(${jsq(a.id)},${jsq(a.machine||'')})" title="Dismiss">×</button>
      </div>`];
    }));
    /* Same session bays as card mode, just with compact rows inside. */
    agentsEl.innerHTML=_groupIntoBays(visibleAgents, data.sessions_list||[])
      .map(bay=>_sessionBayHtml(bay, bay.agents.map(a=>_rowById.get(a)).join(''))).join('');
  } else {

  /* detect new agents and newly completed tasks before render */
  const _newThisRender = new Set(visibleAgents.filter(a=>!_knownAgentIds.has(a.id)).map(a=>a.id));
  const _newCompletedKeys = new Set();
  visibleAgents.forEach(a=>{
    (a.tasks||[]).forEach((t,i)=>{
      const key=`${a.id}:${i}`;
      if(t.done && !_completedTaskKeys.has(key)){ _newCompletedKeys.add(key); }
      if(t.done) _completedTaskKeys.add(key);
    });
  });
  /* clean up keys for removed agents */
  const _visibleIdSet=new Set(visibleAgents.map(a=>a.id));
  for(const id of _knownAgentIds){ if(!_visibleIdSet.has(id)){ _knownAgentIds.delete(id); for(const k of _completedTaskKeys){ if(k.startsWith(id+':')) _completedTaskKeys.delete(k); } } }

  /* Progress-bar width/arc don't ease via CSS transition on their own — every
     render fully replaces agentsEl.innerHTML, so a persisting agent's bar
     never has a "before" DOM state to animate from; it just snaps to the new
     value on the freshly-created node. Capture the old values here (before
     they're destroyed) so a FLIP-style patch after the rebuild can force a
     real transition — same trick renderKpi already uses for the cost gauge
     via a direct .style mutation on a persistent node (see cost-gauge-arc). */
  const _progBefore={};
  visibleAgents.forEach(a=>{
    if(_newThisRender.has(a.id)) return;  // no "before" state to ease from
    const old=document.getElementById('card-'+(a._domId||a.id));
    if(!old) return;
    const fill=old.querySelector('.prog-fill');
    const arc=old.querySelector('.arc-wrap circle:last-of-type');
    _progBefore[a._domId||a.id]={
      fillWidth: fill?fill.style.width:null,
      arcDash:   arc?arc.getAttribute('stroke-dasharray'):null,
    };
  });

  const _stripHtml=visibleAgents.map(a=>{
    const dt=(a.tasks||[]).filter(t=>t.done).length;
    const tt=(a.tasks||[]).length;
    const pct=tt>0?Math.round(dt/tt*100):0;
    if(a.status==='running'||a.status==='waiting') running++;
    if(a.status==='done') done++;
    if(a.status==='error') errors++;
    totalT+=tt; doneT+=dt;
    /* if scheduled for removal, show as still running (no done flash) */
    const isFading=_removingIds.has(a.id);
    const dispStatus=isFading?'running':a.status;
    const col=sCol(dispStatus);
    const {c,d}=arcDash(pct);
    const stLabel=stxt({status:dispStatus});
    /* JS-computed opacity: persists across innerHTML rebuilds (CSS animation resets on re-render) */
    const fadeOpacity = isFading ? Math.max(0, 1 - (Date.now()-(_fadingStartedAt[a.id]||Date.now()))/10000) : 1;

    /* live elapsed / total duration */
    let elapsedStr='';
    if(a.started_at){
      const [_eh,_em,_es]=(a.started_at||'').split(':').map(Number);
      const startMs=new Date().setHours(_eh||0,_em||0,_es||0,0);
      let endMs=Date.now();
      if(a.status!=='running'&&a.completed_at){
        const [_ch,_cm,_cs]=(a.completed_at||'').split(':').map(Number);
        endMs=new Date().setHours(_ch||0,_cm||0,_cs||0,0);
      }
      const secs=Math.max(0,Math.floor((endMs-startMs)/1000));
      elapsedStr=secs<60?`${secs}s`:`${Math.floor(secs/60)}m ${secs%60}s`;
    }

    const tasks=a.tasks.map((t,i)=>{
      const isNewDone=t.done&&_newCompletedKeys.has(`${a.id}:${i}`);
      return `<div class="task ${t.done?'d':'p'}">
        <span class="task-chk${isNewDone?' task-chk-pop':''}">${t.done?'✓':'·'}</span>
        <span class="task-lbl">${escHtml(t.label||'')}</span>
      </div>`;
    }).join('');

    /* token counter row */
    const tokensHtml = a.tokens_used
      ? (() => {
          const pct=_ctxPct(a);
          return `<div style="display:flex;align-items:center;gap:6px;margin-top:6px;padding:4px 8px;border-radius:6px;background:rgba(var(--c-rgb),.05);border:1px solid rgba(var(--c-rgb),.1);">
           <span style="font-size:10px;color:var(--t3);font-family:var(--font2);letter-spacing:.06em">TOKENS</span>
           <span style="font-size:11px;font-family:var(--font2);font-weight:700;color:var(--c)">${Number(a.tokens_used).toLocaleString()}</span>
           ${pct!==null?`<div style="flex:1;height:2px;border-radius:999px;background:rgba(255,255,255,.05);overflow:hidden;"><div style="height:100%;width:${pct}%;background:linear-gradient(90deg,var(--c2),var(--c));border-radius:999px;"></div></div>`:''}
         </div>`;
        })() : '';

    /* task velocity */
    const doneTasks=a.tasks.filter(t=>t.done&&t.ts);
    let velocityHtml='';
    if(doneTasks.length>=2&&a.started_at){
      const firstTs=doneTasks[0].ts, lastTs=doneTasks[doneTasks.length-1].ts;
      const [_vh,_vm,_vs]=(a.started_at||'').split(':').map(Number);
      const spanMin=(Date.now()-new Date().setHours(_vh||0,_vm||0,_vs||0,0))/60000||1;
      const tpm=(doneTasks.length/Math.max(spanMin,0.5)).toFixed(1);
      const remaining=a.tasks.filter(t=>!t.done).length;
      const etaMins=remaining>0?(remaining/parseFloat(tpm)).toFixed(0):0;
      velocityHtml=`<div style="font-size:10px;color:var(--t3);font-family:var(--font2);margin-top:3px;display:flex;gap:8px;">
        <span>⚡ ${tpm} tasks/min</span>${remaining>0&&etaMins>0?`<span>ETA ~${etaMins}m</span>`:''}
      </div>`;
    }

    /* error message drill-down + copy */
    const safeErr=escHtml(a.error_message||'');
    const errorHtml = (a.status==='error' && a.error_message)
      ? `<div style="margin-top:8px;padding:6px 10px;border-radius:8px;background:rgba(239,106,87,.06);border:1px solid rgba(239,106,87,.2);border-left:3px solid var(--r);" data-err="${safeErr}">
           <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
             <div style="font-size:10px;font-family:var(--font2);color:var(--r);letter-spacing:.08em;flex:1">ERROR</div>
             <button onclick="copyText(this,this.closest('[data-err]').dataset.err)" style="font-size:10px;padding:1px 8px;border-radius:4px;border:1px solid rgba(239,106,87,.3);background:rgba(239,106,87,.08);color:rgba(240,164,151,.8);cursor:pointer;font-family:var(--font)">copy</button>
             <button onclick="_copyErrorContext(${jsq(a.id)})" title="Copy project + task + error + log tail as one paste-ready block" style="font-size:10px;padding:1px 8px;border-radius:4px;border:1px solid rgba(239,106,87,.3);background:rgba(239,106,87,.08);color:rgba(240,164,151,.8);cursor:pointer;font-family:var(--font)">copy context</button>
           </div>
           <div style="font-size:11px;color:rgba(240,164,151,.85);font-family:var(--font2);line-height:1.5;word-break:break-all">${safeErr}</div>
         </div>` : '';

    /* file changes */
    const filesHtml = (a.files_changed||[]).length
      ? `<div style="display:flex;flex-direction:column;gap:2px;margin-top:6px;">${
          (a.files_changed||[]).map(f=>`<div class="fe ${escHtml(f.type||'changed')}" onclick="showDiff(${jsq(f.path)})" style="cursor:pointer;" title="Click to see diff">
            <span class="fe-badge">${escHtml(String(f.type||'MOD').toUpperCase().slice(0,3))}</span>
            <span class="fe-name">${escHtml(f.path.split('/').pop())}</span>
            ${f.lines?`<span class="fe-lines">${escHtml(f.lines)}L</span>`:''}
          </div>`).join('')
        }</div>` : '';

    const agentLogs = (a.log||[]);
    const logHtml = agentLogs.length
      ? `<div class="log-toggle" id="lt-${escHtml(a.id)}" onclick="toggleAgentLog(${jsq(a.id)})">
           <svg viewBox="0 0 10 10"><path d="M3 2l4 3-4 3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
           LOGS <span style="margin-left:3px;opacity:.6">(${agentLogs.length})</span>
         </div>
         <div class="log-panel" id="lp-${escHtml(a.id)}">
           ${agentLogs.slice(-30).map(e=>
             `<div class="log-entry">${escHtml(String(e))}</div>`
           ).join('')}
         </div>`
      : '';

    const sessProj=a.session_project||'';
    const sessBadge=sessProj?`<div style="font-size:9px;font-family:var(--font2);color:var(--t3);letter-spacing:.06em;margin-bottom:4px;opacity:.7;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(sessProj)}">// ${escHtml(sessProj)}</div>`:'';
    const isCollapsed=_collapsedIds.has(a.id);
    const isPinned=_pinnedIds.has(a.id);
    const isNewCard=_newThisRender.has(a.id);
    const domId=a._domId||a.id;  // machine-namespaced when merged from multiple AOC instances, else just a.id
    const isRemote=a._isLocal===false;
    /* Flight strip (same identity as the cloud board): the holder band
       carries status as colour + glyph + word; the paper face carries the
       callsign and the figures a controller scans; the back (hidden when
       collapsed) keeps every drill-down the old card had. Ids/classes other
       code relies on stay: card-<domId>, cb-<domId> (.cost-badge flash),
       .prog-fill (FLIP ease), lt-/lp- (logs), .card-cmp-btn. */
    const _band={running:['▶','Running'],waiting:['■','Waiting'],done:['✓','Done'],error:['✕','Failed']}[dispStatus]||['•',String(dispStatus||'')];
    const _cur=(a.tasks||[]).find(t=>!t.done);
    /* a running agent whose list is all ticked has not finished -- don't say "done" */
    const _curTxt=a.status==='error'?(a.error_message||'Failed'):(tt===0?'No task list':_cur?(_cur.label||''):a.status==='running'?'Still working…':'All tasks done');
    const _ticks=(a.tasks||[]).slice(0,14).map(t=>`<b class="${t.done?'on':''}"></b>`).join('')+(tt>14?`<i>+${tt-14}</i>`:'');
    /* No "// project" here: the session bay around the strip already names it. */
    const _sub=[_deriveUnit(a), a.subagent_type, a.model?_ccModelName(a.model):null].filter(Boolean).map(escHtml).join(' · ');
    const _tokFmt=n=>{ n=Number(n)||0; return n>=1e6?(n/1e6).toFixed(1)+'M':n>=1e3?(n/1e3).toFixed(n>=1e5?0:1)+'k':String(n); };
    const _stuck=_stuckSecs(a);
    return `<article id="card-${escHtml(domId)}" class="strip-card st-${escHtml(dispStatus)}${isFading?' agent-fading':''}${isCollapsed?' collapsed':''}${isPinned?' pinned':''}${isNewCard?' strip-enter':''}" style="${isFading?`opacity:${fadeOpacity.toFixed(3)};`:''}" onclick="${isCollapsed?`toggleCollapse(${jsq(a.id)},event)`:''}">
      <div class="sc-band" aria-hidden="true"><span class="sc-glyph">${_band[0]}</span><span class="sc-word">${escHtml(_band[1])}</span></div>
      <div class="sc-paper">
        <div class="sc-face">
          <div class="sc-cell sc-call">
            <div class="sc-callsign" title="${escHtml(a.description||a.name)}">${isPinned?'<span class="sc-pin" title="Pinned">◈</span>':''}${escHtml(a.name)}</div>
            <div class="sc-sub">${_sub}</div>
            <div class="sc-flags">
              <span class="sr-only">${escHtml(stLabel)}</span>
              ${isRemote?`<span class="sc-flag" title="From remote machine">⌘ ${escHtml(a.machine)}</span>`:''}
              ${a.detected_via==='transcript'?`<span class="sc-flag warn" title="${escHtml(_hookMissTitle(a))}">⚠ hook miss</span>`:''}
              ${_stuck>300?`<span class="sc-flag warn">⚠ stuck ${_stuck<3600?Math.floor(_stuck/60)+'m':Math.floor(_stuck/3600)+'h'}</span>`:''}
            </div>
          </div>
          <div class="sc-cell sc-tasks">
            <span class="sc-k">Tasks ${tt?`${dt}/${tt}`:''}</span>
            ${tt?`<span class="sc-ticks">${_ticks}</span>`:''}
            <div class="sc-prog"><div class="prog-fill" style="width:${pct}%"></div></div>
            <span class="sc-cur${a.status==='error'?' err':''}">${escHtml(_curTxt)}</span>
          </div>
          <div class="sc-cell sc-num"><span class="sc-k">Time</span><span class="sc-v">${elapsedStr||'—'}</span></div>
          <div class="sc-cell sc-num"><span class="sc-k">Tokens</span><span class="sc-v">${a.tokens_used?_tokFmt(a.tokens_used):'—'}</span></div>
          <div class="sc-cell sc-num"><span class="sc-k">Cost</span><span class="sc-v">${a.tokens_used?`<span class="cost-badge" data-cost="${a.tokens_used}" id="cb-${escHtml(domId)}">$${_agentCost(a).toFixed(3)}</span>`:'—'}</span></div>
          <div class="sc-tools">
            <button class="sc-tool" onclick="openAgentDetail(${jsq(a.id)},event)" title="Agent detail drill-down">INFO</button>
            <button class="sc-tool card-cmp-btn ${_compareIds.includes(a.id)?'selected':''}" data-id="${a.id}" onclick="toggleCompare(${jsq(a.id)},event)" title="${_compareIds.includes(a.id)?'Remove from compare':'Add to compare'}">⊞</button>
            <button class="sc-tool" onclick="togglePin(${jsq(a.id)},event)" title="${isPinned?'Unpin':'Pin to top'}">${isPinned?'◈':'◉'}</button>
            <button class="sc-tool" onclick="toggleCollapse(${jsq(a.id)},event)" title="${isCollapsed?'Show details':'Hide details'}" aria-expanded="${!isCollapsed}">${isCollapsed?'▼':'▲'}</button>
            <button class="sc-tool" onclick="dismissAgent(${jsq(a.id)},${jsq(a.machine||'')})" title="Dismiss">×</button>
          </div>
        </div>
        ${isCollapsed?'':`<div class="sc-back">
          ${a.description&&a.description!==a.name?`<div class="sc-desc">${escHtml(a.description)}</div>`:''}
          ${tt?`<div class="tasks">${tasks}</div>`:''}
          ${errorHtml}
          ${velocityHtml}
          ${filesHtml}
          ${logHtml}
        </div>`}
      </div>
    </article>`;
  });

  /* Session bays (same identity as the cloud board): every CLI session is a
     bay holding its own strips. Bays keep visibleAgents' order -- a bay
     sits where its highest-priority strip would have, so pinned/running
     work still comes first. */
  const _htmlById=new Map(visibleAgents.map((a,i)=>[a,_stripHtml[i]]));
  agentsEl.innerHTML=_groupIntoBays(visibleAgents, data.sessions_list||[])
    .map(bay=>_sessionBayHtml(bay, bay.agents.map(a=>_htmlById.get(a)).join(''))).join('');

  /* FLIP-patch: snap each surviving card's bar/arc back to its pre-rebuild
     value, force a reflow, then let the next frame ease to the real target —
     this is what actually makes the .prog-fill / arc-wrap circle CSS
     transitions (defined once, unchanged) visibly animate. */
  Object.keys(_progBefore).forEach(id=>{
    const before=_progBefore[id];
    const el=document.getElementById('card-'+id);  // id here is already the domId (see _progBefore's keys above)
    if(!el) return;
    const fill=el.querySelector('.prog-fill');
    if(fill && before.fillWidth!=null && before.fillWidth!==fill.style.width){
      const target=fill.style.width, origT=fill.style.transition;
      fill.style.transition='none';
      fill.style.width=before.fillWidth;
      void fill.offsetWidth;  // force reflow
      fill.style.transition=origT;
      requestAnimationFrame(()=>{ fill.style.width=target; });
    }
    const arc=el.querySelector('.arc-wrap circle:last-of-type');
    if(arc && before.arcDash!=null){
      const target=arc.getAttribute('stroke-dasharray');
      if(target!==before.arcDash){
        const origT=arc.style.transition;
        arc.style.transition='none';
        arc.setAttribute('stroke-dasharray', before.arcDash);
        void arc.getBoundingClientRect();  // force reflow for SVG
        arc.style.transition=origT;
        requestAnimationFrame(()=>{ arc.setAttribute('stroke-dasharray', target); });
      }
    }
  });
  } /* end else (card mode) */

  /* track known agent IDs for next render cycle */
  visibleAgents.forEach(a=>_knownAgentIds.add(a.id));

  /* ── CLI view: always render session cards ── */
  if(currentView==='cli'){
    const slist=(data.sessions_list||[]);
    const ghostCount=Math.max(0,(data.sessions_count||0)-slist.length);
    const ghosts=Array.from({length:ghostCount},()=>`<div class="card" style="opacity:.55">
      <div class="card-head">
        <div class="unit-badge" style="border-color:rgba(var(--c-rgb),.4);color:rgba(var(--c-rgb),.6);font-size:9px">CLI</div>
        <div class="card-meta">
          <div class="card-name">Claude Code</div>
          <div class="card-desc" style="font-size:10px;color:var(--t3)">Running — waiting for first message</div>
        </div>
        <div class="status-pill"><span></span>DETECTED</div>
      </div>
    </div>`);
    const _fmtModel=m=>m?_ccModelName(m):'';   // 'claude-haiku-4-5-20251001' -> 'Haiku 4.5'
    const _fmtRuntime=(first,last)=>{
      if(!first||!last) return '';
      try{
        const ms=new Date(last)-new Date(first);
        if(isNaN(ms)||ms<=0) return '';
        const s=Math.floor(ms/1000);
        if(s<60) return s+'s';
        const m=Math.floor(s/60), h=Math.floor(m/60);
        if(h>0) return h+'h '+(m%60)+'m';
        return m+'m '+(s%60)+'s';
      }catch(e){ return ''; }
    };
    const _fmtRelTime=ls=>{
      if(!ls) return '';
      // ls is "HH:MM:SS" string from server
      try{
        const [hh,mm,ss]=ls.split(':').map(Number);
        const now=new Date();
        let t=new Date(); t.setHours(hh,mm,ss,0);
        let diff=Math.floor((now-t)/1000);
        if(diff<0) diff+=86400; // next-day wrap
        if(diff<10) return 'just now';
        if(diff<60) return diff+'s ago';
        if(diff<3600) return Math.floor(diff/60)+'m ago';
        return Math.floor(diff/3600)+'h ago';
      }catch(e){ return ls; }
    };
    const cards=slist.map(s=>{
      const isActive=s.session_active!==false;
      const waitingOnYou=isActive&&!!s.waiting_on_you;
      const waitingDurStr=(waitingOnYou&&(s.waiting_secs||0)>=60)?' · '+_fmtDurationDHM(s.waiting_secs):'';
      const cwdBase=s.cwd?(s.cwd.replace(/\\/g,'/').split('/').filter(Boolean).pop()||''):'';
      const label=escHtml(s.display_name||s.project||cwdBase||'CLI Session');
      const sid6=s.id.slice(-6);
      const agentCount=(data.agents||[]).filter(a=>a.session_id===s.id).length;
      const agentBadge=agentCount>0?`<span style="font-size:9px;color:var(--c);margin-left:6px;opacity:.7">${agentCount} agent${agentCount!==1?'s':''}</span>`:'';
      const sDomId=s._domId||s.id;
      const sIsRemote=s._isLocal===false;
      const machineBadge=sIsRemote?`<span style="font-size:9px;color:var(--c);margin-left:6px;opacity:.7">⌘ ${escHtml(s.machine)}</span>`:'';
      // Model / meta badges
      const modelStr=_fmtModel(s.model||'');
      const versionStr=s.cc_version?`v${s.cc_version}`:'';
      const branchStr=s.git_branch?(s.pr_url
        ?`<a href="${escHtml(s.pr_url)}" target="_blank" onclick="event.stopPropagation()" style="color:inherit;text-decoration:underline;text-decoration-style:dotted" title="Open PR on GitHub">⎇ ${escHtml(s.git_branch)}</a>`
        :`⎇ ${escHtml(s.git_branch)}`):'';
      const metaParts=[branchStr,modelStr,versionStr].filter(Boolean);
      const metaLine=metaParts.length?`<div style="font-size:9px;color:var(--t3);font-family:var(--font2);margin-top:2px;letter-spacing:.04em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${metaParts.join(' · ')}</div>`:'';
      // Cost / tokens / runtime / rel-time
      const totalTok=(s.input_tokens||0)+(s.output_tokens||0);
      const costStr=`$${(s.estimated_cost||0).toFixed(4)}`;
      const tokStr=totalTok>0?`${(totalTok/1000).toFixed(1)}k tok`:'';
      const runtimeStr=_fmtRuntime(s.first_ts,s.last_ts);
      const relStr=_fmtRelTime(s.last_seen||'');
      const statsRow=[costStr,tokStr,runtimeStr,relStr].filter(x=>x&&x!='$0.0000').join(' · ');
      /* input/output/cache_read/cache_write_tokens + msg_count are all
         already tracked per session and shipped in /status, but were
         never actually shown anywhere -- cache tokens in particular
         matter for cost (cache reads are far cheaper than fresh input),
         so surface the full breakdown in a hover tooltip rather than
         cluttering the compact stats line itself. */
      const tokTitle=totalTok>0?_tokBreakdownStr(s)+(s.msg_count?' · '+s.msg_count+' msg':''):'';
      const statsLine=statsRow?`<div style="font-size:9px;color:var(--t2);font-family:var(--font2);margin-top:2px;opacity:.8;letter-spacing:.03em"${tokTitle?` title="${escHtml(tokTitle)}"`:''}>${statsRow}</div>`:'';
      const noteLine=s.note?`<div style="font-size:9px;color:var(--t3);font-family:var(--font2);margin-top:2px;opacity:.85;letter-spacing:.02em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${escHtml(s.note)}">📝 ${escHtml(s.note)}</div>`:'';
      const burnRates=isActive?_computeBurnRates(_tokenHistory[s.id]||[], Date.now()/1000):null;
      const burnBadge=(burnRates&&_isBurnSpike(burnRates[0],burnRates[1]))?`<div class="burn-badge">⚡ BURN SPIKE — ${Math.round(burnRates[0])} tok/min</div>`:'';
      const _actHist=_activityHistory[s.id]||[];
      const activitySparkline=_actHist.length<3?'':(()=>{
        const actColors={active:'var(--c)',waiting:'var(--o)',idle:'rgba(255,255,255,.1)'};
        const buckets=_bucketActivity(_actHist, Date.now()/1000);
        const segs=buckets.map(b=>`<div style="flex:1;height:7px;border-radius:1px;background:${b?actColors[b]:'rgba(255,255,255,.04)'}"></div>`).join('');
        return `<div style="display:flex;gap:1px;margin-top:5px" title="Activity over the last 2h (cyan=working, amber=waiting on you, grey=idle)">${segs}</div>`;
      })();
      return `<div class="card ${isActive?'running':''}" style="${isActive?'':'opacity:.4'}">
        <div class="${isActive?'sweep':''}"></div>
        <div class="card-head">
          <div class="unit-badge" style="border-color:var(--c);color:var(--c);font-size:9px">CLI</div>
          <div class="card-meta" style="min-width:0;flex:1">
            <div class="card-name" id="cli-name-${escHtml(sDomId)}" style="display:flex;align-items:center;gap:6px">
              ${label}
              ${agentBadge}
              ${machineBadge}
            </div>
            ${metaLine}
            ${statsLine}
            ${isActive?_budgetBadgeHtml(s)+_loopBadgeHtml(s)+_ctxMeterHtml(s, Date.now()/1000):''}
            ${waitingOnYou?_woyQuoteHtml(s):''}
            ${noteLine}
            ${burnBadge}
            ${activitySparkline}
          </div>
          <div class="status-pill ${isActive?(waitingOnYou?'waiting'+(_waitLabel(s).blocked?' blocked':''):'running'):''}" ${waitingOnYou?`title="${escHtml(_waitLabel(s).tip)}"`:''}><span class="${isActive?'sdot':''}"></span>${isActive?(waitingOnYou?_waitLabel(s).word+waitingDurStr:'ACTIVE'):'CLOSED'}</div>
          ${!sIsRemote?`<button onclick="event.stopPropagation();openNotesPanel(${jsq(s.id)})" title="${s.note?'Edit note':'Add note'}" style="background:none;border:none;color:${s.note?'var(--c)':'var(--t3)'};cursor:pointer;font-size:13px;padding:4px 6px;margin-left:4px;line-height:1;border-radius:4px;transition:color .2s" onmouseover="this.style.color='var(--c)'" onmouseout="this.style.color='${s.note?'var(--c)':'var(--t3)'}'">📝</button>`:''}
          ${isActive&&!sIsRemote?`<button onclick="event.stopPropagation();_focusSession(${jsq(s.id)})" title="Switch to this session's terminal window" style="background:none;border:none;color:${waitingOnYou?'var(--o)':'var(--t3)'};cursor:pointer;font-size:13px;padding:4px 6px;margin-left:4px;line-height:1;border-radius:4px;transition:color .2s" onmouseover="this.style.color='var(--c)'" onmouseout="this.style.color='${waitingOnYou?'var(--o)':'var(--t3)'}'">⧉</button>`:''}
          <button onclick="event.stopPropagation();exportSessionDetail(${jsq(s.id)})" title="Export session as Markdown" style="background:none;border:none;color:var(--t3);cursor:pointer;font-size:13px;padding:4px 6px;margin-left:4px;line-height:1;border-radius:4px;transition:color .2s" onmouseover="this.style.color='var(--c)'" onmouseout="this.style.color='var(--t3)'">⇩</button>
          <button onclick="toggleSessionCompare(${jsq(s.id)},event)" title="${_sessCompareIds.includes(s.id)?'Remove from compare':'Add to compare (pick 2 sessions)'}" style="background:none;border:none;color:${_sessCompareIds.includes(s.id)?'var(--o)':'var(--t3)'};cursor:pointer;font-size:13px;padding:4px 6px;margin-left:4px;line-height:1;border-radius:4px;transition:color .2s" onmouseover="this.style.color='var(--o)'" onmouseout="this.style.color='${_sessCompareIds.includes(s.id)?'var(--o)':'var(--t3)'}'">⊞</button>
          ${_changesBtnHtml(s)}${_replayBtnHtml(s)}
          ${isActive&&s.host_pid?`<button onclick="event.stopPropagation();_forceStopSession(${jsq(s.id)},${jsq(s.project||s.cwd||'this session')},${jsq(s.machine||'')})" title="Force stop this CLI session (kills its claude.exe process)" style="background:none;border:none;color:rgba(240,164,151,.7);cursor:pointer;font-size:13px;padding:4px 6px;margin-left:4px;line-height:1;border-radius:4px;transition:color .2s" onmouseover="this.style.color='var(--r)'" onmouseout="this.style.color='rgba(240,164,151,.7)'">⛔</button>`:''}
          ${!isActive&&!sIsRemote?`<button onclick="event.stopPropagation();_copyResumeCmd(${jsq(s.id)})" title="Copy resume command to clipboard" style="background:none;border:none;color:var(--t3);cursor:pointer;font-size:13px;padding:4px 6px;margin-left:4px;line-height:1;border-radius:4px;transition:color .2s" onmouseover="this.style.color='var(--c)'" onmouseout="this.style.color='var(--t3)'">⟲</button>`:''}
          ${!isActive?` <button onclick="event.stopPropagation();_dismissSession(${jsq(s.id)},${jsq(s.machine||'')})" title="Dismiss" style="background:none;border:none;color:var(--t3);cursor:pointer;font-size:13px;padding:4px 6px;margin-left:4px;line-height:1;border-radius:4px;transition:color .2s" onmouseover="this.style.color='var(--r)'" onmouseout="this.style.color='var(--t3)'">✕</button>`:''}
        </div>
      </div>`;
    });
    agentsEl.innerHTML=[...cards,...ghosts].join('');
  } else if(visibleAgents.length===0){
    /* AGENTS view, no agents → empty state */
    const hasAnyCLI=(data.sessions_count||0)>0||data.session_active;
    agentsEl.innerHTML=hasAnyCLI
      ?`<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;padding:40px 20px;color:var(--t3);text-align:center">
          <div style="font-family:var(--font2);font-size:11px;letter-spacing:.1em;opacity:.6">NO ACTIVE AGENTS</div>
          <div style="font-size:11px;opacity:.45">Switch to <button onclick="setView('cli')" style="background:none;border:none;color:var(--c);cursor:pointer;font-size:11px;padding:0;text-decoration:underline">CLI view</button> to see running sessions</div>
        </div>`
      :`<div style="display:flex;align-items:center;justify-content:center;padding:40px 20px;color:var(--t3)">
          <div style="font-family:var(--font2);font-size:11px;letter-spacing:.1em;opacity:.45">NO ACTIVE AGENTS</div>
        </div>`;
  }

  /* flash cost badges where tokens changed */
  requestAnimationFrame(()=>{
    (data.agents||[]).forEach(a=>{
      if(!a.tokens_used) return;
      const prev=_prevTokens[a.id];
      if(prev!==undefined && prev!==a.tokens_used){
        const el=document.getElementById('cb-'+(a._domId||a.id));
        if(el){ el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash'); }
      }
      _prevTokens[a.id]=a.tokens_used;
    });
  });

  function _statSet(id, val){
    const el=document.getElementById(id); if(!el) return;
    const newTxt=String(val);
    if(el.textContent===newTxt) return;
    el.classList.remove('stat-flip'); void el.offsetWidth;
    el.classList.add('stat-flip');
    el.textContent=newTxt;
  }
  _statSet('s-run', pad(running));
  _statSet('s-done', pad(done));
  const errEl=document.getElementById('s-errors');
  if(errEl){ const et=errors>0?pad(errors):'—'; if(errEl.textContent!==et){ errEl.classList.remove('stat-flip'); void errEl.offsetWidth; errEl.classList.add('stat-flip'); errEl.textContent=et; } errEl.style.color=errors>0?'var(--r)':'var(--t3)'; if(errEl.parentNode) errEl.parentNode.style.opacity=errors>0?'1':'.5'; }
  document.title=running>0?`(${running} running) A.O.C`:done>0?`(${done} done) A.O.C`:'A.O.C · idle';
  /* counts live in the sidebar badges now (_sbUpdate) */
  document.getElementById('s-tasks').textContent=`${doneT}/${totalT}`;
  const _fcSet=new Set(); agents.forEach(a=>(a.files_changed||[]).forEach(f=>_fcSet.add(f.path))); fcCount=_fcSet.size;
  document.getElementById('s-files').textContent=pad(fcCount);
  const cliCount=data.sessions_count||0;
  const cliEl=document.getElementById('s-clis');
  if(cliEl) _statSet('s-clis', String(cliCount).padStart(2,'0'));

  if(currentView==='timeline') renderTimeline(data);
}

/* ── diff to detect changes and log them ── */
function diffStatus(prev,next){
  if(!prev||!prev.agents) return;

  /* detect new session */
  if(prev.started_at && next.started_at && prev.started_at !== next.started_at){
    log('NEW MISSION SESSION STARTED','success');
    return;
  }

  const _prevMap=Object.fromEntries((prev.agents||[]).map(a=>[a.id,a]));
  (next.agents||[]).forEach(a=>{
    const o=_prevMap[a.id];
    if(!o) return;
    const tag=_deriveUnit(a);
    if(o.status!==a.status){
      if(a.status==='done')    log(`MISSION COMPLETE`,'success',tag);
      if(a.status==='running') log(`ACTIVATED`,'info',tag);
      if(a.status==='error')   log(`SYSTEM FAILURE — ${a.error_message||'unknown error'}`,'error',tag);
      if(a.status==='waiting') log(`AWAITING ORDERS`,'warn',tag);
    }
    (a.tasks||[]).forEach((task,j)=>{
      if((o.tasks||[])[j]&&!(o.tasks||[])[j].done&&task.done)
        log(task.label,'info',tag);
    });
    if(a.log&&o.log) a.log.slice(o.log.length).forEach(m=>log(m,'info',tag));
  });
}

/* ── clock ── */
function updateClock(){
  const d=new Date();
  document.getElementById('clock').textContent=`${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  document.getElementById('cdate').textContent=`${d.getFullYear()}.${pad(d.getMonth()+1)}.${pad(d.getDate())}`;
}
setInterval(updateClock,1000); updateClock();

/* ── session elapsed counter ── */
let _sessStartMs = null;
function _updateElapsed(){
  const el=document.getElementById('s-elapsed');
  if(!el) return;
  if(!_sessStartMs){ el.textContent='—'; return; }
  const secs=Math.max(0,Math.floor((Date.now()-_sessStartMs)/1000));
  if(secs<60) el.textContent=secs+'s';
  else if(secs<3600) el.textContent=Math.floor(secs/60)+'m '+(secs%60)+'s';
  else el.textContent=Math.floor(secs/3600)+'h '+Math.floor((secs%3600)/60)+'m';
}
setInterval(_updateElapsed,1000);

/* ── reset session ── */
async function resetSession(){
  if(!confirm('Reset session? Agent history will be cleared from the monitor.')) return;
  try {
    await fetch('/reset', {method:'POST'});
    lastStatus=null;
    logs=[];
    log('SESSION RESET — AWAITING NEW ORDERS','warn');
  } catch(e){
    log('RESET FAILED: '+e.message,'error');
  }
}

let _lastAuditData = null;
let _auditTab = 'log';
function setAuditTab(tab){
  _auditTab = tab;
  ['log','files'].forEach(t=>{
    const el=document.getElementById('audt-'+t);
    if(el){ el.classList.toggle('active', t===tab); el.setAttribute('aria-selected',t===tab); }
  });
  renderAuditLog();
}

/* ── audit log ── */
function eventClass(line){
  const m=line.match(/\|\s+(\w+)\s+\|/);
  return m?m[1]:'';
}
async function renderAuditLog(){
  const listEl=document.getElementById('audit-list');
  const dlBtn=document.getElementById('btn-dl');
  const filesEl=document.getElementById('audit-files');
  const fnameEl=document.getElementById('al-fname');
  const fcEl=document.getElementById('audit-file-changes');

  /* update FILES tab label with count from lastStatus */
  const _auAgents=lastStatus?(lastStatus.agents||[]).filter(a=>!String(a.id||'').startsWith('hook_')):[];
  const _auSeen=new Set(); const _auFiles=[];
  _auAgents.forEach(a=>{
    (a.files_changed||[]).forEach(f=>{
      if(!_auSeen.has(f.path)){ _auSeen.add(f.path); _auFiles.push({...f, agent: a.unit||a.name||String(a.id).slice(-4).toUpperCase()}); }
    });
  });
  const ftEl=document.getElementById('audt-files'); if(ftEl) ftEl.textContent=`FILES (${_auFiles.length})`;

  if(_auditTab==='files'){
    /* FILES tab — show agent file changes */
    listEl.style.display='none'; listEl.innerHTML='';
    filesEl.style.display='none';
    if(dlBtn) dlBtn.style.display='none';
    if(fnameEl) fnameEl.textContent='';
    if(fcEl){
      fcEl.style.display='flex';
      if(!_auFiles.length){
        fcEl.innerHTML='<div style="color:var(--t3);font-family:var(--font2);font-size:11px;text-align:center;padding:50px 0;letter-spacing:.08em">// no file changes this session</div>';
      } else {
        fcEl.innerHTML=_auFiles.map(f=>`
          <div class="fe ${f.type==='new'?'new':'changed'}" onclick="showDiff(${jsq(f.path)});event.stopPropagation()" style="cursor:pointer;padding:10px 12px;border-radius:8px">
            <span class="fe-badge">${f.type==='new'?'NEW':'MOD'}</span>
            <span class="fe-name" style="flex:1;font-size:12px">${escHtml(f.path)}</span>
            ${f.lines?`<span class="fe-lines">${escHtml(f.lines)}L</span>`:''}
            <span style="font-size:9px;color:var(--t3);font-family:var(--font2);opacity:.6;flex-shrink:0">${escHtml(f.agent)}</span>
          </div>`).join('');
      }
    }
    return;
  }

  /* LOG tab — existing fetch + render */
  if(fcEl){ fcEl.style.display='none'; fcEl.innerHTML=''; }
  listEl.style.display='';
  try{
    const d=await fetch('/auditlog').then(r=>r.json());
    _lastAuditData=d;

    if(d.current){
      /* active session — show lines + download button */
      fnameEl.textContent=d.current;
      const auditLines = d.lines.slice(-20);
      listEl.innerHTML=auditLines.slice().reverse().map(l=>
        `<div class="al ${eventClass(l)}">${l}</div>`
      ).join('');
      dlBtn.href='/logs/'+encodeURIComponent(d.current);
      dlBtn.style.display='inline-flex';
      filesEl.style.display='none';
    } else {
      /* no active session — show log file list */
      fnameEl.textContent='NO ACTIVE SESSION';
      listEl.innerHTML='';
      dlBtn.style.display='none';
      if(d.files && d.files.length>0){
        filesEl.style.display='flex';
        filesEl.innerHTML=d.files.map(f=>
          `<a class="audit-file-link" href="/logs/${encodeURIComponent(f)}" target="_blank">${f}</a>`
        ).join('');
      } else {
        filesEl.style.display='flex';
        filesEl.innerHTML='<span style="font-size:9px;color:var(--t3)">// no logs</span>';
      }
    }
  }catch(e){}
}

/* ── trend chart: thin line + area, hover crosshair/tooltip, single series
   (no legend needed -- the title above it names the series). Values only,
   never color alone: the tooltip always shows the exact date + number. ── */
let _trendChartSeq=0;
function _renderTrendChart(containerEl, points, opts){
  const id='tc'+(_trendChartSeq++);
  const color=opts.color||'var(--c)';
  const fmt=opts.formatValue||(v=>String(v));
  const W=600,H=64,PAD=4;
  if(!points.length){
    containerEl.innerHTML=`<div class="trend-title">${escHtml(opts.label||'')}</div><div style="font-family:var(--font2);font-size:10px;color:var(--t3);padding:10px 0">// no data yet</div>`;
    return;
  }
  const vals=points.map(p=>p.value);
  const maxV=Math.max(...vals,1), minV=Math.min(...vals,0);
  const range=(maxV-minV)||1;
  const n=points.length;
  const xAt=i=>n>1?PAD+(W-PAD*2)*i/(n-1):W/2;
  const yAt=v=>PAD+(H-PAD*2)*(1-(v-minV)/range);
  const coords=points.map((p,i)=>[xAt(i),yAt(p.value)]);
  const linePath=coords.map(([x,y],i)=>`${i===0?'M':'L'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const areaPath=`${linePath} L${coords[n-1][0].toFixed(1)},${H-PAD} L${coords[0][0].toFixed(1)},${H-PAD} Z`;
  containerEl.innerHTML=`
    <div class="trend-title">${escHtml(opts.label||'')}</div>
    <div style="position:relative">
      <svg class="trend-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" id="${id}">
        <defs><linearGradient id="${id}-g" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${color}" stop-opacity="0.28"/>
          <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
        </linearGradient></defs>
        <path d="${areaPath}" fill="url(#${id}-g)" stroke="none"/>
        <path d="${linePath}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <circle cx="${coords[0][0]}" cy="${coords[0][1]}" r="2.5" fill="${color}"/>
        <circle cx="${coords[n-1][0]}" cy="${coords[n-1][1]}" r="2.5" fill="${color}"/>
        <line class="trend-hover-line" id="${id}-hl" x1="0" y1="0" x2="0" y2="${H}" style="display:none"/>
        <circle class="trend-hover-dot" id="${id}-hd" r="3.5" fill="${color}" stroke="var(--bg)" stroke-width="1.5" style="display:none"/>
        <rect x="0" y="0" width="${W}" height="${H}" fill="transparent" id="${id}-hit"/>
      </svg>
      <div class="trend-tip" id="${id}-tip"></div>
    </div>`;
  const svg=document.getElementById(id), hit=document.getElementById(id+'-hit');
  const hl=document.getElementById(id+'-hl'), hd=document.getElementById(id+'-hd'), tip=document.getElementById(id+'-tip');
  function show(i){
    const [x,y]=coords[i];
    hl.setAttribute('x1',x); hl.setAttribute('x2',x); hl.style.display='';
    hd.setAttribute('cx',x); hd.setAttribute('cy',y); hd.style.display='';
    tip.style.display='block';
    tip.style.left=(x/W*100)+'%'; tip.style.top=(y/H*100)+'%';
    tip.textContent=`${points[i].date} — ${fmt(points[i].value)}`;
  }
  function hide(){ hl.style.display='none'; hd.style.display='none'; tip.style.display='none'; }
  hit.addEventListener('mousemove',e=>{
    const rect=svg.getBoundingClientRect();
    const relX=(e.clientX-rect.left)/rect.width*W;
    let best=0,bestD=Infinity;
    coords.forEach(([x],i)=>{ const d=Math.abs(x-relX); if(d<bestD){bestD=d;best=i;} });
    show(best);
  });
  hit.addEventListener('mouseleave',hide);
}

/* Simple run-rate cost forecast: (cost so far this month / days elapsed)
   x days remaining, added to cost so far -- pure arithmetic over
   analytics' by_day array (last 30 days), no new query. Shared by
   renderHistory's analytics view and the Cost/Budget settings pane so
   this projection is computed in exactly one place. */
/* Auto-generated callouts for the top of History -> Analytics -- every
   number here already exists elsewhere on the page (retry_patterns,
   common_errors, hook_misses/total agents), but finding the single most
   important one meant scrolling through every section in turn. Pure and
   deterministic (no randomness, no LLM call) so the same data always
   produces the same callouts -- a fixed, small rule set, not an
   open-ended "summarize this" prompt. */
function _buildInsights({totalAgents, hookMisses, commonErrors, retryPatterns}){
  const out=[];
  if(retryPatterns && retryPatterns.length){
    const r=retryPatterns[0];
    out.push({icon:'⚠',text:`"${r.name}" has been retried ${r.retries}× across ${r.sessions} session${r.sessions!==1?'s':''}`});
  }
  if(commonErrors && commonErrors.length && commonErrors[0].occurrences>=3){
    const e=commonErrors[0];
    out.push({icon:'🔴',text:`"${(e.error_msg||'').slice(0,80)}" has recurred ${e.occurrences} times`});
  }
  if(totalAgents>0 && hookMisses>0){
    const pct=Math.round(hookMisses/totalAgents*100);
    if(pct>=10) out.push({icon:'⚠',text:`${pct}% of agent starts were only caught by the transcript fallback, not Claude Code's own hook`});
  }
  return out;
}
function _computeMonthProjection(byDay){
  const now=new Date();
  const monthPrefix=now.toISOString().slice(0,7);
  const costSoFar=(byDay||[]).filter(r=>(r.date||'').startsWith(monthPrefix)).reduce((s,r)=>s+(r.cost||0),0);
  const daysElapsed=now.getDate();
  const daysInMonth=new Date(now.getFullYear(),now.getMonth()+1,0).getDate();
  const daysRemaining=daysInMonth-daysElapsed;
  const avgDaily=daysElapsed>0?costSoFar/daysElapsed:0;
  const projectedMonthEnd=costSoFar+avgDaily*daysRemaining;
  return {costSoFar,projectedMonthEnd};
}
/* Per-project counterpart to the global "projected month-end spend" line
   above -- project_budgets (Settings -> COST) was write-only until now,
   a JSON textarea with no feedback about where each listed project
   actually stands against its own number. by_day_project already has
   every project's daily cost with no cap, so each configured project's
   own rows just get run through the exact same _computeMonthProjection
   math, one project at a time. */
function _perProjectBudgetSummary(projectBudgets,byDayProject){
  return Object.entries(projectBudgets||{}).map(([project,budget])=>{
    const rows=(byDayProject||[]).filter(r=>r.project===project);
    const {costSoFar,projectedMonthEnd}=_computeMonthProjection(rows);
    return {project, budget:+budget||0, costSoFar, projectedMonthEnd,
             pctUsed: budget>0?costSoFar/budget*100:0};
  });
}
function _renderProjectBudgetSummary(byDayProject){
  const el=document.getElementById('st-project-budget-summary');
  if(!el) return;
  const rows=_perProjectBudgetSummary(_projectBudgets, byDayProject||[]);
  const fmt=c=>'$'+(+c).toFixed(2);
  el.innerHTML=rows.map(r=>{
    const over=r.budget>0&&r.projectedMonthEnd>r.budget;
    const near=!over&&r.pctUsed>=80;
    const color=over?'var(--r)':near?'rgba(224,161,58,.9)':'var(--t3)';
    return `<div style="display:flex;justify-content:space-between;gap:8px"><span style="color:var(--t2)">${escHtml(r.project)}</span><span style="color:${color}">${fmt(r.costSoFar)} / ${fmt(r.budget)} — projected ${fmt(r.projectedMonthEnd)}</span></div>`;
  }).join('');
}

/* Formats /analytics' hook_miss_concurrency (already computed server-side
   by _db_analytics, previously only ever shown buried inside HISTORY →
   ANALYTICS) into one short line for the INFRA settings tab -- surfaces
   whether the PreToolUse hook drops more often under concurrent-session
   load without needing to open that tab. Pure/testable: takes the object
   as-is, no DOM/fetch inside. */
function _formatHookReliability(hmc){
  hmc = hmc || {};
  const miss = hmc.hook_miss, normal = hmc.normal;
  if(!miss && !normal) return 'No data yet.';
  const missN = miss ? miss.n : 0, normalN = normal ? normal.n : 0;
  const total = missN + normalN;
  if(!total) return 'No data yet.';
  const missPct = (missN/total*100).toFixed(1);
  const parts = [`${missPct}% hook misses (${missN}/${total})`];
  if(miss && normal && miss.avg_concurrent_sessions != null && normal.avg_concurrent_sessions != null){
    parts.push(`avg concurrency: ${miss.avg_concurrent_sessions} (miss) vs ${normal.avg_concurrent_sessions} (normal)`);
  }
  return parts.join(' · ');
}

