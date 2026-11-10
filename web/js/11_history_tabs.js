/* ── One-click fixes on CONTEXT / FAILURES tips ── */
let _fixesApplied={};
const _fixLocal=()=>['127.0.0.1','localhost','[::1]'].includes(location.hostname);
async function _fixesLoad(){
  try{ const d=await fetch('/fixes').then(r=>r.json()); _fixesApplied={}; (d.applied||[]).forEach(r=>{ _fixesApplied[r.key]=r; }); }
  catch(e){ /* keep the last known state */ }
}
/* Pure: the button on a tip -- Fix… / ✓ Fixed + Undo / manual steps / local-only hint. */
function _fixBtnHtml(fix, applied, local, labels){
  if(!fix||!fix.kind) return '';
  const L=Object.assign({go:'Fix…',done:'✓ Fixed'},labels||{});
  if(fix.kind==='manual') return `<button class="adp-btn fx-btn" onclick="_fixManual(${jsq(fix.steps||'')})" title="This one can't be changed from this PC">How to turn off</button>`;
  const rec=(applied||{})[fix.kind+':'+fix.target];
  if(rec) return `<span class="fx-done" title="${escHtml(rec.title||'')}">${L.done}</span><button class="adp-btn fx-btn" onclick="_fixUndo(${jsq(rec.id)})" title="Put back what this fix changed">Undo</button>`;
  if(!local) return `<span class="fx-dim" title="Fixes change Claude Code's config files, so they run only from the dashboard on this PC">Fix on the PC</span>`;
  return `<button class="adp-btn fx-btn fx-go" onclick="_fixOpen(${jsq(JSON.stringify({kind:fix.kind,target:fix.target}))})" title="Preview the change, then apply it (backup first, undo any time)">${L.go}</button>`;
}
/* Pure: a fix preview -- what changes in which file. */
function _fixPlanHtml(p){
  if(!p||!p.ok) return `<div class="chg-empty">${escHtml((p&&p.error)||'Could not prepare the fix.')}</div>`;
  const val=v=>v===undefined||v===null?'(not set)':JSON.stringify(v);
  const rows=(p.changes||[]).map(c=>{
    if(c.op==='line_add') return `<li><b>CLAUDE.md</b> + <code>${escHtml(c.text)}</code></li>`;
    const where=c.file==='claude_json'?`projects["${escHtml(c.path[1])}"].disabledMcpServers`:escHtml(c.path.join('.'));
    if(c.op==='list_remove') return `<li><code>${where}</code>: remove <code>${escHtml(c.item)}</code></li>`;
    if(c.op==='list_add') return `<li><code>${where}</code>: ${escHtml(val(c.before))} → add <code>${escHtml(c.item)}</code></li>`;
    return `<li><code>${where}</code>: ${escHtml(val(c.before))} → <code>${escHtml(val(c.after))}</code></li>`;
  }).join('');
  return `<div class="fx-plan"><div class="fx-title">${escHtml(p.title)}</div>
    <div class="fx-files">${(p.files||[]).map(f=>`<code>${escHtml(f)}</code>`).join(' ')}</div>
    <ul class="fx-list">${rows}</ul>
    <div class="cl-help">AOC backs the file up first; Undo puts it back. Takes effect in Claude Code sessions you start after this.</div>
    <div class="fx-actions"><button class="adp-btn" onclick="this.closest('.diff-overlay').remove()">Cancel</button><button class="adp-btn cl-save" onclick="_fixApply(this)">Apply</button></div></div>`;
}
let _fixPending=null;
async function _fixOpen(fixJson){
  _fixPending=JSON.parse(fixJson);
  const o=_overlay({allow:'Allow a rule',unallow:'Remove a rule'}[_fixPending.kind]||'Fix', '<div class="chg-empty">Checking…</div>', 'chg-box fx-box');
  let p; try{ p=await fetch('/fixes/preview',{method:'POST',body:JSON.stringify(_fixPending)}).then(r=>r.json()); }catch(e){ p={ok:false,error:'Could not reach AOC.'}; }
  o.querySelector('.chg-body').innerHTML=_fixPlanHtml(p);
}
async function _fixApply(btn){
  btn.disabled=true; btn.textContent='Applying…';
  let r; try{ r=await fetch('/fixes/apply',{method:'POST',body:JSON.stringify(_fixPending)}).then(x=>x.json()); }catch(e){ r={ok:false,error:'Could not reach AOC.'}; }
  const o=btn.closest('.diff-overlay'); if(o) o.remove();
  if(r.ok) showToast(r.stuck===false?'warning':'success', r.stuck===false?'Fixed, but a running session may undo it':'Fixed',
    r.stuck===false?'A Claude Code session rewrote ~/.claude.json. Close running sessions and apply the fix again.':'Takes effect in Claude Code sessions you start from now on.');
  else showToast('error','Fix not applied', r.error||'failed');
  await _fixesLoad(); renderHistory();
}
async function _fixUndo(id){
  let r; try{ r=await fetch('/fixes/undo',{method:'POST',body:JSON.stringify({id})}).then(x=>x.json()); }catch(e){ r={ok:false,error:'Could not reach AOC.'}; }
  if(r.ok) showToast('success','Undone', r.skipped?`${r.skipped} value(s) you changed by hand since were left as they are.`:'The change is back to how it was.');
  else showToast('error','Undo failed', r.error||'failed');
  await _fixesLoad(); renderHistory();
}
function _fixManual(steps){
  _overlay('How to turn it off', `<div class="fx-plan"><div class="adv-tip-body">${escHtml(steps)}</div></div>`, 'chg-box fx-box');
}
/* ── History -> CONTEXT ── */
let _ctxDays=30;
/* Pure: the CONTEXT tab from GET /context_report -- what every session
   carries before your first message, what re-reading it costs, and tips. */
