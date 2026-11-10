/* ── TODAY ── */
let _todayCommits=null, _todayCommitsAt=0;
/* ── Today -> Pick up where you left off: one card per project ── */
let _resumeCards=null, _resumeAt=0;
/* Pure: the cards (GET /resume_projects). A project with a session open
   offers a switch to it instead of a second session. */
function _resumeHtml(cards, nowS){
  if(!cards||!cards.length) return '';
  const money=v=>'$'+(Number(v)||0).toFixed(2);
  return `<div class="rs-grid">${cards.map((c,i)=>{
    const ago=c.last&&c.last.at?_fmtDurationDHM(Math.max(60,nowS-c.last.at))+' ago':'';
    const meta=[c.last&&c.last.title?'last: '+c.last.title:'', ago, `${c.sessions} session${c.sessions===1?'':'s'} · ${money(c.cost)} in 30 days`].filter(Boolean).join(' · ');
    const open=(c.open||[]).length;
    const main=open
      ?`<button class="adp-btn rs-go running" onclick="_focusSession(${jsq(c.open[0])})" title="A session for this project is open: switch to its terminal">● Running · Switch</button>`
      :`<button class="adp-btn rs-go" onclick="_resumeGo(${i},false)" title="New session in ${escHtml(c.cwd)} that starts with: ${escHtml(c.prompt)}">▶ Continue</button>`;
    return `<div class="rs-card${open?' is-open':''}">
      <div class="rs-name">${escHtml(c.name)}${c.folder?'<span class="rs-folder">folder</span>':''}</div>
      ${c.hook?`<div class="rs-hook" title="${escHtml(c.hook)}">${escHtml(c.hook)}</div>`:''}
      <div class="rs-meta">${escHtml(meta)}</div>
      <div class="rs-prompt" title="What the new session starts with">“${escHtml(c.prompt)}”</div>
      <div class="rs-btns">${main}
        <button class="adp-btn rs-sub" onclick="_resumeGo(${i},true)" title="Reopen the last conversation itself (claude --resume): its full history, nothing new typed">⟲ Last conversation</button>
        <button class="adp-btn rs-sub" onclick="_resumeEdit(${i})" title="Change the folder, message or model first">✎</button></div></div>`;
  }).join('')}</div>`;
}
async function _resumeGo(i, resume){
  const c=(_resumeCards||[])[i];
  if(!c) return;
  const body=resume?{cwd:c.cwd, resume:c.resume_id}:{cwd:c.cwd, prompt:c.prompt};
  try{
    const d=await fetch('/launch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(r=>r.json());
    if(d.ok){ _resumeAt=0; showToast('done', resume?'Conversation reopened':'Session started', c.name+(d.trusted===false?' · accept "trust this folder" in the new window first.':' shows up here in a few seconds.'), d.trusted===false?12000:5000); }
    else showToast('warning','Not started', d.error||'Could not start it.');
  }catch(e){ showToast('warning','Not started','Could not reach AOC.'); }
}
function _resumeEdit(i){
  const c=(_resumeCards||[])[i];
  if(!c) return;
  openLaunch();
  const f=document.querySelector('.ln-box form')||document.querySelector('form #ln-cwd')?.closest('form');
  if(!f) return;
  f.querySelector('#ln-cwd').value=c.cwd;
  f.querySelector('#ln-prompt').value=c.prompt;
}
/* ── Today -> Parallel worktrees (GET /worktrees) ── */
let _wtItems=null, _wtAt=0;
/* Pure: the Start button's label for the form's Start + Where choices. */
function _launchGoLabel(when, wt){
  if(when!=='now') return 'Add to queue';
  return wt>1?`Start ${wt} sessions`:wt===1?'Start in a worktree':'Start session';
}
/* Pure: why Merge is not possible for this worktree yet ('' = it is). */
function _wtMergeBlock(w){
  if(!w.exists) return 'The worktree folder is gone';
  if((w.sessions||[]).some(s=>s.active)) return 'A session is still open in it — close it first';
  if(w.dirty) return `${w.dirty} uncommitted file${w.dirty===1?'':'s'} — have the session commit (or discard)`;
  if(!w.ahead) return 'No commits on this branch yet';
  return '';
}
/* Pure: the panel -- one block per launch (group), one row per worktree. */
function _wtHtml(items, nowS){
  if(!items||!items.length) return '';
  const groups=[];
  items.forEach(w=>{ let g=groups.find(x=>x.id===w.group); if(!g){ g={id:w.group, items:[]}; groups.push(g); } g.items.push(w); });
  return `<div class="wt-list">${groups.map(g=>{
    const f=g.items[0];
    const ago=f.created?_fmtDurationDHM(Math.max(60,nowS-f.created))+' ago':'';
    const head=`<div class="wt-ghead"><span class="wt-repo">${escHtml(f.repo_name||f.repo||'')}</span><span class="wt-base">from ${escHtml(f.base||'')}</span>
      ${f.model?`<span class="wt-model">${escHtml(f.model)}</span>`:''}<span class="wt-ago">${escHtml(ago)}</span></div>
      ${f.prompt?`<div class="wt-prompt" title="${escHtml(f.prompt)}">“${escHtml(f.prompt)}”</div>`:''}`;
    const rows=g.items.map(w=>{
      const ss=w.sessions||[];
      const open=ss.find(s=>s.active);
      const sess=!w.exists?'<span class="wt-st gone">folder gone</span>'
        :open?(open.waiting?'<span class="wt-st wait">Waiting on you</span>':'<span class="wt-st run">Working</span>')
        :ss.length?'<span class="wt-st done">Closed</span>'
        :w.error?`<span class="wt-st gone" title="${escHtml(w.error)}">not started</span>`:'<span class="wt-st idle">starting…</span>';
      const cost=ss.reduce((a,s)=>a+(Number(s.cost)||0),0);
      const facts=[w.ahead?`${w.ahead} commit${w.ahead===1?'':'s'}`:'', (w.add||w.del)?`<span class="wt-add">+${w.add}</span> <span class="wt-del">−${w.del}</span> in ${w.files} file${w.files===1?'':'s'}`:'',
        w.dirty?`<span class="wt-dirty">${w.dirty} uncommitted</span>`:'', w.behind?`${escHtml(w.base)} moved ${w.behind} ahead`:'', cost?'$'+cost.toFixed(2):''].filter(Boolean).join(' · ');
      const block=_wtMergeBlock(w);
      return `<div class="wt-row" data-wt="${escHtml(w.id)}">
        <div class="wt-main"><span class="wt-branch" title="${escHtml(w.path||'')}">${escHtml(w.branch||'')}</span>${sess}
          ${open?`<button class="adp-btn wt-btn" onclick="_focusSession(${jsq(open.id)})" title="Bring its terminal to the front">Switch</button>`:''}</div>
        <div class="wt-facts">${facts||'<span class="wt-none">no changes yet</span>'}${w.last_subject?` · <span class="wt-subj" title="${escHtml(w.last_subject)}">${escHtml(w.last_subject)}</span>`:''}</div>
        <div class="wt-btns">
          <button class="adp-btn wt-btn" onclick="_wtDiff(${jsq(w.id)},${jsq(w.branch||'')})"${w.exists||w.ahead?'':' disabled'} title="Everything this branch changed compared with ${escHtml(w.base||'')}">Diff</button>
          <button class="adp-btn wt-btn wt-merge" onclick="_wtAct(${jsq(w.id)},'merge')"${block?` disabled title="${escHtml(block)}"`:` title="Merge into ${escHtml(w.base||'')} (checked for conflicts first), then remove the worktree"`}>Merge</button>
          <button class="adp-btn wt-btn wt-discard" onclick="_wtAct(${jsq(w.id)},'discard')" title="Delete the worktree and its branch, with everything done in it">Discard</button></div></div>`;
    }).join('');
    return `<div class="wt-group">${head}${rows}</div>`;
  }).join('')}</div>`;
}
async function _wtLoad(){
  try{ _wtItems=(await fetch('/worktrees').then(r=>r.json())).items||[]; }catch(e){}
}
async function _wtAct(id, kind){
  const w=(_wtItems||[]).find(x=>x.id===id);
  const name=w?w.branch:id;
  if(kind==='discard'&&!confirm(`Discard ${name}? The worktree folder and the branch are deleted, with every change made in it. This can't be undone.`)) return;
  if(kind==='merge'&&!confirm(`Merge ${name} into ${w?w.base:'its base branch'}? The worktree and branch are removed afterwards.`)) return;
  let d={};
  const post=force=>fetch('/worktrees/'+kind,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id, force})}).then(r=>r.json());
  try{
    d=await post(false);
    if(!d.ok&&kind==='discard'&&/still open/.test(d.error||'')&&confirm('A session is still open in it. Discard anyway? (Close that terminal first, or Windows may keep the folder.)')) d=await post(true);
  }catch(e){ d={ok:false, error:'Could not reach AOC.'}; }
  if(d.ok) showToast('done', kind==='merge'?'Merged':'Discarded', kind==='merge'?`${name} → ${w?w.base:''}${d.sha?' @'+d.sha.slice(0,7):''}${d.cleanup_error?' · worktree not removed: '+d.cleanup_error:''}`:name, 6000);
  else showToast('warning', kind==='merge'?'Not merged':'Not discarded', d.error||'failed', 9000);
  _wtAt=0;
  await _wtLoad();
  if(lastStatus&&currentView==='today') renderToday(lastStatus);
}
/* ── Stranded worktrees (GET /worktrees/stranded): an agent's or a parallel
   session's worktree that still holds work nobody is doing any more. Shown
   in Inbox and on Today; refreshed once a minute. ── */
