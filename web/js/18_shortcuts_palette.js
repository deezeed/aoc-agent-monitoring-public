/* ── keyboard shortcuts ── */
const _KB_GROUPS = [
  { title:'VIEWS', items:[
    ['Y','Today'],['J','Inbox'],['C','Agents'],['B','CLI'],['T','Timeline'],['S','Summary'],['G','Graph'],
    ['X','Heatmap'],['W','Tree'],['V','History'],['K','Health'],['M','Term'],
  ]},
  { title:'RIGHT PANEL', items:[
    ['I','Panel → Files tab'],['U','Panel → Audit tab'],
  ]},
  { title:'PANELS & UI', items:[
    ['P','Toggle sidebar'],['N','Notification history'],
    ['O','Session notes'],[',','Settings'],
    ['Z','Fullscreen toggle'],['H','Toggle dark / light theme'],['?','Toggle this help'],
    ['Ctrl+K','Command palette'],
  ]},
  { title:'AGENTS', items:[
    ['F','Focus agent search'],['L','Toggle list mode'],
    ['D','Collapse done agents'],['A','Expand all agents'],
    ['E','Export agent detail (MD)'],
  ]},
  { title:'SESSIONS', items:[
    ['0','All sessions'],['1 – 5','Jump to CLI session'],['R','Reset session'],
  ]},
  { title:'TIMELINE ZOOM', items:[
    ['+','Zoom in'],['−','Zoom out'],['0','Fit / reset zoom'],
  ]},
];
let _kbOpen=false;
function _toggleKbHelp(){
  _kbOpen=!_kbOpen;
  const ov=document.getElementById('kb-overlay');
  if(!ov) return;
  ov.classList.toggle('open',_kbOpen);
  if(_kbOpen){
    const body=document.getElementById('kb-body');
    if(body&&!body.dataset.rendered){
      body.dataset.rendered='1';
      const half=Math.ceil(_KB_GROUPS.length/2);
      const cols=[_KB_GROUPS.slice(0,half),_KB_GROUPS.slice(half)];
      body.innerHTML=cols.map(col=>`<div>${col.map(g=>
        `<div class="kb-group">
          <div class="kb-group-title">${g.title}</div>
          ${g.items.map(([k,d])=>`<div class="kb-row"><kbd class="kb-key">${k}</kbd><span class="kb-desc">${d}</span></div>`).join('')}
        </div>`
      ).join('')}</div>`).join('');
    }
  }
}

/* ── command palette ── */
let _paletteOpen=false;
let _paletteSelIdx=0;
let _paletteResults=[];
// item ids, newest first (aoc_prefs 'pal'). `var` without an initializer:
// _loadPrefs() runs further up the page and has already filled it by now.
var _paletteRecent;
if(!Array.isArray(_paletteRecent)) _paletteRecent=[];
const _PALETTE_RECENT_MAX=8;
const _PALETTE_LIMIT=12;
let _paletteCtx={resume:null, resumeAt:0, fixes:null, fixesAt:0};
/* Other features add palette items here: functions (sr, ctx) -> items. */
const _PALETTE_SOURCES=[];
/* Pure: every palette item for this status + context. An item is
   {id, label, sub, kind, words, run:[action, ...args], urgent?}; `run` names an
   entry of _PALETTE_ACTIONS, so items stay plain data (testable, no closures).
   ctx = {local, resume:[cards from /resume_projects]|null, fixes:[{title,fix}]|null, applied:{}} */