function _contextReportHtml(d, applied, local){
  d=d||{};
  if(d.error) return `<div class="conv-empty">The context report failed — see Health → background errors.</div>`;
  if(!d.sessions) return `<div class="conv-empty">No Claude Code sessions in this period.</div>`;
  const money=v=>'$'+(Number(v)||0).toFixed(2);
  const tok=v=>'~'+(Number(v)||0).toLocaleString();
  const kpi=(v,l,tip)=>`<div class="hist-kpi" title="${escHtml(tip)}"><div class="hk-val">${v}</div><div class="hk-lbl">${l}</div></div>`;
  const best=(d.tips||[]).reduce((a,t)=>a+(t.saving_month||0),0);
  const kpis=[
    kpi(tok(d.avg_tokens),'Tokens at start',`Average size of a session's first prompt before your message (largest: ${(d.max_tokens||0).toLocaleString()})`),
    kpi(money(d.cost),'Startup cost',`Writing that context to the cache once per session and re-reading it on every later call (${d.avg_calls} calls per session on average), ${d.sessions} sessions in ${d.days} days`),
    kpi(best?'~'+money(best):'—','Could save / month','Sum of the suggestions below, scaled to 30 days'),
  ].join('');
  const md=t=>escHtml(t).replace(/`([^`]+)`/g,'<code>$1</code>');
  const tips=(d.tips||[]).map(t=>`<div class="adv-tip"><div class="adv-tip-head"><b>${escHtml(t.title)}</b>${t.saving_month?`<span class="adv-save">~${money(t.saving_month)} / month</span>`:''}${_fixBtnHtml(t.fix,applied,local)}</div><div class="adv-tip-body">${md(t.detail)}</div></div>`).join('');
  const partLabel={'built-in tools & rest':'Claude Code built-in tools & rest','skills':'Skills listing','deferred tool names':'Deferred tool names (mostly MCP)',
    'system prompt':'System prompt','MCP server instructions':'MCP server instructions','agent types':'Agent types','CLAUDE.md & memory':'CLAUDE.md & memory','environment':'Environment'};
  const bar=(v,max)=>`<span class="cc-bar"><span style="width:${(v/Math.max(max,1)*100).toFixed(1)}%"></span></span>`;
  const bmax=Math.max(...(d.breakdown||[]).map(b=>b.tokens),1);
  const parts=(d.breakdown||[]).map(b=>`<tr><td class="cache-name">${escHtml(partLabel[b.part]||b.part)}</td><td class="num cc-cost">${bar(b.tokens,bmax)}${tok(b.tokens)}</td><td class="num cache-opt">${Number(b.share).toFixed(0)}%</td></tr>`).join('');
  const used=n=>n?`<span>${n.toLocaleString()}×</span>`:'<span class="ctx-unused">never</span>';
  const table=(head,rows)=>rows?`<div class="adv-table"><table class="cache-table"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>`:'';
  const section=t=>`<div class="ctx-sec">${t}</div>`;
  const skills=(d.skills||[]).map(g=>`<tr><td class="cache-name">${escHtml(g.plugin==='(no plugin)'?'Built-in & personal':g.plugin)}</td><td class="num cache-opt">${g.skills}</td><td class="num">${tok(g.tokens)}</td><td class="num">${used(g.uses)}</td><td class="num">${money(g.cost)}</td></tr>`).join('');
  const mcp=(d.mcp||[]).filter(g=>g.cost>=0.01).map(g=>`<tr><td class="cache-name">${escHtml(g.server)}</td><td class="num">${tok(g.tokens)}</td><td class="num">${used(g.uses)}</td><td class="num">${money(g.cost)}</td></tr>`).join('');
  const base=p=>String(p).split(/[\\/]/).slice(-3).join('/');
  const files=(d.files||[]).filter(f=>f.tokens>=20).map(f=>`<tr><td class="cache-name" title="${escHtml(f.path)}">${escHtml(base(f.path))}${f.kind?`<span class="cc-repo">${escHtml(f.kind)}</span>`:''}</td><td class="num">${tok(f.tokens)}</td><td class="num cache-opt">${f.sessions}</td><td class="num">${money(f.cost)}</td></tr>`).join('');
  const projects=(d.projects||[]).map(p=>`<tr><td class="cache-name">${escHtml(p.project)}</td><td class="num cache-opt">${p.sessions}</td><td class="num">${tok(p.avg_tokens)}</td><td class="num cache-opt">${p.calls.toLocaleString()}</td><td class="num">${money(p.cost)}</td></tr>`).join('');
  return `<div class="ctx-wrap"><div class="hist-analytics">${kpis}</div>
    <div class="adv-tips">${tips}</div>
    ${section('What a session starts with')}${table('<th>Part</th><th class="num">Tokens</th><th class="num cache-opt">Share</th>',parts)}
    ${section('Skills by plugin')}${table('<th>Plugin</th><th class="num cache-opt">Skills</th><th class="num">Tokens</th><th class="num">Used</th><th class="num">Cost</th>',skills)}
    ${mcp?section('MCP servers')+table('<th>Server</th><th class="num">Tokens</th><th class="num">Tool calls</th><th class="num">Cost</th>',mcp):''}
    ${files?section('CLAUDE.md & memory files')+table('<th>File</th><th class="num">Tokens</th><th class="num cache-opt">Sessions</th><th class="num">Cost</th>',files):''}
    ${section('By project')}${table('<th>Project</th><th class="num cache-opt">Sessions</th><th class="num">At start</th><th class="num cache-opt">Calls</th><th class="num">Cost</th>',projects)}
    <div class="cl-help" style="margin-top:8px">Sizes come from your transcripts (about 3.7 characters per token); the rest of the first prompt is Claude Code's own tool definitions. Cost = the startup context written to the cache once per session plus re-read on every later API call, at API prices.</div></div>`;
}
/* ── History -> MEMORY: CLAUDE.md & memory files, lint + editor ── */
let _memSel='', _memData=null, _memDoc=null, _memMsg='';   // _memDoc: {path, kind, text, mtime, draft}
let _memArch=null;   // the open ARCHIVE OLD preview (GET /memory_archive)
const _memKindLabel={user:'Your CLAUDE.md', project:'Project CLAUDE.md', local:'CLAUDE.local.md', index:'MEMORY.md index', memory:'Memory'};
/* Pure: which group a file is listed under. Memory folders are named after
   the encoded project path ('C--Users-me-Projekty-AOC-Agent-Monitoring');
   the user prefix goes and the last three parts stay. */
function _memGroupLabel(f){
  const parts=String(f.path||'').split(/[\\/]/).filter(Boolean);
  if(f.kind==='user') return 'Every session';
  if(f.kind==='project'||f.kind==='local'){
    const i=parts.length-(parts[parts.length-2]==='.claude'?3:2);
    return 'Project · '+(parts[i]||'?');
  }
  const enc=(parts[parts.length-3]||'').replace(/^[A-Za-z]--Users-[^-]+-?/,'');
  return 'Memory · '+(enc?enc.split('-').filter(Boolean).slice(-3).join('-'):'home folder');
}
/* Pure: the file to open first -- the one with the most errors, then
   warnings, preferring files that load into every session. */
