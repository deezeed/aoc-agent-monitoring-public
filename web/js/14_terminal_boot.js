/* ── TERMINAL — multi-session ── */
/* ── TERMINAL: multi-session, grid, rename, completion notifications ── */
const _tSess={};   /* id → {term,ws,fit,cell,xtermEl,title,status,isClaude,badge,busy,lastOut} */
let _tActive=null, _tNext=1, _tXtermLoaded=false, _tGridMode=false;

const _XTERM_OPTS={
  theme:{background:'#16201e',foreground:'#dde6e0',cursor:'#e0a13a',cursorAccent:'#16201e',
         selection:'rgba(91,155,224,.3)',black:'#16201e',brightBlack:'#5d6f68'},
  fontFamily:"Consolas,'Cascadia Code','Courier New',monospace",
  fontSize:13, lineHeight:1.4, cursorBlink:true, convertEol:true, scrollback:8000,
};

function _initTerm(){
  if(_tXtermLoaded){ if(!_tActive)_termNew(); else{ const s=_tSess[_tActive]; if(s?.fit)s.fit.fit(); } return; }
  const lnk=document.createElement('link');
  lnk.rel='stylesheet';
  lnk.href='https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.min.css';
  document.head.appendChild(lnk);
  const s1=document.createElement('script'); s1.src='https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js';
  s1.onload=()=>{
    const s2=document.createElement('script'); s2.src='https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js';
    s2.onload=()=>{ _tXtermLoaded=true; _termNew(); };
    document.head.appendChild(s2);
  };
  document.head.appendChild(s1);
}

function _termNew(autoCmd, isClaude){
  if(!window.Terminal) return;
  const id='t'+(_tNext++);
  const titleDefault=isClaude?'◈ claude':'shell '+(_tNext-1);

  /* cell wrapper (single mode: fills area; grid mode: grid cell) */
  const cell=document.createElement('div');
  cell.className='tg-cell';

  /* mini header (visible only in grid mode) */
  const hdr=document.createElement('div');
  hdr.className='tg-hdr'+(isClaude?' claude-hdr':'');
  hdr.innerHTML=`<span class="tg-title" ondblclick="_termRenameInline('${id}',this)" title="Double-click to rename">${escHtml(titleDefault)}</span>
    <button onclick="_termClose('${id}',event)" style="background:none;border:none;cursor:pointer;color:var(--t3);font-size:11px;padding:0;line-height:1;opacity:.5" title="Close">×</button>`;
  cell.appendChild(hdr);

  /* xterm inner container */
  const xtermEl=document.createElement('div');
  xtermEl.className='tg-inner';
  cell.appendChild(xtermEl);

  const area=document.getElementById('xterm-area');
  if(_tGridMode){
    /* grid mode: all cells always visible */
    cell.classList.add('grid-cell');
    if(isClaude) cell.classList.add('tg-claude');
    hdr.style.display='flex';
    area.appendChild(cell);
  } else {
    /* single mode: hide others, show this one full-size */
    Object.values(_tSess).forEach(s=>{ s.cell.style.display='none'; });
    cell.style.cssText='display:flex;flex-direction:column;flex:1;min-height:0;';
    area.appendChild(cell);
  }

  const term=new Terminal(_XTERM_OPTS);
  const fit=new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(xtermEl);
  term.focus();
  fit.fit();
  new ResizeObserver(()=>{ if(currentView==='term'&&(_tGridMode||_tActive===id)){try{fit.fit();}catch(_){}} }).observe(xtermEl);

  /* wss:// required when the page itself loaded over https (Cloudflare tunnel) —
     browsers block insecure ws:// from a secure origin. The token must ride along
     as a query param since the WebSocket API can't set custom headers. */
  const _wsProto=location.protocol==='https:'?'wss':'ws';
  const _wsAuth=window._AOC_TOKEN?('?token='+encodeURIComponent(window._AOC_TOKEN)):'';
  const ws=new WebSocket(`${_wsProto}://${location.host}/terminal/ws${_wsAuth}`);
  ws.binaryType='arraybuffer';
  const sess={term,ws,fit,cell,xtermEl,title:titleDefault,status:'connecting',isClaude:!!isClaude,badge:0,busy:false,lastOut:0};
  _tSess[id]=sess;
  _tActive=id;
  _renderTermTabs();

  ws.onopen=()=>{
    sess.status='connected'; _renderTermTabs();
    term.writeln(isClaude
      ?'\x1b[32m◈ Starting Claude CLI...\x1b[0m\r\n'
      :'\x1b[32m✓ Git Bash — type \x1b[96mclaude\x1b[32m for a new Claude\x1b[0m\r\n');
    if(autoCmd) setTimeout(()=>{ if(ws.readyState===1) ws.send(autoCmd+'\r'); }, 1200);
  };
  ws.onmessage=e=>{
    if(!_tSess[id]) return;
    const d=e.data instanceof ArrayBuffer?new Uint8Array(e.data):e.data;
    term.write(d);
    /* completion detection: track idle → busy → idle transitions */
    sess.lastOut=Date.now();
    if(!sess.busy) sess.busy=true;
  };
  ws.onclose=()=>{ sess.status='closed'; _renderTermTabs(); term.writeln('\r\n\x1b[33m[Closed]\x1b[0m'); };
  ws.onerror=()=>{ sess.status='error'; _renderTermTabs(); };
  term.onData(d=>{ if(ws.readyState===1)ws.send(d); sess.lastOut=Date.now(); });
  term.onResize(({rows,cols})=>{ if(ws.readyState===1)ws.send('\x00resize:'+rows+':'+cols); });
}

