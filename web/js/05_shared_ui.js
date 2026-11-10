/* ── diff overlay ── */
/* Escapes &<> for text-node safety plus "' so the 4 existing title="${escHtml(...)}"
   attribute-context call sites are actually safe too -- &<> alone stops tag
   injection in text content but a bare " still terminates a double-quoted HTML
   attribute early, letting injected text add its own event-handler attribute. */
/* Tolerates null/undefined/numbers -- callers pass server fields straight through. */
function escHtml(s){ return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
/* A value as a JS string literal that's safe inside an HTML on*="..." attribute:
   JSON-encode first, then HTML-escape. escHtml alone isn't enough there -- the
   browser decodes &#39; back to ' before running the handler, so a machine or
   project name like "Marek's laptop" ended the '...' literal early and broke
   the button (or injected code). Use as onclick="fn(${jsq(x)})", no quotes. */
function jsq(s){ return escHtml(JSON.stringify(String(s))); }
/* Shared HOOK MISS badge tooltip -- 4 near-identical call sites (card,
   SUMMARY agent row, AGENTS-tab row, Compare) previously hardcoded the
   same fixed text. concurrent_sessions is exactly the metric this whole
   feature exists to test (see the concurrent_sessions comment in
   monitor.py's _apply_agent_update: does the hook drop PreToolUse more
   often under concurrent session load?), but was only ever shown as a
   cross-agent average in Settings -> INFRA, never per-agent -- append it
   here when known instead of adding a whole new badge just for one
   number. */
function _hookMissTitle(a){
  const base="Claude Code's PreToolUse hook never fired for this agent -- surfaced via transcript fallback instead";
  return a.concurrent_sessions!=null ? base+` (${a.concurrent_sessions} other session${a.concurrent_sessions!==1?'s':''} active at the time)` : base;
}
/* Shared "Input: X · Output: Y · Cache read: Z · Cache write: W" token
   breakdown formatter -- independently written twice (the session-card
   stats tooltip and the agent detail panel's cost badge tooltip both
   surface the same four input/output/cache_read/cache_write_tokens
   fields, tracked at the session and agent level respectively). Callers
   append their own extra suffix (msg_count, tool_use_count) separately. */
function _tokBreakdownStr(o){
  return `Input: ${(o.input_tokens||0).toLocaleString()} · Output: ${(o.output_tokens||0).toLocaleString()} · Cache read: ${(o.cache_read_tokens||0).toLocaleString()} · Cache write: ${(o.cache_write_tokens||0).toLocaleString()}`;
}
/* Tool-use efficiency -- tokens spent per tool call, for a group of
   agents (COST BY MODEL / COST BY AGENT TYPE rows). tool_use_count and
   tokens were both already persisted per-agent and summed into those
   rows for their own existing stats, but never divided against each
   other -- this is the one place that turns "which model/type burns
   more tokens overall" into "...per action it actually took", a
   genuinely different signal (a model that's simply used on bigger
   tasks would win the raw-tokens comparison either way). Returns null
   (not a string) when there's no tool_use_count to divide by, same
   null-for-"nothing to show" convention _ctxPct already uses. */
function _tokPerCall(r){
  return (r.tool_use_count>0) ? Math.round((r.tokens||0)/r.tool_use_count) : null;
}
/* Task completion rate -- task_done/task_total were already summed into
   COST BY PROJECT rows (and by_day, for the trend chart) for other
   stats, but never divided against each other. Same null-for-"nothing to
   show" convention as _tokPerCall/_ctxPct. */
function _taskCompletionPct(r){
  return (r.task_total>0) ? Math.round((r.task_done||0)/r.task_total*100) : null;
}
/* Shared days/hours/minutes formatter -- this exact expression was
   independently duplicated 3 times (the WAITING pill's duration, the
   infra panel's monitor uptime, and renderDiag's own fmtUptime); factored
   out once enough copies existed that "3 similar lines" stopped being the
   simpler option. */
function _fmtDurationDHM(totalSeconds){
  if(totalSeconds==null) return '—';
  const d=Math.floor(totalSeconds/86400), h=Math.floor((totalSeconds%86400)/3600), m=Math.floor((totalSeconds%3600)/60);
  return d>0?`${d}d ${h}h ${m}m`:h>0?`${h}h ${m}m`:`${m}m`;
}
/* Shared context-window-used percentage -- token_limit/tokens_used yield
   the same clamped ratio in three places (renderAgents' token counter bar,
   the agent detail panel's ctx badge, and the Markdown export); factored
   out so all three read the same number instead of three copies of the
   same Math.min/toFixed expression. Returns null (not a string) when
   either input is missing so callers can gate their own rendering. */
function _ctxPct(a){
  return (a.token_limit&&a.tokens_used)?Math.min(a.tokens_used/a.token_limit*100,100).toFixed(0):null;
}
/* Shared parent/child agent resolvers -- (lastStatus.agents||[]).find(ag=>ag.id===a.parent_id)
   for the parent and (lastStatus.agents||[]).filter(ag=>ag.parent_id===a.id) for children were
   independently written in 5 places across the parent_id thread (Agent Compare, the GRAPH/TREE
   hover tooltip, the agent detail panel, exportAgentDetail, exportSessionDetail) -- all 5 resolving
   against this same live lastStatus.agents shape, keyed by "id". showHistoryDetail resolves parent/
   child too, but against a session's DB-row agents array keyed by "agent_id" instead -- a genuinely
   different shape, left alone rather than bent into one helper with an extra key-name parameter. */
function _resolveParentAgent(a){
  return a.parent_id ? (lastStatus?.agents||[]).find(ag=>ag.id===a.parent_id) : null;
}
function _resolveChildAgents(a){
  return (lastStatus?.agents||[]).filter(ag=>ag.parent_id===a.id);
}
/* Shared short duration formatter ("Xs" / "Xm Ys", no days) -- distinct
   range from _fmtDurationDHM above (that one's for multi-hour/day uptimes;
   this one's for session/agent durations that are usually under an hour).
   Was independently written 6 times across the compare modals, history
   view, and KPI bar with tiny drift (===null vs falsy null-check, rounded
   vs not). Standardized here on `s==null` (only null/undefined -> em-dash)
   rather than the falsy-check 4 of the 6 originals used -- a sub-second
   session duration can legitimately compute to a real 0 (see _renderCompare's
   local dur(), which floors ms/1000), and those 4 sites were silently
   showing '—' for that case instead of the more honest '0s' the other 2
   sites already showed. Minor latent inconsistency fixed as a side effect
   of consolidating, not left in place under the new shared function. */
function _fmtDurShort(s, round=false){
  if(s==null) return '—';
  return s<60?(round?Math.round(s):s)+'s':Math.floor(s/60)+'m '+((round?Math.round(s):s)%60)+'s';
}
/* Shared cost formatter ($X.XXXX) -- independently re-declared identically
   4 times (session compare, history view, KPI-adjacent render paths). */
function _fmtCost(c){ return c!=null?'$'+(+c).toFixed(4):'—'; }
async function dismissAgent(id,machineName){
  try{
    await fetch(_apiUrl(_machineFor(machineName),'/remove'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});
  }catch(e){}
}
async function clearDoneAgents(){
  try{
    await fetch('/clear_done',{method:'POST'});
    log('All done agents cleared','success');
  }catch(e){ log('Clear failed: '+e.message,'error'); }
}
function toggleAgentLog(id){
  const tog=document.getElementById('lt-'+id);
  const pan=document.getElementById('lp-'+id);
  if(!tog||!pan) return;
  const open=pan.classList.toggle('open');
  tog.classList.toggle('open',open);
}

/* ── copy to clipboard ── */
function copyText(btn,text){
  navigator.clipboard.writeText(text).then(()=>{
    const o=btn.textContent; btn.textContent='✓ copied';
    setTimeout(()=>btn.textContent=o,1500);
  }).catch(()=>{});
}

/* ── toast notifications ── */
(function(){
  const style=document.createElement('style');
  style.textContent=`
    #toast-container{position:fixed;bottom:24px;right:24px;z-index:9998;display:flex;flex-direction:column-reverse;gap:8px;pointer-events:none;}
    .toast{display:flex;align-items:flex-start;gap:10px;padding:10px 14px;border-radius:12px;background:rgba(34,48,45,.92);backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,.1);box-shadow:0 8px 32px rgba(0,0,0,.5);font-size:12px;min-width:220px;max-width:320px;pointer-events:auto;animation:toastIn .3s ease;}
    .toast.done{border-color:rgba(76,194,133,.3);} .toast.error{border-color:rgba(239,106,87,.3);}
    .toast-icon{font-size:15px;flex-shrink:0;margin-top:1px;}
    .toast-body{flex:1;} .toast-title{font-family:var(--font2);font-size:11px;font-weight:700;letter-spacing:.06em;margin-bottom:2px;}
    .toast.done .toast-title{color:var(--g);} .toast.error .toast-title{color:var(--r);}
    .toast-msg{color:var(--t2);font-size:11px;line-height:1.4;word-break:break-word;}
    .toast-close{background:none;border:none;color:var(--t3);cursor:pointer;font-size:13px;padding:0;flex-shrink:0;line-height:1;}
    @keyframes toastIn{from{opacity:0;transform:translateX(24px)}to{opacity:1;transform:none}}
  `;
  document.head.appendChild(style);
  const container=document.createElement('div');
  container.id='toast-container';
  document.body.appendChild(container);
})();

/* title/msg are plain text (agent names, error_message -- attacker-controlled): always escaped. */
function showToast(type, title, msg, duration=5000){
  const c=document.getElementById('toast-container');
  if(!c) return;
  const t=document.createElement('div');
  t.className=`toast ${type}`;
  t.innerHTML=`<div class="toast-icon">${type==='done'?'✓':'✕'}</div><div class="toast-body"><div class="toast-title">${escHtml(title)}</div><div class="toast-msg">${escHtml(msg||'')}</div></div><button class="toast-close" onclick="this.parentNode.remove()">✕</button>`;
  c.appendChild(t);
  setTimeout(()=>t.remove(),duration);
}

/* ── audio notifications (Web Audio API) ── */
function _note(ac, freq, vol, start, dur, wave='sine'){
  const o=ac.createOscillator(), g=ac.createGain();
  o.type=wave; o.connect(g); g.connect(ac.destination);
  o.frequency.value=freq;
  g.gain.setValueAtTime(Math.max(vol,0.001), ac.currentTime+start);
  g.gain.exponentialRampToValueAtTime(0.001, ac.currentTime+start+dur);
  o.start(ac.currentTime+start); o.stop(ac.currentTime+start+dur);
}
const _soundProfiles = {
  default: {
    done:    (ac,v)=>{ _note(ac,880,v*.18,0,.35); },
    error:   (ac,v)=>{ _note(ac,220,v*.2,0,.25); _note(ac,180,v*.2,.18,.25); },
    running: (ac,v)=>{ _note(ac,440,v*.1,0,.12); _note(ac,660,v*.1,.1,.12); },
    stuck:   (ac,v)=>{ [0,.28,.56].forEach(t=>_note(ac,330,v*.15,t,.2)); },
    burn_spike: (ac,v)=>{ [0,.15,.3,.45].forEach(t=>_note(ac,500,v*.15,t,.12)); },
    waiting_nudge: (ac,v)=>{ _note(ac,392,v*.1,0,.5); },
    cost_spike: (ac,v)=>{ _note(ac,340,v*.15,0,.2); _note(ac,300,v*.15,.18,.25); },
  },
  minimal: {
    done:    (ac,v)=>{ _note(ac,1000,v*.08,0,.1); },
    error:   (ac,v)=>{ _note(ac,200,v*.1,0,.15); },
    running: (ac,v)=>{ _note(ac,600,v*.05,0,.07); },
    stuck:   (ac,v)=>{ _note(ac,300,v*.07,0,.14); },
    burn_spike: (ac,v)=>{ _note(ac,450,v*.07,0,.1); },
    waiting_nudge: (ac,v)=>{ _note(ac,392,v*.05,0,.3); },
    cost_spike: (ac,v)=>{ _note(ac,340,v*.06,0,.12); },
  },
  retro: {
    done:    (ac,v)=>{ [523,659,784].forEach((f,i)=>_note(ac,f,v*.2,i*.08,.1,'square')); },
    error:   (ac,v)=>{ [200,160,120].forEach((f,i)=>_note(ac,f,v*.22,i*.1,.14,'square')); },
    running: (ac,v)=>{ _note(ac,440,v*.15,0,.07,'square'); _note(ac,880,v*.1,.06,.07,'square'); },
    stuck:   (ac,v)=>{ [0,.2,.4].forEach(t=>_note(ac,220,v*.18,t,.13,'square')); },
    burn_spike: (ac,v)=>{ [0,.1,.2,.3].forEach(t=>_note(ac,330,v*.16,t,.08,'square')); },
    waiting_nudge: (ac,v)=>{ _note(ac,392,v*.12,0,.2,'square'); },
    cost_spike: (ac,v)=>{ [340,300].forEach((f,i)=>_note(ac,f,v*.16,i*.1,.12,'square')); },
  },
  subtle: {
    done:    (ac,v)=>{ _note(ac,660,v*.06,0,.55); },
    error:   (ac,v)=>{ _note(ac,160,v*.07,0,.4); },
    running: (ac,v)=>{ _note(ac,440,v*.04,0,.2); },
    stuck:   (ac,v)=>{ _note(ac,250,v*.05,0,.35); },
    burn_spike: (ac,v)=>{ _note(ac,380,v*.05,0,.3); },
    waiting_nudge: (ac,v)=>{ _note(ac,392,v*.04,0,.4); },
    cost_spike: (ac,v)=>{ _note(ac,340,v*.045,0,.35); },
  },
  alert: {
    done:    (ac,v)=>{ _note(ac,880,v*.25,0,.13); _note(ac,1100,v*.25,.11,.13); _note(ac,1320,v*.22,.2,.22); },
    error:   (ac,v)=>{ [440,220,440,180].forEach((f,i)=>_note(ac,f,v*.28,i*.12,.12,'sawtooth')); },
    running: (ac,v)=>{ _note(ac,550,v*.18,0,.11); _note(ac,770,v*.15,.09,.11); },
    stuck:   (ac,v)=>{ [0,.14,.28,.42].forEach(t=>_note(ac,330,v*.22,t,.11,'sawtooth')); },
    burn_spike: (ac,v)=>{ [0,.08,.16,.24].forEach(t=>_note(ac,500,v*.2,t,.08,'sawtooth')); },
    waiting_nudge: (ac,v)=>{ _note(ac,392,v*.15,0,.3,'sawtooth'); },
    cost_spike: (ac,v)=>{ [340,300,260].forEach((f,i)=>_note(ac,f,v*.22,i*.1,.12,'sawtooth')); },
  },
};
function _playSound(eventType){
  if(_soundMuted||!_soundPrefs.events[eventType]) return;
  try{
    const ac=new(window.AudioContext||window.webkitAudioContext)();
    ((_soundProfiles[_soundPrefs.profile]||_soundProfiles.default)[eventType]||(() => {}))(ac, _soundPrefs.volume);
  }catch(e){}
}
/* legacy shim for any remaining playBeep calls */
function playBeep(type){ _playSound(type); }

/* ── sound settings panel ── */
function openSoundPanel(){
  _soundPanelOpen=!_soundPanelOpen;
  _renderSoundPanel();
}
function closeSoundPanel(){
  _soundPanelOpen=false;
  const p=document.getElementById('sound-panel');
  if(p) p.style.display='none';
}
function _renderSoundPanel(){
  let p=document.getElementById('sound-panel');
  if(!p){
    p=document.createElement('div');
    p.id='sound-panel';
    p.style.cssText='position:fixed;bottom:50px;right:16px;z-index:4500;background:rgba(34,48,45,.97);backdrop-filter:blur(28px);border:1px solid rgba(var(--c-rgb),.18);border-radius:14px;padding:14px 16px;width:272px;box-shadow:0 8px 40px rgba(0,0,0,.65);font-size:11px';
    document.body.appendChild(p);
    document.addEventListener('click', e=>{
      if(!p.contains(e.target) && e.target.id!=='btn-mute'){ closeSoundPanel(); }
    }, {capture:true});
  }
  p.style.display=_soundPanelOpen?'block':'none';
  if(!_soundPanelOpen) return;
  const profiles=['default','minimal','retro','subtle','alert'];
  const evts=[{k:'done',l:'MISSION COMPLETE'},{k:'error',l:'SYSTEM FAILURE'},{k:'running',l:'AGENT ACTIVATED'},{k:'stuck',l:'AGENT STUCK'},{k:'burn_spike',l:'BURN SPIKE'},{k:'waiting_nudge',l:'WAITING TOO LONG'},{k:'cost_spike',l:'COST SPIKE'}];
  const btnBase='padding:3px 10px;border-radius:6px;font-family:var(--font2);font-size:9px;letter-spacing:.08em;cursor:pointer;';
  p.innerHTML=`
  <div style="display:flex;align-items:center;gap:6px;margin-bottom:11px">
    <div style="font-family:var(--font2);font-size:11px;letter-spacing:.14em;color:var(--c);font-weight:700;flex:1">SOUND SETTINGS</div>
    <button onclick="closeSoundPanel()" style="background:none;border:none;color:var(--t3);cursor:pointer;font-size:15px;padding:0;line-height:1">✕</button>
  </div>
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:9px">
    <span style="font-family:var(--font2);font-size:9px;letter-spacing:.1em;color:var(--t3);min-width:46px">MUTE</span>
    <button onclick="_spToggleMute()" style="${btnBase}border:1px solid ${_soundMuted?'rgba(239,106,87,.4)':'rgba(var(--c-rgb),.3)'};background:${_soundMuted?'rgba(239,106,87,.08)':'rgba(var(--c-rgb),.07)'};color:${_soundMuted?'var(--r)':'var(--c)'};">${_soundMuted?'🔇 MUTED':'🔊 ON'}</button>
  </div>
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:11px">
    <span style="font-family:var(--font2);font-size:9px;letter-spacing:.1em;color:var(--t3);min-width:46px">VOLUME</span>
    <input type="range" min="0" max="1" step="0.05" value="${_soundPrefs.volume}" oninput="_spSetVol(this.value)" style="flex:1;accent-color:var(--c);height:3px">
    <span id="sp-vol-lbl" style="font-family:var(--font2);font-size:10px;color:var(--c);min-width:30px;text-align:right">${Math.round(_soundPrefs.volume*100)}%</span>
  </div>
  <div style="font-family:var(--font2);font-size:9px;letter-spacing:.1em;color:var(--t3);margin-bottom:6px">PROFILE</div>
  <div style="display:flex;gap:5px;flex-wrap:wrap;margin-bottom:11px">
    ${profiles.map(pr=>`<button onclick="_spSetProfile('${pr}')" style="${btnBase}border:1px solid ${_soundPrefs.profile===pr?'rgba(var(--c-rgb),.5)':'rgba(255,255,255,.08)'};background:${_soundPrefs.profile===pr?'rgba(var(--c-rgb),.12)':'transparent'};color:${_soundPrefs.profile===pr?'var(--c)':'var(--t2)'};">${pr.toUpperCase()}</button>`).join('')}
  </div>
  <div style="font-family:var(--font2);font-size:9px;letter-spacing:.1em;color:var(--t3);margin-bottom:5px">EVENTS</div>
  ${evts.map(ev=>`<div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid rgba(255,255,255,.04)">
    <input type="checkbox" ${_soundPrefs.events[ev.k]?'checked':''} onchange="_spToggleEvent('${ev.k}',this.checked)" style="accent-color:var(--c);flex-shrink:0;cursor:pointer">
    <span style="flex:1;font-family:var(--font2);font-size:9px;letter-spacing:.06em;color:var(--t2)">${ev.l}</span>
    <button onclick="_spPreview('${ev.k}')" title="Preview sound" style="padding:2px 9px;border-radius:5px;border:1px solid rgba(255,255,255,.07);background:rgba(255,255,255,.03);color:var(--t3);font-family:var(--font2);font-size:9px;cursor:pointer" onmouseover="this.style.color='var(--c)';this.style.borderColor='rgba(var(--c-rgb),.3)'" onmouseout="this.style.color='var(--t3)';this.style.borderColor='rgba(255,255,255,.07)'">▶</button>
  </div>`).join('')}`;
}
function _spToggleMute(){ _soundMuted=!_soundMuted; _savePrefs(); _applyMuteUI(); _renderSoundPanel(); }
function _spSetVol(v){
  _soundPrefs.volume=parseFloat(v);
  const l=document.getElementById('sp-vol-lbl'); if(l) l.textContent=Math.round(v*100)+'%';
  _saveSoundPrefs();
}
function _spSetProfile(pr){ _soundPrefs.profile=pr; _saveSoundPrefs(); _renderSoundPanel(); }
function _spToggleEvent(k,v){ _soundPrefs.events[k]=v; _saveSoundPrefs(); }
function _spPreview(eventType){
  const wasM=_soundMuted, wasE=_soundPrefs.events[eventType];
  _soundMuted=false; _soundPrefs.events[eventType]=true;
  _playSound(eventType);
  _soundMuted=wasM; _soundPrefs.events[eventType]=wasE;
}
function _saveSoundPrefs(){ try{ localStorage.setItem('aoc_sound',JSON.stringify(_soundPrefs)); }catch(e){} }
function _loadSoundPrefs(){
  try{
    const s=localStorage.getItem('aoc_sound');
    if(s){ const p=JSON.parse(s); _soundPrefs={..._soundPrefs,...p}; if(!_soundPrefs.events) _soundPrefs.events={done:true,error:true,running:false,stuck:false,burn_spike:false,waiting_nudge:false,cost_spike:false}; }
  }catch(e){}
}

/* ── conflict detector ── */
function checkConflicts(agents){
  const fileMap={};
  agents.filter(a=>!String(a.id||'').startsWith('hook_')).forEach(a=>{
    (a.files_changed||[]).forEach(f=>{
      if(!fileMap[f.path]) fileMap[f.path]=[];
      fileMap[f.path].push(a.unit||String(a.id).slice(-4).toUpperCase());
    });
  });
  return Object.entries(fileMap).filter(([,units])=>units.length>1).map(([path,units])=>({path,units}));
}

function renderConflicts(agents){
  const conflicts=checkConflicts(agents);
  let el=document.getElementById('conflict-banner');
  if(!el){
    el=document.createElement('div');
    el.id='conflict-banner';
    el.style.cssText='display:none;margin:0 16px 8px;padding:7px 12px;border-radius:10px;background:rgba(224,161,58,.08);border:1px solid rgba(224,161,58,.3);border-left:3px solid var(--o);font-size:11px;color:rgba(224,161,58,.85);';
    const cardsArea=document.getElementById('cards-area');
    cardsArea&&cardsArea.parentNode.insertBefore(el,cardsArea);
  }
  if(conflicts.length===0){ el.style.display='none'; return; }
  el.style.display='block';
  el.innerHTML='<b style="font-family:var(--font2);font-size:10px;letter-spacing:.06em;color:var(--o)">⚠ CONFLICT</b> '+conflicts.map(c=>`<span style="font-family:var(--font2)">${escHtml(c.path.split('/').pop())}</span> edited by ${escHtml(c.units.join(' + '))}`).join(' &nbsp;·&nbsp; ');
}

/* Pure: unified diff text -> coloured, escaped lines. */
function _diffLinesHtml(text){
  return String(text||'').split('\n').map(line=>{
    const s=escHtml(line);
    if(line.startsWith('+++') || line.startsWith('---')) return `<span class="diff-meta">${s}</span>`;
    if(line.startsWith('+')) return `<span class="diff-add">${s}</span>`;
    if(line.startsWith('-')) return `<span class="diff-del">${s}</span>`;
    if(line.startsWith('@@')) return `<span class="diff-hunk">${s}</span>`;
    if(line.startsWith('diff')||line.startsWith('index')) return `<span class="diff-meta">${s}</span>`;
    return s;
  }).join('\n');
}
/* Pure: "Δ n" button for a session that edited files. */