function _memPickFile(files){
  const score=f=>{ const n=s=>(f.issues||[]).filter(x=>x.sev===s).length; return n('error')*1000+n('warn')*10+(f.kind==='memory'?0:5); };
  return (files||[]).slice().sort((a,b)=>score(b)-score(a))[0];
}
const _memTok=n=>'~'+Math.round(n).toLocaleString();
/* Pure: the file list, grouped. */
function _memListHtml(files, sel){
  const groups=new Map();
  (files||[]).forEach(f=>{ const g=_memGroupLabel(f); if(!groups.has(g)) groups.set(g,[]); groups.get(g).push(f); });
  return [...groups].map(([g,list])=>`<div class="mem-grp">${escHtml(g)}</div>`+list.map(f=>{
    const n=s=>(f.issues||[]).filter(x=>x.sev===s).length, e=n('error'), w=n('warn');
    const name=String(f.path).split(/[\\/]/).pop();
    return `<div class="mem-file${f.path===sel?' active':''}" onclick="_memOpen(${jsq(f.path)})" title="${escHtml(f.path)}">
      <span class="mem-fname">${escHtml(name)}</span><span class="mem-ftok">${_memTok(f.tokens)}</span>
      ${e?`<span class="mem-badge err">${e}</span>`:''}${w?`<span class="mem-badge warn">${w}</span>`:''}</div>`;
  }).join('')).join('');
}
/* Pure: a unified diff, coloured line by line (long ones cut for the DOM). */
function _memDiffHtml(diff, maxLines){
  const lines=String(diff||'').split('\n'), cap=maxLines||2500;
  const cls=l=>l.startsWith('@@')?'hunk':l.startsWith('+++')||l.startsWith('---')?'file':l.startsWith('+')?'add':l.startsWith('-')?'del':'';
  return lines.slice(0,cap).map(l=>`<span class="md-${cls(l)||'ctx'}">${escHtml(l)||' '}</span>`).join('\n')
    +(lines.length>cap?`\n<span class="md-hunk">… ${(lines.length-cap).toLocaleString()} more diff lines</span>`:'');
}
/* Pure: ARCHIVE OLD's preview -- what moves where, the diff, apply/cancel. */
function _memArchiveHtml(p){
  if(!p) return '';
  const name=String(p.path||'').split(/[\\/]/).pop(), aname=String(p.archive_path||'').split(/[\\/]/).pop();
  const moved=p.moved||[], show=moved.slice(0,40);
  return `<div class="mem-arch">
    <div class="mem-arch-head"><b>Archive old entries</b>
      <span>${_memTok(p.before_tokens||0)} → ${_memTok(p.after_tokens||0)} tokens in ${escHtml(name)} · ${moved.length} ${moved.length===1?'entry':'entries'} (${_memTok(p.moved_tokens||0)} tokens) go to <code>${escHtml(aname)}</code>${p.archive_exists?' (added at its end)':' (new file)'}</span></div>
    <ol class="mem-arch-list">${show.map(m=>`<li><span class="mem-ln">L${Number(m.line)||0}</span><span class="mem-arch-t">${escHtml(m.title)}</span><span class="mem-ftok">${_memTok(m.tokens||0)}</span></li>`).join('')}</ol>
    ${moved.length>show.length?`<div class="mem-arch-more">… and ${moved.length-show.length} more</div>`:''}
    <details class="mem-arch-diff"><summary>Changes to ${escHtml(name)}${p.link_added?' (plus a link to the archive)':''}</summary><pre class="mem-diff">${_memDiffHtml(p.diff)}${p.diff_cut?'\n<span class="md-hunk">… diff cut</span>':''}</pre></details>
    <div class="mem-arch-btns"><button class="adp-btn hh-fix" onclick="_memArchiveApply()">MOVE ${moved.length} ${moved.length===1?'ENTRY':'ENTRIES'}</button>
      <button class="adp-btn" onclick="_memArchiveCancel()">CANCEL</button></div>
    <small class="mem-arch-note">The frontmatter, the intro, headings, every RESUME HERE entry and the newest history stay. UNDO SAVE puts both files back.</small>
  </div>`;
}
/* Pure: the editor pane for the open file. */
function _memEditorHtml(doc, file, local, msg, arch){
  if(!doc) return `<div class="conv-empty">Pick a file on the left.</div>`;
  const text=doc.draft!=null?doc.draft:doc.text, dirty=doc.draft!=null&&doc.draft!==doc.text;
  const issues=(file&&file.issues)||[];
  const loaded=['user','project','local','index'].includes(doc.kind);
  const sevIcon={error:'✕',warn:'!',info:'i'};
  const list=issues.length?issues.map(x=>`<div class="mem-issue ${x.sev}"${x.line?` onclick="_memJump(${x.line})"`:''}><span class="mem-sev">${sevIcon[x.sev]||'·'}</span>${x.line?`<span class="mem-ln">L${x.line}</span>`:''}<span>${escHtml(x.msg)}</span></div>`).join('')
    :`<div class="mem-issue ok"><span class="mem-sev">✓</span><span>Nothing to fix in the saved version.</span></div>`;
  const backups=(file&&file.backups)||0;
  return `<div class="mem-head"><div><b>${escHtml(String(doc.path).split(/[\\/]/).pop())}</b><span class="mem-kind">${escHtml(_memKindLabel[doc.kind]||doc.kind)}${loaded?' · loaded at session start':' · read when Claude needs it'}</span>
      <small class="mem-path">${escHtml(doc.path)}</small></div>
    <div class="mem-actions"><span class="mem-live" id="mem-live">${_memTok(text.length/3.7)} tokens${dirty?' · unsaved':''}</span>
      ${local?`<button class="adp-btn" onclick="_memSave()" ${dirty?'':'disabled'}>SAVE</button>
      <button class="adp-btn" onclick="_memRevert()" title="Throw away unsaved changes and re-read the file from disk">REVERT</button>
      <button class="adp-btn" onclick="_memUndo()" ${backups?'':'disabled'} title="Put back the version before AOC's last save (${backups} kept)">UNDO SAVE</button>
      ${file&&file.archive&&doc.kind==='memory'?`<button class="adp-btn" onclick="_memArchivePreview()" ${dirty?'disabled':''} title="Move ${Number(file.archive.entries)||0} older dated entries (${_memTok(file.archive.tokens||0)} tokens) into ${escHtml(file.archive.file||'')} -- you see a preview first">ARCHIVE OLD</button>`:''}`
      :`<span class="mem-ro">Read-only here: editing works on the PC running AOC.</span>`}</div></div>
    ${msg?`<div class="mem-msg">${escHtml(msg)}</div>`:''}
    ${local&&arch&&arch.path===doc.path?_memArchiveHtml(arch):''}
    <div class="mem-issues">${list}</div>
    <textarea id="mem-text" class="mem-text" spellcheck="false" ${local?'':'readonly'} oninput="_memInput(this)">${escHtml(text)}</textarea>`;
}
/* Pure: the whole tab. */
function _memHtml(d, sel, doc, local, msg, arch){
  d=d||{};
  if(d.error) return `<div class="conv-empty">Reading the memory files failed — see Health → background errors.</div>`;
  if(!(d.files||[]).length) return `<div class="conv-empty">No CLAUDE.md or memory files found yet.</div>`;
  const c=d.counts||{};
  const kpi=(v,l,tip,cls)=>`<div class="hist-kpi${cls?' '+cls:''}" title="${escHtml(tip)}"><div class="hk-val">${v}</div><div class="hk-lbl">${l}</div></div>`;
  const file=(d.files||[]).find(f=>f.path===sel);
  return `<div class="mem-wrap"><div class="hist-analytics">
      ${kpi(d.files.length,'Files','CLAUDE.md files of the folders Claude Code worked in, plus every auto-memory folder')}
      ${kpi(_memTok(d.start_tokens||0),'Tokens in every start','Your CLAUDE.md plus the MEMORY.md indexes, read before your first message (a project\'s own CLAUDE.md adds to it there)')}
      ${kpi(c.error||0,'Errors','Links to files that don\'t exist',c.error?'bad':'')}
      ${kpi(c.warn||0,'Warnings','Oversized files, long index lines, dead paths, unlinked memories, repeated lines',c.warn?'warn':'')}
    </div>
    <div class="mem-split"><div class="mem-list">${_memListHtml(d.files,sel)}</div><div class="mem-ed">${_memEditorHtml(doc,file,local,msg,arch)}</div></div></div>`;
}
function _memDirty(){ return !!(_memDoc&&_memDoc.draft!=null&&_memDoc.draft!==_memDoc.text); }
function _memPaint(){
  const body=document.getElementById('hist-body');
  if(!body||_histTab!=='memory') return;
  const ta=document.getElementById('mem-text'), pos=ta?[ta.selectionStart,ta.selectionEnd,ta.scrollTop]:null;
  body.innerHTML=_memHtml(_memData,_memSel,_memDoc,_fixLocal(),_memMsg,_memArch);
  const nt=document.getElementById('mem-text');
  if(nt&&pos&&_memDoc&&_memDoc.path===_memSel){ nt.setSelectionRange(pos[0],pos[1]); nt.scrollTop=pos[2]; }
}
async function _memFetchDoc(path){
  const r=await fetch('/memory_file?path='+encodeURIComponent(path));
  if(!r.ok) throw new Error('not found');
  const d=await r.json();
  _memDoc={path:d.path, kind:d.kind, text:d.text, mtime:d.mtime, draft:null};
}
async function _memLoad(){
  _memData=await fetch('/memory_lint').then(r=>r.json());
  const files=_memData.files||[];
  if(!files.some(f=>f.path===_memSel)){ const f=_memPickFile(files); _memSel=f?f.path:''; }
  if(_memSel&&(!_memDoc||_memDoc.path!==_memSel)) await _memFetchDoc(_memSel);
}
async function _memOpen(path){
  if(path===_memSel) return;
  if(_memDirty()&&!confirm('Discard your unsaved changes to '+String(_memDoc.path).split(/[\\/]/).pop()+'?')) return;
  _memSel=path; _memMsg=''; _memArch=null;
  try{ await _memFetchDoc(path); }catch(e){ _memMsg='Could not open that file.'; }
  _memPaint();
}
function _memInput(el){
  if(!_memDoc) return;
  _memDoc.draft=el.value;
  const live=document.getElementById('mem-live');
  if(live) live.textContent=_memTok(el.value.length/3.7)+' tokens'+(_memDirty()?' · unsaved':'');
  const save=document.querySelector('.mem-actions .adp-btn'); if(save) save.disabled=!_memDirty();
}
/* Drop the draft and re-read the file from disk (also how you pick up a
   change Claude made while you were editing). */
async function _memRevert(){
  if(!_memDoc) return;
  _memArch=null;
  try{ await _memLoad(); await _memFetchDoc(_memSel); _memMsg=''; }catch(e){ _memMsg='Could not re-read the file.'; }
  _memPaint();
}
/* Select line n (1-based) of the editor and scroll it into view. */
function _memJump(n){
  const ta=document.getElementById('mem-text');
  if(!ta) return;
  const lines=ta.value.split('\n'); let a=0;
  for(let i=0;i<Math.min(n-1,lines.length);i++) a+=lines[i].length+1;
  const b=a+((lines[n-1]||'').length);
  ta.focus(); ta.setSelectionRange(a,b);
  ta.scrollTop=Math.max(0,(n-3)*parseFloat(getComputedStyle(ta).lineHeight||'16'));
}
async function _memSave(){
  if(!_memDirty()) return;
  _memArch=null;
  let r; try{ r=await fetch('/memory_file',{method:'POST',body:JSON.stringify({path:_memDoc.path,text:_memDoc.draft,mtime:_memDoc.mtime})}).then(x=>x.json()); }catch(e){ r={ok:false,error:'Could not reach AOC.'}; }
  if(!r.ok){ _memMsg=r.error==='changed'?r.detail:(r.error||'Saving failed.'); _memPaint(); return; }
  const draft=_memDoc.draft;
  _memMsg='Saved. The next session that starts picks it up.';
  await _memLoad(); await _memFetchDoc(_memSel);
  if(_memDoc.text!==draft.replace(/\r\n/g,'\n')) _memMsg='Saved, but the file has changed again since.';
  _memPaint();
}
async function _memUndo(){
  if(!_memDoc||!confirm('Put back the version from before the last save made in AOC?')) return;
  let r; try{ r=await fetch('/memory_file/undo',{method:'POST',body:JSON.stringify({path:_memDoc.path})}).then(x=>x.json()); }catch(e){ r={ok:false,error:'Could not reach AOC.'}; }
  _memMsg=r.ok?'Previous version restored.':(r.error||'Undo failed.');
  if(r.ok){ _memArch=null; await _memLoad(); await _memFetchDoc(_memSel); }
  _memPaint();
}
async function _memArchivePreview(){
  if(!_memDoc||_memDirty()) return;
  let r; try{ r=await fetch('/memory_archive?path='+encodeURIComponent(_memDoc.path)).then(x=>x.json()); }catch(e){ r={ok:false,error:'Could not reach AOC.'}; }
  _memArch=r.ok?r:null; _memMsg=r.ok?'':(r.error||'No preview.');
  _memPaint();
}
function _memArchiveCancel(){ _memArch=null; _memPaint(); }
async function _memArchiveApply(){
  const p=_memArch; if(!p) return;
  if(_memDirty()){ _memMsg='Save or revert your edit first -- archiving works on the saved file.'; _memPaint(); return; }
  let r; try{ r=await fetch('/memory_archive',{method:'POST',body:JSON.stringify({path:p.path,mtime:p.mtime,archive_mtime:p.archive_mtime})}).then(x=>x.json()); }catch(e){ r={ok:false,error:'Could not reach AOC.'}; }
  _memArch=null;
  _memMsg=r.ok?`Moved ${r.moved} ${r.moved===1?'entry':'entries'} (${_memTok(r.moved_tokens||0)} tokens) to ${String(r.archive_path||'').split(/[\\/]/).pop()}. UNDO SAVE puts both files back.`
    :r.error==='changed'?r.detail:(r.error||'Archiving failed.');
  try{ await _memLoad(); await _memFetchDoc(_memSel); }catch(e){}
  _memPaint();
}
/* ── History -> FAILURES ── */
let _failDays=30;
/* Pure: 'mcp__claude_ai_Gmail__create_draft' -> 'Gmail · create_draft'. */
function _failToolName(t){
  const m=/^mcp__(.+?)__(.+)$/.exec(String(t||''));
  if(!m) return String(t||'?');
  const server=m[1].replace(/^claude_ai_/,'').replace(/^plugin_[^_]+_/,'').replace(/_/g,' ');
  return server+' · '+m[2];
}
/* Pure: the FAILURES tab from GET /failure_map -- which tool calls fail most,
   why, what that lost (time + the next turn's cost), and a replay link to the
   latest case. */
