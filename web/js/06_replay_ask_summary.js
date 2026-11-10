/* ── Session replay (GET /session_replay) ── */
function _replayBtnHtml(s){
  if(!s||!s.id) return '';
  const label=s.display_name||s.project||'this session';
  return `<button class="chg-btn rp-btn" onclick="event.stopPropagation();openReplay(${jsq(s.id)},${jsq(s.machine||'')},${jsq(label)})" title="Replay this session step by step: prompts, tool calls, errors, commits and what each step cost">▶</button>`;
}
const _RP_GLYPH={prompt:'›',command:'/',text:'◆',tool:'⚙'};
/* Pure: which steps a filter shows. */
function _replayFilter(steps, f, top){
  const t=new Set(top||[]);
  return (steps||[]).filter(s=>f==='prompts'?(s.kind==='prompt'||s.kind==='text')
    :f==='tools'?s.kind==='tool':f==='errors'?!!s.err:f==='costly'?t.has(s.i):f==='commits'?!!s.commit:true);
}
/* Pure: "+1:02:03" offset of a step from the session start. */
function _replayOffset(t, start){
  if(t==null||start==null) return '';
  let s=Math.max(0,Math.round(t-start));
  const h=Math.floor(s/3600), m=Math.floor(s%3600/60); s%=60;
  return '+'+(h?h+':'+String(m).padStart(2,'0'):m)+':'+String(s).padStart(2,'0');
}
/* Pure: the replay panel from GET /session_replay. */
function _replayHtml(d, f){
  if(!d||!d.ok) return `<div class="chg-empty">${escHtml((d&&d.error)||'Could not load the session.')}</div>`;
  const sm=d.summary||{}, steps=d.steps||[], money=v=>'$'+(Number(v)||0).toFixed(v>=1?2:3);
  const dur=sm.start!=null&&sm.end!=null?_fmtDurationDHM(Math.max(60,sm.end-sm.start)):'';
  const facts=[dur, money(sm.cost), `${sm.calls||0} API calls`, `${sm.prompts||0} prompts`, `${sm.tools||0} tool calls`,
    sm.errors?`<span class="rp-bad">${sm.errors} failed</span>`:'', sm.commits?`${sm.commits} commit${sm.commits===1?'':'s'}`:''].filter(Boolean);
  const total=steps.reduce((a,s)=>a+(s.cost||0),0)||1;
  const strip=steps.filter(s=>s.cost).map(s=>`<i class="rp-seg k-${s.err?'err':s.kind}" style="flex-grow:${(s.cost/total*1000).toFixed(2)}" onclick="_replayGo(${s.i})" title="${escHtml(_replayOffset(s.t,sm.start)+' · '+money(s.cost)+' · '+(s.text||'').slice(0,80))}"></i>`).join('');
  const top=new Set(sm.top||[]);
  const tabs=[['all','All'],['prompts','Conversation'],['tools','Tools'],['errors','Failed'],['commits','Commits'],['costly','Costliest 5']]
    .map(([k,l])=>`<button class="rp-tab${f===k?' on':''}" onclick="_replaySetFilter(${jsq(k)})">${l}</button>`).join('');
  const rows=_replayFilter(steps, f, sm.top).map(s=>{
    const k=s.err?'err':s.commit?'commit':s.kind;
    const glyph=s.err?'✖':s.commit?'⎇':(_RP_GLYPH[s.kind]||'·');
    const head=s.kind==='tool'?`<b>${escHtml(s.tool||'')}</b> ${escHtml(String(s.text||'').replace(/^[^:]*:\s*/,''))}`:escHtml(s.text||'');
    const more=(s.detail||s.result)?`<details class="rp-more"><summary>${s.err?'Error':'Details'}</summary>${s.detail?`<pre>${escHtml(s.detail)}</pre>`:''}${s.result?`<pre class="${s.err?'rp-errout':''}">${escHtml(s.result)}</pre>`:''}</details>`:'';
    const cost=s.cost?`<span class="rp-cost${top.has(s.i)?' top':''}" title="${escHtml((s.model||'')+' · '+(s.tokens||0).toLocaleString()+' tokens')}">${money(s.cost)}</span>`:'';
    return `<div class="rp-row k-${k}" id="rp-${s.i}" data-i="${s.i}"><span class="rp-t">${_replayOffset(s.t,sm.start)}</span><span class="rp-g">${glyph}</span>
      <div class="rp-body"><div class="rp-text">${head}${s.secs>=10?`<span class="rp-secs">${Math.round(s.secs)}s</span>`:''}</div>${more}</div>${cost}</div>`;
  }).join('');
  return `<div class="rp-wrap">
    <div class="rp-facts">${facts.map(x=>`<span>${x}</span>`).join('')}</div>
    ${strip?`<div class="rp-strip" title="Where the money went: one block per API call, width = its cost">${strip}</div>`:''}
    <div class="rp-ctl"><button class="adp-btn rp-play" onclick="_replayPlay()" title="Step through the session">▶ Play</button>
      <input type="range" class="rp-scrub" min="0" max="${Math.max(0,steps.length-1)}" value="0" oninput="_replayGo(+this.value,true)" aria-label="Scrub through the session">
      <span class="rp-pos">${steps.length} steps</span></div>
    <div class="rp-tabs">${tabs}</div>
    ${d.truncated?'<div class="rp-note">Long session: showing its last 4,000 steps.</div>':''}
    <div class="rp-list">${rows||'<div class="chg-empty">Nothing of this kind in this session.</div>'}</div>
  </div>`;
}
let _replayData=null, _replayFilterKey='all', _replayTimer=null;
async function openReplay(sid, machine, label, filter){
  const o=_overlay('Replay · '+label, '<div class="chg-empty">Loading…</div>', 'chg-box rp-box');
  _replayFilterKey=filter||'all';
  try{ _replayData=await fetch(_apiUrl(_machineFor(machine),'/session_replay?session_id='+encodeURIComponent(sid))).then(r=>r.json()); }
  catch(e){ _replayData={ok:false,error:'Could not reach AOC.'}; }
  o.querySelector('.chg-body').innerHTML=_replayHtml(_replayData,_replayFilterKey);
  const obs=new MutationObserver(()=>{ if(!document.body.contains(o)){ clearInterval(_replayTimer); _replayTimer=null; obs.disconnect(); } });
  obs.observe(document.body,{childList:true});
}
function _replaySetFilter(k){
  _replayFilterKey=k;
  const b=document.querySelector('.rp-box .chg-body');
  if(b) b.innerHTML=_replayHtml(_replayData,k);
}
function _replayGo(i, fromScrub){
  const box=document.querySelector('.rp-box'); if(!box) return;
  let row=box.querySelector('#rp-'+i);
  if(!row&&_replayFilterKey!=='all'){ _replaySetFilter('all'); row=box.querySelector('#rp-'+i); }
  box.querySelectorAll('.rp-row.cur').forEach(r=>r.classList.remove('cur'));
  if(row){ row.classList.add('cur'); row.scrollIntoView({block:'center',behavior:fromScrub?'auto':'smooth'}); }
  const sc=box.querySelector('.rp-scrub'); if(sc&&!fromScrub) sc.value=i;
  const pos=box.querySelector('.rp-pos'), n=(_replayData&&_replayData.steps||[]).length;
  if(pos) pos.textContent=`step ${i+1} of ${n}`;
}
function _replayPlay(){
  const box=document.querySelector('.rp-box'); if(!box) return;
  const btn=box.querySelector('.rp-play');
  if(_replayTimer){ clearInterval(_replayTimer); _replayTimer=null; btn.textContent='▶ Play'; return; }
  if(_replayFilterKey!=='all') _replaySetFilter('all');
  const sc=document.querySelector('.rp-box .rp-scrub'), n=(_replayData&&_replayData.steps||[]).length;
  let i=+((sc&&sc.value)||0); if(i>=n-1) i=0;
  document.querySelector('.rp-box .rp-play').textContent='❚❚ Pause';
  _replayTimer=setInterval(()=>{
    if(!document.querySelector('.rp-box')||i>=n){ clearInterval(_replayTimer); _replayTimer=null; const b=document.querySelector('.rp-box .rp-play'); if(b) b.textContent='▶ Play'; return; }
    _replayGo(i++);
  },450);
}
function _changesBtnHtml(s){
  if(!s||!s.edited_count) return '';
  const label=s.display_name||s.project||'this session';
  return `<button class="chg-btn" onclick="event.stopPropagation();openSessionChanges(${jsq(s.id)},${jsq(s.machine||'')},${jsq(label)})" title="Uncommitted changes in the ${s.edited_count} file${s.edited_count===1?'':'s'} this session edited">Δ ${s.edited_count}</button>`;
}
/* Pure: the Changes panel body from GET /session_changes. */
function _sessionChangesHtml(d, sid, machine){
  if(!d||!d.ok) return `<div class="chg-empty">${escHtml((d&&d.error)||'Could not load the changes.')}</div>`;
  const kindTag={modified:'M',added:'A',deleted:'D',renamed:'R',untracked:'NEW'};
  // review actions write to the repo: only on this PC's own sessions
  const act=!!d.local&&!machine;
  const all=(d.repos||[]).flatMap(r=>r.files);
  const seenFiles=all.filter(f=>f.seen);
  const repos=(d.repos||[]).map(r=>`<div class="chg-repo"><div class="chg-repo-head"><b>${escHtml(r.name)}</b>${r.branch?`<span class="chg-branch">⎇ ${escHtml(r.branch)}</span>`:''}<span class="chg-root">${escHtml(r.root)}</span></div>
    ${r.files.map(f=>`<div class="chg-row${f.seen?' seen':''}"><button class="chg-file" onclick="_showSessionFileDiff(${jsq(sid)},${jsq(machine)},${jsq(f.path)},${jsq(f.rel)})">
      <span class="chg-kind k-${f.kind}">${kindTag[f.kind]||'M'}</span><span class="chg-rel">${escHtml(f.rel)}</span>
      <span class="chg-num">${f.ins!=null?`<span class="cc-ins">+${f.ins}</span>`:''} ${f.dels?`<span class="cc-del">−${f.dels}</span>`:''}</span></button>${act?`
      <button class="chg-act chg-seen${f.seen?' on':''}" onclick="_chgSeen(${jsq(sid)},${jsq(f.path)},${!f.seen})" title="${f.seen?'Reviewed — click to unmark':'Mark as reviewed (cleared if the file changes again)'}">✓</button>${f.kind==='renamed'?'':`
      <button class="chg-act chg-disc" onclick="_chgDiscard(${jsq(sid)},${jsq(f.path)},${jsq(f.rel)},${jsq(f.kind)},${!!d.working},${f.sig==null?'null':jsq(f.sig)})" title="${f.kind==='untracked'||f.kind==='added'?'Remove this new file (kept in AOC\'s backup, can be restored)':'Throw away the uncommitted changes (AOC keeps a copy, can be restored)'}">Discard</button>`}`:''}</div>`).join('')}</div>`).join('');
  const n=all.length;
  const head=act&&n?`<div class="chg-bar">${d.working?'<span class="chg-warn">⚠ The session is still working — it may edit these files again.</span>':''}
    <span class="chg-progress">${seenFiles.length}/${n} reviewed</span>
    ${seenFiles.length?`<button class="adp-btn chg-stage" onclick="_chgStage(${jsq(sid)},${escHtml(JSON.stringify(seenFiles.map(f=>String(f.path))))})" title="git add the reviewed files">Stage reviewed (${seenFiles.length})</button>`:''}</div>`:'';
  const disc=act&&(d.discards||[]).length?`<div class="chg-discards"><div class="chg-sec">Discarded — can be put back</div>${d.discards.map(m=>`<div class="chg-row disc">
      <span class="chg-kind k-${escHtml(m.kind||'modified')}">${kindTag[m.kind]||'M'}</span><span class="chg-rel">${escHtml(m.rel||m.path)}</span>
      <span class="chg-when">${m.at?escHtml(new Date(m.at*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})):''}</span>
      <button class="chg-act" onclick="_chgUndo(${jsq(sid)},${jsq(m.id)},${jsq(m.rel||m.path)})" title="Put the file back as it was before the discard">↶ Restore</button></div>`).join('')}</div>`:'';
  const foot=[d.clean?`${d.clean} edited file${d.clean===1?' is':'s are'} committed or unchanged`:'', d.outside?`${d.outside} outside any git repo`:''].filter(Boolean).join(' · ');
  return `${head}${n?repos:'<div class="chg-empty">Nothing uncommitted — everything this session edited is committed.</div>'}${disc}${foot?`<div class="chg-foot">${escHtml(foot)}</div>`:''}`;
}
/* Review actions in the Changes panel (this PC only, see _sessionChangesHtml). */
let _chgCtx=null;
async function _chgReload(){
  const x=_chgCtx;
  if(!x||!document.body.contains(x.o)) return;
  try{
    const d=await fetch(_apiUrl(_machineFor(x.machine),'/session_changes?session_id='+encodeURIComponent(x.sid))).then(r=>r.json());
    x.o.querySelector('.chg-body').innerHTML=_sessionChangesHtml(d, x.sid, x.machine);
  }catch(e){ x.o.querySelector('.chg-body').innerHTML=_sessionChangesHtml(null); }
}
async function _chgPost(route, body){
  try{ return await fetch(route,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(r=>r.json()); }
  catch(e){ return {ok:false,error:'Could not reach AOC.'}; }
}
async function _chgSeen(sid, path, seen){
  const r=await _chgPost('/session_changes/seen',{session_id:sid, path, seen});
  if(!r.ok) showToast('warning','Not saved', r.error||'failed');
  _chgReload();
}
async function _chgDiscard(sid, path, rel, kind, working, sig){
  const isNew=kind==='untracked'||kind==='added';
  const msg=(isNew?`Remove the new file "${rel}"?\n\nIt moves into AOC's backup — you can restore it from this panel.`
    :`Throw away the uncommitted changes in "${rel}" and go back to the last commit?\n\nAOC keeps a copy — you can restore it from this panel.`)
    +(working?'\n\n⚠ The session is still working and may edit this file again.':'');
  if(!confirm(msg)) return;
  const r=await _chgPost('/session_changes/discard',sig==null?{session_id:sid, path}:{session_id:sid, path, sig});
  if(r.ok) showToast('done','Discarded', rel+' · ↶ Restore is in the Changes panel.');
  else if(r.conflict) showToast('warning','Not discarded', rel+' changed since you opened the panel — the list is refreshed, check it again.');
  else showToast('warning','Not discarded', r.error||'failed');
  _chgReload();
}
async function _chgUndo(sid, id, rel){
  let r=await _chgPost('/session_changes/undo',{session_id:sid, id});
  if(!r.ok&&r.conflict&&confirm(`"${rel}" changed again after the discard.\n\nRestore the old copy anyway? The newer changes in that file will be lost.`))
    r=await _chgPost('/session_changes/undo',{session_id:sid, id, force:true});
  if(r.ok) showToast('done','Restored', rel);
  else if(!r.conflict) showToast('warning','Not restored', r.error||'failed');
  _chgReload();
}
async function _chgStage(sid, paths){
  const r=await _chgPost('/session_changes/stage',{session_id:sid, paths});
  if(r.ok) showToast('done','Staged', `${r.staged.length} file${r.staged.length===1?'':'s'} added to the index — ready to commit.`);
  else showToast('warning','Not staged', r.error||((r.failed||[]).length+' file(s) failed'));
  _chgReload();
}
function _overlay(title, bodyHtml, cls){
  const o=document.createElement('div');
  o.className='diff-overlay';
  o.innerHTML=`<div class="diff-box ${cls||''}"><div class="diff-hdr"><div class="diff-hdr-name">${escHtml(title)}</div>
    <button class="diff-close" onclick="this.closest('.diff-overlay').remove()">✕</button></div><div class="diff-body chg-body">${bodyHtml}</div></div>`;
  o.addEventListener('click',e=>{ if(e.target===o) o.remove(); });
  document.body.appendChild(o);
  return o;
}
/* Ask AOC: a question about your own history, answered by Haiku from facts
   AOC gathers locally (POST /ask). One question at a time. "Follow up"
   sends the next question with the shown answer's key (prev); "Deeper"
   re-asks the same question with Sonnet. */
const _ASK_EXAMPLES=['How much did I spend this week?','Which sessions failed the most in the last 7 days?',
  'Koľko ma stál OmniSocial tento mesiac?','What did I commit yesterday?'];
let _askState={busy:false, last:null, error:'', history:null, follow:null, busyModel:'haiku'};
/* Pure: an answer's text -> html. Escaped first; [#1a2b3c4d] ids the server
   sent in `sessions` become Replay buttons, '- ' lines a list. */
function _askAnswerHtml(entry){
  if(!entry||!entry.answer) return '';
  const sessions=entry.sessions||{};
  const inline=t=>escHtml(t).replace(/\[#([0-9a-f]{8})\]/gi,(m,k)=>{
    const s=sessions[k]||sessions[k.toLowerCase()];
    if(!s||!s.id) return m;
    const label=s.title||k;
    return `<button class="ask-ref" onclick="openReplay(${jsq(s.id)},'',${jsq(label)})" title="Replay ${escHtml(label)}">${escHtml(label.length>40?label.slice(0,39)+'…':label)}</button>`;
  });
  const lines=String(entry.answer).split(/\n+/).map(l=>l.trim()).filter(Boolean);
  let html='', list=[];
  const flush=()=>{ if(list.length){ html+=`<ul>${list.map(l=>`<li>${l}</li>`).join('')}</ul>`; list=[]; } };
  lines.forEach(l=>{
    if(/^[-*•]\s/.test(l)) list.push(inline(l.replace(/^[-*•]\s+/,'')));
    else { flush(); html+=`<p>${inline(l)}</p>`; }
  });
  flush();
  const p=entry.period||{};
  const meta=[p.label?`${escHtml(p.label)}${p.from?` (${escHtml(p.from)}${p.to&&p.to!==p.from?' – '+escHtml(p.to):''})`:''}`:'',
    entry.model==='sonnet'?'Sonnet':'',
    entry.cached?'cached answer':(entry.cost!=null?'$'+(Number(entry.cost)||0).toFixed(3):'')].filter(Boolean).join(' · ');
  return `<div class="ask-answer">${html}${meta?`<div class="ask-meta">${meta}</div>`:''}</div>`;
}
/* Pure: the whole overlay body (state = _askState). */
function _askBodyHtml(state){
  state=state||{};
  let out='';
  const last=state.last;
  if(state.follow&&last&&last.key===state.follow&&!state.busy)
    out+=`<div class="ask-note">Follow-up to “${escHtml((last.q||'').length>60?last.q.slice(0,59)+'…':last.q||'')}” <button class="ask-chip" onclick="_askNew()">New question instead</button></div>`;
  if(state.busy) out+=`<div class="ask-note">Gathering facts and asking Claude ${state.busyModel==='sonnet'?'Sonnet':'Haiku'}…</div>`;
  else if(state.error) out+=`<div class="ask-note err">${escHtml(state.error)}</div>`;
  if(last){
    const thread=(last.thread||[]).filter(Boolean);
    if(thread.length) out+=`<div class="ask-note">↳ after: ${thread.map(t=>escHtml(t.length>50?t.slice(0,49)+'…':t)).join(' › ')}</div>`;
    out+=`<div class="ask-q-echo ask-h-title">${escHtml(last.q||'')}</div>`+_askAnswerHtml(last);
    if(!state.busy){
      const chips=[];
      if(state.follow!==last.key) chips.push(`<button class="ask-chip" onclick="_askFollow()">Follow up</button>`);
      if(last.model!=='sonnet') chips.push(`<button class="ask-chip" onclick="_askRun(${jsq(last.q||'')},false,${jsq(last.prev||'')},'sonnet')" title="Ask the same question with Claude Sonnet: more careful, a few cents">Deeper (Sonnet)</button>`);
      if(last.cached) chips.push(`<button class="ask-chip" onclick="_askRun(${jsq(last.q||'')},true,${jsq(last.prev||'')},${jsq(last.model||'haiku')})">Ask again (fresh)</button>`);
      if(chips.length) out+=`<div class="ask-hint">${chips.join('')}</div>`;
    }
  }
  if(!state.last&&!state.busy){
    out+=`<div class="ask-hint">${_ASK_EXAMPLES.map(q=>`<button class="ask-chip" onclick="_askRun(${jsq(q)})">${escHtml(q)}</button>`).join('')}</div>`;
  }
  const hist=(state.history||[]).filter(h=>!state.last||h.key!==state.last.key).slice(0,8);
  if(hist.length){
    out+=`<div class="ask-h-title">Earlier questions</div><div class="ask-hint">${hist.map(h=>
      `<button class="ask-chip" onclick="_askShow(${jsq(h.key)})" title="${escHtml(h.q||'')}">${escHtml((h.q||'').length>60?h.q.slice(0,59)+'…':h.q||'')}</button>`).join('')}</div>`;
  }
  out+='<div class="ask-note">Answers come only from your own AOC data (sessions, spend, commits, failures, clients). Claude Haiku, about 1 cent per question (Deeper uses Sonnet, a few cents); repeats on the same day are free.</div>';
  return out;
}
function _askRender(){
  const o=document.getElementById('ask-overlay');
  if(o) o.querySelector('.ask-out').innerHTML=_askBodyHtml(_askState);
  const b=o&&o.querySelector('.ask-go'); if(b) b.disabled=_askState.busy;
}
async function openAsk(q){
  let o=document.getElementById('ask-overlay');
  if(!o){
    o=_overlay('Ask AOC', `<form class="ask-form" onsubmit="event.preventDefault();_askRun(this.q.value,false,_askState.follow||'')">
      <input class="ask-input" name="q" maxlength="500" autocomplete="off" placeholder="e.g. How much did the AOC sessions cost last week?">
      <button class="adp-btn ask-go" type="submit">Ask</button></form><div class="ask-out"></div>`, 'ask-box');
    o.id='ask-overlay';
  }
  _askRender();
  const input=o.querySelector('.ask-input');
  if(q){ input.value=q; }
  setTimeout(()=>input.focus(),30);
  try{ _askState.history=(await fetch('/ask/history').then(r=>r.json())).items||[]; _askRender(); }catch(e){}
}
function _askShow(key){
  const h=(_askState.history||[]).find(x=>x.key===key);
  if(h){ _askState.last={...h, cached:true}; _askState.error=''; _askState.follow=null; _askRender();
    const i=document.querySelector('#ask-overlay .ask-input'); if(i){ i.value=h.q||''; i.placeholder=_ASK_PLACEHOLDER; } }
}
const _ASK_PLACEHOLDER='e.g. How much did the AOC sessions cost last week?';
/* The next question continues the shown answer (or, _askNew, starts over). */
function _askFollow(){
  if(!_askState.last) return;
  _askState.follow=_askState.last.key; _askRender();
  const i=document.querySelector('#ask-overlay .ask-input');
  if(i){ i.value=''; i.placeholder='Follow-up, e.g. “a včera?” or “which of those failed?”'; i.focus(); }
}
function _askNew(){
  _askState.follow=null; _askRender();
  const i=document.querySelector('#ask-overlay .ask-input'); if(i){ i.placeholder=_ASK_PLACEHOLDER; i.focus(); }
}
async function _askRun(q, force, prev, model){
  q=String(q||'').trim();
  if(!q||_askState.busy) return;
  const i=document.querySelector('#ask-overlay .ask-input'); if(i) i.value=q;
  _askState.busy=true; _askState.busyModel=model||'haiku'; _askState.error=''; _askRender();
  try{
    const d=await fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({q, force:!!force, prev:prev||'', model:model||'haiku'})}).then(r=>r.json());
    if(d.ok){
      _askState.last=d; _askState.follow=null;
      if(i) i.placeholder=_ASK_PLACEHOLDER;
      const h=(_askState.history||[]).filter(x=>x.key!==d.key);
      _askState.history=[d].concat(h);
    } else _askState.error=d.error||'failed';
  }catch(e){ _askState.error='Could not reach AOC.'; }
  _askState.busy=false;
  _askRender();
}
async function openSessionChanges(sid, machine, label){
  const o=_overlay('Changes · '+label, '<div class="chg-empty">Loading…</div>', 'chg-box');
  _chgCtx={o, sid, machine};
  try{
    const d=await fetch(_apiUrl(_machineFor(machine),'/session_changes?session_id='+encodeURIComponent(sid))).then(r=>r.json());
    o.querySelector('.chg-body').innerHTML=_sessionChangesHtml(d, sid, machine);
  }catch(e){ o.querySelector('.chg-body').innerHTML=_sessionChangesHtml(null); }
}
async function _showSessionFileDiff(sid, machine, path, rel){
  const o=_overlay('// '+rel, '<span style="color:var(--t3)">Loading…</span>');
  try{
    const d=await fetch(_apiUrl(_machineFor(machine),'/session_changes/diff?session_id='+encodeURIComponent(sid)+'&file='+encodeURIComponent(path))).then(r=>r.json());
    o.querySelector('.diff-body').innerHTML=d.ok?(_diffLinesHtml(d.diff)||'<span style="color:var(--t3)">(no uncommitted change)</span>'):escHtml(d.error||'failed');
  }catch(e){ o.querySelector('.diff-body').textContent='Could not load the diff.'; }
}
async function showDiff(filepath){
  try{
    const d=await fetch('/diff?file='+encodeURIComponent(filepath)).then(r=>r.json());
    const html=_diffLinesHtml(d.diff);
    const overlay=document.createElement('div');
    overlay.className='diff-overlay';
    overlay.innerHTML=`
      <div class="diff-box">
        <div class="diff-hdr">
          <div class="diff-hdr-name">// ${filepath}</div>
          <button onclick="copyText(this,this.closest('.diff-box').querySelector('.diff-body').innerText)" style="font-size:11px;padding:2px 10px;border-radius:6px;border:1px solid rgba(var(--c-rgb),.22);background:rgba(var(--c-rgb),.06);color:var(--t2);cursor:pointer;font-family:var(--font);margin-right:6px">copy</button>
          <button class="diff-close" onclick="this.closest('.diff-overlay').remove()">✕</button>
        </div>
        <div class="diff-body">${html||'<span style="color:var(--t3)">(no diff — file unchanged)</span>'}</div>
      </div>`;
    overlay.addEventListener('click',e=>{ if(e.target===overlay) overlay.remove(); });
    document.body.appendChild(overlay);
  }catch(e){ log('DIFF ERROR: '+e.message,'error'); }
}

