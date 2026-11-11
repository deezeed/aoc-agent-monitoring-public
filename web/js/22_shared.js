/* ── SHARED SESSIONS: one Claude Code terminal, several people (server/16) ──
   🤝 in the top bar: the shared sessions running on this AOC. The owner
   starts one (an interactive `claude`, or a shell, in a folder) and ends it;
   anyone signed in opens it: same screen for everyone, the owner and members
   type, viewers watch; who's there and who's typing shows on top. Globals
   are `var` (boot runs before this part, see web/js/19). */
var _shState={open:false, list:null, err:'', dirs:[], join:null};

/* Pure: the list + (owner) the start form. */
function _shListHtml(list, isOwner, dirs, err){
  const rows=(list||[]).map(s=>`<div class="sh-row${s.alive?'':' off'}"><div class="sh-who"><b>${escHtml(s.name)}</b>
      <span class="or-note">${escHtml(s.kind==='claude'?'Claude Code':'shell')} · ${escHtml(s.cwd)} · by ${escHtml(s.by||'?')}${s.people.length?' · here: '+s.people.map(p=>escHtml(p.name)).join(', '):''}</span></div>
      ${s.alive?`<button class="adp-btn ln-go" onclick="_shJoin(${jsq(s.id)},${jsq(s.name)})">Open</button>`:'<span class="or-note">ended</span>'}
      ${isOwner?`<button class="adp-btn" onclick="_shStop(${jsq(s.id)},${jsq(s.name)})">End</button>`:''}</div>`).join('');
  const form=isOwner?`<form class="sh-start" onsubmit="event.preventDefault();_shStart(this)">
      <input name="cwd" class="settings-input" list="sh-dirs" placeholder="folder, e.g. C:\\path\\to\\project" required>
      <datalist id="sh-dirs">${(dirs||[]).map(d=>`<option value="${escHtml(d.path)}"></option>`).join('')}</datalist>
      <input name="name" class="settings-input" placeholder="name (optional)">
      <select name="kind" class="settings-input"><option value="claude">Claude Code</option><option value="shell">Shell</option></select>
      <button class="adp-btn ln-go">Start a shared session</button></form>
    <div class="or-note">It runs on this PC. Everyone you invited (Settings → PEOPLE) can open it; members can type, viewers watch.</div>`
    :'<div class="or-note">The owner starts shared sessions; open one to work in it together.</div>';
  return `<div class="vo-h"><b>🤝 Shared sessions</b><span style="flex:1"></span><button class="adp-btn" onclick="sharedToggle()">✕</button></div>
    ${list==null?'<div class="or-note">Loading…</div>':(rows||'<div class="or-note">No shared session running.</div>')}
    ${err?`<div class="or-warn">${escHtml(err)}</div>`:''}${form}`;
}
/* Pure: the strip above a joined session: who's there, who's typing, read-only. */
function _shBarHtml(j){
  const ppl=(j.people||[]).map(p=>`<span class="pe-av pe-${escHtml(p.role)}" title="${escHtml(p.name)}">${escHtml(_peInitials(p.name))}</span>`).join('');
  return `<b>${escHtml(j.name)}</b>${ppl}<span class="sh-typing">${j.typing?escHtml(j.typing)+' is typing…':''}</span>
    ${j.me&&!j.me.can_type?'<span class="or-chip or-c-wait">watching</span>':''}${j.ended?'<span class="or-chip or-c-err">ended</span>':''}
    <span style="flex:1"></span><button class="adp-btn" onclick="_shLeave()">Leave</button>`;
}

