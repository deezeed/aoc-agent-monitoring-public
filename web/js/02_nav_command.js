/* ── Mobile nav ── */
let _mnavLogsOpen=false;
function _mnavGo(view){
  if(_mnavLogsOpen){ _mnavToggleLogs(false); }
  setView(view);
  document.querySelectorAll('#mobile-nav .mnav-btn').forEach(b=>b.classList.remove('active'));
  const btn=document.getElementById('mnav-'+view);
  if(btn) btn.classList.add('active');
}
function _mnavToggleLogs(force){
  const rp=document.getElementById('right-panel');
  _mnavLogsOpen = force!==undefined ? force : !_mnavLogsOpen;
  if(rp) rp.classList.toggle('mobile-open', _mnavLogsOpen);
  const btn=document.getElementById('mnav-logs');
  if(btn) btn.classList.toggle('active', _mnavLogsOpen);
}
function _savePrefs(){
  try{ localStorage.setItem('aoc_prefs',JSON.stringify({view:currentView,list:listMode,sf:statusFilter,muted:_soundMuted,theme:_theme,collapsed:[..._collapsedIds],pinned:[..._pinnedIds],bays:[..._collapsedBays],panelCollapsed:_panelCollapsed,viz:_vizView,today:1,cc:1,pal:typeof _paletteRecent!=='undefined'?_paletteRecent:[]})); }catch(e){}
}
function _loadPrefs(){
  try{
    const p=JSON.parse(localStorage.getItem('aoc_prefs')||'{}');
    // migrate a pref saved before AGENTS/CLI were promoted from a CARDS
    // sub-tab to top-level views -- v==='cards' would otherwise match no
    // view at all and leave the main area blank for returning users.
    let v=p.view;
    if(v==='cards') v=(p.cardsTab==='cli')?'cli':'agents';
    // TODAY is the home view: first run, and once for prefs saved before it existed
    if(!v||!p.today) v='today';
    // COMMAND is the home view since the Command Center redesign (once for older prefs)
    if(!p.cc) v='command';
    if(p.list)  listMode=p.list;
    if(p.sf && p.sf!=='all') statusFilter=p.sf;
    if(p.muted) { _soundMuted=true; _applyMuteUI(); }
    if(p.theme) { _theme=p.theme; _applyTheme(); }
    if(Array.isArray(p.collapsed)) _collapsedIds=new Set(p.collapsed);
    if(Array.isArray(p.pinned)) _pinnedIds=new Set(p.pinned);
    if(_isVizView(p.viz)) _vizView=p.viz;
    if(Array.isArray(p.bays)) _collapsedBays=new Set(p.bays.filter(k=>typeof k==='string'));
    if(Array.isArray(p.pal)) _paletteRecent=p.pal.filter(k=>typeof k==='string').slice(0,8);
    if(p.panelCollapsed){ _panelCollapsed=true; document.querySelector('.root').classList.add('panel-collapsed'); const btn=document.getElementById('panel-toggle-btn'); if(btn) btn.textContent='▶'; }
    // Last: setView() calls _savePrefs(), so running it before the state
    // above was restored wrote them back empty -- pinned/collapsed/panel state
    // survived one reload and was gone on the next.
    if(v) { currentView=v; setView(v); }
  }catch(e){}
}
function togglePin(id, event){
  if(event) event.stopPropagation();
  if(_pinnedIds.has(id)) _pinnedIds.delete(id);
  else _pinnedIds.add(id);
  _savePrefs();
  if(lastStatus) renderAgents(lastStatus);
}
function toggleCollapse(id, event){
  if(event) event.stopPropagation();
  if(_collapsedIds.has(id)) _collapsedIds.delete(id);
  else _collapsedIds.add(id);
  _savePrefs();
  if(lastStatus) renderAgents(lastStatus);
}
function collapseAllDone(){
  if(!lastStatus) return;
  const agents=(lastStatus.agents||[]).filter(a=>!String(a.id||'').startsWith('hook_'));
  agents.filter(a=>a.status==='done').forEach(a=>_collapsedIds.add(a.id));
  _savePrefs();
  renderAgents(lastStatus);
  log('All done agents collapsed','info');
}
function expandAll(){
  _collapsedIds.clear();
  _savePrefs();
  if(lastStatus) renderAgents(lastStatus);
  log('All agents expanded','info');
}
function _applyMuteUI(){
  const btn=document.getElementById('btn-mute');
  if(btn){ btn.textContent=_soundMuted?'🔇':'🔊'; btn.classList.toggle('muted',_soundMuted); btn.title=_soundMuted?'Sound muted — click to unmute':'Mute sound notifications'; }
}
function _applyTheme(){
  document.body.classList.toggle('light',_theme==='light');
  const btn=document.getElementById('btn-theme');
  if(btn){ btn.textContent=_theme==='light'?'☀️':'🌙'; btn.title=_theme==='light'?'Switch to dark mode [H]':'Switch to light mode [H]'; }
}
function toggleTheme(){
  _theme=_theme==='dark'?'light':'dark';
  _applyTheme();
  _savePrefs();
  log('Theme → '+_theme.toUpperCase(),'info');
}

/* ── VIEWS: Timeline / Graph / Heat / Tree share one sidebar entry and a
   switcher above them. The view names stay as they were, so setView('graph'),
   the T/G/X/W hotkeys and a saved view keep working. ── */