function _failureMapHtml(d, applied, local){
  d=d||{};
  if(d.error) return `<div class="conv-empty">The failure map failed — see Health → background errors.</div>`;
  if(!d.calls) return `<div class="conv-empty">No tool calls in this period.</div>`;
  const money=v=>'$'+(Number(v)||0).toFixed(2);
  const dur=s=>{ s=Math.round(Number(s)||0); return s<60?s+'s':s<3600?Math.round(s/60)+' min':(s/3600).toFixed(1)+' h'; };
  const kpi=(v,l,tip)=>`<div class="hist-kpi" title="${escHtml(tip)}"><div class="hk-val">${v}</div><div class="hk-lbl">${l}</div></div>`;
  const kpis=[
    kpi(`${(d.failed||0).toLocaleString()} <span class="fm-rate">${d.rate}%</span>`,'Failed calls',`${(d.failed||0).toLocaleString()} of ${d.calls.toLocaleString()} tool calls came back as an error, in ${d.sessions} sessions`),
    kpi(dur(d.secs),'Time lost','The failed calls\' run time plus Claude\'s next turn reacting to them (idle gaps over 10 min left out)'),
    kpi(money(d.cost),'Cost of reacting','What the API calls that answered the failures cost'),
    kpi((d.denied||0).toLocaleString(),'Denied','Calls you, a hook or auto mode said no to — decisions, not breakages (listed at the bottom)'),
  ].join('');
  if(!d.failed) return `<div class="hist-analytics">${kpis}</div><div class="conv-empty">No failed tool calls in this period.</div>`;
  const md=t=>escHtml(t).replace(/`([^`]+)`/g,'<code>$1</code>');
  const tips=(d.tips||[]).map(t=>`<div class="adv-tip"><div class="adv-tip-head"><b>${escHtml(`${t.reason} (${_failToolName(t.tool)}${t.target?' · '+t.target:''}): ${t.count}×`)}</b>${t.cost_month?`<span class="adv-save">~${money(t.cost_month)} / month</span>`:''}${_fixBtnHtml(t.fix,applied,local)}</div><div class="adv-tip-body">${md(t.detail)}</div></div>`).join('');
  const bar=(v,max)=>`<span class="cc-bar"><span style="width:${(v/Math.max(max,1)*100).toFixed(1)}%"></span></span>`;
  const tmax=Math.max(...(d.top||[]).map(g=>g.count),1);
  const replay=g=>g.last_sid?`<button class="chg-btn rp-btn" onclick="openReplay(${jsq(g.last_sid)},'',${jsq(g.last_label||g.last_sid.slice(0,8))},'errors')" title="Replay the latest session with this failure, failed steps only">▶</button>`:'';
  const top=(d.top||[]).map(g=>`<tr><td class="cache-name"><b>${escHtml(_failToolName(g.tool))}</b>${g.target?`<span class="cc-repo">${escHtml(g.target)}</span>`:''}
      <div class="fm-reason">${escHtml(g.reason)}</div>${g.example?`<div class="fm-ex" title="${escHtml(g.example)}">${escHtml(g.example)}</div>`:''}</td>
      <td class="num cc-cost">${bar(g.count,tmax)}${g.count}×</td><td class="num cache-opt">${g.sessions}</td>
      <td class="num">${dur(g.secs)}</td><td class="num cache-opt">${money(g.cost)}</td><td class="num">${replay(g)}</td></tr>`).join('');
  const small=(rows,label,name)=>{
    const max=Math.max(...rows.map(g=>g.count),1);
    return rows.map(g=>`<tr><td class="cache-name">${escHtml(name(g))}</td><td class="num cc-cost">${bar(g.count,max)}${g.count}×</td><td class="num">${dur(g.secs)}</td><td class="num cache-opt">${money(g.cost)}</td></tr>`).join('');
  };
  const table=(head,rows)=>rows?`<div class="adv-table"><table class="cache-table"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>`:'';
  const sec=t=>`<div class="ctx-sec">${t}</div>`;
  const head4=n=>`<th>${n}</th><th class="num">Failed</th><th class="num">Lost</th><th class="num cache-opt">Cost</th>`;
  const denied=(d.denied_rows||[]).map(g=>`<tr><td class="cache-name">${escHtml(g.reason)}</td><td class="num">${g.count}×</td><td class="num cache-opt">${g.sessions}</td><td class="num">${replay(g)}</td></tr>`).join('');
  return `<div class="ctx-wrap"><div class="hist-analytics">${kpis}</div>
    ${tips?`<div class="adv-tips">${tips}</div>`:''}
    ${sec('Most frequent failures')}${table('<th>Tool · what failed</th><th class="num">Times</th><th class="num cache-opt">Sessions</th><th class="num">Lost</th><th class="num cache-opt">Cost</th><th class="num">Replay</th>',top)}
    <div class="fm-cols"><div>${sec('By reason')}${table(head4('Reason'),small(d.by_reason||[],'Reason',g=>g.reason))}</div>
      <div>${sec('By tool')}${table(head4('Tool'),small(d.by_tool||[],'Tool',g=>_failToolName(g.tool)))}</div></div>
    ${denied?sec('Denied (not failures)')+table('<th>Why</th><th class="num">Times</th><th class="num cache-opt">Sessions</th><th class="num">Replay</th>',denied):''}
    <div class="cl-help" style="margin-top:8px">From your transcripts, main sessions and subagents. Lost = the failed call's run time plus Claude's next turn reacting to it, and that turn's cost; ▶ opens the latest case in Replay.</div></div>`;
}
/* ── History -> PERMISSIONS ── */
let _permDays=30;
/* Pure: the PERMISSIONS tab from GET /permission_advice -- safe allow rules
   for the prompts that hold you up, prompts that get no rule (and why), and
   broad risky rules already in your settings files. */
