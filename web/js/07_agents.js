/* ── dependency graph view ── */
function sColRgba(s){ return {running:'rgba(var(--c-rgb),.85)',done:'rgba(76,194,133,.85)',error:'rgba(239,106,87,.85)',waiting:'rgba(224,161,58,.85)'}[s]||'rgba(var(--c-rgb),.85)'; }

/* GRAPH/TREE are hand-built SVG with colors baked into inline `style=`/
   presentation attributes at render time -- a `body.light #graph-area svg
   {background:...}` stylesheet rule can never win against an inline style
   on the same element/property (inline style always outranks any
   non-!important stylesheet rule, regardless of selector specificity), so
   the theme has to be read here and baked into the right values up front
   instead. Shared by both views since they use the same dark-radar visual
   language and had the exact same bug. */
function _graphThemeColors(){
  if(document.body.classList.contains('light')) return {
    panelBg:'rgba(255,255,255,.55)', panelBorder:'rgba(0,0,0,.08)',
    nodeFill:'rgba(255,255,255,.85)', nodeStroke:'rgba(0,0,0,.08)',
    nodeText:'rgba(30,55,90,.7)', pctText:'rgba(20,60,95,.7)',
    edgeLine:'rgba(0,90,160,.32)', edgeText:'rgba(20,60,110,.6)',
    depText:'rgba(180,90,0,.55)', legendHint:'rgba(30,60,100,.5)',
    emptyText:'rgba(40,70,110,.5)',
    tooltipBg:'rgba(255,255,255,.97)', tooltipBorder:'rgba(0,0,0,.12)',
  };
  return {
    panelBg:'rgba(0,0,0,.14)', panelBorder:'rgba(255,255,255,.06)',
    nodeFill:'rgba(34,48,45,.9)', nodeStroke:'rgba(255,255,255,.04)',
    nodeText:'rgba(140,180,220,.65)', pctText:'rgba(160,200,230,.7)',
    edgeLine:'rgba(100,180,255,.3)', edgeText:'rgba(180,210,255,.55)',
    depText:'rgba(224,161,58,.4)', legendHint:'rgba(100,150,200,.35)',
    emptyText:'rgba(90,140,180,.3)',
    tooltipBg:'rgba(34,48,45,.96)', tooltipBorder:'rgba(var(--c-rgb),.2)',
  };
}
function renderGraph(data){
  const el=document.getElementById('graph-area');
  if(!el) return;
  if(!data){ el.innerHTML='<div style="color:var(--t3);font-family:var(--font2);font-size:10px;letter-spacing:.1em">WAITING FOR DATA</div>'; return; }
  const agents=(data.agents||[]).filter(a=>!String(a.id||'').startsWith('hook_'));
  if(!agents.length){ _sessFallback('graph', el, data); return; }
  _svPainted.delete(el);
  const tc=_graphThemeColors();
  const W=680, H=Math.max(360, agents.length>4?500:360);
  const cx=W/2, cy=H/2, R=Math.min(cx-90, cy-80, 170);
  const n=agents.length;
  const pos=agents.map((_,i)=>{
    if(n===1) return {x:cx,y:cy};
    const ang=(2*Math.PI*i/n)-Math.PI/2;
    return {x:Math.round(cx+R*Math.cos(ang)), y:Math.round(cy+R*Math.sin(ang))};
  });
  /* build edges: shared files + explicit depends_on */
  const edges=[];
  for(let i=0;i<n;i++) for(let j=i+1;j<n;j++){
    const ai=agents[i], aj=agents[j];
    const fa=new Set((ai.files_changed||[]).map(f=>f.path));
    const shared=(aj.files_changed||[]).filter(f=>fa.has(f.path)).map(f=>f.path);
    const iDepsJ=(ai.depends_on||[]).includes(aj.id);
    const jDepsI=(aj.depends_on||[]).includes(ai.id);
    if(shared.length||iDepsJ||jDepsI) edges.push({a:i,b:j,files:shared,iDepsJ,jDepsI});
  }
  /* SVG defs for arrow markers */
  const defs=`<defs>
    <marker id="arr-dep" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0,8 3,0 6" fill="rgba(224,161,58,.75)"/></marker>
    <filter id="glow"><feGaussianBlur in="SourceGraphic" stdDeviation="3" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  </defs>`;
  /* edges */
  const edgeSvg=edges.map((e,ei)=>{
    const a=pos[e.a], b=pos[e.b];
    const mx=Math.round((a.x+b.x)/2), my=Math.round((a.y+b.y)/2)-10;
    const sw=(Math.min(e.files.length*1.2+1.2,5)).toFixed(1);
    const isDep=e.iDepsJ||e.jDepsI;
    const stroke=isDep?'rgba(224,161,58,.55)':tc.edgeLine;
    const dash=isDep?'':'8 4';
    const marker=e.iDepsJ?'url(#arr-dep)':'';
    const markerS=e.jDepsI?'url(#arr-dep)':'';
    return `<g onclick="_graphEdgeClick(${jsq(agents[e.a].id)},${jsq(agents[e.b].id)})" style="cursor:pointer">
  <line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="transparent" stroke-width="14"/>
  <line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="${stroke}" stroke-width="${sw}" ${dash?`stroke-dasharray="${dash}"`:''}${marker?` marker-end="${marker}"`:''}${markerS?` marker-start="${markerS}"`:''}/>
  ${e.files.length?`<text x="${mx}" y="${my}" text-anchor="middle" font-size="8" fill="${tc.edgeText}" font-family="'Red Hat Mono',Consolas,monospace">${e.files.length} shared</text>`:''}
  ${isDep?`<text x="${mx}" y="${my+10}" text-anchor="middle" font-size="7" fill="rgba(224,161,58,.7)" font-family="'Red Hat Mono',Consolas,monospace">DEPENDS</text>`:''}
</g>`;
  }).join('\n');
  /* nodes */
  const nodeSvg=agents.map((a,i)=>{
    const {x,y}=pos[i];
    const rgba=sColRgba(a.status);
    const lbl=_deriveUnit(a).slice(0,4);
    const name=a.name.length>22?a.name.slice(0,21)+'…':a.name;
    const pct=a.tasks.length?Math.round(a.tasks.filter(t=>t.done).length/a.tasks.length*100):0;
    const r=28;
    const isActive=_adpAgentId===a.id;
    const connCount=edges.filter(e=>e.a===i||e.b===i).length;
    const pts=Array.from({length:6},(_,k)=>{
      const ag=k*Math.PI/3-Math.PI/6;
      return `${(x+r*Math.cos(ag)).toFixed(0)},${(y+r*Math.sin(ag)).toFixed(0)}`;
    }).join(' ');
    const arcR=r+8, arcC=(2*Math.PI*arcR).toFixed(1), arcD=(pct/100*2*Math.PI*arcR).toFixed(1);
    return `<g onclick="openAgentDetail(${jsq(a.id)},null)" onmouseover="_graphNodeHover(event,${jsq(a.id)})" onmouseout="_graphNodeOut()" style="cursor:pointer${isActive?';filter:brightness(1.2)':''}">
  ${a.status==='running'?`<circle cx="${x}" cy="${y}" r="${arcR+6}" fill="none" stroke="${rgba}" stroke-width="1" opacity=".25" style="animation:pulse 2s infinite"/>`:''}
  <polygon points="${pts}" fill="${isActive?rgba.replace(',1)',',.18)').replace('.85)',',.18)'):tc.nodeFill}" stroke="${rgba}" stroke-width="${isActive?2.8:1.8}"/>
  <circle cx="${x}" cy="${y}" r="${arcR}" fill="none" stroke="${tc.nodeStroke}" stroke-width="2.5"/>
  <circle cx="${x}" cy="${y}" r="${arcR}" fill="none" stroke="${rgba}" stroke-width="2.5" opacity="${isActive?.95:.55}" stroke-dasharray="${arcD} ${arcC}" stroke-linecap="round" transform="rotate(-90 ${x} ${y})"/>
  <text x="${x}" y="${y-5}" text-anchor="middle" font-family="'Red Hat Mono',Consolas,monospace" font-size="11" font-weight="700" fill="${rgba}">${escHtml(lbl)}</text>
  <text x="${x}" y="${y+9}" text-anchor="middle" font-family="'Red Hat Mono',Consolas,monospace" font-size="9" fill="${tc.pctText}">${pct}%</text>
  <text x="${x}" y="${y+52}" text-anchor="middle" font-family="'Atkinson Hyperlegible',sans-serif" font-size="9" fill="${tc.nodeText}">${escHtml(name)}</text>
  ${connCount>0?`<text x="${x+r+2}" y="${y-r+4}" text-anchor="middle" font-family="'Red Hat Mono',Consolas,monospace" font-size="8" fill="${rgba}" opacity=".75">${connCount}</text>`:''}
</g>`;
  }).join('\n');
  /* legend */
  const legend=`
  <text x="10" y="${H-20}" font-family="'Red Hat Mono',Consolas,monospace" font-size="8" fill="${tc.legendHint}" letter-spacing=".05em">click node → detail panel · click edge → shared files</text>
  <line x1="10" y1="${H-10}" x2="40" y2="${H-10}" stroke="${tc.edgeLine}" stroke-width="1.5" stroke-dasharray="5 3"/>
  <text x="46" y="${H-7}" font-family="'Red Hat Mono',Consolas,monospace" font-size="7" fill="${tc.edgeText}">shared files</text>
  <line x1="110" y1="${H-10}" x2="140" y2="${H-10}" stroke="rgba(224,161,58,.55)" stroke-width="1.5" marker-end="url(#arr-dep)"/>
  <text x="146" y="${H-7}" font-family="'Red Hat Mono',Consolas,monospace" font-size="7" fill="${tc.depText}">depends_on</text>`;
  el.innerHTML=`<div style="position:relative;width:100%;display:flex;flex-direction:column;align-items:center;gap:10px">
<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:${W}px;background:${tc.panelBg};border-radius:14px;border:1px solid ${tc.panelBorder}">
${defs}
${edgeSvg}
${nodeSvg}
${legend}
${edges.length===0&&n>1?`<text x="${W/2}" y="${H/2+80}" text-anchor="middle" font-family="'Red Hat Mono',Consolas,monospace" font-size="9" fill="${tc.emptyText}" letter-spacing=".1em">NO SHARED FILES — AGENTS INDEPENDENT</text>`:''}
</svg>
<div id="graph-tooltip" style="display:none;position:fixed;z-index:5000;pointer-events:none;background:${tc.tooltipBg};border:1px solid ${tc.tooltipBorder};border-radius:10px;padding:10px 14px;max-width:240px;box-shadow:0 8px 32px rgba(0,0,0,.6)"></div>
</div>`;
}