let _wtStranded=[], _wtStrandedAt=0, _wtStrandedBusy=false;
async function _wtStrandedTick(force){
  if(_wtStrandedBusy||(!force&&Date.now()-_wtStrandedAt<60000)) return;
  _wtStrandedBusy=true; _wtStrandedAt=Date.now();
  try{ _wtStranded=(await fetch('/worktrees/stranded').then(r=>r.json())).items||[]; }catch(e){}
  _wtStrandedBusy=false;
  if(lastStatus){ _sbUpdate(lastStatus); renderInbox(lastStatus); if(currentView==='today') renderToday(lastStatus); }
}
/* Pure: "+12 −3 in 2 files committed · +40 −1 not committed". */
function _wtStrandedStat(w){
  const parts=[];
  if(w.ahead) parts.push(`${w.ahead} commit${w.ahead===1?'':'s'} (+${w.add||0} −${w.del||0} in ${w.files||0} file${w.files===1?'':'s'})`);
  if(w.dirty) parts.push(`${w.dirty} uncommitted file${w.dirty===1?'':'s'}${w.wip_add||w.wip_del?` (+${w.wip_add||0} −${w.wip_del||0} in tracked files)`:''}`);
  return parts.join(' · ');
}
/* Pure: the cards. */
function _wtStrandedHtml(items, nowS){
  if(!items||!items.length) return '';
  const kinds={agent:'Agent worktree', aoc:'Parallel session', other:'Worktree'};
  return `<div class="wts-list">${items.map(w=>{
    const ago=w.last_activity?_fmtDurationDHM(Math.max(60,nowS-w.last_activity))+' ago':'';
    return `<article class="td-card ib-item wts-item" data-wts="${escHtml(w.id)}">
      <div class="ib-row"><span class="ib-kind">Stranded work</span><span class="ib-name" title="${escHtml(w.path||'')}">${escHtml(w.repo_name||'')} · ${escHtml(w.branch||w.name||'')}</span>
        <span class="ib-when">${escHtml([kinds[w.kind]||kinds.other, ago].filter(Boolean).join(' · '))}</span></div>
      <div class="ib-text">🧳 ${escHtml(w.reason||'')}. Nothing is working in it any more.</div>
      <div class="wt-facts">${escHtml(_wtStrandedStat(w))}${w.last_subject?` · <span class="wt-subj" title="${escHtml(w.last_subject)}">${escHtml(w.last_subject)}</span>`:''}</div>
      ${_wtStrandedBtns(w)}</article>`;
  }).join('')}</div>`;
}
/* Pure: Commit WIP / Relaunch / Discard for a stranded worktree. */
function _wtStrandedBtns(w){
  const noCommit=!w.dirty?'Nothing uncommitted':w.detached||!w.branch?'Detached HEAD — a commit there would get lost':'';
  return `<div class="ib-btns">
        <button class="adp-btn ib-btn" onclick="_wtStrandedAct(${jsq(w.id)},'commit')"${noCommit?` disabled title="${escHtml(noCommit)}"`:' title="Commit everything on its branch as &quot;WIP: rescued by AOC&quot; so nothing gets lost"'}>Commit WIP</button>
        <button class="adp-btn ib-btn" onclick="_wtStrandedAct(${jsq(w.id)},'relaunch')" title="Start a new Claude Code session in this worktree, told to finish the work">Relaunch</button>
        <button class="adp-btn ib-btn wt-discard" onclick="_wtStrandedAct(${jsq(w.id)},'discard')"${w.locked?' disabled title="git has it locked — an agent may still own it"':' title="Delete the worktree and its branch, with everything done in it"'}>Discard</button></div>`;
}
/* ── Background agents (GET /agents/background): forks and other
   background subagents, with their worktree. working / quiet / dead /
   finished / failed; a dead one (plan limit, its session or claude.exe
   gone, silent for 30 min) is flagged in red, shown in Inbox too, and can
   be relaunched -- through the stranded-worktree buttons when its
   worktree is stranded (that card is then not shown twice). ── */