function _permAdviceHtml(d, applied, local){
  d=d||{};
  if(d.error) return `<div class="conv-empty">The permission advisor failed — see Health → background errors.</div>`;
  const dur=s=>{ s=Math.round(Number(s)||0); return !s?'—':s<60?s+'s':s<3600?Math.round(s/60)+' min':(s/3600).toFixed(1)+' h'; };
  const kpi=(v,l,tip)=>`<div class="hist-kpi" title="${escHtml(tip)}"><div class="hk-val">${v}</div><div class="hk-lbl">${l}</div></div>`;
  const since=d.log_since?new Date(d.log_since*1000).toLocaleDateString():'';
  const kpis=[
    kpi((d.prompts||0).toLocaleString(),'Prompts',d.log_since?`Permission prompts Claude Code showed you in the last ${d.days} days (logged since ${since})`:'Permission prompts are logged from now on'),
    kpi(dur(d.waited),'Held up','From each prompt until the call\'s result. Includes the command\'s own run time (capped at 30 min a prompt)'),
    kpi((d.denied||0).toLocaleString(),'Auto-mode denials','Calls auto mode refused or could not check (from your transcripts)'),
    kpi((d.saved||0).toLocaleString(),'Saved rules',`Allow rules in your settings files; ${(d.one_offs||0).toLocaleString()} of them are one-off exact commands from "Yes, don't ask again" that rarely match again`),
  ].join('');
  const notes=[];
  if(d.hook===false) notes.push('AOC\'s permission hook is not in your Claude Code settings, so new prompts are not logged. Run <code>python setup.py</code> in the AOC folder to add it.');
  else if(!d.log_since) notes.push('Prompts are logged from now on. Until then the suggestions come from auto-mode denials and the rules you saved with "Yes, don\'t ask again".');
  else if(Date.now()/1000-d.log_since<7*86400) notes.push(`Prompts are logged since ${escHtml(since)}, so the counts grow as you work.`);
  const tierChip=t=>t==='project'
    ?'<span class="pa-tier pa-project" title="Runs your project\'s own test/build/lint scripts. Claude can edit those scripts, so allow it only where you trust the project.">project scripts</span>'
    :'<span class="pa-tier pa-read" title="Only reads, changes nothing">read-only</span>';
  const n=v=>v?v.toLocaleString()+'×':'<span class="adv-dim">—</span>';
  const replay=g=>g.last_sid?`<button class="chg-btn rp-btn" onclick="openReplay(${jsq(g.last_sid)},'',${jsq(g.last_label||g.last_sid.slice(0,8))})" title="Replay the latest session with this prompt">▶</button>`:'';
  const table=(head,rows)=>rows?`<div class="adv-table"><table class="cache-table"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>`:'';
  const sec=t=>`<div class="ctx-sec">${t}</div>`;
  const btn=g=>{
    if(g.covered&&!(applied||{})['allow:'+g.rule]) return '<span class="fx-done" title="Already allowed in ~/.claude/settings.json">✓ Allowed</span>';
    return _fixBtnHtml({kind:'allow',target:g.rule},applied,local,{go:'Allow…',done:'✓ Allowed'});
  };
  const ex=g=>g.example?`<div class="fm-ex" title="${escHtml(g.example)}">${escHtml(g.example)}</div>`:'';
  const suggest=(d.suggest||[]).map(g=>`<tr><td class="cache-name"><span class="pa-rule">${escHtml(g.rule)}</span>${tierChip(g.tier)}${ex(g)}</td>
      <td class="num">${n(g.asked)}</td><td class="num cache-opt">${n(g.denied)}</td><td class="num cache-opt">${n(g.saved)}</td>
      <td class="num cache-opt">${dur(g.secs)}</td><td class="num">${replay(g)}</td><td class="num">${btn(g)}</td></tr>`).join('');
  const blocked=(d.blocked||[]).map(g=>`<tr><td class="cache-name"><b>${escHtml(g.family||g.tool)}</b><span class="cc-repo">${escHtml(_failToolName(g.tool))}</span>
      <div class="fm-reason pa-why">${escHtml(g.why||'')}</div>${ex(g)}</td>
      <td class="num">${n(g.asked+g.rejected)}</td><td class="num cache-opt">${n(g.denied)}</td><td class="num cache-opt">${n(g.saved)}</td><td class="num">${replay(g)}</td></tr>`).join('');
  const fileName=p=>String(p).split(/[\\/]/).slice(-3).join('/');
  const risky=(d.risky||[]).map(r=>{
    const fix=(r.file==='settings'||r.file==='settings_local')?{kind:'unallow',target:r.file+'|'+r.rule}:null;
    const act=fix?_fixBtnHtml(fix,applied,local,{go:'Remove…',done:'✓ Removed'}):'<span class="fx-dim" title="Edit this project\'s settings file by hand">by hand</span>';
    return `<tr><td class="cache-name"><span class="pa-rule">${escHtml(r.rule)}</span><div class="fm-reason pa-why">${escHtml(r.why)}</div></td>
      <td class="cache-opt" title="${escHtml(r.path)}">${escHtml(fileName(r.path))}</td><td class="num">${act}</td></tr>`;
  }).join('');
  const empty=!suggest&&!blocked&&!risky;
  return `<div class="ctx-wrap"><div class="hist-analytics">${kpis}</div>
    ${notes.map(t=>`<div class="pa-note">${t}</div>`).join('')}
    ${empty?'<div class="conv-empty">No permission prompts, denials or saved rules to go on yet.</div>':''}
    ${suggest?sec('Suggested rules')+table('<th>Rule</th><th class="num">Asked</th><th class="num cache-opt">Auto-mode</th><th class="num cache-opt">Saved</th><th class="num cache-opt">Held up</th><th class="num">Replay</th><th class="num"></th>',suggest):''}
    ${blocked?sec('Asked, but no rule suggested')+table('<th>Command · why not</th><th class="num">Asked</th><th class="num cache-opt">Auto-mode</th><th class="num cache-opt">Saved</th><th class="num">Replay</th>',blocked):''}
    ${risky?sec(`Broad rules you already have (${(d.risky||[]).length})`)+table('<th>Rule · what it lets through</th><th class="cache-opt">File</th><th class="num"></th>',risky):''}
    <div class="cl-help" style="margin-top:8px">AOC suggests only read-only commands, your project's own test/build/lint scripts, web domains, reading MCP tools, skills and folders to read. Never deleting or writing files, installing, network transfers, git changes, running code (python, node, powershell, npx…), system changes or deploys. Rules go to <code>~/.claude/settings.json</code> (every project) with a preview first, and Undo takes them out. "Saved" counts one-off rules from "Yes, don't ask again" that a rule would cover.</div></div>`;
}
/* ── History -> CLIENTS ── */
let _clientsMonth='', _clientsData=null, _clientsEdit=false;
/* Pure: the CLIENTS tab. d = GET /clients, edit = editor open, isPro =
   CSV export allowed, tok = auth token for download links (tunnel), lang =
   the page's language (the client report's labels follow it). */