function _graphNodeHover(event, agentId){
  if(!lastStatus) return;
  const a=(lastStatus.agents||[]).find(ag=>ag.id===agentId);
  if(!a) return;
  const tip=document.getElementById('graph-tooltip');
  if(!tip) return;
  const col=sCol(a.status);
  const stLabel=stxt(a);
  const dt=(a.tasks||[]).filter(t=>t.done).length, tt=(a.tasks||[]).length;
  const cost=a.tokens_used?`$${_agentCost(a).toFixed(4)}`:null;
  // Shared by GRAPH and TREE (_treeNodeHover wraps this), yet TREE -- the
  // one view that's literally about parent_id hierarchy -- never
  // mentioned it in its own hover tooltip; the layout showed it, the text
  // never did. Last surface in the same thread as features 106/107.
  const parentAg=_resolveParentAgent(a);
  const childAgs=_resolveChildAgents(a);
  tip.innerHTML=`<div style="font-weight:700;color:${col};margin-bottom:3px;font-size:12px">${escHtml(a.name)}</div>
<div style="color:${col};font-size:9px;font-family:var(--font2);letter-spacing:.08em;margin-bottom:5px">${escHtml(stLabel)}</div>
${a.description?`<div style="color:var(--t2);margin-bottom:5px;line-height:1.4;font-size:10px">${escHtml(a.description)}</div>`:''}
${a.model?`<div style="color:var(--t3);margin-bottom:5px;font-size:9px;font-family:var(--font2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(a.model)}</div>`:''}
${parentAg?`<div style="color:var(--t3);margin-bottom:5px;font-size:9px;font-family:var(--font2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">↳ spawned by ${escHtml(parentAg.name)}</div>`:''}
${childAgs.length?`<div style="color:var(--t3);margin-bottom:5px;font-size:9px;font-family:var(--font2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">spawned ${childAgs.length} subagent${childAgs.length!==1?'s':''} →</div>`:''}
<div style="font-family:var(--font2);font-size:9px;color:var(--t3);display:flex;gap:10px;flex-wrap:wrap">
  ${a.detected_via==='transcript'?`<span style="color:rgba(224,161,58,.95)">⚠ HOOK MISS</span>`:''}
  ${_stuckSecs(a)>300?`<span style="color:rgba(224,161,58,.95)">⚠ STUCK</span>`:''}
  ${a.subagent_type?`<span>${escHtml(a.subagent_type)}</span>`:''}
  ${a.tool_use_count!=null?`<span>${a.tool_use_count} tc</span>`:''}
  ${tt?`<span>TASKS ${dt}/${tt}</span>`:''}
  ${cost?`<span style="color:rgba(76,194,133,.8)">${cost}</span>`:''}
  ${(a.files_changed||[]).length?`<span>${a.files_changed.length} files</span>`:''}
</div>
${a.status==='error'&&a.error_message?`<div style="margin-top:6px;font-size:10px;color:rgba(240,164,151,.9);font-family:var(--font2);word-break:break-all">${escHtml(a.error_message.slice(0,100))}</div>`:''}
<div style="margin-top:6px;font-size:9px;color:var(--t3);font-family:var(--font2)">click to open detail panel</div>`;
  tip.style.display='block';
  const _move=e=>{
    const vw=window.innerWidth, vh=window.innerHeight;
    let lx=e.clientX+16, ly=e.clientY-20;
    if(lx+250>vw) lx=e.clientX-260;
    if(ly+180>vh) ly=e.clientY-180;
    tip.style.left=lx+'px'; tip.style.top=ly+'px';
  };
  _move(event);
  const svg=event.target.closest('svg');
  if(svg){ svg._gTipMove=_move; svg.addEventListener('mousemove',_move); }
}

function _graphNodeOut(){
  const tip=document.getElementById('graph-tooltip');
  if(tip) tip.style.display='none';
  const svg=document.querySelector('#graph-area svg');
  if(svg&&svg._gTipMove){ svg.removeEventListener('mousemove',svg._gTipMove); delete svg._gTipMove; }
}

function _graphEdgeClick(idA, idB){
  if(!lastStatus) return;
  const agA=(lastStatus.agents||[]).find(a=>a.id===idA);
  const agB=(lastStatus.agents||[]).find(a=>a.id===idB);
  if(!agA||!agB) return;
  const fa=new Set((agA.files_changed||[]).map(f=>f.path));
  const shared=(agB.files_changed||[]).filter(f=>fa.has(f.path)).map(f=>f.path);
  const depAB=(agA.depends_on||[]).includes(idB);
  const depBA=(agB.depends_on||[]).includes(idA);
  let msg=`${agA.name} ↔ ${agB.name}`;
  if(shared.length) msg+='\nShared: '+shared.join(', ');
  if(depAB) msg+=`\n${agA.name} depends on ${agB.name}`;
  if(depBA) msg+=`\n${agB.name} depends on ${agA.name}`;
  showToast('done', 'EDGE DETAIL', shared.length?`${shared.length} shared file${shared.length>1?'s':''}: ${shared.slice(0,3).join(', ')}${shared.length>3?` +${shared.length-3} more`:''}`:depAB||depBA?'Dependency link (no shared files)':'Connected');
  log(msg.replace(/\n/g,' · '),'info');
}


/* ── render agents ── */
/* Group already-sorted agents into session bays, in order of first
   appearance. Key is machine + session_id: two machines can't share a
   session id in practice, but merged remote data must never fold into a
   local bay. Agents with no session_id share one "unattached" bay. */
function _groupIntoBays(agents, sessionsList){
  const bays=[], byKey=new Map();
  agents.forEach(a=>{
    const key=(a.machine||'')+'|'+(a.session_id||'');
    let bay=byKey.get(key);
    if(!bay){
      const s=a.session_id ? (sessionsList||[]).find(s=>s.id===a.session_id&&(s.machine||'')===(a.machine||'')) : null;
      bay={key, session:s||null, sessionId:a.session_id||null, machine:a.machine||'', isRemote:a._isLocal===false, project:a.session_project||'', agents:[]};
      byKey.set(key,bay); bays.push(bay);
    }
    bay.agents.push(a);
  });
  return bays;
}

function toggleBay(key){
  if(_collapsedBays.has(key)) _collapsedBays.delete(key);
  else {
    _collapsedBays.add(key);
    // Keys of long-gone sessions are never cleaned up otherwise; a Set keeps
    // insertion order, so drop the oldest beyond 50.
    while(_collapsedBays.size>50) _collapsedBays.delete(_collapsedBays.values().next().value);
  }
  _savePrefs();
  if(lastStatus) renderAgents(lastStatus);
}

/* ── waiting on you ──
   Pure: Claude's last message -> what to show on a waiting session. The end
   of the message matters (a question to you comes last), so long text keeps
   its tail; markdown markers are dropped for a one-glance quote. */
function _lastMessageInfo(text, max){
  max=max||220;
  let t=String(text||'').trim();
  if(!t) return null;
  const paras=t.split(/\n\s*\n/).map(p=>p.trim()).filter(Boolean);
  const last=paras[paras.length-1]||t;
  const clean=x=>x.replace(/```[\s\S]*?```/g,' ').replace(/[*_`#]+/g,'').replace(/^\s*>\s?/gm,'').replace(/^\s*[-•]\s+/gm,'').replace(/\s+/g,' ').trim();
  const asked=/\?\s*$/.test(clean(last));
  let q=clean(last)||clean(t);
  if(q.length>max) q='…'+q.slice(q.length-max+1).replace(/^\S*\s/,'');
  return {asked, text:q, full:clean(t)};
}
/* Pure: how to name what a waiting session waits for. waiting_kind comes
   from the hooks: 'turn' = Claude finished its turn (Stop), 'permission' /
   'question' = blocked mid-turn on a permission prompt / question dialog
   (Notification hook). blocked = Claude can't go on until you answer. */