function _paletteItems(sr, ctx){
  sr=sr||{}; ctx=ctx||{};
  const out=[];
  const add=(id,label,sub,kind,run,extra)=>out.push(Object.assign({id,label,sub,kind,run,words:''},extra||{}));
  add('act:new','New session…','Action','action',['newSession'],{words:'launch start claude prompt'});
  add('act:theme','Toggle light / dark','Action','action',['theme'],{words:'theme day night mode'});
  add('act:focus','Focus mode (hide side panel)','Action','action',['focus'],{words:'panel log'});
  add('act:ask','Ask AOC…','Action · question about your history','action',['ask'],{words:'question kolko spend cost history haiku'});
  const sessions=(sr.sessions_list||[]);
  sessions.forEach(s=>{
    const name=s.display_name||s.project||'CLI session';
    const m=s.machine||'';
    const where=m?' · '+m:'';
    const k=(m||'')+'|'+s.id;
    (s.perm_requests||[]).forEach(q=>{
      const what=q.summary||q.tool_name||'permission';
      add('perm:allow:'+k+':'+q.id,'Allow: '+what,'Needs your OK · '+name+where,'permission',['perm',m,q.id,'allow'],{urgent:true,words:'approve yes permission '+name});
      add('perm:deny:'+k+':'+q.id,'Deny: '+what,'Needs your OK · '+name+where,'permission',['perm',m,q.id,'deny'],{urgent:true,words:'reject no permission '+name});
    });
    if(!m&&s.session_active!==false) add('sess:focus:'+k,'Switch to terminal · '+name,'Session','session',['focusSession',s.id],{words:'focus window go '+(s.project||'')});
    add('sess:agents:'+k,'Show agents · '+name,'Session'+where,'session',['sessionAgents',s.id],{words:'filter '+(s.project||'')});
    add('sess:replay:'+k,'Replay · '+name,'Session'+where,'session',['replay',s.id,m,name],{words:'timeline steps '+(s.project||'')});
    if(s.edited_count) add('sess:changes:'+k,`Changes (${s.edited_count} file${s.edited_count===1?'':'s'}) · `+name,'Session'+where,'session',['changes',s.id,m,name],{words:'diff edits uncommitted '+(s.project||'')});
    if(s.session_active===false) add('sess:dismiss:'+k,'Dismiss · '+name,'Closed session'+where,'session',['dismiss',s.id,m,name],{words:'remove close '+(s.project||'')});
    else if(s.host_pid||m) add('sess:stop:'+k,'Force stop · '+name,'Session'+where,'danger',['forceStop',s.id,m,name],{words:'kill stop '+(s.project||'')});
  });
  (sr.agents||[]).filter(a=>!String(a.id||'').startsWith('hook_')).forEach(a=>{
    add('agent:'+a.id,a.name||a.id,'Agent · '+(a.status||''),'agent',['agent',a.id]);
  });
  (ctx.resume||[]).forEach(c=>{
    if(!c||!c.name||!c.cwd) return;
    if((c.open||[]).length&&ctx.local!==false) add('resume:switch:'+c.name,'Switch to '+c.name,'Project · open','project',['focusSession',c.open[0]],{words:'continue resume pokracuj project'});
    else add('resume:go:'+c.name,'Continue '+c.name,'Project · “'+(c.prompt||'')+'”','project',['launch',c.cwd,c.prompt||''],{words:'continue resume pokracuj project start'});
    if(c.resume_id) add('resume:last:'+c.name,'Reopen last conversation · '+c.name,'Project','project',['launchResume',c.cwd,c.resume_id],{words:'resume history conversation'});
  });
  if(ctx.local!==false) (ctx.fixes||[]).forEach(t=>{
    const f=t&&t.fix;
    if(!f||!f.kind||f.kind==='manual'||(ctx.applied||{})[f.kind+':'+f.target]) return;
    add('fix:'+f.kind+':'+f.target,'Fix: '+(t.title||f.target),'One-click fix · preview first','fix',['fix',f.kind,f.target],{words:'tip apply settings'});
  });
  [['command','Command Center'],['today','Today'],['inbox','Inbox'],['orch','Orchestrate'],['agents','Agents'],['cli','Sessions'],['timeline','Timeline'],['summary','Summary'],
   ['graph','Graph'],['heat','Heatmap'],['tree','Tree'],['history','History'],['diag','Health'],['term','Terminal']].forEach(([v,label])=>{
    add('view:'+v,'View → '+label,'View','view',['view',v]);
  });
  [['sessions','Sessions'],['analytics','Analytics'],['search','Conversations'],['cache','Cache'],['commits','Commits'],['clients','Clients'],
   ['models','Models'],['context','Context'],['failures','Failures'],['permissions','Permissions'],['memory','Memory']].forEach(([t,label])=>{
    add('hist:'+t,'History → '+label,'History','history',['hist',t]);
  });
  [['cost','Cost'],['notify','Notify'],['look','Look'],['access','Access'],['tunnel','Tunnel'],['remote','Remote'],['infra','Infra']].forEach(([pane,label])=>{
    add('settings:'+pane,'Settings → '+label,'Settings','settings',['settings',pane]);
  });
  _PALETTE_SOURCES.forEach(fn=>{ try{ (fn(sr,ctx)||[]).forEach(i=>{ if(i&&i.id&&i.label&&i.run) out.push(Object.assign({sub:'',kind:'action',words:''},i)); }); }catch(e){} });
  return out;
}
/* Pure: fold diacritics + case ("pokračuj" matches "pokracuj"). */
function _paletteNorm(s){
  return String(s||'').normalize('NFKD').replace(/[̀-ͯ]/g,'').toLowerCase();
}
/* Pure: score of one item for query words (0 = no match). Every word must
   be found: a word start in the label scores most, then anywhere in the
   label, then sub/extra words, then letters in order (fuzzy). */