function _clientsHtml(d, edit, isPro, tok, lang){
  d=d||{};
  const rep=d.report, cfg=d.config||{clients:[],overrides:{}};
  const money=v=>'$'+(Number(v)||0).toFixed(2);
  const hm=sec=>{ sec=Math.max(0,Number(sec)||0); return Math.floor(sec/3600)+':'+String(Math.floor(sec%3600/60)).padStart(2,'0'); };
  const q=u=>tok?u+(u.includes('?')?'&':'?')+'token='+encodeURIComponent(tok):u;
  if(d.error) return `<div class="conv-empty">The client report failed — see Health → background errors.</div>`;
  if(!rep) return `<div class="conv-empty">Still reading your Claude Code transcripts — the report appears once the index is built.</div>`;
  const monthName=m=>{ const [y,mo]=m.split('-').map(Number); return new Date(y,mo-1,1).toLocaleDateString(undefined,{month:'long',year:'numeric'}); };
  const months=(rep.months&&rep.months.length?rep.months:[rep.month]);
  if(!months.includes(rep.month)) months.unshift(rep.month);
  const named=rep.clients.filter(g=>g.name), un=rep.clients.find(g=>!g.name)||{sessions:[],cost:0};
  const assigned=named.reduce((a,g)=>a+g.cost,0);
  const opts=(cur)=>`<option value="">Auto (keywords)</option>${cfg.clients.map(c=>`<option value="${escHtml(c.name)}"${cur===c.name?' selected':''}>${escHtml(c.name)}</option>`).join('')}<option value="-"${cur==='-'?' selected':''}>No client</option>`;
  const sessRows=(g)=>g.sessions.map(x=>{
    const ov=cfg.overrides[x.id];
    const when=x.first_day===x.last_day?x.first_day.slice(5):x.first_day.slice(5)+' – '+x.last_day.slice(5);
    return `<tr><td class="cache-name"><span title="${escHtml(x.id)}">${escHtml(x.title)}</span>${x.repos.length?`<span class="cc-repo">${escHtml(x.repos.join(', '))}</span>`:''}</td>
      <td class="num cc-when">${escHtml(when)}</td><td class="num cache-opt">${x.commits||''}</td>
      <td class="num cl-time" title="Active time this month">${hm(x.active_s)}</td>
      <td class="num">${money(x.cost)}</td>
      <td><select class="cl-assign" onchange="_clientAssign(${jsq(x.id)},this.value)" title="${escHtml(x.how?'Matched by '+x.how:'Not matched')}">${opts(ov===undefined?'':ov)}</select></td></tr>`;
  }).join('');
  const table=(g)=>g.sessions.length?`<table class="cache-table cl-table"><thead><tr><th>Session</th><th class="num cc-when">Dates</th><th class="num cache-opt">Commits</th><th class="num cl-time">Time</th><th class="num">Cost</th><th>Client</th></tr></thead><tbody>${sessRows(g)}</tbody></table>`:'<div class="cl-none">No sessions this month.</div>';
  const exportBtns=(name)=>{
    const qs=`month=${encodeURIComponent(rep.month)}&client=${encodeURIComponent(name)}`;
    const forClient=name?`<a class="adp-btn cl-btn" href="${escHtml(q('/clients/report?'+qs+'&lang='+encodeURIComponent(lang||'en')))}" target="_blank" rel="noopener" title="For the client: worked time per project and what was done, no AI costs -- Save as PDF from the print dialog">⎙ Client report</a>`:'';
    const csv=isPro?`<a class="adp-btn cl-btn" href="${escHtml(q('/clients/export.csv?'+qs))}" title="One line per session, for your invoice">⬇ CSV</a>`
      :`<span class="cl-lock" title="CSV export is a Pro feature -- Settings -> LICENSE to upgrade">🔒 CSV (PRO)</span>`;
    return `${forClient}<a class="adp-btn cl-btn" href="${escHtml(q('/clients/print?'+qs))}" target="_blank" rel="noopener" title="Printable report with AI costs and time, for your records -- Save as PDF from the print dialog">⎙ Report</a>${csv}`;
  };
  const card=(g)=>`<section class="cl-card">
      <div class="cl-head"><div class="cl-name">${escHtml(g.name||'Unassigned')}</div>
        <div class="cl-nums"><span><b>${money(g.cost)}</b> cost</span>${g.markup?`<span>+${g.markup}% → <b>${money(g.billed)}</b> to bill</span>`:''}${g.active_s?`<span><b>${hm(g.active_s)}</b> h${g.rate?` → <b>${(Number(g.hours)||0).toFixed(2)} h</b> billed (rounded up to 0.25 h) × ${Number(g.rate).toFixed(2)} ${escHtml(g.currency||'')} = <b>${(Number(g.time_amount)||0).toFixed(2)} ${escHtml(g.currency||'')}</b>`:''}</span>`:''}<span>${g.sessions.length} session${g.sessions.length===1?'':'s'}</span>${g.commits?`<span>${g.commits} commit${g.commits===1?'':'s'}</span>`:''}</div>
        <div class="cl-actions">${g.sessions.length?exportBtns(g.name):''}</div></div>
      ${table(g)}</section>`;
  const editor=edit?`<section class="cl-card cl-editor"><div class="cl-head"><div class="cl-name">Clients</div></div>
      <div class="cl-help">A session goes to the first client with a keyword in its title, project, folder or the repos it committed to. Pick a client on a session row to override.</div>
      <div id="cl-rows">${(cfg.clients.length?cfg.clients:[{name:'',match:[],markup:0}]).map((c,i)=>_clientEditRow(c,i)).join('')}</div>
      <div class="cl-ed-actions"><button class="adp-btn" onclick="_clientAddRow()">+ Client</button><span style="flex:1"></span>
        <button class="adp-btn" onclick="_clientsEdit=false;renderHistory()">Cancel</button><button class="adp-btn cl-save" onclick="_clientsSave()">Save</button></div></section>`:'';
  return `<div class="cl-wrap">
    <div class="cl-bar"><select class="cl-month" onchange="_clientsMonth=this.value;renderHistory()">${months.map(m=>`<option value="${m}"${m===rep.month?' selected':''}>${escHtml(monthName(m))}</option>`).join('')}</select>
      <span class="cl-sum"><b>${money(rep.total)}</b> spent · <b>${money(assigned)}</b> assigned to ${named.length} client${named.length===1?'':'s'}${un.cost?` · <b>${money(un.cost)}</b> unassigned`:''}</span>
      <span style="flex:1"></span>${edit?'':`<button class="adp-btn" onclick="_clientsEdit=true;renderHistory()">${cfg.clients.length?'Edit clients':'+ Add clients'}</button>`}</div>
    ${editor}
    ${!cfg.clients.length&&!edit?`<div class="cache-insight"><span>◎</span><span>Add your clients with a few keywords each (for example <b>omnisocial</b>) and AOC sorts every session's cost under the right client, ready to export for an invoice.</span></div>`:''}
    ${named.map(card).join('')}
    ${un.sessions.length?card(un):''}
  </div>`;
}
function _clientEditRow(c, i){
  return `<div class="cl-row" data-i="${i}">
    <input class="settings-input cl-in-name" placeholder="Client name" value="${escHtml(c.name||'')}">
    <input class="settings-input cl-in-match" placeholder="keywords, comma separated" value="${escHtml((c.match||[]).join(', '))}">
    <span class="cl-mk"><input class="settings-input cl-in-markup" type="number" min="0" max="500" step="1" value="${Number(c.markup)||0}" title="Markup % added on top of the cost when you bill">%</span>
    <span class="cl-mk"><input class="settings-input cl-in-rate" type="number" min="0" step="0.5" value="${Number(c.rate)||0}" title="Hourly rate for your active time (0 = don't bill time)">/h</span>
    <input class="settings-input cl-in-cur" maxlength="3" placeholder="EUR" value="${escHtml(c.currency||'EUR')}" title="Currency of the hourly rate (costs stay in USD)">
    <button class="adp-btn cl-del" onclick="this.parentNode.remove()" title="Remove">✕</button>
    <textarea class="settings-input cl-in-note" rows="2" maxlength="2000" placeholder="Note on the client report (optional), e.g. contract number or contact person">${escHtml(c.note||'')}</textarea></div>`;
}
function _clientAddRow(){
  const box=document.getElementById('cl-rows');
  if(box) box.insertAdjacentHTML('beforeend', _clientEditRow({name:'',match:[],markup:0,rate:0,currency:'EUR',note:''}, box.children.length));
}
async function _clientsPost(cfg){
  try{
    const r=await fetch('/clients',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)});
    if(!r.ok) throw new Error();
    return await r.json();
  }catch(e){ showToast('warning','Not saved','Could not reach AOC.'); return null; }
}
async function _clientsSave(){
  const cfg=(_clientsData&&_clientsData.config)||{clients:[],overrides:{}};
  const clients=[...document.querySelectorAll('#cl-rows .cl-row')].map(r=>({
    name:r.querySelector('.cl-in-name').value.trim(),
    match:r.querySelector('.cl-in-match').value.split(',').map(x=>x.trim()).filter(Boolean),
    markup:parseFloat(r.querySelector('.cl-in-markup').value)||0,
    rate:parseFloat(r.querySelector('.cl-in-rate').value)||0,
    currency:r.querySelector('.cl-in-cur').value.trim().toUpperCase()||'EUR',
    note:(r.querySelector('.cl-in-note')||{value:''}).value.trim()})).filter(c=>c.name);
  if(await _clientsPost({clients, overrides:cfg.overrides||{}})){ _clientsEdit=false; renderHistory(); }
}
async function _clientAssign(sid, value){
  const cfg=JSON.parse(JSON.stringify((_clientsData&&_clientsData.config)||{clients:[],overrides:{}}));
  if(value) cfg.overrides[sid]=value; else delete cfg.overrides[sid];
  if(await _clientsPost(cfg)) renderHistory();
}
/* ── History -> WORK LOG ── */
let _wlRange='today', _wlGroup='project', _wlFrom='', _wlTo='', _wlMode='standup', _wlData=null, _wlBusy=false;
/* Pure: [from, to] (YYYY-MM-DD) for a range button; week = Monday..today. */
function _worklogRange(range, todayStr, from, to){
  const [y,m,d]=todayStr.split('-').map(Number);
  const day=off=>_localDateStr(new Date(y,m-1,d+off));
  if(range==='yesterday') return [day(-1),day(-1)];
  if(range==='week') return [day(-((new Date(y,m-1,d).getDay()+6)%7)),todayStr];
  if(range==='custom'){
    const ok=s=>/^\d{4}-\d{2}-\d{2}$/.test(s||'');
    const f=ok(from)?from:todayStr, t=ok(to)?to:f;
    return f<=t?[f,t]:[t,f];
  }
  return [todayStr,todayStr];
}
function _wlHrs(s){ s=Number(s)||0; return s>=3600?(s/3600).toFixed(1)+' h':Math.max(s?1:0,Math.round(s/60))+' min'; }
/* Pure: the period as Markdown (summary first if there is one). */
function _worklogMarkdown(rep, sum){
  if(!rep) return '';
  const period=rep.from+(rep.to===rep.from?'':' – '+rep.to);
  const out=[`# Work log ${period}`,''];
  if(sum&&sum.text) out.push(sum.text,'');
  for(const g of rep.groups||[]){
    out.push(`## ${g.name||'Unassigned'}`,'',`${_wlHrs(g.active_s)} active · ${g.sessions.length} session${g.sessions.length===1?'':'s'} · ${g.commits.length} commit${g.commits.length===1?'':'s'}`,'');
    if(g.commits.length){ out.push('Commits:'); g.commits.forEach(c=>out.push(`- \`${String(c.sha||'').slice(0,7)}\` ${c.subject||'(no message)'}${c.repo?' ('+c.repo+')':''}`)); out.push(''); }
    out.push('Sessions:'); g.sessions.forEach(s=>out.push(`- ${s.title} (${_wlHrs(s.active_s)})`)); out.push('');
  }
  return out.join('\n').trim()+'\n';
}
/* Pure: the WORK LOG tab. d = GET /worklog, mode = standup|client, busy =
   a summary is being written, local = Summarize allowed (it runs claude -p
   on this PC), tok = auth token for the print link, range/from/to = picker,
   isPro = CSV export unlocked. */