function _waitLabel(s){
  const k=s&&s.waiting_kind;
  if(k==='permission') return {word:'NEEDS OK', bay:'Needs your OK', blocked:true, icon:'🔐',
    tip:'Claude is blocked on a permission prompt'+(s.waiting_message?': '+s.waiting_message:'')};
  if(k==='question') return {word:'QUESTION', bay:'Has a question', blocked:true, icon:'❓',
    tip:'Claude is waiting for your answer in a dialog'+(s.waiting_message?': '+s.waiting_message:'')};
  return {word:'WAITING', bay:'Waiting on you', blocked:false, icon:'',
    tip:'Claude finished its last turn and is waiting for your next message'};
}
/* Pure: sessions waiting on you for at least minSecs, longest wait first.
   A session blocked mid-turn (permission/question) counts after 30 s. */
function _waitingSessions(list, minSecs){
  return (list||[]).filter(s=>s&&s.session_active!==false&&s.waiting_on_you
      &&(s.waiting_secs||0)>=(_waitLabel(s).blocked?Math.min(minSecs,30):minSecs))
    .sort((a,b)=>(_waitLabel(b).blocked-_waitLabel(a).blocked)||((b.waiting_secs||0)-(a.waiting_secs||0)));
}
/* Pure: Allow / Deny for a permission prompt AOC is holding for the
   dashboard (remote approve: s.perm_requests, see _perm_open). '' if none. */