function _paletteScore(item, words){
  const label=_paletteNorm(item.label), rest=_paletteNorm((item.sub||'')+' '+(item.words||''));
  let total=0;
  for(const w of words){
    const i=label.indexOf(w);
    if(i===0||(i>0&&/[^a-z0-9]/.test(label[i-1]))) total+=10;
    else if(i>0) total+=6;
    else if(rest.includes(w)) total+=3;
    else{
      let p=0;
      for(const ch of label){ if(ch===w[p]) p++; if(p===w.length) break; }
      if(p<w.length) return 0;
      total+=1;
    }
  }
  return total;
}
/* Pure: what the palette lists for query q. Empty query: recent actions,
   then waiting permission prompts, then everything else in order. Recent
   items also get a boost while typing. */
function _paletteFilter(items, q, recent, limit){
  recent=recent||[]; limit=limit||_PALETTE_LIMIT;
  const rank=id=>{ const i=recent.indexOf(id); return i<0?0:(recent.length-i); };
  const words=_paletteNorm(q).trim().split(/\s+/).filter(Boolean);
  if(!words.length){
    const rec=recent.map(id=>items.find(x=>x.id===id)).filter(Boolean);
    const urgent=items.filter(x=>x.urgent&&!recent.includes(x.id));
    const restI=items.filter(x=>!x.urgent&&!recent.includes(x.id));
    return rec.concat(urgent,restI).slice(0,limit);
  }
  return items.map((x,i)=>({x,i,s:_paletteScore(x,words)})).filter(r=>r.s>0)
    .sort((a,b)=>(b.s+(rank(b.x.id)?2+rank(b.x.id)/10:0)+(b.x.urgent?1:0))-(a.s+(rank(a.x.id)?2+rank(a.x.id)/10:0)+(a.x.urgent?1:0))||a.i-b.i)
    .slice(0,limit).map(r=>r.x);
}
/* Pure: recent list after using item id (newest first, capped). */
function _paletteRemember(recent, id){
  return [id].concat((recent||[]).filter(x=>x!==id&&typeof x==='string')).slice(0,_PALETTE_RECENT_MAX);
}
async function _paletteLaunch(body, label){
  try{
    const d=await fetch('/launch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(r=>r.json());
    if(d.ok) showToast('done',body.resume?'Conversation reopened':'Session started',label+(d.trusted===false?' · accept "trust this folder" in the new window first.':' shows up here in a few seconds.'),d.trusted===false?12000:5000);
    else showToast('warning','Not started',d.error||'Could not start it.');
  }catch(e){ showToast('warning','Not started','Could not reach AOC.'); }
}
const _PALETTE_ACTIONS={
  newSession:()=>openLaunch(),
  theme:()=>toggleTheme(),
  focus:()=>toggleRightPanel(),
  view:v=>setView(v),
  hist:t=>{ _histTab=t; setView('history'); },
  settings:p=>{ openSettings(); setSettingsTab(p); },
  agent:id=>{ setView('agents'); openAgentDetail(id); },
  sessionAgents:id=>{ setView('agents'); setSessionFilter(id); },
  focusSession:id=>_focusSession(id),
  replay:(id,m,name)=>openReplay(id,m,name),
  changes:(id,m,name)=>openSessionChanges(id,m,name),
  dismiss:(id,m,name)=>{ if(confirm(`Dismiss "${name}" from the dashboard?`)) _dismissSession(id,m); },
  forceStop:(id,m,name)=>_forceStopSession(id,name,m),   // asks itself
  perm:(m,id,d)=>_permDecide(m,id,d),
  launch:(cwd,prompt)=>_paletteLaunch({cwd,prompt},prompt||cwd),
  launchResume:(cwd,id)=>_paletteLaunch({cwd,resume:id},cwd),
  fix:(kind,target)=>_fixOpen(JSON.stringify({kind,target})),
  ask:()=>openAsk(),
};
function _paletteRun(item){
  const fn=item&&item.run&&_PALETTE_ACTIONS[item.run[0]];
  if(fn) fn(...item.run.slice(1));
}
/* Background: project cards and fix tips for the palette (each at most every
   5 min; the palette re-renders when they arrive). */
async function _paletteLoadCtx(){
  const now=Date.now(), local=_fixLocal();
  const jobs=[];
  if(now-_paletteCtx.resumeAt>300000){
    _paletteCtx.resumeAt=now;
    jobs.push(fetch('/resume_projects').then(r=>r.ok?r.json():null).then(d=>{ _paletteCtx.resume=d&&Array.isArray(d.cards)?d.cards:null; }).catch(()=>{}));
  }
  if(local&&now-_paletteCtx.fixesAt>300000){
    _paletteCtx.fixesAt=now;
    jobs.push(Promise.all([fetch('/context_report?days=30').then(r=>r.json()).catch(()=>null),
                           fetch('/failure_map?days=30').then(r=>r.json()).catch(()=>null), _fixesLoad()]).then(([c,f])=>{
      _paletteCtx.fixes=[].concat((c&&c.tips)||[], (f&&f.tips)||[]).filter(t=>t&&t.fix)
        .map(t=>({title:t.title||`${t.reason||'Failing'}${t.target?' · '+t.target:''}`, fix:t.fix}));
    }));
  }
  if(!jobs.length) return;
  await Promise.all(jobs);
  if(_paletteOpen){ const inp=document.getElementById('palette-input'); _paletteRender(inp?inp.value:''); }
}
function _paletteCandidates(){
  return _paletteItems(lastStatus, {local:_fixLocal(), resume:_paletteCtx.resume, fixes:_paletteCtx.fixes, applied:_fixesApplied});
}
function openPalette(){
  _paletteOpen=true;
  const ov=document.getElementById('palette-overlay');
  const input=document.getElementById('palette-input');
  if(ov) ov.classList.add('open');
  if(input){ input.value=''; setTimeout(()=>input.focus(),50); }
  _paletteRender('');
  _paletteLoadCtx();
}
function closePalette(){
  _paletteOpen=false;
  const ov=document.getElementById('palette-overlay');
  if(ov) ov.classList.remove('open');
}
const _PALETTE_ICON={action:'⚡',permission:'🔐',session:'›',danger:'⛔',agent:'◆',project:'▶',fix:'🛠',view:'▦',history:'⟲',settings:'⚙'};
/* Pure: the result list. */
function _paletteListHtml(items, sel, recent){
  if(!items.length) return '<div class="palette-empty">No matches</div>';
  return items.map((c,i)=>
    `<div class="palette-item k-${escHtml(c.kind||'action')}${i===sel?' active':''}" onclick="_paletteActivate(${i})" onmousemove="_paletteHover(${i})">
      <span class="pi-ico">${_PALETTE_ICON[c.kind]||'›'}</span>
      <span class="pi-label">${escHtml(c.label)}</span>
      <span class="pi-sub">${(recent||[]).includes(c.id)?'<span class="pi-recent">recent</span> ':''}${escHtml(c.sub||'')}</span>
    </div>`
  ).join('');
}
function _paletteRender(query){
  const prev=_paletteResults[_paletteSelIdx];
  _paletteResults=_paletteFilter(_paletteCandidates(),query,_paletteRecent);
  // a background refresh keeps the highlighted item; typing starts at the top
  const keep=prev&&query===_paletteLastQuery?_paletteResults.findIndex(x=>x.id===prev.id):-1;
  _paletteSelIdx=keep>=0?keep:0;
  _paletteLastQuery=query;
  const el=document.getElementById('palette-results');
  if(!el) return;
  el.innerHTML=_paletteListHtml(_paletteResults,_paletteSelIdx,_paletteRecent);
}
let _paletteLastQuery='';
function _paletteActivate(idx){
  const c=_paletteResults[idx];
  if(!c) return;
  closePalette();
  _paletteRecent=_paletteRemember(_paletteRecent,c.id);
  _savePrefs();
  try{ _paletteRun(c); }catch(e){ console.error(e); }
}
function _paletteHover(i){
  if(i===_paletteSelIdx) return;
  _paletteSelIdx=i;
  document.querySelectorAll('#palette-results .palette-item').forEach((el,j)=>el.classList.toggle('active',j===i));
}
function _paletteMove(delta){
  if(!_paletteResults.length) return;
  _paletteSelIdx=(_paletteSelIdx+delta+_paletteResults.length)%_paletteResults.length;
  const els=document.querySelectorAll('#palette-results .palette-item');
  els.forEach((el,i)=>el.classList.toggle('active',i===_paletteSelIdx));
  if(els[_paletteSelIdx]&&els[_paletteSelIdx].scrollIntoView) els[_paletteSelIdx].scrollIntoView({block:'nearest'});
}
function _paletteInputKeydown(e){
  if(e.key==='ArrowDown'){ e.preventDefault(); _paletteMove(1); }
  else if(e.key==='ArrowUp'){ e.preventDefault(); _paletteMove(-1); }
  else if(e.key==='Enter'){ e.preventDefault(); _paletteActivate(_paletteSelIdx); }
  else if(e.key==='Escape'){ e.preventDefault(); closePalette(); }
}
document.addEventListener('keydown',e=>{
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){
    e.preventDefault();
    _paletteOpen?closePalette():openPalette();
  }
});
document.addEventListener('keydown',e=>{
  if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA'||e.metaKey||e.ctrlKey||e.altKey) return;
  if(e.key==='Escape'){ if(_kbOpen){ _toggleKbHelp(); return; } }
  switch(e.key.toUpperCase()){
    case 'Y': setView('today');    log('View → TODAY','info');    break;
    case 'J': setView('inbox');    log('View → INBOX','info');    break;
    case 'C': setView('agents');   log('View → AGENTS','info');   break;
    case 'B': setView('cli');      log('View → CLI','info');      break;
    case 'T': setView('timeline'); log('View → TIMELINE','info'); break;
    case 'S': setView('summary');  log('View → SUMMARY','info');  break;
    case 'G': setView('graph');    log('View → GRAPH','info');    break;
    case 'X': setView('heat');     log('View → HEAT','info');     break;
    case 'K': setView('diag');     log('View → HEALTH','info');   break;
    case 'W': setView('tree');     log('View → TREE','info');     break;
    case 'I': setRightTab('files'); log('Panel → FILES','info');  break;
    case 'U': setRightTab('audit'); log('Panel → AUDIT','info');  break;
    case 'V': setView('history');  log('View → HISTORY','info'); break;
    case 'M': setView('term');     log('View → TERM','info');    break;
    case 'R': resetSession();      break;
    case '?': _toggleKbHelp();    break;
    case 'F': { const si=document.getElementById('search-input'); if(si){si.focus();e.preventDefault();} break; }
    case 'L': toggleListMode(); break;
    case 'D': collapseAllDone(); break;
    case 'A': expandAll(); break;
    case 'P': toggleRightPanel(); break;
    case 'N': _notifOpen?closeNotifPanel():openNotifPanel(); break;
    case 'O': _notesOpen?closeNotesPanel():openNotesPanel(); break;
    case 'Z': toggleFullscreen(); break;
    case ',': _settingsOpen?closeSettings():openSettings(); break;
    case 'E': if(_adpAgentId) exportAgentDetail(); break;
    case 'H': toggleTheme(); break;
    case '+': case '=': if(currentView==='timeline'){ _tlZoomIn(); e.preventDefault(); } break;
    case '-': if(currentView==='timeline'){ _tlZoomOut(); e.preventDefault(); } break;
    case '0': { if(currentView==='timeline'){ _tlZoomReset(); } else { setSessionFilter(null); log('Session → ALL','info'); } break; }
    case '1': case '2': case '3': case '4': case '5': {
      if(lastStatus){
        const now=Date.now(); const cutoff=now-1800000;
        const sess=(lastStatus.sessions_list||[]);
        const idx=parseInt(e.key)-1;
        if(idx<sess.length){ setSessionFilter(sess[idx].id); log(`Session → ${sess[idx].project||sess[idx].id.slice(-6)}`,'info'); }
      }
      break;
    }
    case 'TAB': {
      e.preventDefault();
      const _views=['agents','cli','timeline','summary','graph','heat','diag','tree','history','term'];
      const _ci=_views.indexOf(currentView);
      setView(_views[(_ci+1)%_views.length]);
      break;
    }
  }
});
/* Escape: close overlays in priority order */
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'){
    if(_paletteOpen){ closePalette(); e.preventDefault(); return; }
    if(_kbOpen){ _toggleKbHelp(); e.preventDefault(); return; }
    /* _overlay() dialogs (Launch session, diffs, reply, ...): topmost first */
    const ovs=document.querySelectorAll('.diff-overlay');
    if(ovs.length){ ovs[ovs.length-1].remove(); e.preventDefault(); return; }
    if(_soundPanelOpen){ closeSoundPanel(); e.preventDefault(); return; }
    if(_cmpOpen){ closeCompare(); e.preventDefault(); return; }
    if(_sessCmpOpen){ closeSessionCompare(); e.preventDefault(); return; }
    if(_notifOpen){ closeNotifPanel(); e.preventDefault(); return; }
    if(_notesOpen){ closeNotesPanel(); e.preventDefault(); return; }
    if(_settingsOpen){ closeSettings(); e.preventDefault(); return; }
    if(_adpAgentId){ closeAgentDetail(); e.preventDefault(); return; }
    const si=document.getElementById('search-input');
    if(si&&document.activeElement===si){ setSearch(''); si.blur(); e.preventDefault(); }
  }
});
/* role="tab" elements are <div>s, not native <button>s -- give them the
   keyboard behavior a real ARIA tab pattern requires: Enter/Space
   activates the focused tab, Left/Right roves focus within the same
   tablist (wrapping), matching what a screen reader user expects from
   the role it's being told these elements have. */
document.addEventListener('keydown',e=>{
  const el=e.target;
  if(!el.matches||!el.matches('[role="tab"]')) return;
  if(e.key==='Enter'||e.key===' '){ e.preventDefault(); el.click(); return; }
  if(e.key==='ArrowRight'||e.key==='ArrowLeft'){
    const tabs=Array.from(el.closest('[role="tablist"]').querySelectorAll('[role="tab"]'));
    const i=tabs.indexOf(el);
    if(i===-1) return;
    e.preventDefault();
    const next=tabs[(i+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length];
    next.focus();
    next.click();
  }
});
log('Shortcuts: Y J C B T S G X W I U = views · R = reset · F = search · Ctrl+K = palette · ? = help','info');