/* ── session summary view ── */
function renderSummary(data){
  const el=document.getElementById('summary-area');
  if(!el) return;
  const agents=(data.agents||[]).filter(a=>!String(a.id||'').startsWith('hook_'));
  if(!agents.length && !data.session_active){
    el.innerHTML='<div style="color:var(--t3);font-family:var(--font2);font-size:11px;text-align:center;padding:60px 0;letter-spacing:.1em">// no session data — start agents to see summary</div>';
    return;
  }
  const running=agents.filter(a=>a.status==='running').length;
  const done=agents.filter(a=>a.status==='done').length;
  const errors=agents.filter(a=>a.status==='error').length;
  const totalTokens=agents.reduce((s,a)=>s+(a.tokens_used||0),0);
  const cost=agents.reduce((s,a)=>s+_agentCost(a),0).toFixed(4);
  const allTasks=agents.flatMap(a=>a.tasks||[]);
  const doneTasks=allTasks.filter(t=>t.done).length;
  const seen=new Set(); const allFiles=[];
  agents.forEach(a=>{
    (a.files_changed||[]).forEach(f=>{
      if(!seen.has(f.path)){ seen.add(f.path); allFiles.push({...f,agent:a.unit||a.name}); }
    });
  });
  const sessStart=data.started_at?parseTimeStr(data.started_at):null;
  const duration=sessStart?Math.round((Date.now()-sessStart)/1000):0;
  const durStr=duration>0?(duration<60?duration+'s':Math.floor(duration/60)+'m '+(duration%60)+'s'):'—';
  const hdr=(k,v)=>`<div style="font-family:var(--font2);font-size:10px;color:var(--t3);letter-spacing:.08em;padding:4px 0 2px">${k}: <span style="color:var(--c)">${escHtml(v)}</span></div>`;

  /* ── per-session breakdown ── */
  const _sessMap={};
  agents.forEach(a=>{
    const sid=a.session_id||'default';
    if(!_sessMap[sid]) _sessMap[sid]={id:sid,project:a.session_project||a.session_id||'—',agents:[],running:0,done:0,errors:0,tasksDone:0,tasksTotal:0,tokens:0,cost:0,files:new Set()};
    const _s=_sessMap[sid];
    _s.agents.push(a);
    if(a.status==='running'||a.status==='waiting') _s.running++;
    else if(a.status==='done') _s.done++;
    else if(a.status==='error') _s.errors++;
    (a.tasks||[]).forEach(t=>{_s.tasksTotal++;if(t.done)_s.tasksDone++;});
    _s.tokens+=a.tokens_used||0;
    _s.cost+=_agentCost(a);
    (a.files_changed||[]).forEach(f=>_s.files.add(f.path));
  });
  const _sessions=Object.values(_sessMap).sort((a,b)=>b.running-a.running||b.agents.length-a.agents.length);

  el.innerHTML=`<div style="max-width:860px;width:100%;margin:0 auto;display:flex;flex-direction:column;gap:14px">
    <div class="sum-grid">
      <div class="sum-stat"><div class="sv">${agents.length}</div><div class="sl">Agents</div></div>
      <div class="sum-stat"><div class="sv" style="color:var(--g)">${done}</div><div class="sl">Completed</div></div>
      <div class="sum-stat"><div class="sv" style="color:var(--o)">${running}</div><div class="sl">Active</div></div>
      <div class="sum-stat"><div class="sv" style="color:${errors?'var(--r)':'var(--t3)'}">${errors}</div><div class="sl">Errors</div></div>
    </div>
    <div class="sum-grid">
      <div class="sum-stat"><div class="sv">${totalTokens>0?Number(totalTokens).toLocaleString():'—'}</div><div class="sl">Tokens</div></div>
      <div class="sum-stat"><div class="sv" style="color:rgba(76,194,133,.9)">${totalTokens>0?'$'+cost:'—'}</div><div class="sl">Est. Cost</div></div>
      <div class="sum-stat"><div class="sv">${doneTasks}/${allTasks.length}</div><div class="sl">Tasks</div></div>
      <div class="sum-stat"><div class="sv">${allFiles.length}</div><div class="sl">Files</div></div>
    </div>
    ${hdr('DURATION',durStr)} ${hdr('PROJECT',data.project||'—')}
    ${_sessions.length>1?`
    <div style="font-family:var(--font2);font-size:10px;color:var(--t3);letter-spacing:.08em;padding:8px 0 4px">CLI SESSIONS (${_sessions.length})</div>
    <div style="display:flex;flex-direction:column;gap:6px">
    ${_sessions.map(_s=>{
      const _spct=_s.tasksTotal>0?Math.round(_s.tasksDone/_s.tasksTotal*100):(_s.done>0?100:0);
      const _scol=_s.errors>0?'var(--r)':_s.running>0?'var(--c)':'var(--g)';
      const _scost=_s.cost.toFixed(4);
      const _stokpct=totalTokens>0?(_s.tokens/totalTokens*100).toFixed(0):0;
      const _sstatus=_s.errors>0?'ERROR':_s.running>0?`${_s.running} RUNNING`:_s.done===_s.agents.length?'DONE':'IDLE';
      return `<div style="display:flex;align-items:center;gap:12px;padding:10px 14px;border-radius:11px;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.07);border-left:3px solid ${_scol};transition:background .2s">
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:5px">
            <span style="font-family:var(--font2);font-size:12px;font-weight:700;color:${_scol};overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:200px">${escHtml(_s.project)}</span>
            <span style="font-size:9px;font-family:var(--font2);font-weight:700;letter-spacing:.07em;padding:1px 6px;border-radius:4px;border:1px solid ${_scol};color:${_scol};opacity:.8;flex-shrink:0">${_sstatus}</span>
            ${_s.running>0?`<span style="display:flex;align-items:center;gap:3px;font-size:9px;font-family:var(--font2);color:var(--c);flex-shrink:0"><span style="width:5px;height:5px;border-radius:50%;background:var(--c);animation:pulse 1.5s infinite;display:inline-block"></span></span>`:''}
          </div>
          <div style="height:3px;background:rgba(255,255,255,.06);border-radius:999px;overflow:hidden;margin-bottom:5px">
            <div style="height:100%;width:${_spct}%;background:${_scol};border-radius:999px;transition:width .6s ease;box-shadow:0 0 4px ${_scol}55"></div>
          </div>
          <div style="display:flex;gap:12px;font-size:10px;color:var(--t3);font-family:var(--font2);flex-wrap:wrap">
            <span>${_s.agents.length} agent${_s.agents.length!==1?'s':''}</span>
            <span style="color:${_s.tasksDone===_s.tasksTotal&&_s.tasksTotal>0?'var(--g)':'var(--t3)'}">${_s.tasksDone}/${_s.tasksTotal} tasks</span>
            <span>${_s.files.size} file${_s.files.size!==1?'s':''}</span>
            ${_s.tokens>0?`<span style="color:rgba(76,194,133,.8)">$${_scost}</span>`:''}
            ${totalTokens>0&&_s.tokens>0?`<span style="color:var(--t3)">${_stokpct}% tokens</span>`:''}
          </div>
        </div>
        <div style="text-align:center;flex-shrink:0;min-width:42px">
          <div style="font-family:var(--font2);font-size:22px;font-weight:800;color:${_scol};line-height:1;">${_spct}</div>
          <div style="font-size:9px;color:var(--t3);letter-spacing:.06em">%</div>
        </div>
      </div>`;
    }).join('')}
    </div>`:''}
    ${totalTokens>0?`<div style="font-family:var(--font2);font-size:10px;color:var(--t3);letter-spacing:.08em;padding:8px 0 4px">TOKEN USAGE HEATMAP</div>
    <div style="display:flex;flex-direction:column;gap:4px">
    ${[...agents].filter(a=>a.tokens_used>0).sort((a,b)=>(b.tokens_used||0)-(a.tokens_used||0)).map(a=>{
      const barW=(a.tokens_used/totalTokens*100).toFixed(1);
      const pct=(a.tokens_used/totalTokens*100).toFixed(0);
      const cost=_agentCost(a).toFixed(4);
      const heat=a.tokens_used/totalTokens;
      const col=heat>0.5?'rgba(240,164,151,.9)':heat>0.25?'rgba(224,161,58,.85)':'rgba(var(--c-rgb),.75)';
      return `<div style="display:flex;align-items:center;gap:8px">
        <div style="font-family:var(--font2);font-size:10px;color:var(--t2);min-width:38px;flex-shrink:0">${escHtml(_deriveUnit(a))}</div>
        <div style="flex:1;height:10px;background:rgba(255,255,255,.04);border-radius:3px;overflow:hidden">
          <div style="height:100%;width:${barW}%;background:${col};border-radius:3px;transition:width .6s ease;box-shadow:0 0 6px ${col}"></div>
        </div>
        <div style="font-family:var(--font2);font-size:10px;color:${col};width:34px;text-align:right">${pct}%</div>
        <div style="font-family:var(--font2);font-size:10px;color:rgba(76,194,133,.7);width:52px;text-align:right">$${cost}</div>
      </div>`;
    }).join('')}
    </div>`:''}
    ${agents.length?`<div style="font-family:var(--font2);font-size:10px;color:var(--t3);letter-spacing:.08em;padding:6px 0 2px">AGENTS</div>
    <div style="display:flex;flex-direction:column;gap:5px">${agents.map(a=>{
      const ac=(a.tasks||[]).filter(t=>t.done).length, at=(a.tasks||[]).length;
      const col=sCol(a.status);
      return `<div class="sum-agent-row">
        <div style="font-family:var(--font2);font-size:11px;color:${col};min-width:36px">${escHtml(a.unit||String(a.id).slice(-4).toUpperCase())}</div>
        ${a.detected_via==='transcript'?`<span style="color:rgba(224,161,58,.95);font-size:10px;flex-shrink:0" title="${escHtml(_hookMissTitle(a))}">⚠</span>`:''}
        <div style="flex:1;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(a.name)}</div>
        <div style="font-size:10px;color:var(--t3);font-family:var(--font2)">${ac}/${at}</div>
        ${a.tokens_used?`<div style="font-size:10px;color:rgba(76,194,133,.7);font-family:var(--font2)">$${_agentCost(a).toFixed(4)}</div>`:''}
        <div class="status-pill ${escHtml(a.status)}" style="font-size:9px;padding:2px 7px"><span class="sdot"></span>${escHtml(String(a.status||'').toUpperCase())}</div>
      </div>`;
    }).join('')}</div>`:''}
    ${allFiles.length?`<div style="font-family:var(--font2);font-size:10px;color:var(--t3);letter-spacing:.08em;padding:6px 0 2px">FILES CHANGED (${allFiles.length})</div>
    <div style="display:flex;flex-direction:column;gap:3px">${allFiles.map(f=>`
      <div class="fe ${f.type==='new'?'new':'changed'}" onclick="showDiff(${jsq(f.path)})" style="cursor:pointer">
        <span class="fe-badge">${f.type==='new'?'NEW':'MOD'}</span>
        <span class="fe-name" title="${escHtml(f.path)}">${escHtml(f.path)}</span>
        <span style="font-size:9px;color:var(--t3);font-family:var(--font2);flex-shrink:0">${escHtml(f.agent)}</span>
      </div>`).join('')}</div>`:''}
    <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">
      <button onclick="exportSession()" style="font-family:var(--font2);font-size:11px;letter-spacing:.06em;padding:6px 16px;border-radius:8px;border:1px solid rgba(var(--c-rgb),.3);background:rgba(var(--c-rgb),.08);color:var(--c);cursor:pointer;transition:all .2s" onmouseover="this.style.background='rgba(var(--c-rgb),.18)'" onmouseout="this.style.background='rgba(var(--c-rgb),.08)'">↓ EXPORT JSON</button>
      <button id="copy-md-btn" onclick="copyMarkdownSummary(this)" style="font-family:var(--font2);font-size:11px;letter-spacing:.06em;padding:6px 16px;border-radius:8px;border:1px solid rgba(76,194,133,.25);background:rgba(76,194,133,.07);color:rgba(76,194,133,.8);cursor:pointer;transition:all .2s" onmouseover="this.style.background='rgba(76,194,133,.16)'" onmouseout="this.style.background='rgba(76,194,133,.07)'">⧉ COPY MARKDOWN</button>
    </div>
    ${(()=>{
      const meta=(_lastAuditData&&_lastAuditData.file_meta)||[];
      if(!meta.length) return '';
      const fmtSize=b=>b<1024?b+'B':b<1048576?(b/1024).toFixed(1)+'KB':(b/1048576).toFixed(2)+'MB';
      return `<div style="font-family:var(--font2);font-size:10px;color:var(--t3);letter-spacing:.08em;padding:10px 0 4px">RECENT SESSION LOGS</div>
      <div style="display:flex;flex-direction:column;gap:3px">${meta.slice(0,8).map(f=>{
        const date=f.name.replace('session_','').replace('.log','').replace(/_/g,' ');
        return `<div style="display:flex;align-items:center;gap:8px;padding:5px 8px;border-radius:7px;background:rgba(255,255,255,.02);border:1px solid rgba(255,255,255,.05)">
          <span style="font-size:10px;font-family:var(--font2);color:var(--t2);flex:1">${date}</span>
          <span style="font-size:9px;color:var(--t3);font-family:var(--font2)">${fmtSize(f.size)}</span>
          <a href="/logs/${encodeURIComponent(f.name)}" target="_blank" style="font-size:9px;color:var(--c);font-family:var(--font2);text-decoration:none;padding:1px 6px;border:1px solid rgba(var(--c-rgb),.2);border-radius:4px">↓</a>
        </div>`;
      }).join('')}</div>`;
    })()}
  </div>`;
}

