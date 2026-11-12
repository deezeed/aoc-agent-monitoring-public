/* ── self-diagnostics view ── */
async function renderDiag(){
  const el=document.getElementById('diag-area');
  if(!el) return;
  const fmtBytes=b=>{
    if(b==null) return '—';
    if(b>=1e9) return (b/1e9).toFixed(2)+' GB';
    if(b>=1e6) return (b/1e6).toFixed(1)+' MB';
    if(b>=1e3) return (b/1e3).toFixed(1)+' KB';
    return b+' B';
  };
  const fmtUptime=s=>_fmtDurationDHM(s);
  try{
    const [d,backups]=await Promise.all([
      fetch('/diag').then(r=>r.json()),
      fetch('/backups').then(r=>r.json()).catch(()=>[]),
    ]);
    const fmtDate=t=>t?new Date(t*1000).toLocaleString():'—';
    const [hooks,upd,rel]=await Promise.all([fetch('/hooks_health').then(r=>r.json()).catch(()=>null),
                                            fetch('/update/status').then(r=>r.json()).catch(()=>null),
                                            fetch('/release/status').then(r=>r.json()).catch(()=>null)]);
    const backupRows=(backups||[]).map(b=>`
      <div style="display:flex;align-items:center;gap:8px;padding:4px 0;border-bottom:1px solid rgba(255,255,255,.05);font-family:var(--font2);font-size:10px">
        <span style="flex:1;color:${b.corrupt?'var(--r)':'var(--t2)'}">${escHtml(b.filename)}${b.corrupt?' ⚠ CORRUPT':''}</span>
        <span style="color:var(--t3)">${fmtBytes(b.size_bytes)}</span>
        <span style="color:var(--t3)">${fmtDate(b.mtime)}</span>
        ${b.corrupt?'':`<button class="adp-btn" onclick="_restoreBackup(${jsq(b.filename)})" style="font-size:9px;padding:2px 10px;color:var(--r)">RESTORE</button>`}
      </div>`).join('');
    el.innerHTML=`
      <div style="font-size:10px;font-family:var(--font2);color:var(--t3);margin-bottom:2px">
        AOC's own process health -- not your agents or CLI sessions. For watchdog/sentinel status and hook reliability, see
        <a href="#" onclick="openSettings();setSettingsTab('infra');return false" style="color:var(--c);text-decoration:underline;text-decoration-style:dotted">Settings → INFRA →</a>
      </div>
      <div id="hooks-health">${_hooksHealthHtml(hooks)}</div>
      <div id="update-status">${_updateStatusHtml(upd, Date.now()/1000)}</div>
      <div id="release-tools">${_releaseToolsHtml(rel)}</div>
      <div class="hist-section hh-sec-row">PROCESS <button class="adp-btn" id="aoc-restart-btn" onclick="_restartAoc()" title="Stops every AOC process and starts it again (python setup.py --restart)">Restart AOC</button></div>
      <div id="aoc-restart-msg" class="hh-note"></div>
      <div class="hist-analytics">
        <div class="hist-kpi"><div class="hk-val">${fmtUptime(d.uptime_s)}</div><div class="hk-lbl">Uptime</div></div>
        <div class="hist-kpi"><div class="hk-val">${d.thread_count??'—'}</div><div class="hk-lbl">Threads</div></div>
        <div class="hist-kpi"><div class="hk-val">${fmtBytes(d.memory_rss_bytes)}</div><div class="hk-lbl">Memory (RSS)</div></div>
      </div>
      <div class="hist-section">DATABASE</div>
      <div class="hist-analytics">
        <div class="hist-kpi"><div class="hk-val">${fmtBytes(d.db_size_bytes)}</div><div class="hk-lbl">history.db size</div></div>
        <div class="hist-kpi"><div class="hk-val">${d.backups_count??'—'}</div><div class="hk-lbl">Backups</div></div>
      </div>
      <div style="font-size:9px;font-family:var(--font2);color:var(--t3);margin-bottom:6px">
        Restoring overwrites the live history.db (this only affects Session History/Analytics, not the live dashboard). A safety copy of whatever's currently live is taken automatically first.
      </div>
      <div id="backup-restore-msg" style="font-size:10px;font-family:var(--font2);margin-bottom:6px"></div>
      ${backupRows||'<div style="font-size:10px;color:var(--t3);font-family:var(--font2)">// no backups yet</div>'}
    `;
    if(rel&&rel.state==='running') _releasePoll();
  }catch(e){ el.innerHTML='<div style="color:var(--r);padding:20px;font-family:var(--font2);font-size:10px">Error loading diagnostics</div>'; }
}