function _worklogHtml(d, mode, busy, local, tok, range, from, to, isPro){
  d=d||{};
  const money=v=>'$'+(Number(v)||0).toFixed(2);
  const custom=range==='custom'?`<div class="wl-custom"><label>From <input type="date" class="settings-input" value="${escHtml(from||'')}" onchange="_wlFrom=this.value;renderHistory()"></label>
      <label>To <input type="date" class="settings-input" value="${escHtml(to||'')}" onchange="_wlTo=this.value;renderHistory()"></label></div>`:'';
  if(d.error) return custom+`<div class="conv-empty">The work log failed — see Health → background errors.</div>`;
  const rep=d.report;
  if(!rep) return custom+`<div class="conv-empty">Still reading your Claude Code transcripts — the work log appears once the index is built.</div>`;
  const period=rep.from===rep.to?rep.from:rep.from+' – '+rep.to;
  if(!(rep.groups||[]).length) return custom+`<div class="conv-empty">No Claude Code work recorded ${escHtml(period)}.</div>`;
  const t=rep.total||{};
  const kpi=(v,l,tip)=>`<div class="hist-kpi" title="${escHtml(tip)}"><div class="hk-val">${v}</div><div class="hk-lbl">${l}</div></div>`;
  const kpis=[kpi(_wlHrs(t.active_s),'Active','Time between your messages, Claude\'s answers and commits; silences over 10 min count as breaks'),
    kpi(String(t.sessions),'Sessions','Sessions with activity in this period'),
    kpi(String(t.commits),'Commits','Commits Claude Code made in this period'),
    kpi(money(t.cost),'Cost','At API list prices, from the transcripts')].join('');
  const sum=(d.summaries||{})[mode];
  const qs=`from=${encodeURIComponent(rep.from)}&to=${encodeURIComponent(rep.to)}&group=${encodeURIComponent(rep.group)}&mode=${encodeURIComponent(mode)}`;
  const printUrl='/worklog/print?'+qs+(tok?'&token='+encodeURIComponent(tok):'');
  const csvUrl='/worklog/export.csv?'+qs+(tok?'&token='+encodeURIComponent(tok):'');
  const csv=isPro?`<a class="adp-btn" href="${escHtml(csvUrl)}" title="One line per session with active time and cost">⬇ CSV</a>`
    :`<span class="cl-lock" title="CSV export is a Pro feature -- Settings -> LICENSE to upgrade">🔒 CSV (PRO)</span>`;
  const sumBtn=!local?`<span class="fx-dim" title="Summaries run Claude Code on the AOC computer -- open AOC there">Summarize on the AOC PC</span>`
    :busy?`<button class="adp-btn" disabled>Writing…</button>`
    :`<button class="adp-btn wl-go" onclick="_wlSummarize(${sum?'true':'false'})" title="${sum?'Write it again (Haiku, about a cent)':'Haiku writes it from your asks, Claude\'s answers and the commits (about a cent)'}">${sum?'↻ Rewrite':'✦ Summarize'}</button>`;
  const summary=`<section class="cl-card wl-sum"><div class="cl-head"><div class="cl-name">${mode==='client'?'Client report':'Standup'}</div>
      <span class="adp-tabs wl-modes">${[['standup','STANDUP'],['client','CLIENT REPORT']].map(([k,l])=>`<span class="adp-tab ${mode===k?'active':''}" onclick="_wlMode=${jsq(k)};_wlPaint()">${l}</span>`).join('')}</span>
      <div class="cl-actions">${sumBtn}<button class="adp-btn" onclick="_wlCopy(this)" title="Copy the summary and the log as Markdown">⧉ Markdown</button>
        <a class="adp-btn" href="${escHtml(printUrl)}" target="_blank" rel="noopener" title="Printable page without costs -- Save as PDF from the print dialog">⎙ Print</a>${csv}</div></div>
      ${sum&&sum.text?`<div class="wl-text">${escHtml(sum.text)}</div><div class="conv-status">Written by Haiku${sum.cost?' · $'+Number(sum.cost).toFixed(3):''}${sum.generated_at?' · '+new Date(sum.generated_at*1000).toLocaleString():''}</div>`
        :`<div class="cl-help">${mode==='client'?'A short report for your client: results per project in plain business language, no costs or AI talk.':'Done / Next / Blockers from this period\'s work, in the language you write to Claude in.'}</div>`}</section>`;
  const card=g=>{
    const cms=g.commits.map(c=>`<li><span class="cc-sha" title="${escHtml(c.sha)}">${escHtml(String(c.sha||'').slice(0,7))}</span> ${escHtml(c.subject||'(no message)')}${c.repo?`<span class="cc-repo">${escHtml(c.repo)}</span>`:''}</li>`).join('');
    const ss=g.sessions.map(s=>`<tr><td class="cache-name"><span title="${escHtml((s.asks||[])[0]||s.id)}">${escHtml(s.title)}</span></td>
        <td class="num cc-when">${escHtml(s.first_day===s.last_day?s.first_day.slice(5):s.first_day.slice(5)+' – '+s.last_day.slice(5))}</td>
        <td class="num">${_wlHrs(s.active_s)}</td><td class="num cache-opt">${s.commits||''}</td><td class="num">${money(s.cost)}</td>
        <td><button class="adp-btn conv-btn" onclick="showHistoryDetail(${jsq(s.id)})">OPEN</button></td></tr>`).join('');
    return `<section class="cl-card"><div class="cl-head"><div class="cl-name">${escHtml(g.name||'Unassigned')}</div>
        <div class="cl-nums"><span><b>${_wlHrs(g.active_s)}</b> active</span><span>${g.sessions.length} session${g.sessions.length===1?'':'s'}</span>${g.commits.length?`<span>${g.commits.length} commit${g.commits.length===1?'':'s'}</span>`:''}<span><b>${money(g.cost)}</b></span></div></div>
      ${cms?`<ul class="wl-commits">${cms}</ul>`:''}
      <div class="cache-table-wrap"><table class="cache-table cl-table"><thead><tr><th>Session</th><th class="num cc-when">Dates</th><th class="num">Active</th><th class="num cache-opt">Commits</th><th class="num">Cost</th><th></th></tr></thead><tbody>${ss}</tbody></table></div></section>`;
  };
  return `<div class="cl-wrap">${custom}<div class="hist-analytics">${kpis}</div>${summary}${rep.groups.map(card).join('')}</div>`;
}
function _wlSetRange(k){
  if(k==='custom'&&!_wlFrom){ const [f,t]=_worklogRange(_wlRange, _localDateStr()); _wlFrom=f; _wlTo=t; }
  _wlRange=k; renderHistory();
}
function _wlPaint(){
  const body=document.getElementById('hist-body');
  if(body&&_histTab==='worklog') body.innerHTML=_worklogHtml(_wlData, _wlMode, _wlBusy, _fixLocal(), window._AOC_TOKEN||'', _wlRange, _wlFrom, _wlTo, !!window._AOC_IS_PRO);
}
async function _wlSummarize(force){
  const rep=_wlData&&_wlData.report;
  if(!rep||_wlBusy) return;
  const mode=_wlMode;
  _wlBusy=true; _wlPaint();
  try{
    const r=await fetch('/worklog/summary',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({from:rep.from,to:rep.to,group:rep.group,mode,force:!!force})}).then(r=>r.json());
    if(r.ok){ if(_wlData&&_wlData.report===rep){ _wlData.summaries=_wlData.summaries||{}; _wlData.summaries[mode]=r; } }
    else showToast('warning','No summary', r.error||'Could not write it.');
  }catch(e){ showToast('warning','No summary','Could not reach AOC.'); }
  _wlBusy=false; _wlPaint();
}
function _wlCopy(btn){
  const md=_worklogMarkdown(_wlData&&_wlData.report, ((_wlData&&_wlData.summaries)||{})[_wlMode]);
  if(!md) return;
  navigator.clipboard.writeText(md).then(()=>{ const o=btn.textContent; btn.textContent='✓ Copied'; setTimeout(()=>btn.textContent=o,1500); })
    .catch(()=>showToast('warning','Not copied','The browser blocked the clipboard.'));
}
function _commitReportHtml(d, sort, showAll){
  d=d||{};
  const idx=d.index||{};
  if(d.error) return `<div class="conv-empty">The commit report failed — see Health → background errors.</div>`;
  const st=d.stats, t=st&&st.total;
  if(!t||!t.commits){
    return `<div class="conv-empty">${idx.building||!idx.updated_at
      ?'Still reading your Claude Code transcripts — commits appear once the index is built (a few minutes after AOC starts).'
      :'No commits made by Claude Code in this period.'}</div>`;
  }
  const money=v=>'$'+(Number(v)||0).toFixed(2);
  const kpi=(v,l,tip)=>`<div class="hist-kpi" title="${escHtml(tip)}"><div class="hk-val">${v}</div><div class="hk-lbl">${l}</div></div>`;
  const lines=n=>Number(n||0).toLocaleString();
  const kpis=[
    kpi(String(t.commits),'Commits',`Commits Claude Code made in ${t.sessions} session${t.sessions===1?'':'s'}`),
    kpi(money(t.cost),'Spent on them','Main-conversation spend (API prices) between each commit and the previous one in its session'),
    kpi(money(t.median),'Median commit','Half the commits cost less than this'),
    kpi(`<span class="cc-ins">+${lines(t.ins)}</span> <span class="cc-del">−${lines(t.dels)}</span>`,'Lines','Lines added / removed by these commits'),
    kpi(money(t.no_commit_spend),'No-commit spend','Spend in sessions that never committed: questions, research, abandoned work'),
  ].join('');
  const share=t.spend?Math.round(t.cost/t.spend*100):0;
  const insight=t.spend?`<div class="cache-insight"><span>⎇</span><span>${share}% of the ${money(t.spend)} spent in this period went into work that ended in a commit — ${money(t.cost/t.commits)} per commit on average.${t.no_commit_spend>0?` ${money(t.no_commit_spend)} went to sessions that never committed.`:''}</span></div>`:'';
  const repos=(st.by_repo||[]).map(r=>`<tr>
      <td class="cache-name">${escHtml(r.repo||'?')}</td><td class="num">${r.commits}</td><td class="num">${money(r.cost)}</td>
      <td class="num">${money(r.per_commit)}</td>
      <td class="num cache-opt"><span class="cc-ins">+${lines(r.ins)}</span> <span class="cc-del">−${lines(r.dels)}</span></td>
      <td class="num cache-opt">${r.sessions}</td></tr>`).join('');
  let list=(st.commits||[]).slice();
  if(sort==='cost') list.sort((a,b)=>b.cost-a.cost);
  const max=Math.max(...list.map(x=>x.cost),0.0001);
  const shown=showAll?list:list.slice(0,50);
  const rows=shown.map(x=>{
    const when=x.epoch?new Date(x.epoch*1000):null;
    const whenStr=when?when.toLocaleDateString(undefined,{day:'numeric',month:'short'})+' '+when.toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'}):'';
    const stat=x.ins!=null?`<span class="cc-ins">+${lines(x.ins)}</span> <span class="cc-del">−${lines(x.dels)}</span>`:'';
    return `<tr>
      <td class="num cc-when">${escHtml(whenStr)}</td>
      <td class="cc-sha" title="${escHtml((x.branch?x.branch+' · ':'')+x.sha+(x.kind&&x.kind!=='committed'?' ('+x.kind+')':''))}">${escHtml(x.sha.slice(0,7))}</td>
      <td class="cache-name cc-subj"><span title="${escHtml(x.subject)}">${escHtml(x.subject||'(no message)')}</span><span class="cc-repo">${escHtml(x.repo||'')}</span></td>
      <td class="num cache-opt">${stat}</td>
      <td class="num cc-cost"><span class="cc-bar"><span style="width:${(x.cost/max*100).toFixed(1)}%"></span></span>${money(x.cost)}</td>
      <td><button class="adp-btn conv-btn" onclick="showHistoryDetail(${jsq(x.session_id)})" title="${escHtml(x.title||'Open the session')}">OPEN</button></td>
    </tr>`;
  }).join('');
  const more=list.length>shown.length?`<button class="adp-btn" onclick="_setCommitView(null,true)">Show all ${list.length}</button>`:'';
  return `<div class="cache-report">
    <div class="hist-analytics">${kpis}</div>
    ${insight}
    <div class="hist-section">BY REPOSITORY</div>
    <div class="cache-table-wrap"><table class="cache-table">
      <thead><tr><th>Repository</th><th class="num">Commits</th><th class="num">Spend</th><th class="num">Per commit</th><th class="num cache-opt">Lines</th><th class="num cache-opt">Sessions</th></tr></thead>
      <tbody>${repos}</tbody></table></div>
    <div class="hist-section cc-head">COMMITS <span class="adp-tabs cc-sort">${[['new','NEWEST'],['cost','MOST EXPENSIVE']].map(([k,l])=>`<span class="adp-tab ${sort===k?'active':''}" onclick="_setCommitView(${jsq(k)})">${l}</span>`).join('')}</span></div>
    <div class="cache-table-wrap"><table class="cache-table">
      <thead><tr><th class="num cc-when">When</th><th>Commit</th><th>Message</th><th class="num cache-opt">Lines</th><th class="num">Cost</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    ${more}
    <div class="conv-status" style="margin-top:6px">A commit's cost is what its session spent since the previous commit (or since it started), estimated at API prices from the Claude Code transcripts on this machine — subagent work not included. Commits made with <code>git commit -q</code> are matched through <code>git log</code>. ${idx.building?'Still indexing — numbers will grow.':''}</div>
  </div>`;
}