/* Periodic check: if a session was busy and went idle for >1.5s → task done notification */
setInterval(()=>{
  const now=Date.now();
  Object.entries(_tSess).forEach(([id,s])=>{
    if(s.busy && now-s.lastOut>1500){
      s.busy=false;
      /* only notify if this session is not the active one OR we're on a different view */
      if(id!==_tActive || currentView!=='term'){
        s.badge++;
        showToast('info','Terminal: \x22'+s.title+'\x22 — command finished');
        _renderTermTabs();
      }
    }
  });
},500);

function _termSwitch(id){
  const s=_tSess[id]; if(!s) return;
  s.badge=0;  /* clear badge on switch */
  if(_tGridMode){
    /* in grid mode just change focus highlight */
    Object.values(_tSess).forEach(ss=>ss.cell.classList.remove('tg-focused'));
    s.cell.classList.add('tg-focused');
    _tActive=id;
    s.term.focus();
    _renderTermTabs();
    return;
  }
  Object.entries(_tSess).forEach(([sid,ss])=>{ ss.cell.style.display=sid===id?'flex':'none'; });
  _tActive=id;
  setTimeout(()=>{ s.fit.fit(); s.term.focus(); },50);
  _renderTermTabs();
}

function _termClose(id, e){
  if(e) e.stopPropagation();
  const s=_tSess[id]; if(!s) return;
  try{s.ws.close();}catch(_){}
  try{s.term.dispose();}catch(_){}
  s.cell.remove(); delete _tSess[id];
  const ids=Object.keys(_tSess);
  if(_tActive===id) _tActive=ids.length?ids[ids.length-1]:null;
  if(_tGridMode){
    /* in grid mode: refit all remaining terminals after DOM reflow */
    setTimeout(()=>{ Object.values(_tSess).forEach(ss=>{try{ss.fit.fit();}catch(_){}}); },80);
    _renderTermTabs();
  } else {
    if(_tActive) _termSwitch(_tActive); else _renderTermTabs();
  }
}

function _termRename(id){
  const s=_tSess[id]; if(!s) return;
  const nv=prompt('Rename terminal:',s.title);
  if(nv!==null&&nv.trim()){
    s.title=nv.trim();
    const hdrTitle=s.cell.querySelector('.tg-title');
    if(hdrTitle) hdrTitle.textContent=s.title;
    _renderTermTabs();
  }
}

function _termRenameInline(id, el){
  const s=_tSess[id]; if(!s) return;
  const old=s.title;
  el.contentEditable='true'; el.focus();
  const sel=window.getSelection(); const rng=document.createRange();
  rng.selectNodeContents(el); sel.removeAllRanges(); sel.addRange(rng);
  const done=()=>{
    el.contentEditable='false';
    const nv=el.textContent.trim()||old;
    s.title=nv; el.textContent=nv;
    _renderTermTabs();
  };
  el.onblur=done;
  el.onkeydown=ev=>{ if(ev.key==='Enter'||ev.key==='Escape'){ev.preventDefault();el.blur();} };
}

function _termToggleGrid(){
  _tGridMode=!_tGridMode;
  const area=document.getElementById('xterm-area');
  if(_tGridMode){
    area.classList.add('grid-mode');
    /* show all cells as grid cells */
    Object.entries(_tSess).forEach(([id,s])=>{
      s.cell.style.cssText='';
      s.cell.classList.add('grid-cell');
      if(s.isClaude) s.cell.classList.add('tg-claude');
      s.cell.querySelector('.tg-hdr').style.display='flex';
    });
    if(_tActive&&_tSess[_tActive]) _tSess[_tActive].cell.classList.add('tg-focused');
    setTimeout(()=>{ Object.values(_tSess).forEach(s=>{try{s.fit.fit();}catch(_){}}); },120);
  } else {
    area.classList.remove('grid-mode');
    Object.entries(_tSess).forEach(([id,s])=>{
      s.cell.classList.remove('grid-cell','tg-claude','tg-focused');
      s.cell.querySelector('.tg-hdr').style.display='none';
      s.cell.style.cssText=id===_tActive?'display:flex;flex-direction:column;flex:1;min-height:0;':'display:none;';
    });
    if(_tActive&&_tSess[_tActive]) setTimeout(()=>{ _tSess[_tActive].fit.fit(); _tSess[_tActive].term.focus(); },120);
  }
  _renderTermTabs();
}