let _bgAgents=[], _bgAgentsAt=0, _bgAgentsBusy=false;
async function _bgAgentsTick(force){
  if(_bgAgentsBusy||(!force&&Date.now()-_bgAgentsAt<20000)) return;
  _bgAgentsBusy=true; _bgAgentsAt=Date.now();
  let d=null;
  try{ d=await fetch('/agents/background'+(force?'?fresh=1':'')).then(r=>r.json()); }catch(e){}
  _bgAgentsBusy=false;
  if(!d) return;
  if(d.items==null){ _bgAgentsAt=Date.now()-17000; return; }   /* first collection still running: ask again in ~3 s */
  const before=JSON.stringify(_bgAgents);
  _bgAgents=d.items;
  if(before!==JSON.stringify(_bgAgents)&&lastStatus){ _sbUpdate(lastStatus); renderInbox(lastStatus); if(currentView==='today') renderToday(lastStatus); }
}
/* Pure: the dead/failed agents, and the stranded worktrees not already
   shown as one of `rows` (matched by the worktree's id). */
function _bgDead(items){ return (items||[]).filter(a=>a.state==='dead'||a.state==='failed'); }
function _bgStrandedLeft(rows, stranded){
  const ids=new Set((rows||[]).map(a=>a.worktree&&a.worktree.stranded_id).filter(Boolean));
  return (stranded||[]).filter(w=>!ids.has(w.id));
}
/* Pure: the agent cards. stranded = the stranded-worktree list (for its buttons). */
function _bgAgentsHtml(items, nowS, stranded){
  if(!items||!items.length) return '';
  const words={working:'Working', quiet:'Quiet', dead:'Dead', finished:'Finished', failed:'Failed'};
  const ago=t=>t?_fmtDurationDHM(Math.max(60,nowS-t))+' ago':'';
  const byId={}; (stranded||[]).forEach(w=>{ byId[w.id]=w; });
  return `<div class="bga-list">${items.map(a=>{
    const wt=a.worktree||null, sw=wt&&byId[wt.stranded_id];
    const st=words[a.state]?a.state:'quiet';
    const when=[a.agent_type, a.session_name, a.last_activity?'active '+ago(a.last_activity):''].filter(Boolean).join(' · ');
    const facts=[];
    if(wt){
      facts.push(`<span title="${escHtml(wt.path||'')}">${escHtml((wt.repo_name?wt.repo_name+' · ':'')+(wt.branch||'detached HEAD'))}</span>`);
      if(wt.dirty) facts.push(`<b class="bga-dirty">${wt.dirty} file${wt.dirty===1?'':'s'} uncommitted</b>`);
      if(wt.ahead) facts.push(`${wt.ahead} commit${wt.ahead===1?'':'s'} not merged`);
      if(wt.last_commit_at) facts.push(`last commit ${escHtml(ago(wt.last_commit_at))}${wt.last_commit_subject?`: <span class="wt-subj" title="${escHtml(wt.last_commit_subject)}">${escHtml(wt.last_commit_subject)}</span>`:''}`);
    }else if(a.worktree_path){
      facts.push('worktree removed');
    }
    if(a.tokens_used) facts.push(`${Math.round(a.tokens_used/1000)}k tokens`);
    const relaunch=(a.state==='dead'||a.state==='failed')
      ?(_bgCanCheckpoint(a)?_bgCheckpointBtns(a, sw)
        :sw?_wtStrandedBtns(sw)
        :`<div class="ib-btns"><button class="adp-btn ib-btn" onclick="_bgAgentRelaunch(${jsq(a.id)})" title="Start a new Claude Code session in ${escHtml(wt?'its worktree':'the folder it worked in')}, told to finish its task">Relaunch</button></div>`)
      :'';
    return `<article class="td-card bga bga-${st}" data-bga="${escHtml(a.id)}">
      <div class="ib-row"><span class="bga-tag bga-t-${st}">${words[st]}</span><span class="ib-name">${escHtml(a.name||a.id)}</span>
        <span class="ib-when">${escHtml(when)}</span></div>
      ${a.reason?`<div class="bga-reason">${a.state==='dead'?'⚠ ':''}${escHtml(a.reason)}</div>`:''}
      ${facts.length?`<div class="wt-facts">${facts.join(' · ')}</div>`:''}
      ${relaunch}</article>`;
  }).join('')}</div>`;
}
/* Pure: a dead agent whose worktree has uncommitted files on its own branch
   can be checkpointed (WIP commit there, never on the main branch). Mirrors
   the server's _bg_checkpoint_blocker, which re-checks before committing. */