const _VIZ_VIEWS=[['timeline','Timeline','T'],['graph','Graph','G'],['heat','Heat map','X'],['tree','Agent tree','W']];
let _vizView='timeline';   // the one the sidebar's Views entry opens: the last one you used
function _isVizView(v){ return _VIZ_VIEWS.some(x=>x[0]===v); }
/* Pure: the switcher's buttons, the current view marked. */
function _vizSwitchHtml(v){
  return _VIZ_VIEWS.map(([k,l,key])=>`<button class="viz-tab${k===v?' active':''}" onclick="setView(${jsq(k)})" title="${l} (${key})" aria-pressed="${k===v}">${l}</button>`).join('');
}
function setView(v){
  currentView=v;
  _savePrefs();
  const areas = { command:'block', today:'block', inbox:'block', orch:'block', timeline:'flex', summary:'flex', graph:'flex', heat:'flex', tree:'flex', history:'flex', term:'flex' };
  ['command-area','today-area','inbox-area','orch-area','timeline-area','summary-area','graph-area','heat-area','diag-area','tree-area','history-area','term-area'].forEach(id=>{
    const key=id.replace('-area','');
    document.getElementById(id).style.display = v===key ? (areas[key]||'flex') : 'none';
  });
  /* AGENTS and CLI are two top-level views over the same #cards-area
     container (agent cards vs. session cards) -- see renderAgents(),
     which reads currentView to decide which to draw into #agents. */
  const cardsAreaEl=document.getElementById('cards-area');
  if(cardsAreaEl) cardsAreaEl.style.display = (v==='agents'||v==='cli') ? 'flex' : 'none';
  [['vt-command','command'],['vt-today','today'],['vt-inbox','inbox'],['vt-orch','orch'],['vt-agents','agents'],['vt-cli','cli'],['vt-summary','summary'],['vt-diag','diag'],['vt-history','history'],['vt-term','term']].forEach(([id,val])=>{
    const el=document.getElementById(id); if(el) el.classList.toggle('active',v===val);
  });
  ['command','today','inbox','agents','term'].forEach(x=>{
    const b=document.getElementById('mnav-'+x);
    if(b) b.classList.toggle('active', v===x||(x==='agents'&&v==='cli'));
  });
  if(_isVizView(v)) _vizView=v;
  const vizBtn=document.getElementById('vt-views');
  if(vizBtn) vizBtn.classList.toggle('active', _isVizView(v));
  const vizSw=document.getElementById('viz-switch');
  if(vizSw){ vizSw.style.display=_isVizView(v)?'flex':'none'; if(_isVizView(v)) vizSw.innerHTML=_vizSwitchHtml(v); }
  /* KPI bar covers both AGENTS and CLI (same underlying live data) */
  const kpiEl=document.getElementById('kpi-bar');
  if(kpiEl) kpiEl.style.display=(v==='agents'||v==='cli')?'flex':'none';
  /* term-mode removes main padding so xterm fills space */
  const mainEl=document.getElementById('main-area');
  if(mainEl) mainEl.classList.toggle('term-mode', v==='term');
  if(mainEl) mainEl.classList.toggle('cc-mode', v==='command');
  renderOrchestrate();   // starts / stops its own polling
  if(v==='term') { _initTerm(); return; }
  if(!lastStatus) return;
  if(v==='agents'||v==='cli') renderAgents(lastStatus);
  if(v==='command')  renderCommand(lastStatus);
  if(v==='today')    renderToday(lastStatus);
  if(v==='inbox')    renderInbox(lastStatus);
  if(v==='timeline') renderTimeline(lastStatus);
  if(v==='summary')  renderSummary(lastStatus);
  if(v==='graph')    renderGraph(lastStatus);
  if(v==='heat')     renderHeatmap(lastStatus);
  if(v==='diag')     renderDiag();
  if(v==='tree')     renderTree(lastStatus);
  /* returns renderHistory()'s promise (existing call sites just ignore it,
     a no-op change for them) so _jumpToSessionHistory below can await the
     SESSIONS list render finishing before overwriting #hist-body with a
     detail view -- both write the same element asynchronously, so without
     this ordering the list fetch resolving second would clobber it back. */
  if(v==='history')  return renderHistory();
}

/* ── COMMAND: the Command Center home view ──
   A shell (panels with ids + the globe canvas) is drawn once; every status
   update only swaps the panels whose html changed, so the globe keeps
   spinning. Everything shown comes from AOC itself: /status, /analytics,
   /cache_stats (7 days), /history, /sysinfo and the event log. */
