/* ── Launch a new Claude Code session ── */
/* Pure: the New session form. dirs = /launch/dirs, machines = [{name,label}]. */
/* Pure: the {{fields}} a template asks for ({{date}} / {{yesterday}} fill themselves). */
function _promptVars(text){
  const out=[];
  for(const m of String(text||'').matchAll(/\{\{\s*([A-Za-z][\w -]{0,30}?)\s*\}\}/g)){
    const v=m[1].trim();
    if(!['date','yesterday'].includes(v.toLowerCase())&&!out.includes(v)) out.push(v);
  }
  return out;
}
/* Pure: the template with its fields filled in; missing = names left empty. */
function _fillPrompt(text, vals, now){
  const ymd=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  const missing=[];
  const out=String(text||'').replace(/\{\{\s*([A-Za-z][\w -]{0,30}?)\s*\}\}/g,(m,name)=>{
    const k=name.trim(), lk=k.toLowerCase();
    if(lk==='date') return ymd(now);
    if(lk==='yesterday') return ymd(new Date(now.getTime()-86400e3));
    const v=String((vals||{})[k]||'').trim();
    if(!v){ if(!missing.includes(k)) missing.push(k); return m; }
    return v;
  });
  return {text:out, missing};
}
function _launchFormHtml(dirs, machines, machine, prompts){
  const chips=(dirs||[]).slice(0,8).map(d=>`<button type="button" class="ln-chip" onclick="_launchPick(this)" data-path="${escHtml(d.path)}" title="${escHtml(d.path)}">${escHtml(d.name)}</button>`).join('');
  const machineSel=(machines||[]).length>1?`<label class="ln-lbl">Run on</label>
    <select id="ln-machine" class="settings-input" onchange="_launchLoadDirs(this.closest('.diff-overlay'), this.value)">${machines.map(m=>`<option value="${escHtml(m.name)}"${m.name===machine?' selected':''}>${escHtml(m.label)}</option>`).join('')}</select>`:'';
  return `<form class="ln-form" onsubmit="event.preventDefault();_launchSubmit(this)">
    ${machineSel}
    <label class="ln-lbl" for="ln-cwd">Folder</label>
    <input id="ln-cwd" class="settings-input" list="ln-dirs" placeholder="C:\\path\\to\\project" autocomplete="off" required>
    <datalist id="ln-dirs">${(dirs||[]).map(d=>`<option value="${escHtml(d.path)}"></option>`).join('')}</datalist>
    <div class="ln-chips">${chips||'<span class="ln-none">Folders Claude Code worked in lately show up here.</span>'}</div>
    <div class="ln-row ln-tplrow"><label class="ln-lbl" for="ln-tpl">Template</label>
      <select id="ln-tpl" class="settings-input" onchange="_launchUseTemplate(this.form)" title="Prompt library: saved first messages">
        <option value="">— write your own —</option>${(prompts||[]).map(t=>`<option value="${escHtml(t.id)}">${escHtml(t.name)}${t.used?` · ${t.used}×`:''}</option>`).join('')}</select>
      <button type="button" class="adp-btn ln-mini" onclick="_launchSaveTemplate(this.form)" title="Save the message below (with any {{fields}}), folder and model as a template">Save as template</button>
      <button type="button" class="adp-btn ln-mini" id="ln-tpl-del" style="display:none" onclick="_launchDeleteTemplate(this.form)">Delete</button></div>
    <label class="ln-lbl" for="ln-prompt">First message</label>
    <textarea id="ln-prompt" class="settings-input" rows="5" placeholder="What should Claude do? (optional) — {{like this}} makes a field to fill in" oninput="_launchVarsRender(this.form)" onkeydown="if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();_launchSubmit(this.form)}"></textarea>
    <div id="ln-vars" class="ln-vars"></div>
    <div class="ln-row"><label class="ln-lbl" for="ln-model">Model</label>
      <select id="ln-model" class="settings-input"><option value="">Default</option><option value="opus">Opus</option><option value="sonnet">Sonnet</option><option value="haiku">Haiku</option></select>
      <label class="ln-lbl" for="ln-when">Start</label>
      <select id="ln-when" class="settings-input" onchange="_launchWhen(this.form)" title="Queue it: start later, once your plan limits allow, or at a set time">
        <option value="now">Now</option><option value="limits">When plan limits allow</option><option value="at">At a time…</option></select>
      <input id="ln-at" type="datetime-local" class="settings-input" style="display:none;width:auto">
      <label id="ln-at-lim" class="ln-chk" style="display:none"><input type="checkbox" id="ln-at-limits" checked> and limits allow</label></div>
    ${machine?'':`<div class="ln-row"><label class="ln-lbl" for="ln-wt">Where</label>
      <select id="ln-wt" class="settings-input" onchange="_launchWhen(this.form)" title="A git worktree is a separate copy of the repo on its own branch: sessions in worktrees never edit the same files, and you merge or discard each one afterwards">
        <option value="0">In this folder</option><option value="1">In a new git worktree</option>
        <option value="2">2 sessions in parallel worktrees</option><option value="3">3 sessions in parallel worktrees</option><option value="4">4 sessions in parallel worktrees</option></select>
      <span class="ln-wt-note" id="ln-wt-note" style="display:none">Branches off the repo's last commit (uncommitted changes are not copied). Merge or discard them under Today → Parallel worktrees.</span></div>`}
    <div class="ln-row"><span style="flex:1"></span><span class="ln-err" id="ln-err"></span>
      <button type="submit" class="adp-btn ln-go">Start session</button></div>
    <div class="ln-note">Opens a new terminal window on that PC. With Remote approve on, its permission prompts reach you here while you're away.
      <button type="button" class="adp-btn ln-mini ab-link" onclick="_abFromLaunch(this.form)" title="Run this message with two models side by side, each in its own copy of the repo, and compare cost, time and changes">A/B: compare two models…</button></div>
  </form>`;
}
function _launchMachines(){
  return [{name:'',label:'This PC'}].concat((typeof _remoteMachines!=='undefined'?_remoteMachines:[]).map(m=>({name:m.name,label:m.name})));
}
async function _launchLoadDirs(o, machine){
  if(!o) return;
  let dirs=[];
  let prompts=[];
  try{ dirs=(await fetch(_apiUrl(_machineFor(machine),'/launch/dirs')).then(r=>r.json())).dirs||[]; }catch(e){}
  try{ prompts=(await fetch(_apiUrl(_machineFor(machine),'/prompts')).then(r=>r.json())).prompts||[]; }catch(e){}
  // keep what's typed across the re-render (machine switch, template saved/deleted)
  const ids=['#ln-prompt','#ln-cwd','#ln-model','#ln-when','#ln-at','#ln-wt'];
  const keep=ids.map(id=>(o.querySelector(id)||{}).value);
  const keepChk=(o.querySelector('#ln-at-limits')||{checked:true}).checked;
  o._prompts=prompts;
  o.querySelector('.chg-body').innerHTML=_launchFormHtml(dirs, _launchMachines(), machine, prompts);
  ids.forEach((id,i)=>{ const el=o.querySelector(id); if(el&&keep[i]!=null) el.value=keep[i]; });
  o.querySelector('#ln-at-limits').checked=keepChk;
  o.dataset.machine=machine||'';
  const f=o.querySelector('form');
  if((keep[3]&&keep[3]!=='now')||(keep[5]&&keep[5]!=='0')) _launchWhen(f);
  _launchVarsRender(f);
}
function _launchVarsRender(form){
  if(!form) return;
  const box=form.querySelector('#ln-vars');
  const keep={}; box.querySelectorAll('input[data-var]').forEach(i=>keep[i.dataset.var]=i.value);
  const vars=_promptVars(form.querySelector('#ln-prompt').value);
  box.innerHTML=vars.map(v=>`<label class="ln-var"><span>${escHtml(v)}</span><input class="settings-input" data-var="${escHtml(v)}" value="${escHtml(keep[v]||'')}"></label>`).join('');
}
function _launchUseTemplate(form){
  const o=form.closest('.diff-overlay'), id=form.querySelector('#ln-tpl').value;
  const t=((o&&o._prompts)||[]).find(x=>x.id===id);
  form.querySelector('#ln-tpl-del').style.display=t?'':'none';
  if(!t) return;
  form.querySelector('#ln-prompt').value=t.text;
  if(t.cwd) form.querySelector('#ln-cwd').value=t.cwd;
  form.querySelector('#ln-model').value=t.model||'';
  _launchVarsRender(form);
  const first=form.querySelector('#ln-vars input'); (first||form.querySelector('#ln-prompt')).focus();
}
async function _launchSaveTemplate(form){
  const o=form.closest('.diff-overlay'), machine=(o&&o.dataset.machine)||'';
  const text=form.querySelector('#ln-prompt').value.trim();
  if(!text){ form.querySelector('#ln-err').textContent='Write the message first.'; return; }
  const cur=((o&&o._prompts)||[]).find(x=>x.id===form.querySelector('#ln-tpl').value);
  const name=prompt('Template name:', cur?cur.name:text.split(/\s+/).slice(0,5).join(' '));
  if(!name) return;
  const d=await fetch(_apiUrl(_machineFor(machine),'/prompts'),{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:cur&&cur.name===name?cur.id:'', name, text, cwd:form.querySelector('#ln-cwd').value, model:form.querySelector('#ln-model').value})}).then(r=>r.json()).catch(()=>({}));
  if(!d.ok){ form.querySelector('#ln-err').textContent=d.error||'Not saved.'; return; }
  showToast('done','Template saved',name);
  await _launchLoadDirs(o, machine);
  const f=o.querySelector('form'); f.querySelector('#ln-tpl').value=d.template.id; f.querySelector('#ln-tpl-del').style.display='';
}
async function _launchDeleteTemplate(form){
  const o=form.closest('.diff-overlay'), machine=(o&&o.dataset.machine)||'', id=form.querySelector('#ln-tpl').value;
  const t=((o&&o._prompts)||[]).find(x=>x.id===id);
  if(!t||!confirm(`Delete the template "${t.name}"?`)) return;
  await fetch(_apiUrl(_machineFor(machine),'/prompts/delete'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})}).catch(()=>{});
  await _launchLoadDirs(o, machine);
}
function openLaunch(){
  const o=_overlay('New Claude Code session', _launchFormHtml([], _launchMachines(), ''), 'chg-box ln-box');
  _launchLoadDirs(o, '');
}
/* ── A/B model compare (/ab, /ab/get, /ab/estimate, /ab/start|keep|discard) ── */
const _AB_MODEL_NAMES={opus:'Opus',sonnet:'Sonnet',haiku:'Haiku'};
/* Pure: the A/B form. dirs = /launch/dirs. */
function _abFormHtml(dirs, cwd, prompt){
  const chips=(dirs||[]).slice(0,8).map(d=>`<button type="button" class="ln-chip" onclick="_launchPick(this)" data-path="${escHtml(d.path)}" title="${escHtml(d.path)}">${escHtml(d.name)}</button>`).join('');
  const sel=(id,v)=>`<select id="${id}" class="settings-input" onchange="_abEstimate(this.form)">${Object.entries(_AB_MODEL_NAMES).map(([k,n])=>`<option value="${k}"${k===v?' selected':''}>${n}</option>`).join('')}</select>`;
  return `<form class="ln-form ab-form" onsubmit="event.preventDefault();_abSubmit(this)">
    <label class="ln-lbl" for="ln-cwd">Folder (a git repository)</label>
    <input id="ln-cwd" class="settings-input" list="ab-dirs" placeholder="C:\\path\\to\\repo" autocomplete="off" required value="${escHtml(cwd||'')}">
    <datalist id="ab-dirs">${(dirs||[]).map(d=>`<option value="${escHtml(d.path)}"></option>`).join('')}</datalist>
    <div class="ln-chips">${chips}</div>
    <label class="ln-lbl" for="ln-prompt">Task both models get</label>
    <textarea id="ln-prompt" class="settings-input" rows="5" required placeholder="e.g. Add input validation to the signup form and a test for it" oninput="_abEstimate(this.form)">${escHtml(prompt||'')}</textarea>
    <div class="ln-row"><label class="ln-lbl" for="ab-m-a">A</label>${sel('ab-m-a','opus')}
      <label class="ln-lbl" for="ab-m-b">B</label>${sel('ab-m-b','sonnet')}
      <label class="ln-lbl" for="ab-budget">Cap per run $</label>
      <input id="ab-budget" class="settings-input ab-budget" type="number" min="0.1" max="20" step="0.5" value="2" oninput="_abEstimate(this.form)"></div>
    <div id="ab-est" class="ab-est"></div>
    <label class="ln-chk"><input type="checkbox" id="ab-ok"> I understand both runs are billed to my Claude account</label>
    <div class="ln-row"><span style="flex:1"></span><span class="ln-err" id="ln-err"></span>
      <button type="submit" class="adp-btn ln-go">Run both</button></div>
    <div class="ln-note">Each model works headless in its own throwaway copy of the repo (git worktree off HEAD) — your working copy isn't touched.
      They can read and edit files there; shell commands, MCP servers and hooks are off. Afterwards keep one (merged into your current branch) or discard both.</div>
  </form>`;
}
/* Pure: /ab/estimate reply -> the cost line. */
function _abEstHtml(q){
  const runs=(q&&q.runs)||[];
  if(runs.length<2) return '<span class="ab-warn">Pick two different models.</span>';
  const m=v=>'$'+(Number(v)||0).toFixed(2);
  return `Estimate: ${runs.map(r=>`<b>${escHtml(r.label)}</b> ${escHtml(_AB_MODEL_NAMES[r.model]||r.model)} ${m(r.low)}–${m(r.high)}${r.capped?' (capped)':''}`).join(' · ')}
    · at most <b>${m(q.max_total)}</b> together`;
}
/* Pure: seconds -> "1m 05s". */
function _abDur(s){
  if(s==null) return '—';
  s=Math.round(Number(s)||0);
  return s<60?s+'s':Math.floor(s/60)+'m '+String(s%60).padStart(2,'0')+'s';
}
const _AB_STATE={queued:'Queued',running:'Running',done:'Done',error:'Failed',stopped:'Stopped'};
/* Pure: one comparison (GET /ab/get) -> results HTML. nowS = epoch seconds. */
function _abResultHtml(c, nowS){
  if(!c||!c.runs) return '<div class="chg-empty">Comparison not found.</div>';
  const m=v=>v==null?'—':'$'+(Number(v)||0).toFixed(2);
  const runs=c.runs, open=c.state==='running'||c.state==='done';
  const fin=runs.filter(r=>r.state==='done'&&r.cost!=null);
  const cheap=fin.length===2&&fin[0].cost!==fin[1].cost?(fin[0].cost<fin[1].cost?fin[0].label:fin[1].label):'';
  const fast=fin.length===2&&fin[0].wall_s!==fin[1].wall_s?(fin[0].wall_s<fin[1].wall_s?fin[0].label:fin[1].label):'';
  const row=(name,f)=>`<tr><th>${name}</th>${runs.map(r=>`<td>${f(r)}</td>`).join('')}</tr>`;
  const live=r=>r.live||r;
  const tbl=`<table class="ab-tbl"><thead><tr><th></th>${runs.map(r=>`<th><span class="ab-lbl">${escHtml(r.label)}</span> ${escHtml(_AB_MODEL_NAMES[r.model]||r.model)}${c.kept===r.label?' <span class="ab-kept">kept</span>':''}</th>`).join('')}</tr></thead><tbody>
    ${row('State',r=>`<span class="ab-st ab-${escHtml(r.state||'')}">${escHtml(_AB_STATE[r.state]||r.state||'')}</span>${r.error?`<div class="ab-err">${escHtml(r.error)}</div>`:''}`)}
    ${row('Time',r=>escHtml(r.wall_s!=null?_abDur(r.wall_s):r.started?_abDur(nowS-r.started)+'…':'—')+(fast===r.label?' <span class="ab-win">faster</span>':''))}
    ${row('Cost',r=>escHtml(m(live(r).cost))+(r.stop==='error_max_budget_usd'?' <span class="ab-warn">hit cap</span>':'')+(cheap===r.label?' <span class="ab-win">cheaper</span>':''))}
    ${row('Tool calls',r=>escHtml(String(live(r).tool_calls??'—')))}
    ${row('Failed calls',r=>{ const x=live(r); return x.errors==null?'—':`${x.errors}${x.denials?` <span class="ab-sub">+${x.denials} blocked</span>`:''}`; })}
    ${row('Changes',r=>{ const d=r.diffstat; return !d?'—':d.files?`${d.files} file${d.files>1?'s':''} <span class="diff-add">+${d.ins}</span> <span class="diff-del">−${d.dels}</span>`:'none'; })}
    ${row('',r=>r.state==='queued'||r.state==='running'?'':`${r.final?`<button class="adp-btn ln-mini" onclick="_abAnswer(${jsq(c.id)},${jsq(r.label)})">Answer</button>`:''}
      ${r.diffstat&&r.diffstat.files&&r.diff!=null?`<button class="adp-btn ln-mini" onclick="_abDiff(${jsq(c.id)},${jsq(r.label)})">Diff</button>`:''}
      ${c.state==='done'&&r.diffstat&&r.diffstat.files?`<button class="adp-btn ln-mini ab-keep" onclick="_abKeep(${jsq(c.id)},${jsq(r.label)})">Keep ${escHtml(r.label)}</button>`:''}`)}
  </tbody></table>`;
  const foot=open?`<div class="ln-row"><span class="ln-note" style="flex:1">${c.state==='running'?'Running — this updates by itself.':'Keep merges that copy into your current branch and removes the other one.'}</span>
    <button class="adp-btn ln-mini" onclick="_abDiscard(${jsq(c.id)})">${c.state==='running'?'Stop & discard':'Discard both'}</button></div>`
    :`<div class="ln-note">${c.state==='kept'?(c.merge_error?`Merge conflict — the changes are on branch ${escHtml((runs.find(r=>r.label===c.kept)||{}).branch||'')}.`:`Run ${escHtml(c.kept||'')} merged${c.merged?' as '+escHtml(String(c.merged).slice(0,7)):''}.`):'Discarded.'}</div>`;
  return `<div class="ab-res"><div class="ab-prompt">${escHtml(c.prompt||'')}</div><div class="ab-meta">${escHtml(c.repo||'')} · off ${escHtml(String(c.head||'').slice(0,7))} · cap ${m(c.budget)} per run</div>${tbl}${foot}</div>`;
}
/* Pure: GET /ab items -> list of past comparisons. */
function _abListHtml(items){
  if(!(items||[]).length) return '';
  return `<div class="ln-lbl">Earlier comparisons</div><div class="ab-list">${items.slice(0,8).map(c=>{
    const cost=(c.runs||[]).reduce((s,r)=>s+(Number(r.cost)||0),0);
    const st=c.state==='kept'?'kept '+(c.kept||''):c.state;
    return `<button type="button" class="ab-item" onclick="_abShow(${jsq(c.id)}, this.closest('.diff-overlay'))"><span class="ab-it-p">${escHtml((c.prompt||'').slice(0,80))}</span>
      <span class="ab-it-m">${(c.runs||[]).map(r=>escHtml(_AB_MODEL_NAMES[r.model]||r.model)).join(' vs ')} · $${cost.toFixed(2)} · ${escHtml(st||'')}</span></button>`;
  }).join('')}</div>`;
}
async function openAB(cwd, prompt){
  const o=_overlay('A/B: compare two models', _abFormHtml([], cwd, prompt), 'chg-box ln-box ab-box');
  let dirs=[], items=[];
  try{ dirs=(await fetch('/launch/dirs').then(r=>r.json())).dirs||[]; }catch(e){}
  try{ items=(await fetch('/ab').then(r=>r.json())).items||[]; }catch(e){}
  const running=items.find(c=>c.state==='running');
  if(running){ _abShow(running.id, o); return; }
  const f=o.querySelector('form'), keep=[f.querySelector('#ln-cwd').value, f.querySelector('#ln-prompt').value];
  o.querySelector('.chg-body').innerHTML=_abFormHtml(dirs, keep[0], keep[1])+_abListHtml(items);
  _abEstimate(o.querySelector('form'));
}
function _abFromLaunch(form){
  const cwd=form.querySelector('#ln-cwd').value, p=form.querySelector('#ln-prompt').value;
  const o=form.closest('.diff-overlay'); if(o) o.remove();
  openAB(cwd, p);
}
async function _abEstimate(form){
  if(!form) return;
  const box=form.querySelector('#ab-est');
  const body={prompt:form.querySelector('#ln-prompt').value, models:[form.querySelector('#ab-m-a').value, form.querySelector('#ab-m-b').value], budget:form.querySelector('#ab-budget').value};
  if(body.models[0]===body.models[1]){ box.innerHTML=_abEstHtml(null); return; }
  try{
    const q=await fetch('/ab/estimate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(r=>r.json());
    box.innerHTML=_abEstHtml(q);
  }catch(e){ box.textContent=''; }
}
async function _abSubmit(form){
  const err=form.querySelector('#ln-err');
  if(!form.querySelector('#ab-ok').checked){ err.textContent='Tick the cost box first.'; return; }
  const btn=form.querySelector('.ln-go'); btn.disabled=true; err.textContent='Making two copies of the repo…';
  const d=await fetch('/ab/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    cwd:form.querySelector('#ln-cwd').value, prompt:form.querySelector('#ln-prompt').value,
    models:[form.querySelector('#ab-m-a').value, form.querySelector('#ab-m-b').value],
    budget:form.querySelector('#ab-budget').value, confirm:true})}).then(r=>r.json()).catch(()=>({ok:false,error:'AOC did not answer'}));
  btn.disabled=false;
  if(!d.ok){ err.textContent=d.error||'Could not start.'; return; }
  _abShow(d.id, form.closest('.diff-overlay'));
}
async function _abShow(id, o){
  if(!o||!document.body.contains(o)) return;
  clearTimeout(o._abTimer);
  let c=null;
  try{ c=await fetch('/ab/get?id='+encodeURIComponent(id)).then(r=>r.json()); }catch(e){}
  if(!document.body.contains(o)) return;
  if(c&&c.error) c=null;
  o._ab=c;
  o.querySelector('.chg-body').innerHTML=_abResultHtml(c, Date.now()/1000);
  if(c&&c.state==='running') o._abTimer=setTimeout(()=>_abShow(id, o), 2000);
}
function _abRun(id, label){
  const o=[...document.querySelectorAll('.ab-box')].map(b=>b.closest('.diff-overlay')).find(x=>x&&x._ab&&x._ab.id===id);
  return o?{o, r:(o._ab.runs||[]).find(r=>r.label===label)}:{};
}
function _abDiff(id, label){
  const {r}=_abRun(id, label); if(!r) return;
  _overlay(`Run ${label} · ${_AB_MODEL_NAMES[r.model]||r.model}`, _diffLinesHtml(r.diff||'')+(r.diff_truncated?'\n\n'+escHtml('… diff cut — see the rest in '+(r.wt||'the worktree')):''));
}
function _abAnswer(id, label){
  const {r}=_abRun(id, label); if(!r) return;
  _overlay(`Run ${label} · ${_AB_MODEL_NAMES[r.model]||r.model} — final answer`, `<div class="ab-final">${escHtml(r.final||'')}</div>`, 'chg-box');
}
async function _abKeep(id, label){
  const {o, r}=_abRun(id, label); if(!r) return;
  if(!confirm(`Keep run ${label} (${_AB_MODEL_NAMES[r.model]||r.model})? Its changes get committed and merged into your current branch; the other copy is deleted.`)) return;
  const d=await fetch('/ab/keep',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,label})}).then(r=>r.json()).catch(()=>({ok:false,error:'AOC did not answer'}));
  if(d.ok) showToast('done','A/B: run '+label+' merged',String(d.merged||'').slice(0,7));
  else showToast('error','A/B: not merged',d.error||'');
  _abShow(id, o);
}
async function _abDiscard(id){
  const o=[...document.querySelectorAll('.ab-box')].map(b=>b.closest('.diff-overlay')).find(x=>x&&x._ab&&x._ab.id===id);
  if(!confirm('Discard both runs? Their copies and branches are deleted.')) return;
  await fetch('/ab/discard',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})}).catch(()=>{});
  if(o) _abShow(id, o);
}
function _launchPick(btn){
  const f=btn.closest('form');
  f.querySelector('#ln-cwd').value=btn.dataset.path;
  f.querySelectorAll('.ln-chip').forEach(b=>b.classList.toggle('on', b===btn));
  f.querySelector('#ln-prompt').focus();
}
function _launchWhen(form){
  const w=form.querySelector('#ln-when').value, at=form.querySelector('#ln-at');
  at.style.display=w==='at'?'':'none';
  form.querySelector('#ln-at-lim').style.display=w==='at'?'':'none';
  if(w==='at'&&!at.value){
    const d=new Date(Date.now()+3600e3); d.setMinutes(0,0,0);
    at.value=new Date(d.getTime()-d.getTimezoneOffset()*60e3).toISOString().slice(0,16);
  }
  const wt=+((form.querySelector('#ln-wt')||{}).value||0);
  const note=form.querySelector('#ln-wt-note'); if(note) note.style.display=wt?'':'none';
  form.querySelector('.ln-go').textContent=_launchGoLabel(w, wt);
}
/* Pure: the POST /queue body for the form's "Start" choice, or {error}. */
function _queueBody(cwd, prompt, model, when, atValue, atLimits){
  if(!String(prompt||'').trim()) return {error:'A queued task needs a first message.'};
  if(when==='limits') return {cwd, prompt, model, at:null, limits:true};
  const t=Date.parse(atValue||'');
  if(!isFinite(t)) return {error:'Pick a start time.'};
  return {cwd, prompt, model, at:Math.round(t/1000), limits:!!atLimits};
}
async function _launchSubmit(form){
  const o=form.closest('.diff-overlay');
  const machine=(o&&o.dataset.machine)||'';
  const err=form.querySelector('#ln-err');
  const go=form.querySelector('.ln-go');
  const when=form.querySelector('#ln-when').value;
  const vals={}; form.querySelectorAll('#ln-vars input[data-var]').forEach(i=>vals[i.dataset.var]=i.value);
  const filled=_fillPrompt(form.querySelector('#ln-prompt').value, vals, new Date());
  if(filled.missing.length){ err.textContent='Fill in: '+filled.missing.join(', '); return; }
  const tplId=form.querySelector('#ln-tpl').value;
  const countUse=()=>{ if(tplId) fetch(_apiUrl(_machineFor(machine),'/prompts/used'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:tplId})}).catch(()=>{}); };
  const wt=machine?0:+((form.querySelector('#ln-wt')||{}).value||0);
  if(wt){
    if(when!=='now'){ err.textContent='Worktree sessions start right away — pick Start: Now.'; return; }
    go.disabled=true; err.textContent='';
    try{
      const d=await fetch('/worktrees/launch',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({cwd:form.querySelector('#ln-cwd').value, prompt:filled.text, model:form.querySelector('#ln-model').value, count:wt})}).then(r=>r.json());
      if(d.ok){ countUse(); o.remove(); _wtAt=0;
        showToast('done', d.items.length>1?d.items.length+' sessions started':'Session started',
          (d.items.length>1?'Each in its own worktree: ':'Branch ')+d.items.map(w=>w.branch).join(', ')+(d.trusted===false?' · accept "trust this folder" in each new window first.':''), d.trusted===false?12000:6000); }
      else err.textContent=d.error||'Could not start them.';
    }catch(e){ err.textContent='Could not reach AOC.'; }
    go.disabled=false;
    return;
  }
  if(when!=='now'){
    const body=_queueBody(form.querySelector('#ln-cwd').value, filled.text, form.querySelector('#ln-model').value,
      when, form.querySelector('#ln-at').value, form.querySelector('#ln-at-limits').checked);
    if(body.error){ err.textContent=body.error; return; }
    go.disabled=true; err.textContent='';
    try{
      const d=await fetch(_apiUrl(_machineFor(machine),'/queue'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(r=>r.json());
      if(d.ok){ countUse(); o.remove(); showToast('done','Queued',machine?'It starts on '+machine+' when it is due.':'It shows under Queued on Today.'); }
      else err.textContent=d.error||'Could not queue it.';
    }catch(e){ err.textContent='Could not reach AOC.'; }
    go.disabled=false;
    return;
  }
  go.disabled=true; err.textContent='';
  try{
    const d=await fetch(_apiUrl(_machineFor(machine),'/launch'),{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({cwd:form.querySelector('#ln-cwd').value, prompt:filled.text, model:form.querySelector('#ln-model').value})}).then(r=>r.json());
    if(d.ok){ countUse(); o.remove(); showToast('done','Session started', d.trusted===false?'New folder: accept "trust this folder" in the new window first.':'It shows up here in a few seconds.', d.trusted===false?12000:5000); }
    else err.textContent=d.error||'Could not start it.';
  }catch(e){ err.textContent='Could not reach AOC.'; }
  go.disabled=false;
}
/* ── History -> MODELS ── */
let _adviceDays=30;
/* Pure: the MODELS tab from GET /model_advice. */
function _modelAdviceHtml(d){
  d=d||{};
  if(d.error) return `<div class="conv-empty">The model report failed — see Health → background errors.</div>`;
  if(!d.total) return `<div class="conv-empty">No Claude Code usage in this period.</div>`;
  const money=v=>'$'+(Number(v)||0).toFixed(2);
  const kpi=(v,l,tip)=>`<div class="hist-kpi" title="${escHtml(tip)}"><div class="hk-val">${v}</div><div class="hk-lbl">${l}</div></div>`;
  const best=(d.tips||[]).reduce((a,t)=>a+(t.saving_month||0),0);
  const top=(d.by_model||[])[0]||{};
  const kpis=[
    kpi(money(d.total),'Spent',`Claude Code API-price spend in the last ${d.days} days`),
    kpi(`${escHtml(top.label||'—')} ${Math.round(top.share||0)}%`,'Main model','Model with the biggest share of the spend'),
    kpi(best?'~'+money(best):'—','Could save / month','Sum of the suggestions below, scaled to 30 days. An estimate: same tokens, cheaper prices.'),
  ].join('');
  const md=t=>escHtml(t).replace(/`([^`]+)`/g,'<code>$1</code>');
  const tips=(d.tips||[]).map(t=>`<div class="adv-tip"><div class="adv-tip-head"><b>${escHtml(t.title)}</b>${t.saving_month?`<span class="adv-save">~${money(t.saving_month)} / month</span>`:''}</div><div class="adv-tip-body">${md(t.detail)}</div></div>`).join('');
  const kindLabel={explore:'Reading & searching',edit:'Editing files',shell:'Running commands',delegate:'Delegating to subagents',text:'Writing answers',other:'Other tools'};
  const max=Math.max(...d.rows.map(r=>r.cost),0.0001);
  const rows=d.rows.filter(r=>r.cost>=0.01).map(r=>{
    const cheap=r.label.startsWith('Haiku');
    const same=r.label.startsWith('Sonnet');
    return `<tr><td class="cache-name">${escHtml(kindLabel[r.kind]||r.kind)}${r.where==='subagent'?'<span class="cc-repo">subagent</span>':''}</td>
      <td>${escHtml(r.label)}</td><td class="num cache-opt">${r.calls.toLocaleString()}</td>
      <td class="num cc-cost"><span class="cc-bar"><span style="width:${(r.cost/max*100).toFixed(1)}%"></span></span>${money(r.cost)}</td>
      <td class="num">${same||cheap?'<span class="adv-dim">—</span>':money(r.on_sonnet)}</td>
      <td class="num cache-opt">${cheap?'<span class="adv-dim">—</span>':money(r.on_haiku)}</td></tr>`;
  }).join('');
  return `<div class="hist-analytics">${kpis}</div>
    <div class="adv-tips">${tips}</div>
    <div class="adv-table"><table class="cache-table"><thead><tr><th>Work</th><th>Model</th><th class="num cache-opt">Calls</th><th class="num">Cost</th><th class="num">On Sonnet</th><th class="num cache-opt">On Haiku</th></tr></thead><tbody>${rows}</tbody></table></div>
    <div class="cl-help" style="margin-top:8px">Each API call is sorted by the tools it used and re-priced with the same tokens. Another model would use a different number of tokens, so treat these as estimates.</div>`;
}