function _renderTermTabs(){
  const bar=document.getElementById('term-tabs'); if(!bar) return;
  const sIcon={connected:'●',closed:'○',connecting:'◌',error:'✕'};
  const sCol ={connected:'var(--g)',closed:'var(--t3)',connecting:'var(--o)',error:'var(--r)'};
  bar.innerHTML=Object.entries(_tSess).map(([id,s])=>`
    <div class="t-tab${id===_tActive?' active':''}${s.isClaude?' claude-tab':''}" onclick="_termSwitch('${id}')">
      <span style="color:${sCol[s.status]||'var(--t3)'};font-size:9px">${sIcon[s.status]||'?'}</span>
      <span ondblclick="_termRename('${id}')" title="Double-click to rename">${escHtml(s.title)}</span>
      ${s.badge?`<span class="t-badge">${s.badge}</span>`:''}
      <button class="t-close" onclick="_termClose('${id}',event)" title="Close">×</button>
    </div>`).join('')+
  `<button class="t-tab-add" onclick="_termNew()" title="New shell">+</button>
   <button class="t-grid-btn${_tGridMode?' active':''}" onclick="_termToggleGrid()" title="${_tGridMode?'Single view':'Grid view (3×3)'}">${_tGridMode?'▣':'⊞'}</button>`;
}

/* public API */
function _termNew_pub(){ _termNew(); }
function _termClaude(){
  /* full path from /status so bash finds claude regardless of its PATH */
  const bin=(lastStatus&&lastStatus.claude_bin)||'claude';
  _termNew(bin, true);
}
function _termClearActive(){
  const s=_tActive&&_tSess[_tActive]; if(!s) return;
  /* send cls to shell to clear its viewport, then clear xterm scrollback */
  if(s.ws.readyState===1) s.ws.send('clear\r');
  setTimeout(()=>{ try{s.term.clear();}catch(_){} },150);
}
function _termKillActive(){ if(_tActive) _termClose(_tActive); }

/* git status in toolbar */
function _termUpdateGit(){
  /* session_project is just basename(cwd) (see /update's cwd_name derivation in
     monitor.py) -- not a real filesystem path, so passing it to /git's `git -C`
     always failed silently (empty branch). The real path lives on the matching
     entry in sessions_list, not on the agent object itself. */
  const runningAgent=(lastStatus?.agents||[]).find(a=>a.status==='running');
  const sess=runningAgent&&(lastStatus?.sessions_list||[]).find(s=>s.id===runningAgent.session_id);
  const cwd=sess?.cwd||null;
  if(!cwd){ const el=document.getElementById('term-git'); if(el)el.textContent=''; return; }
  fetch('/git?cwd='+encodeURIComponent(cwd)).then(r=>r.json()).then(d=>{
    const el=document.getElementById('term-git'); if(!el)return;
    if(d.branch) el.textContent='⎇ '+d.branch+(d.changes?' ['+d.changes+']':'');
    else el.textContent='';
  }).catch(()=>{});
}

/* ── boot ── */
const boot=[
  ['INITIALIZING A.O.C INTERFACE...','info'],
  ['CONNECTING TO ORCHESTRATOR...','info'],
  ['WAITING FOR AGENT STATUS...','info'],
  ['REAL-TIME AGENT TRACKING ENABLED','success'],
];
boot.forEach(([m,t],i)=>setTimeout(()=>log(m,t),i*250));
_loadPrefs();
// Unconditional (not just inside _loadPrefs' own `if(p.theme)` branch) so
// a first-run OS-detected _theme actually reaches the DOM (body class +
// toggle button icon) even when there's no saved preference to load yet
// -- without this call, a light-OS user's first visit would still
// render dark, since nothing else ever applies the initial value.
_applyTheme();
_requestNotifPermission();
_loadSoundPrefs();
_connectEvents();
let _gitLastPoll=0;
setInterval(()=>{ if(currentView==='term'&&Date.now()-_gitLastPoll>10000){ _gitLastPoll=Date.now(); _termUpdateGit(); } },5000);
renderAuditLog();