function _shRender(){
  let el=document.getElementById('shared-panel');
  if(!_shState.open){ if(el) el.style.display='none'; return; }
  if(!el){ el=document.createElement('div'); el.id='shared-panel'; el.className='vo-panel sh-panel'; document.body.appendChild(el); }
  el.style.display='flex';
  const isOwner=(_peState.me||{}).role==='owner';
  const a=document.activeElement, typing=a&&el.contains(a)&&['INPUT','SELECT'].includes(a.tagName);
  if(!typing) el.innerHTML=_shListHtml(_shState.list, isOwner, _shState.dirs, _shState.err);
}
async function _shLoad(){
  try{ _shState.list=(await fetch('/shared').then(r=>r.json())).sessions||[]; _shState.err=''; }catch(e){ _shState.err=e.message; }
  _shRender();
}
function sharedToggle(){
  _shState.open=!_shState.open;
  if(_shState.open){ _shLoad(); if(!_shState.dirs.length) fetch('/launch/dirs').then(r=>r.json()).then(d=>{ _shState.dirs=d.dirs||[]; _shRender(); }).catch(()=>{}); }
  _shRender();
}
async function _shStart(form){
  const r=await fetch('/shared/start',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({cwd:form.cwd.value, name:form.name.value, kind:form.kind.value})}).then(r=>r.json()).catch(e=>({ok:false,error:e.message}));
  if(!r.ok){ _shState.err=r.error||'failed'; _shRender(); return; }
  await _shLoad(); _shJoin(r.id, form.name.value||form.cwd.value);
}
async function _shStop(id, name){
  if(!confirm(`End “${name}” for everyone?`)) return;
  await fetch('/shared/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})}).catch(()=>{});
  if(_shState.join&&_shState.join.id===id) _shLeave();
  _shLoad();
}
/* Loads xterm once; a second Open while it's still loading queues its
   callback instead of adding the scripts again. */
var _shXtermWaiting=null;
function _shXterm(cb){
  if(window.Terminal&&window.FitAddon) return cb();
  if(_shXtermWaiting){ _shXtermWaiting.push(cb); return; }
  _shXtermWaiting=[cb];
  const done=()=>{ const q=_shXtermWaiting||[]; _shXtermWaiting=null; q.forEach(f=>f()); };
  const lnk=document.createElement('link'); lnk.rel='stylesheet'; lnk.href='https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.min.css'; document.head.appendChild(lnk);
  const s1=document.createElement('script'); s1.src='https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js';
  s1.onload=()=>{ const s2=document.createElement('script'); s2.src='https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js'; s2.onload=done; document.head.appendChild(s2); };
  document.head.appendChild(s1);
}
function _shJoin(id, name){
  _shLeave();
  _shState.open=false; _shRender();
  const ov=document.createElement('div'); ov.id='shared-term'; ov.className='sh-term';
  ov.innerHTML='<div class="sh-bar" id="sh-bar"></div><div class="sh-x" id="sh-x"></div>';
  document.body.appendChild(ov);
  const j={id, name, people:[], typing:'', me:null, ended:false, ws:null, term:null, fit:null, timer:null, ro:null, ov};
  _shState.join=j;
  /* The join's own overlay, not a global #sh-bar: a double-clicked Open
     leaves a stale join whose late callbacks must not paint the new one. */
  const bar=()=>{ const b=ov.querySelector('.sh-bar'); if(b) b.innerHTML=_shBarHtml(j); };
  bar();
  _shXterm(()=>{
    if(_shState.join!==j) return;  // left / reopened while xterm loaded
    const term=new Terminal(typeof _XTERM_OPTS!=='undefined'?_XTERM_OPTS:{convertEol:true});
    const x=ov.querySelector('.sh-x');
    const fit=new FitAddon.FitAddon(); term.loadAddon(fit); term.open(x); fit.fit(); term.focus();
    j.term=term; j.fit=fit;
    const proto=location.protocol==='https:'?'wss':'ws';
    const auth=window._AOC_TOKEN?('&token='+encodeURIComponent(window._AOC_TOKEN)):'';
    const ws=new WebSocket(`${proto}://${location.host}/shared/ws?id=${encodeURIComponent(id)}${auth}`);
    ws.binaryType='arraybuffer'; j.ws=ws;
    ws.onopen=()=>{ try{ ws.send(`\x00resize:${term.rows}:${term.cols}`); }catch(e){} };
    ws.onmessage=ev=>{
      if(_shState.join!==j) return;
      if(typeof ev.data!=='string'){ term.write(new Uint8Array(ev.data)); return; }
      const t=ev.data;
      if(t.startsWith('\x00people:')){ j.people=JSON.parse(t.slice(8)); bar(); }
      else if(t.startsWith('\x00who:')){ j.typing=t.slice(5); bar(); clearTimeout(j.timer); j.timer=setTimeout(()=>{ j.typing=''; bar(); }, 2500); }
      else if(t.startsWith('\x00me:')){ j.me=JSON.parse(t.slice(4)); if(!j.me.can_type) term.options.disableStdin=true; bar(); }
      else if(t==='\x00ended'){ j.ended=true; bar(); }
    };
    ws.onclose=()=>{ j.ended=true; bar(); };
    term.onData(d=>{ if(ws.readyState===1&&!(j.me&&!j.me.can_type)) ws.send(d); });
    j.ro=new ResizeObserver(()=>{ try{ fit.fit(); if(ws.readyState===1) ws.send(`\x00resize:${term.rows}:${term.cols}`); }catch(e){} });
    j.ro.observe(x);
  });
}
function _shLeave(){
  const j=_shState.join; _shState.join=null;
  if(j){
    clearTimeout(j.timer);
    try{ j.ro&&j.ro.disconnect(); }catch(e){}
    try{ if(j.ws){ j.ws.onmessage=j.ws.onclose=null; j.ws.close(); } }catch(e){}
    try{ j.term&&j.term.dispose(); }catch(e){}
    if(j.ov) j.ov.remove();
  }
  const ov=document.getElementById('shared-term'); if(ov) ov.remove();
}