/* Pure: Health -> CLAUDE CODE SETUP (GET /hooks_health = setup.py --check). */
function _hooksHealthHtml(h, busy){
  if(!h||!Array.isArray(h.checks)){
    const why=h&&h.error?escHtml(h.error):'not checked yet';
    return `<div class="hist-section">CLAUDE CODE SETUP</div><div class="hh-note">Couldn't check the install: ${why}</div>`;
  }
  const bad=h.checks.filter(c=>!c.ok);
  const rows=h.checks.map(c=>`<div class="hh-row ${c.ok?'ok':'bad'}"><span class="hh-ic">${c.ok?'✓':'✗'}</span>`+
    `<span class="hh-lbl">${escHtml(c.label)}</span>${c.detail?`<span class="hh-det">${escHtml(c.detail)}</span>`:''}</div>`).join('');
  const when=h.at?new Date(h.at*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}):'';
  const lr=h.last_repair, err=h.error&&!(lr&&lr.state==='failed'&&lr.error===h.error)?`<div class="hh-note bad">Last run: ${escHtml(h.error)}</div>`:'';
  return `<div class="hist-section">CLAUDE CODE SETUP</div>
    <div class="hh-head ${bad.length?'bad':'ok'}">
      <span>${bad.length?`${bad.length} part${bad.length>1?'s':''} of AOC not set up -- those features silently do nothing`:'Everything AOC needs is set up'}</span>
      <span class="hh-btns">
        <button class="adp-btn" ${busy?'disabled':''} onclick="_hooksHealthRun(false)">${busy==='check'?'Checking…':'Re-check'}</button>
        ${bad.length?`<button class="adp-btn hh-fix" ${busy?'disabled':''} onclick="_hooksHealthRun(true)">${busy==='repair'?'Repairing…':'Repair'}</button>`:''}
      </span>
    </div>
    ${rows}${h.orch_hint?`<div class="hh-note bad">${escHtml(h.orch_hint)}</div>`:''}${_repairResultHtml(lr)}${err}
    <div class="hh-note">${when?`Checked ${when}. `:''}Repair re-runs only the needed setup.py steps. Claude Code sessions that are already open pick up hook changes after a restart.</div>
    <label class="hh-note hh-auto"><input type="checkbox" id="hh-auto-repair" ${h.auto_repair===false?'':'checked'} onchange="_autoRepairToggle(this.checked)"> Repair automatically after an update (once per update, only hooks the update made outdated)</label>`;
}
/* Pure: the last Repair click's outcome (GET /hooks_health last_repair). */
function _repairResultSentence(r){
  if(!r) return '';
  const list=a=>(a||[]).join(', ');
  return r.state==='fixed'?`fixed ${list(r.fixed)}. Open Claude Code sessions use the new hooks after a restart.`
    :r.state==='partial'?`fixed ${list(r.fixed)}; still not set up: ${list(r.left)}.`
    :r.state==='unchanged'?`ran, but nothing changed -- still not set up: ${list(r.left)}.`
    :`failed: ${r.error}`;
}
function _repairResultHtml(r){
  if(!r) return '';
  const when=r.at?new Date(r.at*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}):'';
  const what=r.source==='auto'?'Automatic repair after the update':'Repair';
  return `<div class="hh-note hh-repair ${r.state==='fixed'?'ok':'bad'}">${r.state==='fixed'?'✓':'✗'} ${what}${when?' at '+when:''}: ${escHtml(_repairResultSentence(r))}</div>`;
}
async function _autoRepairToggle(on){
  try{
    const h=await fetch('/hooks_health/auto_repair',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:!!on})}).then(r=>r.json());
    log(h.auto_repair?'Automatic repair after updates: on':'Automatic repair after updates: off','info');
  }catch(e){ log('Could not save the auto-repair setting: '+e.message,'error'); }
}
async function _hooksHealthRun(repair){
  const el=document.getElementById('hooks-health');
  if(!el) return;
  let h=null;
  try{ h=await fetch('/hooks_health').then(r=>r.json()); }catch(e){}
  el.innerHTML=_hooksHealthHtml(h, repair?'repair':'check');
  try{
    h=await fetch(repair?'/hooks_health/repair':'/hooks_health?refresh=1', repair?{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}:{}).then(r=>r.json());
    if(repair) log('Repair: '+_repairResultSentence(h.last_repair), h.last_repair&&h.last_repair.state==='fixed'?'success':'error');
  }catch(e){ h={...(h||{}), error:'request failed: '+e.message}; }
  el.innerHTML=_hooksHealthHtml(h);
  if(lastStatus&&lastStatus.infra_health) lastStatus.infra_health.hooks_bad=h.bad||[];
  _ccPillUpdate();
}