const _ccData={an:null, anAt:0, cache:null, cacheAt:0, hist:null, histAt:0, sys:null, sysAt:0, cm:null, cmAt:0, act:null, actAt:0};
let _ccStatusAt=0, _ccGlobe=null;
const _CC_ICONS={
  term:'<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>',
  agents:'<circle cx="12" cy="8" r="3.2"/><path d="M5 20c.8-3.6 3.6-5.5 7-5.5s6.2 1.9 7 5.5"/>',
  bell:'<path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20a2 2 0 0 0 4 0"/>',
  gauge:'<path d="M4 16a8 8 0 1 1 16 0"/><path d="M12 16l4-5"/>',
  coin:'<circle cx="12" cy="12" r="8"/><path d="M14.5 9.5c-.5-1-1.5-1.5-2.5-1.5-1.4 0-2.5.8-2.5 2s1.1 1.6 2.5 2 2.5.8 2.5 2-1.1 2-2.5 2c-1 0-2-.5-2.5-1.5M12 6.5v11"/>',
  shield:'<path d="M12 3l7 3v6c0 4.2-3 7.5-7 9-4-1.5-7-4.8-7-9V6z"/><path d="M9 12l2 2 4-4"/>',
  info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
  warn:'<path d="M12 3l9.5 17h-19z"/><path d="M12 10v4M12 17v.5"/>',
  x:'<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>',
  check:'<circle cx="12" cy="12" r="9"/><path d="M8 12.5l3 3 5-6"/>',
  chip:'<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
  plus:'<circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/>',
  cmd:'<path d="M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3z"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  lock:'<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  pc:'<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
  wifi:'<path d="M2 9a15 15 0 0 1 20 0M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0"/><circle cx="12" cy="19" r="1"/>',
  week:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
  doc:'<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>',
};
/* AOC's own start-up lines and the shortcut hint aren't events */
const _CC_FEED_SKIP=/^(INITIALIZING A\.O\.C|CONNECTING TO ORCHESTRATOR|WAITING FOR AGENT STATUS|REAL-TIME AGENT TRACKING|Shortcuts:)/;
const _ccI=k=>`<svg viewBox="0 0 24 24">${_CC_ICONS[k]||''}</svg>`;
const _ccIco=(k,tone)=>`<span class="cc-ico ${tone?'ic-'+tone:''}">${_ccI(k)}</span>`;
/* Pure: 'claude-opus-5-5' -> 'Opus 5.5', 'claude-haiku-4-5-20251001' -> 'Haiku 4.5'. */
function _ccModelName(m){
  const s=String(m||'').replace(/^claude-/,'').replace(/-\d{8}$/,'');
  const mm=/^([a-z]+)-(\d+)(?:-(\d+))?$/.exec(s);
  if(!mm) return s||'?';
  return mm[1][0].toUpperCase()+mm[1].slice(1)+' '+mm[2]+(mm[3]!=null?'.'+mm[3]:'');
}
/* Pure: deterministic bar heights (0.25..1) for a mini waveform. */
function _ccWave(seed, n){
  let h=0; for(const ch of String(seed||'x')) h=(h*31+ch.charCodeAt(0))>>>0;
  const out=[];
  for(let i=0;i<n;i++){ h=(h*1103515245+12345)>>>0; out.push(.25+((h>>>8)%1000)/1000*.75); }
  return out;
}
function _ccWaveHtml(seed, n, cls){
  return `<span class="${cls||'cc-wv'}">${_ccWave(seed,n).map((v,i)=>`<i style="height:${Math.round(v*100)}%;animation-delay:${(i*0.07).toFixed(2)}s"></i>`).join('')}</span>`;
}
function _ccRing(pct, label){
  const p=typeof pct==='number'?Math.max(0,Math.min(100,pct)):null;
  const C=2*Math.PI*40, col=p==null?'rgba(120,150,170,.5)':p>=90?'var(--r)':p>=75?'var(--o)':'var(--c)';
  return `<div class="cc-ring"><svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="40" stroke="rgba(var(--c-rgb),.12)" stroke-width="8" fill="none"/>
    <circle cx="50" cy="50" r="40" stroke="${col}" stroke-width="8" fill="none" stroke-linecap="round" stroke-dasharray="${((p||0)/100*C).toFixed(1)} ${C.toFixed(1)}" style="filter:drop-shadow(0 0 4px ${col})"/></svg>
    <div class="cc-ring-v"><small>${label}</small><b>${p==null?'—':Math.round(p)+'%'}</b></div></div>`;
}
/* Pure: html of every COMMAND panel. sr = /status, d = _ccData, nowS = epoch
   s, todayStr = local YYYY-MM-DD, notifs = the event log (oldest first). */