/* ── conversation search (History → CONVERSATIONS) ── */
let _convQuery='';
let _convTimer=null;
let _convSeq=0;
function setConvSearch(v){
  _convQuery=v;
  clearTimeout(_convTimer);
  _convTimer=setTimeout(_runConvSearch, 250);
}
async function _runConvSearch(){
  const body=document.getElementById('hist-body');
  if(!body||_histTab!=='search') return;
  const seq=++_convSeq;
  let d;
  try{
    d=await fetch('/search_transcripts?q='+encodeURIComponent(_convQuery.trim())).then(r=>r.json());
  }catch(e){ d={error:'search failed', sessions:[]}; }
  if(seq!==_convSeq||_histTab!=='search') return;  // a newer keystroke won
  const b=document.getElementById('hist-body');
  if(b) b.innerHTML=_convResultsHtml(d, Date.now());
}
/* Index snippets mark matches with \x02..\x03 -- escape first, then mark. */
function _convSnippetHtml(s){
  return escHtml(String(s||'')).replace(/\x02/g,'<mark>').replace(/\x03/g,'</mark>').replace(/\n+/g,' ');
}
/* Pure: /search_transcripts response -> results HTML. */
function _convResultsHtml(d, nowMs){
  d=d||{};
  const idx=d.index||{};
  const q=(d.query||'').trim();
  const status=idx.building
    ?`Indexing conversations… ${idx.indexed||0} of ${idx.files||0} transcripts done`
    :(idx.updated_at?`${(idx.messages||0).toLocaleString()} messages from ${idx.files||0} conversations indexed`
                    :'The search index is built a few minutes after AOC starts');
  const head=`<div class="conv-status">${escHtml(status)}</div>`;
  if(d.error) return head+`<div class="conv-empty">Search failed — see Health → background errors.</div>`;
  if(!q) return head+`<div class="conv-empty">Type words you remember from a conversation — yours or Claude's. All words must match; accents don't matter.</div>`;
  const ss=d.sessions||[];
  if(!ss.length) return head+`<div class="conv-empty">No conversation mentions “${escHtml(q)}”${idx.building?' yet — still indexing':''}.</div>`;
  const day=ts=>{ if(!ts) return ''; const t=new Date(ts); if(isNaN(t)) return '';
    const ago=Math.floor((nowMs-t.getTime())/86400000);
    return ago<1?'today':ago<2?'yesterday':ago<7?ago+' days ago':t.toISOString().slice(0,10); };
  const clock=ts=>{ const t=new Date(ts); return isNaN(t)?'':String(t.getHours()).padStart(2,'0')+':'+String(t.getMinutes()).padStart(2,'0'); };
  return head+ss.map(s=>{
    const folder=(s.cwd||'').split(/[\\/]/).filter(Boolean).pop()||'';
    const title=s.title||s.project||folder||s.session_id.slice(0,8);
    const where=s.project||folder;
    const more=s.n_hits>s.hits.length?`<div class="conv-more">+${s.n_hits-s.hits.length} more match${s.n_hits-s.hits.length===1?'':'es'}</div>`:'';
    return `<div class="conv-card">
      <div class="conv-head">
        <span class="conv-title" title="${escHtml(s.session_id)}">${escHtml(title)}</span>
        ${where&&where!==title?`<span class="conv-where">// ${escHtml(where)}</span>`:''}
        <span class="conv-when">${escHtml(day(s.last_ts))}</span>
        <button class="adp-btn conv-btn" onclick="showHistoryDetail(${jsq(s.session_id)})" title="Open this session in History">OPEN</button>
        <button class="adp-btn conv-btn" onclick="_copyResumeCmd(${jsq(s.session_id)},${jsq(s.cwd||'')})" title="Copy a command that reopens this conversation in Claude Code">⟲ RESUME</button>
      </div>
      ${s.hits.map(h=>`<div class="conv-hit"><span class="conv-role conv-${h.role==='user'?'you':'claude'}">${h.role==='user'?'YOU':'CLAUDE'}</span><span class="conv-time">${escHtml(clock(h.ts))}</span><span class="conv-snip">${_convSnippetHtml(h.snippet)}</span></div>`).join('')}
      ${more}
    </div>`;
  }).join('');
}

function setHistSearch(val){
  _histSearchQuery=val.toLowerCase();
  const inp=document.getElementById('hist-search-input');
  if(inp && inp.value!==val) inp.value=val;
  _renderHistSessionsBody();
}
function setHistDateFrom(val){ _histSearchFrom=val; _renderHistSessionsBody(); }
function setHistDateTo(val){ _histSearchTo=val; _renderHistSessionsBody(); }