function exportSession(){
  if(!lastStatus) return;
  const ts=new Date().toISOString().replace(/[:.]/g,'-').slice(0,19);
  const blob=new Blob([JSON.stringify(lastStatus,null,2)],{type:'application/json'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download=`aoc-session-${ts}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
  log(`Session exported → aoc-session-${ts}.json`,'success');
}

/* ── settings export/import ── bundles every *configuration* field (not
   transient UI state like current view/collapsed-cards, which lives in
   aoc_prefs and is deliberately excluded) into one JSON, for moving to a
   new machine alongside what install.ps1 already does for the code
   itself. _buildSettingsBundle takes its pieces as arguments rather than
   reading globals directly so it stays a plain, testable function. */
function _buildSettingsBundle(state){
  return {
    version: 1,
    exported_at: new Date().toISOString(),
    cost_rate: state.costRate,
    budget: state.budget,
    project_budgets: state.projectBudgets,
    webhook_url: state.webhookUrl,
    webhook_events: state.webhookEvents,
    quiet_start: state.quietStart,
    quiet_end: state.quietEnd,
    muted_projects: state.mutedProjects,
    snitch_url: state.snitchUrl,
    digest_cadence: state.digestCadence,
    backup_retention_days: state.backupRetentionDays,
    agent_retention_hours: state.agentRetentionHours,
    accent: state.accent,
    density: state.density,
    sound: state.sound,
    local_machine_name: state.localMachineName,
    remote_machines: state.remoteMachines,
  };
}
function exportAllSettings(){
  const bundle=_buildSettingsBundle({
    costRate:_costRate, budget:_budgetLimit, projectBudgets:_projectBudgets,
    webhookUrl:_webhookUrl, webhookEvents:_webhookEvents,
    quietStart:_quietStart, quietEnd:_quietEnd, mutedProjects:_mutedProjects,
    snitchUrl:_snitchUrl, digestCadence:_digestCadence, backupRetentionDays:_backupRetentionDays,
    agentRetentionHours:_agentRetentionHours,
    accent:_accentName, density:_density, sound:_soundPrefs,
    localMachineName:_localMachineName, remoteMachines:_remoteMachines,
  });
  const ts=new Date().toISOString().replace(/[:.]/g,'-').slice(0,19);
  const blob=new Blob([JSON.stringify(bundle,null,2)],{type:'application/json'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download=`aoc-settings-${ts}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
  log(`Settings exported → aoc-settings-${ts}.json`,'success');
}
function triggerImportSettings(){
  const inp=document.getElementById('settings-import-input');
  if(inp) inp.click();
}
function importAllSettings(fileInput){
  const file=fileInput.files&&fileInput.files[0];
  if(!file) return;
  const reader=new FileReader();
  reader.onload=async ()=>{
    try{
      const b=JSON.parse(reader.result);
      localStorage.setItem('aoc_cost_rate', b.cost_rate ?? 9);
      localStorage.setItem('aoc_budget', b.budget ?? '');
      localStorage.setItem('aoc_project_budgets', JSON.stringify(b.project_budgets||{}));
      localStorage.setItem('aoc_webhook_url', b.webhook_url||'');
      localStorage.setItem('aoc_webhook_events', JSON.stringify(b.webhook_events||{}));
      localStorage.setItem('aoc_quiet_start', b.quiet_start||'');
      localStorage.setItem('aoc_quiet_end', b.quiet_end||'');
      localStorage.setItem('aoc_muted_projects', JSON.stringify(b.muted_projects||[]));
      localStorage.setItem('aoc_snitch_url', b.snitch_url||'');
      localStorage.setItem('aoc_digest_cadence', b.digest_cadence||'weekly');
      localStorage.setItem('aoc_backup_retention_days', b.backup_retention_days||14);
      localStorage.setItem('aoc_agent_retention_hours', b.agent_retention_hours||12);
      localStorage.setItem('aoc_accent', b.accent||'cyan');
      localStorage.setItem('aoc_density', b.density||'normal');
      localStorage.setItem('aoc_sound', JSON.stringify(b.sound||{}));
      localStorage.setItem('aoc_local_machine_name', b.local_machine_name||'');
      localStorage.setItem('aoc_remote_machines', JSON.stringify(b.remote_machines||[]));
      // Also persist the server-side half, matching saveSettings()'s own
      // two POSTs -- otherwise the background workers (webhook delivery,
      // quiet hours, digest) would keep using whatever was there before
      // until the next manual Settings save. Checked for .ok (not just
      // awaited-then-swallowed) for the same reason saveSettings() checks
      // it -- an import that silently fails to reach the server would
      // otherwise still claim success right before reloading the page.
      let serverSyncOk=true;
      try{
        const [whRes,ntRes]=await Promise.all([
          fetch('/webhook_settings',{method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({url:b.webhook_url||'', events:b.webhook_events||{}})}),
          fetch('/notify_settings',{method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({quiet_start:b.quiet_start||'', quiet_end:b.quiet_end||'', muted_projects:b.muted_projects||[], snitch_url:b.snitch_url||'', digest_cadence:b.digest_cadence||'weekly', backup_retention_days:b.backup_retention_days||14, agent_retention_hours:b.agent_retention_hours||12, project_budgets:b.project_budgets||{}})}),
        ]);
        serverSyncOk=whRes.ok&&ntRes.ok;
      }catch(e){ serverSyncOk=false; }
      log(serverSyncOk
        ? 'Settings imported — reloading to apply'
        : 'Settings imported locally, but syncing to the server failed -- webhook/budget/quiet-hours delivery may be running on stale config (re-save from Settings once the server is reachable)',
        serverSyncOk?'success':'error');
      setTimeout(()=>location.reload(), 800);
    }catch(e){
      log('Import failed: invalid settings file','error');
    }
    fileInput.value='';
  };
  reader.readAsText(file);
}

function copyMarkdownSummary(btn){
  if(!lastStatus) return;
  const agents=(lastStatus.agents||[]).filter(a=>!String(a.id||'').startsWith('hook_'));
  const running=agents.filter(a=>a.status==='running').length;
  const done=agents.filter(a=>a.status==='done').length;
  const errors=agents.filter(a=>a.status==='error').length;
  const totalTokens=agents.reduce((s,a)=>s+(a.tokens_used||0),0);
  const cost=agents.reduce((s,a)=>s+_agentCost(a),0).toFixed(4);
  const allTasks=agents.flatMap(a=>a.tasks||[]);
  const doneTasks=allTasks.filter(t=>t.done).length;
  const sessStart=lastStatus.started_at?parseTimeStr(lastStatus.started_at):null;
  const duration=sessStart?Math.round((Date.now()-sessStart)/1000):0;
  const durStr=duration>0?(duration<60?duration+'s':Math.floor(duration/60)+'m '+(duration%60)+'s'):'—';
  const now=new Date();
  const timeStr=`${pad(now.getHours())}:${pad(now.getMinutes())}`;

  let md=`## AOC Session — ${timeStr}\n\n`;
  md+=`- **Project**: ${lastStatus.project||'—'}\n`;
  md+=`- **Agents**: ${agents.length} (${done} done, ${running} running${errors?`, ${errors} error`:''}) \n`;
  md+=`- **Tasks**: ${doneTasks}/${allTasks.length} completed\n`;
  md+=`- **Duration**: ${durStr}\n`;
  if(totalTokens>0) md+=`- **Tokens**: ${Number(totalTokens).toLocaleString()} (~$${cost})\n`;
  /* per-session breakdown in markdown */
  const _mdSessMap={};
  agents.forEach(a=>{
    const sid=a.session_id||'default';
    if(!_mdSessMap[sid]) _mdSessMap[sid]={project:a.session_project||sid.slice(-6),agents:[],tasksDone:0,tasksTotal:0,tokens:0,cost:0,running:0};
    const _ms=_mdSessMap[sid];
    _ms.agents.push(a);
    if(a.status==='running'||a.status==='waiting') _ms.running++;
    (a.tasks||[]).forEach(t=>{_ms.tasksTotal++;if(t.done)_ms.tasksDone++;});
    _ms.tokens+=a.tokens_used||0;
    _ms.cost+=_agentCost(a);
  });
  const _mdSessions=Object.values(_mdSessMap);
  if(_mdSessions.length>1){
    md+=`\n### CLI Sessions (${_mdSessions.length})\n`;
    _mdSessions.forEach(_ms=>{
      const _pct=_ms.tasksTotal>0?Math.round(_ms.tasksDone/_ms.tasksTotal*100):0;
      const _cost=_ms.tokens>0?` · $${_ms.cost.toFixed(4)}`:'';
      const _status=_ms.running>0?`${_ms.running} running`:_ms.agents.every(a=>a.status==='done')?'done':'idle';
      md+=`- **${_ms.project}** — ${_ms.agents.length} agents · ${_ms.tasksDone}/${_ms.tasksTotal} tasks (${_pct}%) · ${_status}${_cost}\n`;
    });
  }
  md+=`\n### Agents\n`;
  agents.forEach(a=>{
    const icon=a.status==='done'?'✓':a.status==='error'?'✗':a.status==='running'?'▶':'·';
    const taskStr=(a.tasks||[]).filter(t=>t.done).length+'/'+((a.tasks||[]).length)+' tasks';
    const tokStr=a.tokens_used?` · ${Number(a.tokens_used).toLocaleString()} tokens`:'';
    const errStr=(a.status==='error'&&a.error_message)?` — ${a.error_message.slice(0,60)}`:'';
    md+=`- ${icon} **${a.name}** (${taskStr}${tokStr}${errStr})\n`;
  });
  navigator.clipboard.writeText(md).then(()=>{
    const orig=btn.textContent; btn.textContent='✓ COPIED';
    setTimeout(()=>btn.textContent=orig,2000);
    log('Markdown summary copied to clipboard','success');
  }).catch(()=>{ log('Clipboard copy failed','error'); });
}