function _permAskHtml(s){
  const reqs=(s&&s.perm_requests)||[];
  if(!reqs.length) return '';
  const q=reqs[0];
  const call=(d)=>`_permDecide(${jsq(s.machine||'')},${jsq(q.id)},'${d}')`;
  const more=reqs.length>1?`<span class="perm-more">+${reqs.length-1} more after this</span>`:'';
  const detail=q.detail?`<details class="perm-detail"><summary>Details</summary><pre>${escHtml(q.detail)}</pre></details>`:'';
  const lbl=q.danger?`⚠ Dangerous command · ${escHtml(q.danger)}`:'🔐 Needs your OK';
  return `<div class="perm-ask${q.danger?' danger':''}" onclick="event.stopPropagation()">
    <div class="perm-head"><span class="woy-lbl">${lbl}</span>${more}</div>
    <div class="perm-sum">${escHtml(q.summary)}</div>${detail}
    <div class="perm-btns">
      <button class="perm-btn allow" onclick="${call('allow')}">${q.danger?'Run it':'Allow'}</button>
      <button class="perm-btn deny" onclick="${call('deny')}">Deny…</button>
      <button class="perm-btn term" onclick="${call('terminal')}" title="Let go of the prompt: Claude Code shows its normal dialog in the terminal">Answer in terminal</button>
    </div></div>`;
}
async function _permDecide(machineName,id,decision){
  let message='';
  if(decision==='deny'){
    const m=prompt('Deny. Tell Claude why, or what to do instead (optional):','');
    if(m===null) return;
    message=m;
  }
  try{
    const r=await fetch(_apiUrl(_machineFor(machineName),'/permission/decide'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,decision,message})});
    const d=await r.json().catch(()=>({}));
    if(d.ok) showToast('done',decision==='allow'?'Allowed':decision==='deny'?'Denied':'Sent to the terminal','');
    else showToast('warning','Already answered','This prompt is no longer waiting.');
  }catch(e){ showToast('warning','Not sent','Could not reach AOC.'); }
}
async function _recapSaveMode(){
  const mode=document.getElementById('st-recap-mode').value;
  try{ _recapMode=(await fetch('/recap/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode})}).then(r=>r.json())).mode; }
  catch(e){ showToast('warning','Not saved','Could not reach AOC.'); }
  if(lastStatus&&currentView==='today') renderToday(lastStatus);
}
async function _raLoad(){
  try{
    const d=await (await fetch('/permission/settings')).json();
    const m=document.getElementById('st-ra-mode'), i=document.getElementById('st-ra-idle');
    if(m) m.value=d.mode||'away';
    if(i) i.value=d.idle_min||5;
    const rp=document.getElementById('st-ra-reply');
    if(rp) rp.value=d.reply||'away';
    _raSyncIdle();
    const rm=document.getElementById('st-recap-mode');
    if(rm) rm.value=(await (await fetch('/recap/settings')).json()).mode||'auto';
  }catch(e){}
}
function _raSyncIdle(){
  const m=document.getElementById('st-ra-mode'), w=document.getElementById('st-ra-idle-wrap');
  const rp=document.getElementById('st-ra-reply');
  if(m&&w) w.style.display=m.value==='away'||(rp&&rp.value==='away')?'inline-flex':'none';
}
/* Pure: the built-in guard rules as checkboxes (checked = guarded). */
function _dgRulesHtml(rules, off){
  return (rules||[]).map(r=>`<label class="dg-rule"><input type="checkbox" data-rule="${escHtml(r.id)}" ${(off||[]).includes(r.id)?'':'checked'} onchange="_dgSave()">
    <span><b>${escHtml(r.label)}</b><br><small>${escHtml(r.why)}</small></span></label>`).join('');
}
function _dgApply(d){
  const v=n=>document.getElementById(n);
  if(v('st-dg-mode')) v('st-dg-mode').value=d.mode||'hold';
  if(v('st-dg-hold')) v('st-dg-hold').value=d.hold_min||10;
  if(v('st-dg-patterns')) v('st-dg-patterns').value=(d.patterns||[]).join('\n');
  if(d.rules&&v('st-dg-rules')) v('st-dg-rules').innerHTML=_dgRulesHtml(d.rules,d.off_rules);
  if(v('st-dg-hook')&&'hook_installed' in d) v('st-dg-hook').style.display=d.hook_installed?'none':'block';
  if(d.log&&v('st-dg-log')) v('st-dg-log').innerHTML=_dgLogHtml(d.log);
}
/* Pure: the self-test's verdict (POST /danger_guard/test). */
function _dgTestHtml(d){
  if(!d||!d.ok) return '<span class="dg-t-err">Could not check -- is AOC running?</span>';
  if(!d.hits.length) return '<span class="dg-t-ok">✓ Runs normally -- no rule matches.</span>';
  const what=d.hits.map(h=>`<b>${escHtml(h.label)}</b>`).join(', ');
  return (d.would_hold?`<span class="dg-t-hold">⚠ Would be held for your OK:</span> ${what}`
                      :`<span class="dg-t-err">Matches ${what}, but the guard is off -- it would run.</span>`)+
    d.hits.map(h=>`<div class="dg-t-part">${escHtml(h.part)}</div>`).join('');
}
/* Pure: the recently held commands (GET /danger_guard -> log, newest first). */
function _dgLogHtml(log){
  if(!log||!log.length) return '';
  const col={allowed:'var(--g)',denied:'var(--r)','no answer, denied':'var(--r)'};
  const rows=log.slice(0,10).map(e=>{
    const when=new Date(e.t*1000);
    const w=when.toDateString()===new Date().toDateString()?when.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}):when.toLocaleDateString([], {day:'numeric',month:'short'});
    const proj=(e.cwd||'').split(/[\\/]/).filter(Boolean).pop()||'';
    return `<div class="dg-log-row"><span class="dg-log-when">${escHtml(w)}</span>`+
      `<span class="dg-log-cmd" title="${escHtml(e.cmd)}">${proj?`<i>${escHtml(proj)}</i> `:''}${escHtml(e.cmd)}</span>`+
      `<span class="dg-log-out" style="color:${col[e.outcome]||'var(--t3)'}">${escHtml(e.outcome)}</span></div>`;
  }).join('');
  return `<div class="settings-lbl" style="margin:6px 0 4px">Recently held <span style="color:var(--t3);font-weight:400">(${log.length>10?'last 10 of '+log.length:log.length})</span></div>${rows}`;
}
async function _dgTest(){
  const out=document.getElementById('st-dg-test-out');
  const command=(document.getElementById('st-dg-test').value||'').trim();
  if(!out||!command) return;
  let d=null;
  try{ d=await (await fetch('/danger_guard/test',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({command,shell:document.getElementById('st-dg-test-shell').value})})).json(); }catch(e){}
  out.innerHTML=_dgTestHtml(d);
}
async function _dgLoad(){
  try{ _dgApply(await (await fetch('/danger_guard')).json()); }catch(e){}
}
async function _dgSave(){
  const v=n=>document.getElementById(n);
  const off_rules=[...document.querySelectorAll('#st-dg-rules input[data-rule]')].filter(x=>!x.checked).map(x=>x.dataset.rule);
  const patterns=(v('st-dg-patterns').value||'').split('\n').map(x=>x.trim()).filter(Boolean);
  const body={mode:v('st-dg-mode').value,hold_min:parseInt(v('st-dg-hold').value,10)||10,off_rules,patterns};
  try{
    const d=await (await fetch('/danger_guard',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
    if(!d.ok) throw new Error(d.error||'');
    v('st-dg-hold').value=d.hold_min;
    if(d.rejected&&d.rejected.length) showToast('warning','Pattern not saved','Not a valid regex: '+d.rejected.join(', '));
    v('st-dg-patterns').value=(d.patterns||[]).join('\n');
  }catch(e){ showToast('warning','Not saved','Could not reach AOC.'); }
}
async function _csLoad(){
  try{
    const d=await (await fetch('/context_save')).json();
    const v=n=>document.getElementById(n);
    if(v('st-cs-mode')) v('st-cs-mode').value=d.mode||'offer';
    if(v('st-cs-thr')) v('st-cs-thr').value=d.threshold||90;
    if(v('st-cs-prompt')){ v('st-cs-prompt').value=d.prompt||''; v('st-cs-prompt').placeholder=d.default_prompt||''; }
  }catch(e){}
}
async function _csSave(){
  const v=n=>document.getElementById(n);
  const body={mode:v('st-cs-mode').value, threshold:parseInt(v('st-cs-thr').value,10)||90, prompt:v('st-cs-prompt').value};
  try{
    const d=await (await fetch('/context_save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
    if(!d.ok) throw new Error(d.error||'');
    v('st-cs-thr').value=d.threshold; v('st-cs-prompt').value=d.prompt;
  }catch(e){ showToast('warning','Not saved','Could not reach AOC.'); }
}
/* Pure: the "save state" strip under the context meter (s.ctx_save, see
   _ctxsave_view). '' when the context isn't past the threshold. */
function _ctxSaveHtml(s){
  const cs=s&&s.ctx_save;
  if(!cs||s.session_active===false) return '';
  const sid=jsq(s.id), local=s._isLocal!==false;
  if(cs.state==='sent'){
    const at=cs.sent_at?' '+new Date(cs.sent_at*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}):'';
    return `<div class="cs-flag sent" title="Claude was asked to save its state to memory${escHtml(at)}."><span class="cs-lbl">🧠 State save sent${escHtml(at)}</span></div>`;
  }
  if(cs.state==='armed')
    return `<div class="cs-flag armed" onclick="event.stopPropagation()"><span class="cs-lbl">🧠 Saves state when this turn ends</span>${local?`<button class="adp-btn cs-btn" onclick="_ctxSaveCancel(${sid})" title="Don't send the save-state prompt">Cancel</button>`:''}</div>`;
  return `<div class="cs-flag" onclick="event.stopPropagation()"><span class="cs-lbl">🧠 Context over ${escHtml(String(cs.threshold))} % — save state before it compacts</span>${local?`<button class="adp-btn cs-btn" onclick="_ctxSaveSend(${sid})" title="Ask Claude to write what's done, where to resume and next steps to memory (sent when the current turn ends)">Save state</button>`:''}</div>`;
}
async function _ctxSaveSend(sid){
  try{
    const d=await fetch('/context_save/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sid})}).then(r=>r.json());
    if(!d.ok) showToast('warning','Not sent',d.error||'Could not send it.');
    else showToast('done','Save state',d.state==='sent'?'Sent: Claude is saving its state.':'Queued: it goes in when the current turn ends.');
  }catch(e){ showToast('warning','Not sent','Could not reach AOC.'); }
}
async function _ctxSaveCancel(sid){
  try{ await fetch('/context_save/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sid})}); }
  catch(e){ showToast('warning','Not sent','Could not reach AOC.'); }
}
async function _raSave(){
  _raSyncIdle();
  const mode=document.getElementById('st-ra-mode').value;
  const idle_min=parseInt(document.getElementById('st-ra-idle').value,10)||5;
  const reply=document.getElementById('st-ra-reply').value;
  try{
    const d=await (await fetch('/permission/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode,idle_min,reply})})).json();
    document.getElementById('st-ra-idle').value=d.idle_min;
  }catch(e){ showToast('warning','Not saved','Could not reach AOC.'); }
}
/* Pure: a finished turn AOC is holding so you can answer it from here
   (reply from the dashboard: s.reply_request, see _reply_open). '' if none. */
function _replyAskHtml(s){
  const r=s&&s.reply_request;
  if(!r) return '';
  const info=_lastMessageInfo(r.message||s.last_message, 240);
  const m=jsq(s.machine||'');
  return `<div class="perm-ask reply-ask" onclick="event.stopPropagation()">
    <div class="perm-head"><span class="woy-lbl">💬 ${info&&info.asked?'Claude asks':'Turn finished'} · reply from here</span></div>
    ${info?`<div class="perm-sum reply-sum">${escHtml(info.text)}</div>`:''}
    <div class="perm-btns">
      <button class="perm-btn allow" onclick="_replyOpen(${m},${jsq(s.id)})">Reply…</button>
      <button class="perm-btn term" onclick="_replySend(${m},${jsq(r.id)},'done')" title="Let the turn end: the session waits in its terminal as usual">Done for now</button>
    </div></div>`;
}
function _replyFind(machine, sid){
  return ((lastStatus&&lastStatus.sessions_list)||[]).find(s=>s.id===sid&&(s.machine||'')===(machine||''));
}
function _replyOpen(machine, sid){
  const s=_replyFind(machine, sid), r=s&&s.reply_request;
  if(!r){ showToast('warning','Already answered','This session is no longer waiting for a reply.'); return; }
  const name=s.display_name||s.project||'session';
  const o=_overlay('Reply · '+name, `<form class="ln-form" onsubmit="event.preventDefault();_replySubmit(this)">
    <div class="ln-lbl">Claude's last message</div>
    <pre class="reply-full">${escHtml(r.message||s.last_message||'(no text)')}</pre>
    <label class="ln-lbl" for="rp-text">Your reply</label>
    <textarea id="rp-text" class="settings-input" rows="4" required placeholder="Answer, or tell Claude what to do next" onkeydown="if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();_replySubmit(this.form)}"></textarea>
    <div class="ln-row"><span style="flex:1"></span><span class="ln-err" id="rp-err"></span>
      <button type="submit" class="adp-btn ln-go">Send</button></div>
    <div class="ln-note">Claude carries on with this in the same session. Touch the PC and the session goes back to its terminal.</div>
  </form>`, 'chg-box ln-box');
  o.dataset.machine=machine||''; o.dataset.rid=r.id;
  const pre=o.querySelector('.reply-full'); pre.scrollTop=pre.scrollHeight;
  o.querySelector('#rp-text').focus();
}
async function _replySubmit(form){
  const o=form.closest('.diff-overlay');
  const text=form.querySelector('#rp-text').value.trim();
  if(!text) return;
  const go=form.querySelector('.ln-go'); go.disabled=true;
  const d=await _replySend(o.dataset.machine, o.dataset.rid, 'reply', text, true);
  go.disabled=false;
  if(d.ok) o.remove();
  else form.querySelector('#rp-err').textContent=d.error==='no longer waiting'?'No longer waiting — the session moved on.':(d.error||'Not sent');
}
async function _replySend(machineName, id, action, text, quiet){
  let d={};
  try{
    const r=await fetch(_apiUrl(_machineFor(machineName),'/reply/send'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,action,text:text||''})});
    d=await r.json().catch(()=>({}));
  }catch(e){ d={ok:false,error:'Could not reach AOC.'}; }
  if(d.ok) showToast('done',action==='reply'?'Reply sent':'Left for the terminal',action==='reply'?'Claude carries on.':'');
  else if(!quiet) showToast('warning','Already answered','This session is no longer waiting for a reply.');
  return d;
}
function _woyQuoteHtml(s){
  const ask=_permAskHtml(s)||_replyAskHtml(s);
  if(ask) return ask;
  const wl=_waitLabel(s);
  if(wl.blocked&&s.waiting_message)
    return `<div class="woy-quote asked blocked" title="${escHtml(wl.tip)}"><div class="woy-q"><span class="woy-lbl">${escHtml(wl.bay)}</span>${escHtml(s.waiting_message)}</div></div>`;
  const info=_lastMessageInfo(s.last_message);
  if(!info) return '';
  return `<div class="woy-quote${info.asked?' asked':''}" title="${escHtml(info.full.slice(0,1500))}"><div class="woy-q"><span class="woy-lbl">${info.asked?'Claude asks':'Claude said'}</span>${escHtml(info.text)}</div></div>`;
}
/* Pure: context-window meter for a CLI session (s.context comes from the
   statusline, see _context_snapshot). While the session waits on you it
   also says when the prompt cache goes cold -- after that, the next
   message re-caches the whole context at the cache-write price. */
/* Pure: "looks stuck in a loop" flag for a CLI session (s.loop comes from
   the transcript scanner, see _loop_signal). '' when it looks healthy. */
/* Pure: the hard-budget-cap flag on a session (s.budget, see _budget_verdicts):
   its tool calls are being denied until you let it continue. '' if none. */
function _budgetBadgeHtml(s){
  const b=s&&s.budget;
  if(!b||typeof b.spent!=='number') return '';
  const what=b.kind==='day'?`Today $${b.spent.toFixed(2)} of the $${b.cap.toFixed(2)} daily cap`:`$${b.spent.toFixed(2)} of its $${b.cap.toFixed(2)} cap`;
  return `<div class="loop-flag budget-flag" onclick="event.stopPropagation()" title="Claude Code's tool calls in this session are denied by AOC until you let it continue (Settings → COST → HARD CAPS).">
    <span class="loop-lbl">⛔ Budget cap</span><span class="loop-text">${escHtml(what)} · tools blocked</span>
    <button class="adp-btn budget-go" onclick="_budgetContinue(${jsq(s.machine||'')},${jsq(s.id)},${b.kind==='day'})">${b.kind==='day'?'Lift for today':'Let it continue'}</button></div>`;
}
async function _budgetContinue(machineName, sid, day){
  try{
    const d=await fetch(_apiUrl(_machineFor(machineName),'/budget_caps/continue'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sid,day:!!day})}).then(r=>r.json());
    if(d.ok) showToast('done',day?'Daily cap lifted for today':'Session may continue',day?'':'It gets one more cap of spend.');
    else showToast('warning','Nothing to lift','That session is no longer over a cap.');
  }catch(e){ showToast('warning','Not sent','Could not reach AOC.'); }
}
/* ── Phone push (Web Push) ── */
/* Pure: can this page subscribe to push, and if not, why. */
function _pushSupport(w){
  if(!w.isSecureContext) return {ok:false, why:'Open AOC on your phone through Remote access (the https link) and press "Enable on this device" there.'};
  if(!('serviceWorker' in w.navigator)||!('PushManager' in w)) return {ok:false, why:/iPhone|iPad/.test(w.navigator.userAgent||'')
    ?'On iPhone: Share → Add to Home Screen, open AOC from that icon, then enable push here (iOS 16.4+).'
    :'This browser has no Web Push.'};
  return {ok:true, why:''};
}
function _b64uToBytes(s){
  const b=atob(s.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-s.length%4)%4));
  return Uint8Array.from(b, c=>c.charCodeAt(0));
}
let _pushInfo=null;
async function _pushLoad(){
  const hint=document.getElementById('st-push-hint'), list=document.getElementById('st-push-devices');
  if(!hint) return;
  try{ _pushInfo=await (await fetch('/push/info')).json(); }catch(e){ hint.textContent='Could not reach AOC.'; return; }
  document.getElementById('st-push-mode').value=_pushInfo.mode||'away';
  let mine='';
  try{ const reg=await navigator.serviceWorker.getRegistration(); const sub=reg&&await reg.pushManager.getSubscription(); mine=sub?sub.endpoint:''; }catch(e){}
  list.innerHTML=(_pushInfo.devices||[]).map(d=>`<div style="display:flex;align-items:center;gap:8px;font-size:11px">
    <span>📱 ${escHtml(d.label||'Device')}${d.endpoint===mine?' <span style="color:var(--g);font-size:9px">THIS DEVICE</span>':''}</span>
    <span style="color:var(--t3);font-size:9px">since ${d.created?new Date(d.created*1000).toLocaleDateString():'?'}</span>
    <button class="adp-btn" style="font-size:9px;padding:2px 8px;margin-left:auto" onclick="_pushRemove(${jsq(d.endpoint)})">Remove</button></div>`).join('');
  const sup=_pushSupport(window);
  document.getElementById('st-push-enable').style.display=sup.ok&&!mine?'':'none';
  hint.innerHTML=sup.ok?(mine?'Push is on for this device.':'Press <b>Enable on this device</b> and allow notifications.')
    +' Alerts arrive even with the AOC page closed; tapping one opens AOC.':escHtml(sup.why);
}
function _pushLabel(){
  const ua=navigator.userAgent||'';
  return /iPhone/.test(ua)?'iPhone':/iPad/.test(ua)?'iPad':/Android/.test(ua)?'Android phone':/Windows/.test(ua)?'Windows browser':/Mac/.test(ua)?'Mac browser':'Browser';
}
async function _pushEnable(){
  try{
    if(Notification.permission!=='granted'&&await Notification.requestPermission()!=='granted'){ showToast('warning','Not allowed','Notifications are blocked for this site.'); return; }
    const reg=await navigator.serviceWorker.ready;
    const key=(_pushInfo&&_pushInfo.public_key)||(await (await fetch('/push/info')).json()).public_key;
    const sub=await reg.pushManager.subscribe({userVisibleOnly:true, applicationServerKey:_b64uToBytes(key)});
    const d=await (await fetch('/push/subscribe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({subscription:sub.toJSON(),label:_pushLabel()})})).json();
    if(d.ok){ showToast('done','Push enabled','Sending a test…'); _pushTest(); }
    else showToast('warning','Not enabled',d.error||'');
  }catch(e){ showToast('warning','Push failed',String(e&&e.message||e).slice(0,120)); }
  _pushLoad();
}
async function _pushRemove(endpoint){
  try{
    await fetch('/push/unsubscribe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({endpoint})});
    const reg=await navigator.serviceWorker.getRegistration(); const sub=reg&&await reg.pushManager.getSubscription();
    if(sub&&sub.endpoint===endpoint) await sub.unsubscribe();
  }catch(e){}
  _pushLoad();
}
async function _pushTest(){
  try{
    const d=await (await fetch('/push/test',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json();
    showToast(d.ok?'done':'warning', d.ok?'Test sent':'No device', d.ok?`To ${d.devices} device${d.devices===1?'':'s'}.`:(d.error||''));
  }catch(e){ showToast('warning','Not sent','Could not reach AOC.'); }
}
async function _pushSaveMode(){
  try{ await fetch('/push/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:document.getElementById('st-push-mode').value})}); }
  catch(e){ showToast('warning','Not saved','Could not reach AOC.'); }
}