const _BG_PROTECTED=['master','main','trunk','develop'];
function _bgCanCheckpoint(a){
  const wt=a&&a.worktree;
  return !!(a&&(a.state==='dead'||a.state==='failed')&&wt&&wt.dirty>0&&wt.branch&&!wt.detached
    &&!_BG_PROTECTED.includes(wt.branch)&&wt.branch!==wt.base);
}
/* Pure: Checkpoint + relaunch / Checkpoint (+ the stranded worktree's Discard). */
function _bgCheckpointBtns(a, sw){
  const n=a.worktree.dirty, files=`${n} uncommitted file${n===1?'':'s'}`;
  return `<div class="ib-btns">
        <button class="adp-btn ib-btn bga-cp-go" onclick="_bgAgentCheckpoint(${jsq(a.id)},true)" title="Commit the ${escHtml(files)} as a WIP commit on ${escHtml(a.worktree.branch)}, then start a new Claude Code session that continues from it">Checkpoint + relaunch</button>
        <button class="adp-btn ib-btn" onclick="_bgAgentCheckpoint(${jsq(a.id)},false)" title="Only commit the ${escHtml(files)} as a WIP commit on ${escHtml(a.worktree.branch)} (never pushed) so nothing gets lost">Checkpoint</button>${sw?`
        <button class="adp-btn ib-btn wt-discard" onclick="_wtStrandedAct(${jsq(sw.id)},'discard')"${sw.locked?' disabled title="git has it locked — an agent may still own it"':' title="Delete the worktree and its branch, with everything done in it"'}>Discard</button>`:''}</div>`;
}
async function _bgAgentCheckpoint(id, relaunch){
  const a=(_bgAgents||[]).find(x=>x.id===id);
  if(!a||!a.worktree) return;
  const n=a.worktree.dirty;
  if(!confirm(`${relaunch?'Checkpoint and relaunch':'Checkpoint'} "${a.name}"?\n\n${n} uncommitted file${n===1?'':'s'} in ${a.worktree.path} get committed as "WIP checkpoint" on ${a.worktree.branch} (not pushed).`
    +(relaunch?'\n\nThen a new Claude Code session continues from that commit, with the original task, the file list and the agent\'s last message.':''))) return;
  let d={};
  try{
    d=await fetch('/agents/background/checkpoint',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id, relaunch:!!relaunch})}).then(r=>r.json());
  }catch(e){ d={ok:false, error:'Could not reach AOC.'}; }
  if(!d.ok) showToast('warning','Not checkpointed', d.error||'failed', 9000);
  else if(relaunch&&!d.launched) showToast('warning',`Checkpointed ${String(d.sha||'').slice(0,7)}, session not started`, d.launch_error||'', 9000);
  else showToast('done', relaunch?'Checkpointed + session started':`Checkpointed ${String(d.sha||'').slice(0,7)}`, `${d.files} file${d.files===1?'':'s'} on ${d.branch}`, 6000);
  await _bgAgentsTick(true);
}
async function _bgAgentRelaunch(id){
  const a=(_bgAgents||[]).find(x=>x.id===id);
  if(!a) return;
  if(!confirm(`Start a new Claude Code session to finish "${a.name}"?\n\n${a.reason||''}\n\nIt gets the agent's original task and works in ${(a.worktree&&a.worktree.path)||a.cwd||'its folder'}.`)) return;
  let d={};
  try{
    d=await fetch('/agents/background/relaunch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})}).then(r=>r.json());
  }catch(e){ d={ok:false, error:'Could not reach AOC.'}; }
  if(d.ok) showToast('done','Session started', a.name, 6000);
  else showToast('warning','Not started', d.error||'failed', 9000);
  await _bgAgentsTick(true);
}
async function _wtStrandedAct(id, kind){
  const w=(_wtStranded||[]).find(x=>x.id===id);
  if(!w) return;
  const name=`${w.repo_name} · ${w.branch||w.name}`;
  if(kind==='discard'&&!confirm(`Discard ${name}?\n\n${_wtStrandedStat(w)||'No changes'}\n\nThe worktree folder${w.branch?' and the branch '+w.branch:''} are deleted with all of that. This can't be undone.`)) return;
  if(kind==='relaunch'&&!confirm(`Start a new Claude Code session in ${w.path}, told to finish what was left there?`)) return;
  let d={};
  try{
    d=await fetch('/worktrees/stranded/'+kind,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})}).then(r=>r.json());
  }catch(e){ d={ok:false, error:'Could not reach AOC.'}; }
  const title={commit:['Committed','Not committed'], relaunch:['Session started','Not started'], discard:['Discarded','Not discarded']}[kind];
  if(d.ok) showToast('done', title[0], kind==='commit'?`${name} @${(d.sha||'').slice(0,7)}`:name, 6000);
  else showToast('warning', title[1], d.error||'failed', 9000);
  await _wtStrandedTick(true);
}
async function _wtDiff(id, branch){
  const o=_overlay('// '+branch, '<span style="color:var(--t3)">Loading…</span>');
  try{
    const d=await fetch('/worktrees/diff?id='+encodeURIComponent(id)).then(r=>r.json());
    o.querySelector('.diff-body').innerHTML=d.ok?(_diffLinesHtml(d.diff)||'<span style="color:var(--t3)">(no changes yet)</span>'):escHtml(d.error||'failed');
  }catch(e){ o.querySelector('.diff-body').textContent='Could not load the diff.'; }
}
async function renderToday(sr){
  const el=document.getElementById('today-area');
  if(!el||!sr) return;
  const now=Date.now();
  if(now-_wtAt>((_wtItems||[]).length?15000:60000)){
    _wtAt=now;
    await _wtLoad();
  }
  if(now-_kpiLastFetch>30000){
    _kpiLastFetch=now;
    try{ _kpiAnalytics=await fetch('/analytics').then(r=>r.json()); }catch(e){}
  }
  if(now-_todayCommitsAt>60000){
    _todayCommitsAt=now;
    try{ _todayCommits=await fetch('/commit_costs?days=1').then(r=>r.json()); }catch(e){}
  }
  if(now-_resumeAt>60000){
    _resumeAt=now;
    try{ _resumeCards=(await fetch('/resume_projects').then(r=>r.json())).cards||[]; }catch(e){}
  }
  if(currentView!=='today') return;
  const html=_todayHtml(sr, _kpiAnalytics, _todayCommits&&_todayCommits.stats, now/1000, _localDateStr(), _recaps, _recapMode, _resumeCards, _wtItems, _wtStranded, _bgAgents);
  if(el.innerHTML!==html) el.innerHTML=html;
  _recapAuto(sr);
}
/* "While you were away" recaps, keyed by machine|session id:
   {text, generated_at, cost, loading, error, asked_at}. */
const _recaps={};
let _recapMode=null, _recapInFlight=false;
function _recapKey(s){ return (s.machine||'')+'|'+s.id; }
async function _recapLoad(id, machineName, force){
  const s={id, machine:machineName};
  const k=_recapKey(s);
  const cur=_recaps[k]||{};
  _recaps[k]={...cur, loading:true, error:'', asked_at:Date.now()};
  if(lastStatus) renderToday(lastStatus);
  _recapInFlight=true;
  try{
    const d=await fetch(_apiUrl(_machineFor(machineName),'/recap?session_id='+encodeURIComponent(id)+(force?'&force=1':''))).then(r=>r.json());
    _recaps[k]=d.ok?{text:d.text, generated_at:d.generated_at, cost:d.cost, asked_at:Date.now()}
      :{...cur, loading:false, error:d.busy?'':(d.error||'failed'), asked_at:Date.now()};
  }catch(e){ _recaps[k]={...cur, loading:false, error:'Could not reach AOC', asked_at:Date.now()}; }
  _recapInFlight=false;
  if(lastStatus) renderToday(lastStatus);
}
/* Auto mode: one request at a time, each session at most once a minute
   (the server answers from its cache unless the session did more). */
async function _recapAuto(sr){
  if(_recapMode===null){
    _recapMode='loading';
    try{ _recapMode=(await fetch('/recap/settings').then(r=>r.json())).mode||'auto'; }catch(e){ _recapMode='auto'; }
  }
  if(_recapMode!=='auto'||_recapInFlight) return;
  const next=((sr&&sr.sessions_list)||[]).filter(s=>s&&s.away&&s.session_active!==false)
    .find(s=>{ const r=_recaps[_recapKey(s)]; return !r||(!r.loading&&Date.now()-(r.asked_at||0)>60000); });
  if(next) _recapLoad(next.id, next.machine||'', false);
}
/* Pure: "while you were away" block inside a session's Today row: what
   it did since your last message, and the Haiku recap (r = _recaps entry
   or undefined). '' unless the scanner flagged it (s.away). */