function _commandParts(sr, d, nowS, todayStr, notifs){
  sr=sr||{}; d=d||{};
  const money=v=>'$'+(Number(v)||0).toFixed(2);
  const pl=(n,w)=>`${n} ${w}${n===1?'':'s'}`;
  const nameOf=s=>s.display_name||s.project||(s.cwd||'').split(/[\\/]/).filter(Boolean).pop()||String(s.id||'').slice(0,8);
  const sessions=(sr.sessions_list||[]).filter(s=>s&&s.session_active!==false);
  const waiting=_waitingSessions(sessions, 0);
  const running=(sr.agents||[]).filter(a=>a.status==='running');
  const an=d.an||{}, todayCost=(an.today&&an.today.cost)||0;
  const wins=((sr.rate_limits||{}).windows)||[];
  const w5=wins.find(w=>w.kind==='five_hour'&&typeof w.pct==='number'), w7=wins.find(w=>w.kind==='seven_day'&&typeof w.pct==='number');
  const wd=((sr.infra_health||{}).watchdog)||null;
  const parts={};

  const ovRow=(ico,tone,label,sub,cls)=>`<div class="cc-ov-row">${_ccIco(ico,tone)}<div><b>${label}</b><small class="${cls}">${escHtml(sub)}</small></div></div>`;
  const pct5=w5?Math.round(w5.pct):null, fc5=w5?_limitForecast(w5, nowS):null;
  parts.ov=[
    ovRow('term','','Claude Code',sessions.length?pl(sessions.length,'session')+' open':'No session open',sessions.length?'ok':'info'),
    ovRow('agents','purple','Agents',running.length?`${running.length} running`:'Idle',running.length?'ok':'info'),
    ovRow('bell','amber','Needs you',waiting.length?`${waiting.length} waiting`:'Nothing waiting',waiting.length?'warn':'ok'),
    ovRow('gauge','green','Plan limit',pct5==null?'No data yet':(w5.status==='hit'?'Limit reached':`${pct5}% of the 5-hour window${fc5?` · limit in ${fc5.inTxt}`:''}`),pct5==null?'info':w5.status==='hit'||pct5>=90?'bad':pct5>=80||(fc5&&fc5.soon)?'warn':'ok'),
    ovRow('coin','pink','Spend today',money(todayCost),'info'),
    ovRow('shield','blue','Watchdog',wd?(wd.pid_alive?'Running':'Not running'):'Checking…',wd?(wd.pid_alive?'ok':'bad'):'info'),
  ].join('');

  parts.hero=`<div class="cc-hero-t1">AOC</div><div class="cc-hero-t2">AGENT OPERATIONS CENTER</div>`;
  /* the numbers the globe draws, as a legend row under it (the dot colours
     match the satellites: cyan = session, amber = waiting, green = agents) */
  const chip=(n,label,cls)=>`<span class="cc-hs ${cls}"><i></i><b>${escHtml(String(n))}</b>${label}</span>`;
  parts.heroStats=[chip(sessions.length, sessions.length===1?'session':'sessions', 'c'),
    waiting.length?chip(waiting.length, 'waiting', 'warn'):'',
    chip(running.length, running.length===1?'agent live':'agents live', running.length?'ok':'dim'),
    chip(money(todayCost), 'today', 'pink')].join('');

  const tagOf={error:['ERROR','red','x'], err:['ERROR','red','x'], warn:['WARN','amber','warn'], warning:['WARN','amber','warn'], success:['DONE','green','check'], done:['DONE','green','check']};
  const feed=waiting.slice(0,3).map(s=>{
    const wl=_waitLabel(s);
    const info=_lastMessageInfo(s.last_message, 120);
    const text=wl.blocked&&s.waiting_message?s.waiting_message:(info?info.text:'Waiting for your reply');
    return `<div class="cc-fi">${_ccIco(wl.blocked?'lock':'bell',wl.blocked?'red':'amber')}<div class="cc-fi-m"><b>${escHtml(nameOf(s))} needs you</b><small>${escHtml(text)}</small></div>
      <button class="cc-fbtn${wl.blocked?'':' amber'}" onclick="setView('today')">Open</button></div>`;
  });
  (notifs||[]).slice(-40).reverse().forEach(n=>{
    if(feed.length>=6||!n||!n.msg||_CC_FEED_SKIP.test(n.msg)) return;
    const [tag,tone,ico]=tagOf[n.type]||['INFO','','info'];
    feed.push(`<div class="cc-fi">${_ccIco(ico,tone)}<div class="cc-fi-m"><b>${escHtml(n.tag?n.tag+' · '+n.msg:n.msg)}</b><small>${escHtml(n.t||'')}</small></div>
      <span class="cc-tag ${tone?'ic-'+tone:''}">${tag}</span></div>`);
  });
  const commits=((d.cm&&d.cm.stats&&d.cm.stats.commits)||[]).slice(0,6);
  commits.forEach(c=>{
    if(feed.length>=6) return;
    const tm=c.epoch?new Date(c.epoch*1000).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'}):'';
    feed.push(`<div class="cc-fi">${_ccIco('check','purple')}<div class="cc-fi-m"><b>${escHtml(c.subject||'(no message)')}</b><small>${escHtml([c.repo, tm, c.cost?money(c.cost):''].filter(Boolean).join(' · '))}</small></div>
      <span class="cc-tag ic-purple">COMMIT</span></div>`);
  });
  parts.feed=feed.join('')||'<div class="cc-empty">Nothing has happened yet today.</div>';

  const order=[...waiting, ...sessions.filter(s=>!waiting.includes(s))].slice(0,6);
  const tones=['','purple','blue','amber','green','pink'];
  parts.ags=order.map((s,i)=>{
    const isWait=waiting.includes(s), n=running.filter(a=>a.session_id===s.id).length;
    const st=isWait?['wait','Waiting on you']:['run',n?`Working · ${pl(n,'agent')}`:'Working'];
    const model=s.model?_ccModelName(s.model):'';
    return `<div class="cc-ag ${st[0]}" onclick="setView('cli')" title="${escHtml([model, s.estimated_cost?money(s.estimated_cost):''].filter(Boolean).join(' · '))}">${_ccIco('term',tones[i%tones.length])}
      <div class="cc-ag-m"><b>${escHtml(nameOf(s))}</b><small class="${st[0]}"><i></i>${escHtml(st[1])}</small></div>${_ccWaveHtml(s.id,10)}</div>`;
  }).join('')||`<div class="cc-empty">No Claude Code session is open. <button class="cc-more" onclick="openLaunch()">Start one ›</button></div>`;

  const live=new Set(sessions.map(s=>s.id)), waitIds=new Set(waiting.map(s=>s.id));
  const rows=(Array.isArray(d.hist)?d.hist:[]).filter(h=>h&&h.date===todayStr&&h.started_at)
    .sort((a,b)=>String(a.started_at).localeCompare(String(b.started_at))).slice(-5);
  const dmax=Math.max(...rows.map(h=>h.duration_s||0),1);
  parts.tl=rows.map(h=>{
    const cls=waitIds.has(h.id)?'wait':live.has(h.id)?'live':'done';
    const status=cls==='wait'?'Waiting':cls==='live'?'Live':_fmtDurShort(h.duration_s||0);
    return `<div class="cc-tl-row ${cls}" onclick="_jumpToSessionHistory(${jsq(h.id)})" title="Open this session in History"><span class="cc-tl-t">${escHtml(String(h.started_at).slice(0,5))}</span><span class="cc-tl-d"></span>
      <div class="cc-tl-n"><b>${escHtml(h.title||h.project||String(h.id).slice(0,8))}</b><div class="cc-tl-bar"><span style="width:${Math.max(4,(h.duration_s||0)/dmax*100).toFixed(0)}%"></span></div></div>
      <span class="cc-tl-s">${escHtml(status)}</span></div>`;
  }).join('')||'<div class="cc-empty">No sessions yet today.</div>';

  const sy=d.sys||{};
  const gb=b=>b?Math.round(b/1073741824)+' GB':'';
  parts.sys=`<div class="cc-rings">${_ccRing(sy.cpu,'CPU')}${_ccRing(sy.ram,'RAM')}${_ccRing(sy.disk,'Disk')}</div>
    <div class="cc-sub">${escHtml([sy.ram_total?'RAM '+gb(sy.ram_total):'', sy.disk_total?`Disk ${sy.disk_path||''} ${gb(sy.disk_total)}`:''].filter(Boolean).join(' · '))}</div>`;

  const cs=(d.cache&&d.cache.stats)||null, tot=(cs&&cs.total)||{};
  const days=((cs&&cs.by_day)||[]).slice(-14);
  let constel='<div class="cc-empty">Not enough days yet.</div>';
  if(days.length>=2){
    const cmax=Math.max(...days.map(x=>x.cost||0),0.01), W=240, H=100;
    const pts=days.map((x,i)=>[10+i*(W-20)/(days.length-1), H-12-(x.cost||0)/cmax*(H-26)]);
    constel=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><polyline points="${pts.map(p=>p.map(v=>v.toFixed(1)).join(',')).join(' ')}" fill="none" stroke="rgba(var(--c-rgb),.45)" stroke-width="1"/>
      ${pts.map((p,i)=>`<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="${i===pts.length-1?3.2:2.2}" fill="#bff0ff" style="filter:drop-shadow(0 0 3px rgba(var(--c-rgb),.9))"><title>${escHtml(days[i].day||'')}: ${money(days[i].cost)}</title></circle>`).join('')}</svg>`;
  }
  parts.cache=cs?`<div class="cc-cache"><div>${constel}</div><div class="cc-kv">
      <div><small>Cache hit rate</small><b>${tot.hit_rate!=null?Math.round(tot.hit_rate*100)+'%':'—'}</b></div>
      <div><small>Saved by cache</small><b>${money(tot.saved)}</b></div>
      <div><small>Spent, 7 days</small><b>${money(tot.cost)}</b></div></div></div>`
    :'<div class="cc-empty">Reading your transcripts…</div>';

  const mods=((cs&&cs.by_model)||[]).filter(m=>m&&m.model);
  parts.modelsN=mods.length?`${mods.length} in use`:'';
  const mt=['','purple','green','amber','blue','pink'];
  parts.models=mods.map((m,i)=>`<div class="cc-mod">${_ccIco('chip',mt[i%mt.length])}<div style="min-width:0"><b>${escHtml(_ccModelName(m.model))}</b>
      <small>${money(m.cost)} · ${(m.n||0).toLocaleString()} calls</small></div></div>`).join('')||'<div class="cc-empty">No API calls in the last 7 days.</div>';

  const tun=sr.tunnel||{};
  const fitem=(ico,tone,label,val)=>`<div class="cc-fitem">${_ccIco(ico,tone)}<div><small>${label}</small><b>${escHtml(val)}</b></div></div>`;
  parts.foot=`${fitem('pc','','Machine',sy.host||'This PC')}${fitem('wifi','green','Remote access',tun.status==='ready'?'Tunnel live':'Local only')}
    ${fitem('week','purple','Weekly limit',w7?Math.round(w7.pct)+'% used':'No data yet')}
    <span class="cc-fline"></span><button class="cc-launch" onclick="openLaunch()"><b>LAUNCH SESSION</b><small>Start Claude Code in a folder</small></button><span class="cc-fline"></span>
    <button class="cc-brief" onclick="setView('today')">${_ccI('doc')}Today's briefing</button>`;
  return parts;
}
function _commandShell(){
  const panel=(cls,title,id,extra)=>`<section class="cc-panel ${cls}"><div class="cc-hrow"><span class="cc-h">${title}</span>${extra||''}</div><div id="${id}"${['cc-ov','cc-feed','cc-ags','cc-tl','cc-models'].includes(id)?` class="${id}"`:''}></div></section>`;
  const qb=(ico,label,fn)=>`<button class="cc-qb" onclick="${fn}">${_ccI(ico)}${label}</button>`;
  return `<div class="cc-wrap">
    <div class="cc-row cc-r1">
      ${panel('','System overview','cc-ov','')}
      <section class="cc-panel cc-hero" id="cc-hero"><canvas id="cc-globe"></canvas><div class="cc-hero-txt" id="cc-hero-txt"></div><div class="cc-hero-stats" id="cc-hero-stats"></div></section>
      ${panel('','Live feed','cc-feed','<span class="cc-live"><i></i>LIVE</span>')}
    </div>
    <div class="cc-row cc-r2">
      ${panel('','Active sessions','cc-ags','<button class="cc-more" onclick="setView(\'cli\')">View all ›</button>')}
      ${panel('','Today\'s timeline','cc-tl','<button class="cc-more" onclick="setView(\'history\')">Full history ›</button>')}
      <section class="cc-panel"><div class="cc-hrow"><span class="cc-h">Quick commands</span></div><div class="cc-qc">
        ${qb('plus','Start new session','openLaunch()')}${qb('term','Open terminal',"setView('term')")}${qb('cmd','Command palette','openPalette()')}
        ${qb('clock','Session history',"_histTab='sessions';setView('history')")}${qb('lock','Permission advisor',"_histTab='permissions';setView('history')")}</div></section>
    </div>
    <div class="cc-row cc-r3">
      ${panel('','System monitor','cc-sys','')}
      ${panel('','Cache · 7 days','cc-cache','<button class="cc-more" onclick="_histTab=\'cache\';setView(\'history\')">Cache report ›</button>')}
      ${panel('','Models · 7 days','cc-models','<span class="cc-more" id="cc-models-n" style="color:var(--g);cursor:default"></span>')}
    </div>
    <section class="cc-panel cc-foot" id="cc-foot"></section>
  </div>`;
}
async function _ccFetch(key, url, maxAge){
  const now=Date.now();
  if(now-_ccData[key+'At']<maxAge) return;
  _ccData[key+'At']=now;
  try{ _ccData[key]=await fetch(url).then(r=>r.json()); }catch(e){}
}
let _ccFetching=false;
/* html last written per panel: compared against the new html instead of
   n.innerHTML, which the browser re-serialises (<circle/> -> <circle></circle>,
   so every panel with an icon looked changed and was rebuilt on each push) */
const _ccPainted=new WeakMap();
async function renderCommand(sr){
  const el=document.getElementById('command-area');
  if(!el||!sr) return;
  if(!document.getElementById('cc-ov')) el.innerHTML=_commandShell();
  const paint=()=>{
    if(currentView!=='command') return;
    const p=_commandParts(lastStatus||sr, _ccData, Date.now()/1000, _localDateStr(), _notifHistory);
    [['cc-ov','ov'],['cc-hero-txt','hero'],['cc-hero-stats','heroStats'],['cc-feed','feed'],['cc-ags','ags'],['cc-tl','tl'],['cc-sys','sys'],['cc-cache','cache'],['cc-models','models'],['cc-models-n','modelsN'],['cc-foot','foot']]
      .forEach(([id,k])=>{ const n=document.getElementById(id); if(n&&_ccPainted.get(n)!==p[k]){ n.innerHTML=p[k]; _ccPainted.set(n,p[k]); } });
    _ccGlobeRun();
  };
  paint();
  if(_ccFetching) return;
  _ccFetching=true;
  try{
    await Promise.all([_ccFetch('an','/analytics',30000), _ccFetch('cache','/cache_stats?days=7',120000),
      _ccFetch('hist','/history',60000), _ccFetch('sys','/sysinfo',5000), _ccFetch('cm','/commit_costs?days=1',60000)]);
  }finally{ _ccFetching=false; }
  paint();
}
/* Pure: a satellite's opacity at (px,py) given the title box {l,r,t,b} --
   `min` inside it (0 = hidden), rising to 1 over the 16 px around it. */
function _ccSatAlpha(px, py, b, min){
  const dx=Math.max(b.l-px, 0, px-b.r), dy=Math.max(b.t-py, 0, py-b.b);
  const lo=min||0;
  return lo+(1-lo)*Math.min(1, Math.hypot(dx, dy)/16);
}
/* Pure: what the globe draws -- one satellite per open session (name,
   waiting on you, running agents = its moons), the totals, and a signature
   that changes whenever any of that does (each change sends out a ripple). */
function _ccGlobeState(sr){
  const sess=((sr&&sr.sessions_list)||[]).filter(s=>s&&s.session_active!==false);
  const waitIds=new Set(_waitingSessions(sess,0).map(s=>s.id));
  const run=((sr&&sr.agents)||[]).filter(a=>a&&a.status==='running');
  const sats=sess.map(s=>({id:s.id, name:String(s.display_name||s.project||String(s.id||'').slice(0,8)),
    wait:waitIds.has(s.id), agents:run.filter(a=>a.session_id===s.id).length}));
  return {sats, waiting:waitIds.size, running:run.length, sig:sats.map(s=>s.id+(s.wait?':w':'')+':'+s.agents).join('|')};
}
/* The globe: points on a sphere joined to their near neighbours, two orbit
   rings, and one labelled satellite per open session -- amber with a pulse
   when it waits on you, a comet trail behind it, dimmed while it passes
   behind the sphere, and a small moon per running agent. It spins faster
   while agents run, takes an amber tint while anything waits, and sends a
   ripple out whenever a session, its waiting state or its agents change. */
function _ccGlobeRun(){
  const cv=document.getElementById('cc-globe');
  if(!cv) return;
  if(_ccGlobe&&_ccGlobe.cv===cv) return;
  const N=230, pts=[];
  for(let i=0;i<N;i++){ const y=1-2*(i+.5)/N, r=Math.sqrt(1-y*y), th=i*2.39996; pts.push([Math.cos(th)*r, y, Math.sin(th)*r]); }
  const links=[];
  for(let i=0;i<N;i++) for(let j=i+1;j<N;j++){ const dx=pts[i][0]-pts[j][0], dy=pts[i][1]-pts[j][1], dz=pts[i][2]-pts[j][2]; if(dx*dx+dy*dy+dz*dz<0.045) links.push([i,j]); }
  const g=_ccGlobe={cv, a:0, last:0, sig:null, waiting:0, ripples:[]};
  const still=typeof matchMedia!=='undefined'&&matchMedia('(prefers-reduced-motion: reduce)').matches;
  const AMBER='255,190,61', GREEN='76,214,140';
  const font=getComputedStyle(document.body).fontFamily||'sans-serif';
  const frame=(t)=>{
    if(_ccGlobe!==g||!cv.isConnected){ return; }
    if(currentView!=='command'||document.hidden){ requestAnimationFrame(frame); return; }
    if(t-g.last<33){ requestAnimationFrame(frame); return; }
    g.last=t;
    const gs=_ccGlobeState(lastStatus);
    if(gs.sig!==g.sig){
      if(g.sig!==null&&!still) g.ripples.push({t0:t, col:gs.waiting>g.waiting?AMBER:null});
      g.sig=gs.sig; g.waiting=gs.waiting;
    }
    if(!still) g.a+=0.0035+0.0022*Math.min(gs.running,6);
    const tt=still?0:t;
    const dpr=Math.min(2,window.devicePixelRatio||1), w=cv.clientWidth, h=cv.clientHeight;
    if(!w||!h){ requestAnimationFrame(frame); return; }
    /* the title sits over the canvas: satellites fade out as they pass behind it --
       only to half on phones, where the title spans most of the globe */
    const te=document.getElementById('cc-hero-txt'), tr=te&&te.getBoundingClientRect(), cr=cv.getBoundingClientRect();
    const satMin=window.innerWidth<=768?0.5:0;
    const tb=tr&&tr.width?{l:tr.left-cr.left-6, r:tr.right-cr.left+6, t:tr.top-cr.top-4, b:tr.bottom-cr.top+4}:null;
    if(cv.width!==Math.round(w*dpr)||cv.height!==Math.round(h*dpr)){ cv.width=Math.round(w*dpr); cv.height=Math.round(h*dpr); }
    const x=cv.getContext('2d'); x.setTransform(dpr,0,0,dpr,0,0); x.clearRect(0,0,w,h);
    const light=document.body.classList.contains('light');
    const C=light?'30,120,190':'90,215,255';
    const cx=w/2, cy=h*0.45, R=Math.min(w*0.3, h*0.37);
    const glow=x.createRadialGradient(cx,cy,R*0.2,cx,cy,R*1.5);
    glow.addColorStop(0,`rgba(${C},.18)`); glow.addColorStop(1,`rgba(${C},0)`);
    x.fillStyle=glow; x.fillRect(0,0,w,h);
    if(gs.waiting){
      const breath=0.07+0.05*Math.sin(tt/700);
      const wg=x.createRadialGradient(cx,cy,R*0.5,cx,cy,R*1.35);
      wg.addColorStop(0,`rgba(${AMBER},0)`); wg.addColorStop(0.75,`rgba(${AMBER},${breath.toFixed(3)})`); wg.addColorStop(1,`rgba(${AMBER},0)`);
      x.fillStyle=wg; x.fillRect(0,0,w,h);
    }
    const ca=Math.cos(g.a), sa=Math.sin(g.a), tilt=0.38, ct=Math.cos(tilt), st=Math.sin(tilt);
    const P=pts.map(p=>{ const X=p[0]*ca+p[2]*sa, Z0=-p[0]*sa+p[2]*ca, Y=p[1]*ct-Z0*st, Z=p[1]*st+Z0*ct; return [cx+X*R, cy+Y*R, Z]; });
    x.lineWidth=0.6;
    links.forEach(([i,j])=>{ const z=(P[i][2]+P[j][2])/2; x.strokeStyle=`rgba(${C},${(0.05+0.22*(z+1)/2).toFixed(3)})`; x.beginPath(); x.moveTo(P[i][0],P[i][1]); x.lineTo(P[j][0],P[j][1]); x.stroke(); });
    P.forEach(p=>{ const k=(p[2]+1)/2; x.fillStyle=`rgba(${C},${(0.25+0.75*k).toFixed(3)})`; x.beginPath(); x.arc(p[0],p[1],0.7+1.4*k,0,6.283); x.fill(); });
    /* ripples: something changed */
    g.ripples=g.ripples.filter(r=>t-r.t0<1600);
    g.ripples.forEach(r=>{
      const p=(t-r.t0)/1600, col=r.col||C;
      x.strokeStyle=`rgba(${col},${(0.55*(1-p)).toFixed(3)})`; x.lineWidth=2*(1-p)+0.5;
      x.beginPath(); x.arc(cx,cy,R*(1+0.75*p),0,6.283); x.stroke();
    });
    const labels=[];
    /* rounder, wider orbits than the sphere's own rings, so satellites spend
       most of their lap around the title rather than behind it */
    const orbits=[[1.5,0.3,0.9],[1.78,-0.2,-0.6]], OV=0.42;
    orbits.forEach(([rr,tl,sp])=>{ x.strokeStyle=`rgba(${C},.28)`; x.lineWidth=1; x.beginPath(); x.ellipse(cx,cy,R*rr,R*rr*OV,tl,0,6.283); x.stroke(); });
    const posOn=(rr,tl,th)=>{ const ex=Math.cos(th)*R*rr, ey=Math.sin(th)*R*rr*OV;
      return [cx+ex*Math.cos(tl)-ey*Math.sin(tl), cy+ex*Math.sin(tl)+ey*Math.cos(tl), Math.sin(th)]; };
    orbits.forEach(([rr,tl,sp],ri)=>{
      gs.sats.filter((_,i)=>i%2===ri).forEach((s,i,arr)=>{
        const th=g.a*sp*3+i*6.283/Math.max(1,arr.length);
        const [px,py,depth]=posOn(rr,tl,th);
        const col=s.wait?AMBER:C;
        /* far half of the orbit, inside the sphere's disc: behind the globe */
        const behind=depth<0&&Math.hypot(px-cx,py-cy)<R;
        const al=(tb?_ccSatAlpha(px,py,tb,satMin):1)*(behind?0.35:1);
        if(al<=0) return;
        const dir=sp>0?1:-1;
        for(let k=1;k<=12;k++){
          const [qx,qy]=posOn(rr,tl,th-dir*k*0.035);
          x.fillStyle=`rgba(${col},${(al*0.45*(1-k/13)).toFixed(3)})`;
          x.beginPath(); x.arc(qx,qy,2.8*(1-k/15),0,6.283); x.fill();
        }
        if(s.wait){
          const ph=(tt/1100)%1;
          x.strokeStyle=`rgba(${AMBER},${(al*(1-ph)*0.9).toFixed(3)})`; x.lineWidth=1.5;
          x.beginPath(); x.arc(px,py,6+10*ph,0,6.283); x.stroke();
        }
        x.fillStyle=`rgba(${col},${al.toFixed(3)})`; x.shadowColor=`rgba(${col},${al.toFixed(3)})`; x.shadowBlur=14;
        x.beginPath(); x.arc(px,py,4.6,0,6.283); x.fill(); x.shadowBlur=0;
        x.fillStyle=`rgba(255,255,255,${(al*0.85).toFixed(3)})`; x.beginPath(); x.arc(px,py,1.7,0,6.283); x.fill();
        for(let j=0;j<s.agents;j++){
          const ma=tt/520+j*6.283/s.agents, mx=px+Math.cos(ma)*11, my=py+Math.sin(ma)*8;
          x.fillStyle=`rgba(${GREEN},${al.toFixed(3)})`; x.shadowColor=`rgba(${GREEN},${al.toFixed(3)})`; x.shadowBlur=6;
          x.beginPath(); x.arc(mx,my,2.2,0,6.283); x.fill(); x.shadowBlur=0;
        }
        if(w>=300&&!behind) labels.push({px,py,al,s,col});
      });
    });
    /* labels last, so no orbit or satellite crosses them */
    x.font=`600 11px ${font}`; x.textBaseline='middle';
    labels.forEach(({px,py,al,s,col})=>{
      const txt=(s.name.length>18?s.name.slice(0,17)+'…':s.name)+(s.agents?` · ${s.agents}`:'');
      const tw=x.measureText(txt).width, bw=tw+14, bh=18;
      let lx=px+10; if(lx+bw>w-4) lx=px-10-bw;
      const ly=Math.max(2,Math.min(h-bh-2,py-bh-4));
      /* labels always hide behind the title, even where satellites only dim (phones) */
      const la=Math.min(al, tb?Math.min(_ccSatAlpha(lx,ly,tb),_ccSatAlpha(lx+bw,ly+bh,tb),_ccSatAlpha(lx,ly+bh,tb),_ccSatAlpha(lx+bw,ly,tb)):1);
      if(la<=0.02) return;
      x.globalAlpha=la;
      x.fillStyle=light?'rgba(255,255,255,.88)':'rgba(4,14,28,.78)'; x.strokeStyle=`rgba(${col},.55)`; x.lineWidth=1;
      x.beginPath(); if(x.roundRect) x.roundRect(lx,ly,bw,bh,9); else x.rect(lx,ly,bw,bh); x.fill(); x.stroke();
      x.fillStyle=s.wait?(light?'#8a5a00':'#ffd27a'):(light?'#0d2a3d':'#dcf7ff'); x.fillText(txt,lx+7,ly+bh/2+0.5);
      x.globalAlpha=1;
    });
    x.strokeStyle=`rgba(${C},.35)`; x.lineWidth=1;
    for(let k=0;k<3;k++){ x.beginPath(); x.ellipse(cx,cy+R*1.12+k*5,R*(0.9+k*0.18),R*0.1+k*2,0,0,6.283); x.globalAlpha=0.6-k*0.18; x.stroke(); }
    x.globalAlpha=1;
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}
/* Sidebar badges + the "needs you" block, and the topbar status pill. */
function _sbUpdate(sr){
  const sessions=((sr&&sr.sessions_list)||[]).filter(s=>s&&s.session_active!==false);
  const waiting=_waitingSessions(sessions,0);
  const running=((sr&&sr.agents)||[]).filter(a=>a.status==='running').length;
  const set=(id,v,warn)=>{ const e=document.getElementById(id); if(e){ e.textContent=v?String(v):''; e.classList.toggle('warn',!!warn); } };
  set('sb-n-today', waiting.length, true);
  _wtStrandedTick();
  _bgAgentsTick();
  const bgDead=_bgDead(_bgAgents);
  const inbox=_inboxItems(sr, Date.now()/1000).length+bgDead.length+_bgStrandedLeft(bgDead, _wtStranded).length;
  /* an Orchestrate agent's held command (orch_holds) needs you too */
  const orch=((sr&&sr.orch_attention)||[]).length+((sr&&sr.orch_holds)||[]).length;
  set('sb-n-inbox', inbox+orch, true);
  set('mnav-n-inbox', inbox+orch, true);
  set('sb-n-orch', orch, true);
  set('sb-n-agents', running, false);
  set('sb-n-cli', sessions.length, false);
  const box=document.getElementById('sb-attn');
  if(box){
    box.classList.toggle('warn', waiting.length>0);
    const n=document.getElementById('sb-attn-n'), t=document.getElementById('sb-attn-txt');
    if(n) n.textContent=String(waiting.length);
    if(t) t.textContent=waiting.length?`${waiting.length===1?'A session is':waiting.length+' sessions are'} waiting on you`:(sessions.length?'All sessions are working':'No session open');
  }
}
/* Pure: the status pill's state. */
function _ccPillState(sr, ageMs){
  if(!sr||ageMs>20000) return ['off','OFFLINE'];
  const wd=((sr.infra_health||{}).watchdog)||null;
  if((wd&&!wd.pid_alive)||((sr.agents||[]).some(a=>a.status==='error'))) return ['bad','DEGRADED'];
  if((((sr.infra_health||{}).hooks_bad)||[]).length) return ['attn','CHECK HOOKS'];
  const sessions=(sr.sessions_list||[]).filter(s=>s&&s.session_active!==false);
  if(_waitingSessions(sessions,0).length) return ['attn','ATTENTION'];
  return ['ok','OPTIMAL'];
}
function _ccPillUpdate(){
  const el=document.getElementById('cc-pill');
  if(!el) return;
  const [cls,label]=_ccPillState(lastStatus, Date.now()-_ccStatusAt);
  el.className='cc-pill '+cls;
  const b=el.querySelector('b'); if(b) b.innerHTML='<i></i>'+label;
  const bad=((((lastStatus||{}).infra_health)||{}).hooks_bad)||[];
  el.title=label==='CHECK HOOKS'?'Not set up: '+bad.join(', ')+' -- click for Health → Repair':'';
  el.style.cursor=label==='CHECK HOOKS'?'pointer':'';
  el.onclick=label==='CHECK HOOKS'?()=>setView('diag'):null;
}
setInterval(_ccPillUpdate, 5000);