/* Pure: Health -> VERSION & UPDATES (GET /update/status). */
function _updateStatusHtml(u, nowS){
  if(!u) return '';
  const su=u.self_update||{}, job=u.job||{};
  const ago=t=>{ const s=Math.max(0,nowS-t); return s<3600?Math.round(s/60)+' min ago':s<86400?Math.round(s/3600)+' h ago':Math.round(s/86400)+' d ago'; };
  let ver, avail;
  if(u.mode==='release'){
    ver=`AOC ${escHtml(u.installed)}`;
    avail=su.stale?`<span class="uh-new">${escHtml(su.latest)} available</span>`:(su.checked_at?`up to date (checked ${ago(su.checked_at)})`:'not checked yet');
  }else{
    ver='Dev checkout (git)';
    avail=su.stale?`<span class="uh-new">${Number(su.behind_by)||0} commit(s) behind origin/master</span>`:(su.checked_at?'up to date with origin/master':'not checked yet');
  }
  const busy=['downloading','verifying','installing'].includes(job.state);
  const now=busy?`<div class="hh-row"><span class="hh-ic">⟳</span><span class="hh-lbl">Updating to ${escHtml(job.version||'')}: ${escHtml(job.state)}${job.state==='downloading'&&job.pct!=null?' '+Math.round(job.pct)+'%':''}</span></div>`
    :job.state==='error'?`<div class="hh-row bad"><span class="hh-ic">✗</span><span class="hh-lbl">Update failed</span><span class="hh-det">${escHtml(job.error||'')}</span></div>`:'';
  const hist=(u.history||[]).slice(0,5).map(h=>{
    if(h.outcome==='repaired'||h.outcome==='repair_failed'){
      const fixed=h.outcome==='repaired';
      return `<div class="hh-row ${fixed?'ok':'bad'}"><span class="hh-ic">${fixed?'✓':'✗'}</span>`+
        `<span class="hh-lbl">Hooks repaired automatically${fixed?'':' -- not finished'}</span>`+
        `<span class="hh-det">${escHtml(_repairResultSentence(h))} · ${ago(h.t)}</span></div>`;
    }
    const ok=h.outcome==='updated';
    return `<div class="hh-row ${ok?'ok':'bad'}"><span class="hh-ic">${ok?'✓':'✗'}</span>`+
      `<span class="hh-lbl">${ok?`${escHtml(h.from)} → ${escHtml(h.to)}`:`Update to ${escHtml(h.to||'?')} failed`}</span>`+
      `<span class="hh-det">${escHtml(ok?(h.via||''):(h.error||''))} · ${ago(h.t)}</span></div>`;
  }).join('');
  const logBtn=u.log?`<button class="adp-btn" onclick="_updateLogShow()">Installer log</button>`:'';
  return `<div class="hist-section hh-sec-row">VERSION &amp; UPDATES ${logBtn}</div>
    <div class="hh-row"><span class="hh-ic">•</span><span class="hh-lbl">${ver}</span><span class="hh-det">${avail}</span></div>
    ${now}${hist||(u.mode==='release'?'<div class="hh-note">No updates recorded yet.</div>':'')}
    <pre id="update-log" class="uh-log" style="display:none"></pre>`;
}
async function _updateLogShow(){
  const el=document.getElementById('update-log');
  if(!el) return;
  if(el.style.display!=='none'){ el.style.display='none'; return; }
  let d={};
  try{ d=await fetch('/update/log').then(r=>r.json()); }catch(e){}
  el.textContent=d.text?`${d.path}\n\n${d.text}`:'No installer log yet.';
  el.style.display='block';
}
/* Pure: Health -> RELEASE (GET /release/status): Publish release / Sync
   public mirror, each "check first, then confirm". '' unless this PC runs a
   dev checkout (r.available). */