function _awayBlockHtml(s, r, nowS, mode){
  const a=s&&s.away;
  if(!a) return '';
  const ago=_fmtDurationDHM(Math.max(60,nowS-(a.since||nowS)));
  const facts=[`${a.tools} tool call${a.tools===1?'':'s'}`,
    a.commits?`${a.commits} commit${a.commits===1?'':'s'}`:'',
    a.errors?`${a.errors} failed`:'',
    a.files?`${a.files} file${a.files===1?'':'s'} edited`:''].filter(Boolean).join(' · ');
  const md=t=>escHtml(t).replace(/\*\*(.+?)\*\*/g,'<b>$1</b>').replace(/`([^`]+)`/g,'<code>$1</code>');
  let body='';
  if(r&&r.text){
    const lines=String(r.text).split(/\n+/).map(l=>l.trim()).filter(Boolean);
    const bullets=lines.every(l=>/^[-*•]\s/.test(l));
    body=bullets?`<ul class="td-recap">${lines.map(l=>`<li>${md(l.replace(/^[-*•]\s+/,''))}</li>`).join('')}</ul>`
      :`<div class="td-recap">${md(lines.join(' '))}</div>`;
  }
  if(r&&r.loading) body+=`<div class="td-recap-note">Summarizing…</div>`;
  else if(r&&r.error) body+=`<div class="td-recap-note err">${escHtml(r.error)}</div>`;
  const call=`_recapLoad(${jsq(s.id)},${jsq(s.machine||'')},true)`;
  const button=mode==='off'?'':`<button class="td-recap-btn" onclick="${call}" title="Summarize what it did since your last message (Claude Haiku, about 1 cent)"${r&&r.loading?' disabled':''}>${r&&r.text?'Refresh':'Summarize'}</button>`;
  return `<div class="td-away"><div class="td-away-head"><span class="td-away-lbl">Since your message ${escHtml(ago)} ago</span><span>${escHtml(facts)}</span>${button}</div>${body}</div>`;
}
/* Pure: the TODAY view. sr = /status, an = /analytics, cm = /commit_costs
   stats for today (or null), nowS = epoch s, todayStr = local YYYY-MM-DD,
   recaps = _recaps, recapMode = auto|click|off. */
/* Pure: the Queued card's rows (sr.queue from /status, see _queue_view). */
function _queueHtml(queue, nowS){
  const clock=s=>{ const d=new Date(s*1000), t=d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
    return (s-nowS<20*3600&&new Date(nowS*1000).getDate()===d.getDate())?t:d.toLocaleDateString([], {weekday:'short'})+' '+t; };
  return (queue||[]).map(t=>{
    const name=String(t.cwd||'').split(/[\\/]/).filter(Boolean).pop()||t.cwd||'?';
    let when, cls='';
    if(t.state==='queued'){
      when=t.waiting_on==='time'?'at '+clock(t.at)+(t.limits?' · if limits allow':'')
        :t.waiting_on==='limits'?'waits for plan limits':'starting…';
    } else if(t.state==='launched'){ when='started '+clock(t.finished_at); cls=' done'; }
    else if(t.state==='failed'){ when='failed · '+(t.error||''); cls=' bad'; }
    else { when='cancelled'; cls=' bad'; }
    const x=t.state==='queued'
      ?`<button class="adp-btn q-x" onclick="_queueCancel(${jsq(t.id)})" title="Take it off the queue">Cancel</button>`
      :`<button class="adp-btn q-x" onclick="_queueCancel(${jsq(t.id)})" title="Remove from this list">✕</button>`;
    return `<div class="q-row"><div class="q-main"><div class="q-name" title="${escHtml(t.cwd||'')}">${escHtml(name)}<span class="q-when${cls}">${escHtml(when)}</span>${t.model?`<span class="q-when">${escHtml(t.model)}</span>`:''}</div>
      <div class="q-text" title="${escHtml(t.prompt||'')}">${escHtml(t.prompt||'')}</div></div>${x}</div>`;
  }).join('');
}
async function _queueCancel(id){
  try{
    const d=await fetch('/queue/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})}).then(r=>r.json());
    if(!d.ok) showToast('warning','Not found','That task is gone already.');
  }catch(e){ showToast('warning','Not sent','Could not reach AOC.'); }
}
/* ── Stopped by the usage limit (sr.limit_stops, see _limit_stops_view):
   after the reset one click resumes that conversation with "pokračuj";
   before it an explicit opt-in queues exactly that for the reset time. ── */
const _LIMIT_PROMPT='pokračuj';
function _limitClock(s, nowS){
  const d=new Date(s*1000), t=d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
  return (s-nowS<20*3600&&new Date(nowS*1000).getDate()===d.getDate())?t:d.toLocaleDateString([], {weekday:'short'})+' '+t;
}
/* Pure: the status line + buttons of one stopped session. */
function _limitStopRowHtml(ls, nowS){
  const name=ls.title||String(ls.cwd||'').split(/[\\/]/).filter(Boolean).pop()||String(ls.id||'').slice(0,8);
  const win=ls.kind==='five_hour'?'5-hour':ls.kind==='seven_day'?'weekly':'';
  const reset=ls.resets_at?_limitClock(ls.resets_at, nowS):'';
  const status=ls.ready?'Usage limit reset — ready to continue'
    :'Stopped by usage limit'+(reset?' — resets '+reset:'');
  let btns, note='';
  if(ls.open){
    note=`<div class="ls-note">Still open in its terminal — type <b>${escHtml(_LIMIT_PROMPT)}</b> there${ls.ready?'':' after the reset'}.</div>`;
    btns=`<button class="adp-btn ls-btn" onclick="_focusSession(${jsq(ls.id)})" title="Switch to this session's terminal window">Terminal</button>`;
  } else if(ls.ready){
    btns=`<button class="adp-btn ls-btn ls-go" onclick="_limitStopAct(${jsq(ls.id)},'continue')" title="Reopens this conversation in a new terminal and sends “${escHtml(_LIMIT_PROMPT)}” (uses your plan)">Continue</button>`;
  } else if(ls.auto){
    note=`<div class="ls-note ls-auto">✓ Continues by itself${reset?' at '+escHtml(reset):' once the limit allows'} with “${escHtml(_LIMIT_PROMPT)}” — uses your plan.</div>`;
    btns=`<button class="adp-btn ls-btn" onclick="_limitStopAct(${jsq(ls.id)},'auto_off')" title="Don't continue by itself">Cancel auto-continue</button>`;
  } else {
    btns=`<button class="adp-btn ls-btn" onclick="_limitStopAct(${jsq(ls.id)},'auto_on')" title="Opt in: at the reset AOC reopens this conversation and sends “${escHtml(_LIMIT_PROMPT)}” -- that uses your plan">Auto-continue${reset?' at '+escHtml(reset):''}</button>`;
  }
  btns+=`<button class="adp-btn ls-btn ls-x" onclick="_limitStopAct(${jsq(ls.id)},'dismiss')" title="Remove from this list">✕</button>`;
  return `<div class="ls-row${ls.ready?' ready':''}"><div class="ls-main">
      <div class="ls-name" title="${escHtml(ls.cwd||'')}">${escHtml(name)}${win?`<span class="ls-win">${escHtml(win)}</span>`:''}</div>
      <div class="ls-status">${ls.ready?'▶':'⏸'} ${escHtml(status)}</div>${note}</div>
    <div class="ls-btns">${btns}</div></div>`;
}
function _limitStopsHtml(list, nowS){ return (list||[]).map(ls=>_limitStopRowHtml(ls, nowS)).join(''); }
async function _limitStopAct(id, action){
  if(action==='auto_on'&&!confirm(`Continue this session by itself when the usage limit resets?\n\nAOC will reopen the conversation in a new terminal and send “${_LIMIT_PROMPT}” -- that uses your plan. You can cancel it until then.`)) return;
  try{
    const d=await fetch('/limit_stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id, action})}).then(r=>r.json());
    if(!d.ok) showToast('warning','Not done', d.error||'Something went wrong.');
    else if(action==='continue') showToast('success','Continuing','The conversation reopens in a new terminal.');
    if(typeof fetchStatus==='function') fetchStatus();
  }catch(e){ showToast('warning','Not sent','Could not reach AOC.'); }
}
/* ── INBOX: everything that needs you, one card per session, most urgent
   first. Only /status data: a prompt AOC holds (perm_requests) or one
   Claude Code shows in the terminal (waiting_kind), a turn waiting for an
   answer, a hard budget cap, a prompt cache about to go cold (same lead
   times and $0.25 floor as _cache_expiry_alerts), a session that looks
   stuck, and finally plain "your turn". ── */
const _INBOX_KINDS={
  permission:{rank:0, label:'Needs your OK'},
  question:  {rank:1, label:'Claude asks'},
  budget:    {rank:2, label:'Budget cap hit'},
  limit:     {rank:2.5, label:'Usage limit'},
  cache:     {rank:3, label:'Cache going cold'},
  ctxsave:   {rank:4, label:'Context almost full'},
  loop:      {rank:5, label:'Looks stuck'},
  waiting:   {rank:6, label:'Your turn'},
};
/* Pure: [{kind, rank, label, s, secs, cacheLeft}] for sessions that need you. */
function _inboxItems(sr, nowS){
  const out=[], lsBy={}, seen={};
  ((sr&&sr.limit_stops)||[]).forEach(ls=>{ if(ls&&ls.id) lsBy[ls.id]=ls; });
  ((sr&&sr.sessions_list)||[]).forEach(s=>{
    if(!s||s.session_active===false) return;
    const waiting=!!s.waiting_on_you, wl=_waitLabel(s);
    const c=s.context||{};
    let cacheLeft=null;
    if(waiting&&c.cache_expires_at){
      const left=c.cache_expires_at-nowS, lead=c.cache_ttl==='5m'?120:600;
      const extra=(c.recache_usd||0)-(c.warm_usd||0);
      if(left>0&&left<=lead&&c.recache_usd!=null&&extra>=0.25) cacheLeft=left;
    }
    const msg=_lastMessageInfo((s.reply_request&&s.reply_request.message)||s.last_message);
    let kind=null;
    if((s.perm_requests||[]).length||(waiting&&s.waiting_kind==='permission')) kind='permission';
    else if(waiting&&(s.waiting_kind==='question'||(!wl.blocked&&msg&&msg.asked))) kind='question';
    else if(s.budget&&typeof s.budget.spent==='number') kind='budget';
    else if(lsBy[s.id]) kind='limit';
    else if(cacheLeft!=null) kind='cache';
    else if(s.ctx_save&&s.ctx_save.state==='offer') kind='ctxsave';
    else if(s.loop&&s.loop.text) kind='loop';
    else if(waiting||s.reply_request) kind='waiting';
    if(!kind) return;
    if(kind==='limit') seen[s.id]=1;
    out.push({kind, rank:_INBOX_KINDS[kind].rank, label:_INBOX_KINDS[kind].label, s, secs:s.waiting_secs||0, cacheLeft, ls:kind==='limit'?lsBy[s.id]:null});
  });
  /* stopped sessions that are closed (no open session to hang them on) */
  Object.values(lsBy).forEach(ls=>{ if(!seen[ls.id]) out.push({kind:'limit', rank:_INBOX_KINDS.limit.rank, label:_INBOX_KINDS.limit.label, s:null, ls, secs:Math.max(0,nowS-(ls.at||nowS))}); });
  return out.sort((a,b)=>(a.rank-b.rank)||(b.secs-a.secs));
}
/* Pure: the Inbox page for those items. */
function _inboxHtml(items, nowS, stranded, bgDead){
  const nameOf=s=>s.display_name||s.project||(s.cwd||'').split(/[\\/]/).filter(Boolean).pop()||String(s.id||'').slice(0,8);
  bgDead=bgDead||[];
  const strandedAll=stranded;
  stranded=_bgStrandedLeft(bgDead, stranded);
  const n=items.length+bgDead.length+(stranded||[]).length;
  const head=`<div class="ib-head"><h2 class="ib-title">Inbox</h2><span class="ib-sub">${n?`${n} thing${n===1?'':'s'} need${n===1?'s':''} you`:'All clear'}</span></div>`;
  if(!n) return head+`<div class="ib-empty"><div class="ib-empty-mark">✓</div><div>Nothing needs you right now.</div><div class="ib-empty-sub">Permission prompts, questions, budget caps, sessions stopped by the usage limit, caches about to go cold, full contexts, stuck sessions and work an agent left behind show up here.</div></div>`;
  const usd=n=>'$'+(Number(n)||0).toFixed(2);
  return head+(items.length?`<div class="ib-list">${items.map(it=>{
    if(it.kind==='limit') return `<article class="td-card ib-item ib-limit">
      <div class="ib-row"><span class="ib-kind">${escHtml(it.label)}</span><span class="ib-name">${escHtml(it.s?nameOf(it.s):(it.ls.title||String(it.ls.cwd||'').split(/[\\/]/).filter(Boolean).pop()||''))}</span>
        <span class="ib-when">${escHtml([it.s&&it.s.machine||'', it.ls.at?_fmtDurationDHM(Math.max(60,nowS-it.ls.at))+' ago':''].filter(Boolean).join(' · '))}</span></div>
      ${_limitStopRowHtml(it.ls, nowS)}</article>`;
    const s=it.s, wl=_waitLabel(s);
    const info=_lastMessageInfo(s.last_message, 220);
    const text=wl.blocked&&s.waiting_message?s.waiting_message:(info?info.text:'');
    const ask=_permAskHtml(s)||_replyAskHtml(s)
      ||(text&&s.waiting_on_you?`<div class="ib-text">${wl.icon?wl.icon+' ':(info&&info.asked?'❓ ':'')}${escHtml(text)}</div>`:'');
    const cache=it.kind==='cache'
      ?`<div class="ib-cache">⏱ Reply within ${escHtml(_fmtDurationDHM(Math.max(60,Math.ceil(it.cacheLeft/60)*60)))} to keep the cache warm (~${usd(s.context.warm_usd)}) — after that the next message re-caches for ~${usd(s.context.recache_usd)}.</div>`:'';
    const when=[s.machine||'', s.waiting_on_you?_fmtDurationDHM(Math.max(60,it.secs)):''].filter(Boolean).join(' · ');
    const btns=[
      s._isLocal===false?'':`<button class="adp-btn ib-btn" onclick="_focusSession(${jsq(s.id)})" title="Switch to this session's terminal window">Terminal</button>`,
      `<button class="adp-btn ib-btn" onclick="setView('cli')" title="Open the Sessions view">Sessions</button>`,
    ].join('');
    return `<article class="td-card ib-item ib-${it.kind}">
      <div class="ib-row"><span class="ib-kind">${escHtml(it.label)}</span><span class="ib-name">${escHtml(nameOf(s))}</span>
        <span class="ib-when">${escHtml(when)}</span></div>
      ${ask}${cache}${_budgetBadgeHtml(s)}${_loopBadgeHtml(s)}${_ctxMeterHtml(s, nowS)}
      <div class="ib-btns">${btns}</div></article>`;
  }).join('')}</div>`:'')+_bgAgentsHtml(bgDead, nowS, strandedAll)+((stranded||[]).length?_wtStrandedHtml(stranded, nowS):'');
}
function renderInbox(sr){
  const el=document.getElementById('inbox-area');
  if(!el||!sr||currentView!=='inbox') return;
  /* don't swap the DOM under an open "Details" or a focused button */
  if(el.contains(document.activeElement)&&document.activeElement!==el&&document.activeElement.tagName!=='BUTTON') return;
  /* orchestration first: plans, sensitive tasks and results waiting for you (web/js/19) */
  const html=_orInboxHtml(sr.orch_attention)+_inboxHtml(_inboxItems(sr, Date.now()/1000), Date.now()/1000, _wtStranded, _bgDead(_bgAgents));
  if(el.innerHTML!==html) el.innerHTML=html;
}
function _todayHtml(sr, an, cm, nowS, todayStr, recaps, recapMode, resume, worktrees, stranded, bgAgents){
  const awayOf=s=>_awayBlockHtml(s, (recaps||{})[(s.machine||'')+'|'+s.id], nowS, recapMode||'auto');
  sr=sr||{}; an=an||{};
  const money=v=>'$'+(Number(v)||0).toFixed(2);
  const nameOf=s=>s.display_name||s.project||(s.cwd||'').split(/[\\/]/).filter(Boolean).pop()||String(s.id||'').slice(0,8);
  const sessions=(sr.sessions_list||[]).filter(s=>s&&s.session_active!==false);
  const waiting=_waitingSessions(sessions, 0);
  const working=sessions.filter(s=>!s.waiting_on_you);
  const agents=(sr.agents||[]).filter(a=>a.status==='running'||a.status==='waiting');
  /* spend: today vs the average of the 7 days before */
  const day=s=>Date.UTC(+s.slice(0,4),+s.slice(5,7)-1,+s.slice(8,10))/864e5;
  const t0=/^\d{4}-\d\d-\d\d$/.test(todayStr||'')?day(todayStr):null;
  let prev7=0;
  (an.by_day||[]).forEach(r=>{ if(t0!=null&&/^\d{4}-\d\d-\d\d/.test(r.date||'')){ const age=t0-day(r.date); if(age>=1&&age<=7) prev7+=r.cost||0; } });
  const todayCost=(an.today&&an.today.cost)||0, avg=prev7/7;
  const vsAvg=avg>0?Math.round((todayCost-avg)/avg*100):null;
  const commits=(cm&&cm.commits)||[];
  const when=new Date(nowS*1000);
  const dateStr=when.toLocaleDateString(undefined,{weekday:'long',day:'numeric',month:'long'});
  const facts=[
    `<b>${money(todayCost)}</b> spent today${vsAvg!=null?` <span class="td-delta ${vsAvg>0?'up':'down'}" title="Average of the 7 days before: ${money(avg)}">${vsAvg>0?'▲':'▼'} ${Math.abs(vsAvg)}% vs a usual day</span>`:''}`,
    `<b>${sessions.length}</b> session${sessions.length===1?'':'s'} open`,
    `<b>${commits.length}</b> commit${commits.length===1?'':'s'}${cm&&cm.total&&cm.total.cost?` for ${money(cm.total.cost)}`:''}`,
  ];
  const btn=(s,label)=>`<div class="td-btns">${s._isLocal===false?'':`<button class="adp-btn td-btn" onclick="_focusSession(${jsq(s.id)})" title="Switch to this session's terminal window">${label}</button>`}${_changesBtnHtml(s)}${_replayBtnHtml(s)}</div>`;
  const waitRows=waiting.map(s=>{
    const wl=_waitLabel(s);
    const info=_lastMessageInfo(s.last_message, 180);
    const text=wl.blocked&&s.waiting_message?s.waiting_message:(info?info.text:'');
    return `<div class="td-row${wl.blocked?' blocked':''}">
      <div class="td-main"><div class="td-name">${escHtml(nameOf(s))}<span class="td-tag${wl.blocked?' blocked':''}">${escHtml(wl.bay)} · ${escHtml(_fmtDurationDHM(Math.max(60,s.waiting_secs||0)))}</span></div>
        ${_permAskHtml(s)||_replyAskHtml(s)||(text?`<div class="td-text">${wl.icon?wl.icon+' ':(info&&info.asked?'❓ ':'')}${escHtml(text)}</div>`:'')}
        ${_budgetBadgeHtml(s)}${_loopBadgeHtml(s)}${awayOf(s)}${_ctxMeterHtml(s, nowS)}</div>
      ${btn(s,'Terminal')}</div>`;
  }).join('');
  const workRows=working.map(s=>{
    const n=agents.filter(a=>a.session_id===s.id).length;
    const model=s.model?_ccModelName(s.model):'';
    return `<div class="td-row">
      <div class="td-main"><div class="td-name">${escHtml(nameOf(s))}<span class="td-tag run">Working${n?` · ${n} agent${n===1?'':'s'}`:''}</span></div>
        <div class="td-sub">${[model, s.estimated_cost?money(s.estimated_cost):''].filter(Boolean).map(escHtml).join(' · ')}</div>
        ${_budgetBadgeHtml(s)}${_loopBadgeHtml(s)}${awayOf(s)}${_ctxMeterHtml(s, nowS)}</div>
      ${btn(s,'Terminal')}</div>`;
  }).join('');
  const lim=((sr.rate_limits||{}).windows||[]).filter(w=>w.status!=='reset'&&typeof w.pct==='number').map(w=>{
    const left=Math.max(0,(w.resets_at||0)-nowS), h=Math.floor(left/3600), m=Math.floor(left%3600/60);
    const lvl=w.status==='hit'||w.pct>=90?'high':w.status==='warn'||w.pct>=80?'warn':'ok';
    return `<div class="td-lim ctx-${lvl}"><span class="td-lim-name">${w.kind==='five_hour'?'5-hour':'Weekly'}</span>
      <span class="ctx-bar td-lim-bar"><span style="width:${Math.min(100,w.pct).toFixed(1)}%"></span></span>
      <span class="td-lim-val">${w.status==='hit'?'LIMIT':Math.round(w.pct)+'%'}</span>
      <span class="td-lim-reset">resets in ${h>=24?Math.floor(h/24)+'d '+(h%24)+'h':h?h+'h '+m+'m':m+'m'}${w.projected_pct!=null&&w.status!=='hit'?` · ~${Math.min(100,Math.round(w.projected_pct))}% by then`:''}</span>${(fc=>fc?`
      <span class="td-lim-fc${fc.soon?' soon':''}">${escHtml(fc.text)}${fc.advice?`<b>${escHtml(fc.advice)}</b>`:''}</span>`:'')(_limitForecast(w, nowS))}</div>`;
  }).join('');
  const projRows=(an.by_day_project||[]).filter(r=>r.date===todayStr&&(r.cost||0)>0).sort((a,b)=>b.cost-a.cost);
  const pmax=Math.max(...projRows.map(r=>r.cost),0.0001);
  const projects=projRows.slice(0,6).map(r=>`<div class="td-proj"><span class="td-proj-name">${escHtml(r.project)}</span>
      <span class="cc-bar td-proj-bar"><span style="width:${(r.cost/pmax*100).toFixed(1)}%"></span></span><span class="td-proj-val">${money(r.cost)}</span></div>`).join('');
  const cmRows=commits.slice(0,8).map(x=>{
    const tm=x.epoch?new Date(x.epoch*1000).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'}):'';
    return `<div class="td-commit"><span class="td-c-time">${escHtml(tm)}</span><span class="cc-sha">${escHtml(String(x.sha||'').slice(0,7))}</span>
      <span class="td-c-subj" title="${escHtml(x.subject||'')}">${escHtml(x.subject||'(no message)')}<span class="cc-repo">${escHtml(x.repo||'')}</span></span><span class="td-c-cost">${money(x.cost)}</span></div>`;
  }).join('');
  const sec=(title,body,empty,extra)=>`<section class="td-card"><h3>${title}${extra||''}</h3>${body||`<div class="td-empty">${empty}</div>`}</section>`;
  /* background agents: dead/failed first; a stranded worktree that is one
     of their worktrees is relaunched from the agent card, not shown twice */
  const bg=(bgAgents||[]).filter(Boolean);
  let bgSec='', strandedLeft=stranded;
  if(bg.length){
    const rank=a=>{ const i=['dead','failed','quiet','working','finished'].indexOf(a.state); return i<0?5:i; };
    const rows=bg.slice().sort((a,b)=>(rank(a)-rank(b))||((b.last_activity||0)-(a.last_activity||0)));
    const dead=_bgDead(rows).length;
    bgSec=sec(`Background agents <span class="td-count">${rows.length}</span>${dead?` <span class="td-count bga-dead-n" title="Stopped before finishing">${dead} dead</span>`:''}`, _bgAgentsHtml(rows, nowS, stranded), '');
    strandedLeft=_bgStrandedLeft(_bgDead(rows), stranded);
  }
  return `<div class="td-wrap">
    <header class="td-head"><h2>${escHtml(dateStr)}</h2><div class="td-facts">${facts.map(f=>`<span>${f}</span>`).join('')}</div>
      <div class="td-launch"><button class="adp-btn" onclick="openAsk()" title="Ask about your own history in plain words: spend, failures, commits, clients (Claude Haiku, about 1 cent)">Ask AOC</button>
      <button class="adp-btn" onclick="openLaunch()" title="Start a new Claude Code session in a folder, with your first message">+ New session</button></div></header>
    <div class="td-grid">
      <div class="td-col">
        ${sec(`Needs you${waiting.length?` <span class="td-count">${waiting.length}</span>`:''}`, waitRows, 'Nothing is waiting on you.')}
        ${(sr.limit_stops||[]).length?sec(`Stopped by usage limit <span class="td-count">${sr.limit_stops.length}</span>`, _limitStopsHtml(sr.limit_stops, nowS), ''):''}
        ${sec('Working', workRows, sessions.length?'Every open session is waiting on you.':'No Claude Code session is open.')}
        ${(sr.queue||[]).length?sec(`Queued <span class="td-count">${sr.queue.filter(t=>t.state==='queued').length}</span>`, _queueHtml(sr.queue, nowS), ''):''}
        ${bgSec}
        ${(worktrees||[]).length?sec(`Parallel worktrees <span class="td-count">${worktrees.length}</span>`, _wtHtml(worktrees, nowS), ''):''}
        ${(strandedLeft||[]).length?sec(`Stranded work <span class="td-count">${strandedLeft.length}</span>`, _wtStrandedHtml(strandedLeft, nowS), ''):''}
        ${(resume||[]).length?sec('Pick up where you left off', _resumeHtml(resume, nowS), ''):''}
      </div>
      <div class="td-col">
        ${lim?sec('Plan limits', lim, ''):''}
        ${sec('Spend by project', projects, 'Nothing spent yet today.')}
        ${sec('Commits today', cmRows, 'No commits yet today.', commits.length?` <button class="adp-btn td-more" onclick="_histTab='commits';setView('history')">All commits</button>`:'')}
      </div>
    </div>
  </div>`;
}