function _releaseToolsHtml(r){
  if(!r||!r.available) return '';
  const p=r.plan||{}, res=r.result||{}, pub=r.kind==='publish';
  const what=pub?'Publish release':'Mirror sync';
  const idle=r.state==='idle';
  const bar=`<div class="rl-bar">
      <input class="settings-input" id="rel-run-id" placeholder="CI run id (newest green)" inputmode="numeric" ${idle?'':'disabled'}>
      <button class="adp-btn" ${idle?'':'disabled'} onclick="_releaseAct('publish','prepare')" title="Dry run of installer/publish_release.ps1: shows version, SHA-256 and notes before anything is published">Publish release…</button>
      <button class="adp-btn" ${idle?'':'disabled'} onclick="_releaseAct('sync','prepare')" title="Runs export_public_release.ps1 and shows what would be committed to the public mirror">Sync public mirror…</button>
    </div>`;
  let body='';
  if(r.state==='running'){
    body=`<div class="hh-row"><span class="hh-ic">⟳</span><span class="hh-lbl">${what}: ${escHtml(r.step||'working')}…</span></div>`;
  }else if(r.state==='error'){
    body=`<div class="hh-row bad"><span class="hh-ic">✗</span><span class="hh-lbl">${what} failed</span><span class="hh-det">${escHtml(r.error||'')}</span></div>
      <div class="rl-bar"><button class="adp-btn" onclick="_releaseAct(${jsq(pub?'publish':'sync')},'cancel')">OK</button></div>`;
  }else if(r.state==='ready'&&pub){
    const warn=(p.warnings||[]);
    body=`<div class="rl-plan">
      <div class="hh-sec-row"><span>Ready to publish AOC ${escHtml(p.version)}</span></div>
      <div class="hh-row"><span class="hh-ic">•</span><span class="hh-lbl">CI run ${escHtml(p.run_id)}</span><span class="hh-det">commit ${escHtml(p.commit)} · ${Number(p.changes)||0} change(s) since ${escHtml(p.since||'—')}</span></div>
      <div class="hh-row"><span class="hh-ic">•</span><span class="hh-lbl">SHA-256</span><span class="rl-sha hh-det">${escHtml(p.sha256)}</span></div>
      <pre class="uh-log">${escHtml(p.notes||'')}</pre>
      ${warn.length?`<div class="hh-note bad">${warn.length} line(s) read like developer notes:</div>`+
        warn.map(w=>`<div class="hh-row bad"><span class="hh-ic">!</span><span class="hh-det">${escHtml(w)}</span></div>`).join('')+
        `<label class="rl-allow"><input type="checkbox" id="rel-allow-internal"> Publish anyway (then reword these lines on GitHub)</label>`:''}
      <div class="rl-bar"><button class="adp-btn hh-fix" onclick="_releaseAct('publish','confirm')">Publish v${escHtml(p.version)}</button>
        <button class="adp-btn" onclick="_releaseAct('publish','cancel')">Cancel</button></div>
    </div>`;
  }else if(r.state==='ready'){
    body=`<div class="rl-plan">
      <div class="hh-sec-row"><span>${Number(p.changed)||0} file(s) change in the public mirror</span></div>
      ${p.summary?`<div class="hh-note">${escHtml(p.summary)}</div>`:''}
      ${(p.status||[]).length?`<pre class="uh-log">${escHtml((p.status||[]).join('\n'))}</pre>`:''}
      ${p.private_dirty?`<div class="hh-note bad">${Number(p.private_dirty)} uncommitted file(s) in this checkout went into the export -- commit them first, or the mirror gets work that isn't on master.</div>`:''}
      <div class="hh-row"><span class="hh-ic">•</span><span class="hh-lbl">Commit</span><span class="hh-det">${escHtml(p.message||'')} → ${escHtml(p.mirror||'')}</span></div>
      <div class="rl-bar">${p.changed?`<button class="adp-btn hh-fix" onclick="_releaseAct('sync','confirm')">Commit &amp; push</button>`:''}
        <button class="adp-btn" onclick="_releaseAct('sync','cancel')">${p.changed?'Cancel':'OK'}</button></div>
    </div>`;
  }else if(r.state==='done'&&pub){
    const ok=res.verified;
    body=`<div class="hh-row ok"><span class="hh-ic">✓</span><span class="hh-lbl">Published AOC ${escHtml(res.version)}</span>
        <span class="hh-det"><a href="${escHtml(res.url||'')}" target="_blank" rel="noopener" style="color:var(--c)">${escHtml(res.url||'')}</a></span></div>
      <div class="hh-row ${ok?'ok':'bad'}"><span class="hh-ic">${ok?'✓':'✗'}</span><span class="hh-lbl">${ok?'Downloaded installer matches the SHA-256 in the notes':'Downloaded installer could not be verified'}</span>
        <span class="hh-det">${escHtml(ok?'':(res.verify_error||('got '+(res.asset_sha256||'?'))))}</span></div>
      <div class="rl-bar"><button class="adp-btn" onclick="_releaseAct('publish','cancel')">OK</button></div>`;
  }else if(r.state==='done'){
    body=`<div class="hh-row ok"><span class="hh-ic">✓</span><span class="hh-lbl">Public mirror pushed (${escHtml(res.commit||'')})</span><span class="hh-det">${escHtml(res.message||'')}</span></div>
      <div class="rl-bar"><button class="adp-btn" onclick="_releaseAct('sync','cancel')">OK</button></div>`;
  }
  return `<div class="hist-section hh-sec-row">RELEASE <span class="hh-det">dev checkout · this PC</span></div>${bar}${body}`;
}
/* Pure: the browser confirm() before the step that leaves this PC. */
function _releaseConfirmText(kind, r){
  const p=(r&&r.plan)||{};
  return kind==='publish'
    ?`Publish AOC ${p.version} on GitHub now?\n\nEveryone's update badge then offers it.\nCI run ${p.run_id}, SHA-256 ${p.sha256}`
    :`Commit ${p.changed} file(s) to the public mirror and push?\n\n${p.message}`;
}
let _releaseLast=null, _releasePolling=false;
function _releasePaint(r){
  _releaseLast=r;
  const el=document.getElementById('release-tools');
  if(el) el.innerHTML=_releaseToolsHtml(r);
}
async function _releaseAct(kind, action){
  const data={};
  if(action==='prepare'){ const i=document.getElementById('rel-run-id'); if(i&&i.value.trim()) data.run_id=i.value.trim(); }
  if(action==='confirm'){
    const a=document.getElementById('rel-allow-internal');
    data.allow_internal=!!(a&&a.checked);
    if(!confirm(_releaseConfirmText(kind, _releaseLast))) return;
  }
  let d;
  try{ d=await fetch(`/release/${kind}/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).then(r=>r.json()); }
  catch(e){ d={ok:false,error:'could not reach AOC'}; }
  if(!d.ok){ log('Release: '+(d.error||'failed'),'error'); }
  _releasePoll();
}
async function _releasePoll(){
  if(_releasePolling) return;
  _releasePolling=true;
  try{
    for(;;){
      let r=null;
      try{ r=await fetch('/release/status').then(x=>x.json()); }catch(e){}
      if(!document.getElementById('release-tools')) break;
      if(r) _releasePaint(r);
      if(!r||r.state!=='running'){
        if(r&&r.state==='done') log(r.kind==='publish'?`Published AOC ${(r.result||{}).version}`+((r.result||{}).verified?' (installer verified)':' -- installer NOT verified'):'Public mirror pushed',
                                    r.kind==='publish'&&!(r.result||{}).verified?'error':'success');
        if(r&&r.state==='error') log('Release: '+r.error,'error');
        break;
      }
      await new Promise(res=>setTimeout(res,2000));
    }
  }finally{ _releasePolling=false; }
}
/* Pure: the confirm text when something would be lost by a restart. */
function _restartConfirmText(blockers){
  return 'Restart AOC now?\n\nRight now: '+(blockers||[]).join('; ')+
    '.\nA held command or prompt is dropped by the restart (Claude Code then decides on its own), and an open tunnel closes.';
}
async function _restartAoc(){
  const btn=document.getElementById('aoc-restart-btn'), msg=document.getElementById('aoc-restart-msg');
  const say=(t,col)=>{ if(msg){ msg.textContent=t; msg.style.color=col||''; } };
  const post=force=>fetch('/restart',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({force})}).then(r=>r.json());
  try{
    let d=await post(false);
    if(!d.ok&&d.blockers&&d.blockers.length){
      if(!confirm(_restartConfirmText(d.blockers))) return;
      d=await post(true);
    }
    if(!d.ok){ say('✗ '+(d.error||'restart failed'),'var(--r)'); return; }
  }catch(e){ say('✗ could not reach AOC','var(--r)'); return; }
  if(btn) btn.disabled=true;
  say('Restarting… the page reloads when AOC is back.');
  const t0=Date.now();
  await new Promise(r=>setTimeout(r,4000));
  while(Date.now()-t0<120000){
    try{
      const s=await fetch('/status',{cache:'no-store'}).then(r=>r.json());
      const up=((s.infra_health||{}).monitor_uptime_s);
      if(up!=null&&up<(Date.now()-t0)/1000){ location.reload(); return; }
    }catch(e){}
    await new Promise(r=>setTimeout(r,2000));
  }
  if(btn) btn.disabled=false;
  say('✗ AOC did not come back within 2 min -- run python setup.py --restart in the AOC folder','var(--r)');
}

async function _restoreBackup(filename){
  if(!confirm(`Restore history.db from "${filename}"?\n\nThe current database will be safety-copied first, then overwritten. This affects Session History/Analytics only, not the live dashboard.`)) return;
  const msgEl=document.getElementById('backup-restore-msg');
  if(msgEl){ msgEl.style.color='var(--t3)'; msgEl.textContent='Restoring…'; }
  try{
    const r=await fetch('/restore_backup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filename})});
    const result=await r.json();
    if(result.ok){
      if(msgEl){ msgEl.style.color='var(--g)'; msgEl.textContent=`✓ Restored from ${filename} (previous DB saved as ${result.pre_restore_backup})`; }
      log(`Restored history.db from ${filename}`,'success');
    } else {
      if(msgEl){ msgEl.style.color='var(--r)'; msgEl.textContent=`✗ ${result.error||'restore failed'}`; }
      log('Restore failed: '+(result.error||'unknown error'),'error');
    }
    renderDiag();
  }catch(e){
    if(msgEl){ msgEl.style.color='var(--r)'; msgEl.textContent='✗ request failed: '+e.message; }
  }
}

async function showHistoryDetail(sid,baseUrl,token){
  try{
    const url=baseUrl?(baseUrl+'/history/'+sid+(token?'?token='+encodeURIComponent(token):'')):('/history/'+sid);
    const d=await fetch(url).then(r=>r.json());
    const snap=d.snapshot||{};
    const agents=d.agents||[];
    const files=d.files||[];
    const tags=d.tags||[];
    _histDetailTags=tags;
    const isRemote=!!baseUrl;
    const fmtCost=_fmtCost;
    const fmtDur=_fmtDurShort;
    const col=st=>({done:'var(--g)',error:'var(--r)',running:'var(--c)',waiting:'var(--o)'})[st]||'var(--t3)';
    const body=document.getElementById('hist-body');
    body.innerHTML=`
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
        <button class="adp-btn" onclick="renderHistory()" style="font-size:9px;padding:2px 10px">← BACK</button>
        <span style="font-family:var(--font2);font-size:11px;color:var(--c);letter-spacing:.1em">${escHtml(snap.project||'Session '+sid)}</span>
        <span style="font-family:var(--font2);font-size:9px;color:var(--t3)">${snap.started_at||''}</span>
        ${d.cc_version?`<span style="font-family:var(--font2);font-size:9px;color:var(--t3)" title="Claude Code version">v${escHtml(d.cc_version)}</span>`:''}
        ${baseUrl?'':`<button class="adp-btn" style="font-size:9px;padding:2px 10px;margin-left:auto" onclick="openReplay(${jsq(sid)},'',${jsq(snap.project||'session')})" title="Replay this session step by step from its transcript">▶ REPLAY</button>`}
      </div>
      <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:12px">
        ${tags.map(t=>isRemote
          ?`<span class="le-tag" title="${escHtml(t)}">${escHtml(t)}</span>`
          :`<button class="le-tag" onclick="_historyTagRemove(${jsq(sid)},${jsq(t)})" aria-label="Remove tag ${escHtml(t)}" title="Click to remove">${escHtml(t)} ✕</button>`
        ).join('')}
        ${isRemote?'':`<input type="text" id="hist-tag-input" placeholder="+ tag" maxlength="30" style="font-family:var(--font2);font-size:9px;padding:2px 8px;border-radius:5px;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.03);color:var(--t);width:70px" onkeydown="if(event.key==='Enter'&&this.value.trim()){_historyTagAdd(${jsq(sid)},this.value.trim());this.value='';}">`}
      </div>
      ${agents.length?`
      <div class="hist-section">AGENTS (${agents.length})</div>
      ${agents.map(a=>{
        // parent_id (persisted alongside model/detected_via, feature 102)
        // was never read back here either -- resolve within this same
        // session's agent list, same shape _renderAdp/_agentToMarkdown
        // already resolve it from the live agents list.
        const parent=a.parent_id?agents.find(ag=>ag.agent_id===a.parent_id):null;
        const children=agents.filter(ag=>ag.parent_id===a.agent_id);
        return `<div class="hist-row" style="cursor:default">
        <span style="font-family:var(--font2);font-size:11px;color:${col(a.status)};min-width:36px">${escHtml(a.unit||String(a.agent_id).slice(-4).toUpperCase())}</span>
        <span style="flex:1;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${parent?`<span style="color:var(--t3)" title="Spawned by ${escHtml(parent.name||'')}">↳ </span>`:''}${escHtml(a.name||'')}</span>
        ${children.length?`<span style="font-size:9px;color:var(--t3);font-family:var(--font2)" title="${escHtml(children.map(c=>c.name||'').join(', '))}">${children.length} subagent${children.length!==1?'s':''}↓</span>`:''}
        ${a.model?`<span style="font-size:9px;color:var(--t3);font-family:var(--font2);max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(a.model)}">${escHtml(a.model)}</span>`:''}
        ${a.subagent_type?`<span style="font-size:9px;color:rgba(var(--c-rgb),.75);font-family:var(--font2);background:rgba(var(--c-rgb),.08);border-radius:4px;padding:1px 6px" title="Subagent type">${escHtml(a.subagent_type)}</span>`:''}
        ${a.tool_use_count!=null?`<span style="font-size:9px;color:var(--t3);font-family:var(--font2)" title="Tool calls">${a.tool_use_count} tc</span>`:''}
        ${a.detected_via==='transcript'?`<span class="hookmiss-badge" title="${escHtml(_hookMissTitle(a))}">⚠ HOOK MISS</span>`:''}
        <span class="hist-chip ${a.status==='done'?'ok':a.status==='error'?'err':'cost'}">${a.status}</span>
        ${a.cost?`<span class="hist-chip cost">${fmtCost(a.cost)}</span>`:''}
        <span style="font-family:var(--font2);font-size:9px;color:var(--t3)">${fmtDur(a.duration_s)}</span>
        <span style="font-family:var(--font2);font-size:9px;color:var(--t3)">${a.task_done}/${a.task_total}t</span>
      </div>${a.status==='error'&&a.error_msg?`<div style="margin:-2px 0 4px;padding:6px 10px 6px 46px;font-size:10px;color:rgba(240,164,151,.85);font-family:var(--font2);line-height:1.4;white-space:pre-wrap;word-break:break-all;max-height:120px;overflow-y:auto">${escHtml(a.error_msg)}</div>`:''}`;
      }).join('')}`:''}
      ${files.length?`
      <div class="hist-section">FILES CHANGED (${files.length})</div>
      <div style="display:flex;flex-direction:column;gap:3px">${files.slice(0,30).map(f=>`
        <div class="fe ${f.type==='new'?'new':'changed'}" style="padding:4px 8px;border-radius:6px">
          <span class="fe-badge">${f.type==='new'?'NEW':'MOD'}</span>
          <span class="fe-name" style="font-size:11px">${escHtml(f.path)}</span>
          ${f.lines?`<span class="fe-lines">${escHtml(f.lines)}L</span>`:''}
          <span style="font-size:9px;color:var(--t3);font-family:var(--font2)">${f.agent_id.slice(-4).toUpperCase()}</span>
        </div>`).join('')}${files.length>30?`<div style="font-size:10px;color:var(--t3);padding:3px">+${files.length-30} more</div>`:''}</div>`:''}
    `;
  }catch(e){ log('Error loading session detail: '+e.message,'error'); }
}
async function _historySetTags(sid,tags){
  try{
    const r=await fetch('/history_tags',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({session_id:sid,tags})});
    const result=await r.json();
    if(!result.ok) log('Failed to save tags — session not found','error');
  }catch(e){
    log('Failed to save tags: '+e.message,'error');
  }
  await showHistoryDetail(sid);
}
async function _historyTagAdd(sid,tag){ await _historySetTags(sid,[..._histDetailTags,tag]); }
async function _historyTagRemove(sid,tag){ await _historySetTags(sid,_histDetailTags.filter(t=>t!==tag)); }

/* ── KPI bar ── */
/* pure so it's directly testable -- see tests/js/success_rate.test.js */
function _computeSuccessRate(todayAgents, todayDone){
  return todayAgents>0 ? Math.round((todayDone||0)/todayAgents*100) : null;
}
let _kpiAnalytics = null;
let _kpiLastFetch = 0;
let _prevCost = -1;
let _budgetAlerted = false; // fires _browserNotify once per crossing, not every render tick
async function renderKpi(sr){
  const now = Date.now();
  if(now - _kpiLastFetch > 30000){
    _kpiLastFetch = now;
    try{ _kpiAnalytics = await fetch('/analytics').then(r=>r.json()); }catch(e){}
  }
  const agents = sr.agents||[];
  const runCount = agents.filter(a=>a.status==='running'||a.status==='waiting').length;
  const fmtDur = s=>_fmtDurShort(s,true);
  const fmtCost = c=>c!=null&&c>0?'$'+(+c).toFixed(3):'$0';
  const td = (_kpiAnalytics||{}).today||{};
  const total = (_kpiAnalytics||{}).total||{};
  const todayAgents = td.agents!=null ? td.agents : null;
  const todayDone   = td.done!=null   ? td.done   : null;
  const todayErrors = td.errors!=null ? td.errors  : null;
  const successRate = _computeSuccessRate(todayAgents, todayDone);
  const el = id=>document.getElementById(id);
  const set = (id,val,cls='')=>{ const e=el(id); if(e){ e.textContent=val; e.className='kpi-val'+(cls?' '+cls:''); }};
  const sessionCount = (sr.sessions_list||[]).filter(s=>s.session_active).length || (sr.sessions_count||0);
  const errCount = agents.filter(a=>a.status==='error').length;
  set('kpi-running', runCount, runCount>0?'green':'');
  set('kpi-sessions', sessionCount||'—', sessionCount>0?'cyan':'');
  const sessEl=el('kpi-sessions');
  // avg_duration_s sits right next to agents/done/errors in the same `today`
  // DB row (all from _db_analytics), but unlike its siblings it was never
  // read anywhere -- the SESSIONS tile's title was a static "Active CLI
  // sessions" string that never got rewritten, mirroring the kpi-errors
  // tooltip pattern just below.
  if(sessEl && sessEl.parentElement) sessEl.parentElement.title = td.avg_duration_s!=null
    ? `Active CLI sessions — avg session duration today: ${_fmtDurationDHM(td.avg_duration_s)}`
    : 'Active CLI sessions';
  const errEl2=el('kpi-errors');
  if(errEl2){
    errEl2.textContent=errCount||'—';
    errEl2.style.color=errCount>0?'var(--r)':'var(--t3)';
    /* errCount only covers agents still visible on CARDS -- older sessions
       swept out of the live view don't count. todayErrors (from /analytics,
       a straight DB sum) is the true today-wide total, so surface it here
       rather than adding a whole new KPI card just for one number. */
    if(errEl2.parentElement) errEl2.parentElement.title = todayErrors!=null
      ? `Agents with errors (visible now) — ${todayErrors} today across all sessions`
      : 'Agents with errors';
  }
  const succEl=el('kpi-success');
  if(succEl){
    succEl.textContent=successRate!=null?successRate+'%':'—';
    succEl.style.color=successRate==null?'var(--t3)':successRate>=90?'rgba(76,194,133,.9)':successRate>=70?'var(--o)':'var(--r)';
  }
  set('kpi-total', total.agents!=null?total.agents:'—');
  /* ── cost gauge — sums real per-model estimated_cost from transcript scanner ── */
  const sessionCost = (sr.sessions_list||[]).reduce((s,sess)=>s+(sess.estimated_cost||0),0);
  const totalTokens = (sr.sessions_list||[]).reduce((s,sess)=>s+(sess.input_tokens||0)+(sess.output_tokens||0),0);
  const arcLen = 88;
  const pct = _budgetLimit>0 ? Math.min(sessionCost/_budgetLimit,1) : 0;
  const arcOffset = arcLen * (1 - pct);
  const arcColor = pct>0.9?'#ff5050':pct>0.65?'#ffa040':'rgba(76,194,133,.85)';
  const gaugeArc = el('cost-gauge-arc');
  if(gaugeArc){ gaugeArc.style.strokeDashoffset=arcOffset; gaugeArc.style.stroke=arcColor; }
  const tokFmt = totalTokens>=1e6?(totalTokens/1e6).toFixed(2)+'M':totalTokens>=1000?Math.round(totalTokens/1000)+'K':String(totalTokens||0);
  const textColor = sessionCost>0?(pct>0.9?'#ff5050':pct>0.65?'#ffa040':'rgba(76,194,133,.9)'):'rgba(255,255,255,.3)';
  const costEl = el('kpi-cost');
  if(costEl){ costEl.textContent='$'+sessionCost.toFixed(3); costEl.setAttribute('fill',textColor); }
  const tokEl = el('cost-gauge-tok');
  if(tokEl) tokEl.textContent=totalTokens>0?tokFmt+' tok':'— tok';
  if(sessionCost>_prevCost&&_prevCost>=0){
    const svg=el('cost-gauge-svg');
    if(svg){ svg.classList.remove('gflash'); void svg.offsetWidth; svg.classList.add('gflash'); }
  }
  _prevCost=sessionCost;
  /* budget alert -- fires once per crossing (not every ~poll tick while over
     budget), re-arms once cost drops back under the limit (new session/reset) */
  if(_budgetLimit>0&&sessionCost>=_budgetLimit&&!_budgetAlerted){
    _budgetAlerted=true;
    _browserNotify('AOC — Budget exceeded', `Session cost $${sessionCost.toFixed(3)} has reached your $${_budgetLimit.toFixed(2)} budget`);
  } else if(sessionCost<_budgetLimit){
    _budgetAlerted=false;
  }
  /* Per-project budget alerts moved server-side (_webhook_notify_worker's
     budget_alert check, monthly month-to-date spend against
     _notify_settings["project_budgets"]) -- fires toast + webhook and
     works with no browser tab open, unlike this tab-open-only
     _browserNotify path it replaced. _projectBudgets is still read here
     only by _renderProjectBudgetSummary's visual indicator in Settings. */
  _renderTopProjects(_kpiAnalytics);
}

/* Top projects by cost today -- pure function, takes by_day_project (an
   /analytics field renderKpi already fetches every 30s into _kpiAnalytics
   for the KPI bar, so this needs no extra fetch of its own) plus today's
   date string, and returns the top N {project,cost} pairs for today only.
   Visible at a glance on CARDS instead of only inside HISTORY → ANALYTICS. */
function _topProjectsToday(byDayProject, todayStr, topN=3){
  const todayRows=(byDayProject||[]).filter(r=>r.date===todayStr && (r.cost||0)>0);
  return todayRows
    .map(r=>({project:r.project||'(none)', cost:r.cost||0}))
    .sort((a,b)=>b.cost-a.cost)
    .slice(0,topN);
}
/* Week-over-week cost trend -- same last-7-vs-preceding-7 by_day slicing
   as _build_digest_summary's own math server-side (see that function's
   docstring for the "array position, not calendar-exact" caveat), just
   computed here client-side off the same by_day the KPI bar's own 30s
   /analytics fetch already carries. The weekly digest already surfaces
   this exact number, but only once a week in a toast/webhook -- this
   makes it visible live, at a glance, without waiting for that schedule
   or opening History -> Analytics. */
/* Pure: cost of the last 7 calendar days (today included) vs the 7 before.
   by_day only has rows for days with sessions, so windows go by date, not
   by row count (7 rows could span a month). todayStr = local YYYY-MM-DD. */
function _computeWeekOverWeekCost(byDay, todayStr){
  const day=s=>Date.UTC(+s.slice(0,4),+s.slice(5,7)-1,+s.slice(8,10))/864e5;
  let cost=0, prevCost=0;
  if(/^\d{4}-\d\d-\d\d$/.test(todayStr||'')){
    const t=day(todayStr);
    (byDay||[]).forEach(r=>{
      if(!r||!/^\d{4}-\d\d-\d\d/.test(r.date||'')) return;
      const age=t-day(r.date);
      if(age>=0&&age<7) cost+=r.cost||0;
      else if(age>=7&&age<14) prevCost+=r.cost||0;
    });
  }
  const pctChange=prevCost>0?((cost-prevCost)/prevCost*100):null;
  return {cost, prevCost, pctChange};
}
function _localDateStr(d){
  d=d||new Date();
  return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
}
/* The centred clock and the right-hand group grow independently (badges come
   and go), so at ~1300-1650 px the clock sat on top of Search and buttons fell
   off the right edge. Step down until nothing collides: icon-only search,
   no clock, then drop the least-used buttons and badge labels. */
function _fitTopbar(){
  const tb=document.querySelector('.topbar');
  if(!tb) return;
  const steps=['tb-lbl','tb-tight','tb-noclock','tb-squeeze'];
  tb.classList.remove(...steps);
  const ck=tb.querySelector('.clock');
  const tbR=()=>tb.getBoundingClientRect().right;
  const bad=()=>{
    const kids=[...tb.querySelectorAll('.cc-pill,#rl-stat,.topbar-right > *')].filter(e=>e!==ck&&e.offsetParent&&e.getBoundingClientRect().width);
    if(kids.some(e=>e.getBoundingClientRect().right>tbR()+1)) return true;
    if(!ck||!ck.offsetParent||getComputedStyle(ck).display==='none') return false;
    const c=ck.getBoundingClientRect();
    return kids.some(e=>{const r=e.getBoundingClientRect(); return r.right>c.left-12&&r.left<c.right+12;});
  };
  for(const s of steps){ if(!bad()) return; tb.classList.add(s); }
}
window.addEventListener('resize',()=>{ clearTimeout(window._fitTbT); window._fitTbT=setTimeout(_fitTopbar,80); });
function _renderTopProjects(analytics){
  const bar=document.getElementById('top-projects-bar');
  if(!bar) return;
  const todayStr=new Date().toISOString().slice(0,10);
  const top=_topProjectsToday((analytics||{}).by_day_project, todayStr);
  const wow=_computeWeekOverWeekCost((analytics||{}).by_day, _localDateStr());
  const wowHtml=wow.pctChange!=null
    ?`<span title="Last 7 days: $${wow.cost.toFixed(2)} — preceding 7 days: $${wow.prevCost.toFixed(2)}">7d cost <span style="color:${wow.pctChange>=0?'rgba(240,164,151,.9)':'rgba(76,194,133,.9)'}">${wow.pctChange>=0?'▲':'▼'} ${Math.abs(wow.pctChange).toFixed(0)}%</span> vs prior wk</span>`
    :'';
  if(!top.length && !wowHtml){ bar.style.display='none'; bar.innerHTML=''; return; }
  bar.style.display='flex';
  const topHtml=top.length?`<span style="letter-spacing:.1em;opacity:.7">TOP TODAY</span>`+top.map((r,i)=>
    `<span title="${escHtml(r.project)}">${i+1}. ${escHtml(r.project.length>18?r.project.slice(0,18)+'…':r.project)} <span style="color:rgba(76,194,133,.85)">$${r.cost.toFixed(2)}</span></span>`
  ).join('<span style="opacity:.3">·</span>'):'';
  bar.innerHTML=[wowHtml, topHtml].filter(Boolean).join('<span style="opacity:.3">·</span>');
}

/* ── poll ── */
/* single place that applies a fresh status payload to the UI — used to be
   duplicated between an initial poll() and a separate setInterval body that
   had quietly drifted (missing _tunnelUpdateUI, for one) */
let _histLastRender=0;
let _diagLastRender=0;
function _applyStatus(sr){
  sr=_mergeRemoteData(sr);
  diffStatusWithRemove(lastStatus,sr);
  checkStaleAgents(sr);
  lastStatus=sr;
  _sessStartMs=sr.session_active&&sr.started_at?parseTimeStr(sr.started_at):null;
  if(!sr.session_active) document.getElementById('s-elapsed').textContent='—';
  renderAgents(sr);
  renderKpi(sr);
  _fitTopbar();
  _tunnelUpdateUI(sr.tunnel);
  _selfUpdateUpdateUI(sr.self_update);
  _infraUpdateUI(sr.infra_health);
  _rateLimitUpdateUI(sr.rate_limits);
  _waitingStripUpdate(sr);
  _updateSweepButton(sr);
  _ccStatusAt=Date.now(); _sbUpdate(sr); _ccPillUpdate();
  if(_adpAgentId) _renderAdp();
  if(currentView==='command')  renderCommand(sr);
  if(currentView==='today')    renderToday(sr);
  if(currentView==='inbox')    renderInbox(sr);
  if(currentView==='summary')  renderSummary(sr);
  if(currentView==='graph')    renderGraph(sr);
  if(currentView==='heat')     renderHeatmap(sr);
  if(currentView==='tree')     renderTree(sr);
  if(currentView==='timeline') renderTimeline(sr);
  if(currentView==='history'&&_histTab!=='search'&&_histTab!=='cache'&&_histTab!=='commits'&&_histTab!=='memory'&&_histTab!=='worklog'&&Date.now()-_histLastRender>10000){ _histLastRender=Date.now(); renderHistory(); }
  if(currentView==='diag'&&Date.now()-_diagLastRender>10000){ _diagLastRender=Date.now(); renderDiag(); }
  _renderRpFiles(sr); // always: keeps the FILES count badge current; paints only when that tab is open
  if(_rightTab==='audit') _renderRpAudit();
  if(_rightTab==='errors') _renderRpErrors();
  if(_cmpOpen) _renderCompare();
  if(_sessCmpOpen) _renderSessionCompare();
}

