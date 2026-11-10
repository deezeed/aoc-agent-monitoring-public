/* ── Tunnel UI (Cloudflare / ngrok) ── */
let _tunnelProvider='cloudflare';
let _lastTunnelInfo=null;
function _tunnelSetProvider(p){
  _tunnelProvider=p;
  const cfBtn=document.getElementById('tunnel-provider-cf'), ngBtn=document.getElementById('tunnel-provider-ngrok');
  if(cfBtn) cfBtn.style.background=p==='cloudflare'?'rgba(var(--c-rgb),.18)':'';
  if(ngBtn) ngBtn.style.background=p==='ngrok'?'rgba(var(--c-rgb),.18)':'';
  const descCf=document.getElementById('tunnel-desc-cf'), descNg=document.getElementById('tunnel-desc-ngrok');
  if(descCf) descCf.style.display=p==='cloudflare'?'':'none';
  if(descNg) descNg.style.display=p==='ngrok'?'':'none';
  _tunnelUpdateUI(_lastTunnelInfo);
}
async function _tunnelSaveNgrokToken(){
  const input=document.getElementById('tunnel-ngrok-token-input');
  const msg=document.getElementById('tunnel-ngrok-auth-msg');
  const token=input?.value?.trim();
  if(!token) return;
  if(msg){ msg.textContent='Saving…'; msg.style.color='var(--t3)'; }
  const r=await fetch('/tunnel/ngrok_authtoken',{method:'POST',body:JSON.stringify({authtoken:token})}).then(r=>r.json()).catch(()=>({ok:false,error:'network error'}));
  if(input) input.value='';  // don't leave the secret sitting in the DOM
  if(msg){
    msg.textContent=r.ok?'Token saved ✓':('Error: '+(r.error||'?'));
    msg.style.color=r.ok?'rgba(76,194,133,.9)':'var(--r)';
  }
}
function _tunnelUpdateUI(tunnel){
  _lastTunnelInfo=tunnel;
  if(!tunnel) return;
  const lbl=document.getElementById('tunnel-status-lbl');
  const startBtn=document.getElementById('tunnel-start-btn');
  const stopBtn=document.getElementById('tunnel-stop-btn');
  const urlRow=document.getElementById('tunnel-url-row');
  const urlInput=document.getElementById('tunnel-url-input');
  const dlRow=document.getElementById('tunnel-dl-row');
  const dlBar=document.getElementById('tunnel-dl-bar');
  const dlLbl=document.getElementById('tunnel-dl-lbl');
  const badge=document.getElementById('tunnel-badge');
  const badgeLbl=document.getElementById('tunnel-badge-lbl');
  const tokenNote=document.getElementById('tunnel-token-note');
  const ngrokAuthRow=document.getElementById('tunnel-ngrok-auth-row');
  const ngrokMissingRow=document.getElementById('tunnel-ngrok-missing');

  const s=tunnel.status;
  const url=tunnel.url||'';
  const idle=s==='off'||s==='error';
  /* while a tunnel is starting/ready, reflect which provider is actually running,
     not just the locally-selected one */
  const activeProvider=idle?_tunnelProvider:(tunnel.provider||_tunnelProvider);

  if(lbl) lbl.textContent='Status: '+({'off':'OFF','starting':'Starting…','downloading':'Downloading cloudflared…','ready':'ACTIVE','error':'ERROR'}[s]||s).toUpperCase();
  if(lbl) lbl.style.color=s==='ready'?'rgba(76,194,133,.9)':s==='error'?'var(--r)':s==='off'?'var(--t3)':'rgba(var(--c-rgb),.8)';
  /* ngrok_found was already computed and shipped in /status but never
     read here -- the UI used to show the "enter your authtoken" box
     regardless of whether ngrok.exe was even installed, which is
     misleading (typing a token in that case can't help). Distinguish
     the two failure modes: binary missing vs. binary present but
     unauthenticated. */
  const ngrokBinaryMissing=activeProvider==='ngrok'&&idle&&!tunnel.ngrok_found;
  const ngrokNotReady=activeProvider==='ngrok'&&idle&&!tunnel.ngrok_authtoken_ok;
  if(startBtn){
    startBtn.style.display=idle?'':'none';
    startBtn.disabled=ngrokNotReady;
    startBtn.style.opacity=ngrokNotReady?'.4':'';
    startBtn.title=ngrokBinaryMissing?'ngrok.exe not found':(ngrokNotReady?'ngrok authtoken not configured':'');
  }
  if(stopBtn)  stopBtn.style.display=idle?'none':'';
  if(urlRow)   urlRow.style.display=s==='ready'?'flex':'none';
  if(dlRow)    dlRow.style.display=s==='downloading'?'flex':'none';
  if(ngrokMissingRow) ngrokMissingRow.style.display=ngrokBinaryMissing?'flex':'none';
  if(ngrokAuthRow) ngrokAuthRow.style.display=(idle&&_tunnelProvider==='ngrok'&&!tunnel.ngrok_authtoken_ok&&tunnel.ngrok_found)?'flex':'none';
  if(url&&urlInput){ urlInput.value=url+(window._AOC_TOKEN?'?token='+encodeURIComponent(window._AOC_TOKEN):''); }
  if(dlBar)    dlBar.style.width=(tunnel.dl_pct||0)+'%';
  if(dlLbl)    dlLbl.textContent='Downloading cloudflared… '+(tunnel.dl_pct||0)+'%';
  if(tokenNote) tokenNote.textContent=window._AOC_TOKEN||'(none)';
  /* provider toggle disabled while a tunnel is starting/ready — stop it first */
  const provRow=document.getElementById('tunnel-provider-row');
  if(provRow) provRow.style.opacity=idle?'':'.4';
  const cfBtn=document.getElementById('tunnel-provider-cf'), ngBtn=document.getElementById('tunnel-provider-ngrok');
  if(cfBtn) cfBtn.disabled=!idle;
  if(ngBtn) ngBtn.disabled=!idle;
  /* header badge */
  if(badge) badge.style.display=s==='ready'?'flex':'none';
  if(badgeLbl&&url) badgeLbl.textContent=url.replace('https://','');
  const errDetail=document.getElementById('tunnel-error-detail');
  if(errDetail){
    errDetail.style.display=(s==='error'&&tunnel.error_detail)?'':'none';
    errDetail.textContent=tunnel.error_detail||'';
  }
}
function _selfUpdateUpdateUI(su){
  const badge=document.getElementById('selfupdate-badge');
  if(!badge) return;
  if(!su||!su.stale){ badge.style.display='none'; return; }
  badge.style.display='flex';
  /* checked_at was already computed and shipped alongside stale/behind_by
     but never read here -- the self-update worker only checks every 15min,
     so knowing how fresh this specific reading is matters. */
  const checkedAgo=_fmtEpochAgo(su.checked_at);
  const lbl=document.getElementById('selfupdate-badge-lbl');
  if(su.mode==='release'){
    /* installer build: no git to pull -- one click downloads, verifies and
       installs the new release (job = /selfupdate/install's progress) */
    const job=su.job||{};
    if(['downloading','verifying','installing'].includes(job.state)){
      if(lbl) lbl.textContent=job.state==='downloading'?'UPDATING '+(job.pct||0)+'%':job.state==='verifying'?'VERIFYING':'INSTALLING';
      badge.style.cursor='default';
      badge.onclick=null;
      badge.title=job.state==='installing'
        ?'Installing AOC '+(job.version||su.latest)+' — the dashboard reconnects when AOC has restarted'
        :'Downloading AOC '+(job.version||su.latest)+' and checking its SHA-256';
      return;
    }
    const failed=job.state==='error';
    if(lbl) lbl.textContent=failed?'UPDATE FAILED':'UPDATE '+su.latest;
    badge.style.cursor='pointer';
    badge.onclick=()=>_selfUpdateInstall(su);
    badge.title=(failed?'Update failed: '+(job.error||'unknown error')+' — click to try again. ':'')
      +'AOC '+su.latest+' is available (you have '+su.installed+') — click to update'+(checkedAgo?' (checked '+checkedAgo+')':'');
    return;
  }
  if(lbl) lbl.textContent='UPDATE AVAILABLE';
  badge.style.cursor='default';
  badge.onclick=null;
  badge.title=su.behind_by+' commit'+(su.behind_by!==1?'s':'')+' behind origin/master — run git pull'+(checkedAgo?' (checked '+checkedAgo+')':'');
}
async function _selfUpdateInstall(su){
  if(!confirm(`Update AOC to ${su.latest}?\n\nAOC downloads the installer, checks its SHA-256 against the release notes and installs it in the background. The dashboard is gone for about a minute while AOC restarts; your Claude sessions keep running.`)) return;
  let d;
  try{ d=await (await fetch('/selfupdate/install',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json(); }
  catch(e){ d={ok:false,error:'AOC did not answer'}; }
  if(d.ok) return;  /* progress shows on the badge via /status */
  if(confirm('Could not start the update: '+(d.error||'unknown error')+'\n\nOpen the download page instead?')) window.open(su.url,'_blank','noopener');
}
/* Pure: /whats_new -> the dialog's inner HTML ('' = nothing to show).
   Items are release-note text from GitHub, so everything is escaped. */
function _whatsNewHtml(d){
  if(!d||!Array.isArray(d.items)||!d.items.length) return '';
  const lis=d.items.map(t=>'<li>'+escHtml(String(t))+'</li>').join('');
  return '<div class="wn-box" role="document">'
    +'<div class="wn-hdr"><span class="wn-title">WHAT\'S NEW IN AOC '+escHtml(String(d.version||''))+'</span>'
    +'<button class="wn-x" onclick="_whatsNewClose()" aria-label="Close">✕</button></div>'
    +'<ul class="wn-list">'+lis+'</ul>'
    +'<div class="wn-foot"><button class="wn-ok" onclick="_whatsNewClose()">Got it</button></div></div>';
}
let _whatsNewShown=false;
async function _whatsNewCheck(){
  if(_whatsNewShown) return;
  let d;
  try{ d=await (await fetch('/whats_new')).json(); }catch(e){ return; }
  const html=_whatsNewHtml(d);
  if(!html) return;
  _whatsNewShown=true;
  const ov=document.createElement('div');
  ov.id='wn-overlay'; ov.className='wn-overlay';
  ov.setAttribute('role','dialog'); ov.setAttribute('aria-modal','true'); ov.setAttribute('aria-label',"What's new");
  ov.onclick=e=>{ if(e.target===ov) _whatsNewClose(); };
  ov.innerHTML=html;
  document.body.appendChild(ov);
}
function _whatsNewClose(){
  const ov=document.getElementById('wn-overlay');
  if(ov) ov.remove();
  fetch('/whats_new/dismiss',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).catch(()=>{});
}
/* the notes of a hand-installed release are fetched in the background
   right after startup, so look again a little later too */
[1500,30000,120000].forEach(ms=>setTimeout(_whatsNewCheck,ms));
/* pure so it's directly testable -- see tests/js/fmt_epoch_ago.test.js.
   watchdog/sentinel last_log_ts (both Python epoch seconds) were already
   computed and shipped in /status's infra_health, but _infraUpdateUI only
   ever showed the raw log line text, never how stale it is. */
function _fmtEpochAgo(epochSeconds){
  if(epochSeconds==null) return '';
  const diff=Math.floor(Date.now()/1000-epochSeconds);
  if(diff<10) return 'just now'; // also swallows small negative diffs from clock skew
  if(diff<60) return diff+'s ago';
  if(diff<3600) return Math.floor(diff/60)+'m ago';
  if(diff<86400) return Math.floor(diff/3600)+'h ago';
  return Math.floor(diff/86400)+'d ago';
}
/* Pure: the plan-limit forecast for one /status rate_limits window --
   null when there's nothing to forecast (no pace yet, resets first, or
   already hit), else {text, advice, soon}. soon = runs out within 30 min. */
function _limitForecast(w, nowS){
  if(!w||w.status==='hit'||!w.eta_full_at) return null;
  const left=Math.max(60,w.eta_full_at-nowS), m=Math.ceil(left/60);
  const inTxt=m<60?`~${m} min`:`~${Math.floor(m/60)} h${m%60?' '+(m%60)+' min':''}`;
  const dt=new Date(w.resets_at*1000), clock=String(dt.getHours()).padStart(2,'0')+':'+String(dt.getMinutes()).padStart(2,'0');
  return {text:`At this pace you hit the limit in ${inTxt} · resets ${clock}`,
    advice:w.soon?'Pause new agents or switch to a cheaper model':'', soon:!!w.soon, inTxt};
}
/* Pure: the /status rate_limits block -> what the topbar shows. Picks the
   most severe window (hit > warn > ok), 5-hour first on a tie, since that's
   the one that interrupts work. nowS = epoch seconds. */
function _rateLimitDisplay(rl, nowS){
  const ws=((rl||{}).windows||[]).filter(w=>w.status!=='reset'&&typeof w.pct==='number');
  if(!ws.length) return {show:false};
  const rank={hit:2,warn:1,ok:0};
  const w=ws.slice().sort((a,b)=>(rank[b.status]||0)-(rank[a.status]||0))[0];
  const hm=s=>{ s=Math.max(0,Math.round(s)); const d=Math.floor(s/86400), h=Math.floor(s%86400/3600), m=Math.floor(s%3600/60);
    return d?`${d}d ${h}h`:h?`${h}h ${m}m`:`${m}m`; };
  const clock=e=>{ const dt=new Date(e*1000); return String(dt.getHours()).padStart(2,'0')+':'+String(dt.getMinutes()).padStart(2,'0'); };
  const name=k=>k==='five_hour'?'5-hour':'Weekly';
  const lines=ws.map(x=>{
    let t=`${name(x.kind)}: ${x.pct.toFixed(0)}% used · resets in ${hm(x.resets_at-nowS)}`;
    if(x.pace_pct_per_h!=null) t+=` · pace ${x.pace_pct_per_h.toFixed(1)}%/h`;
    if(x.eta_full_at) t+=` → hits 100% at ${clock(x.eta_full_at)}${x.soon?' (pause new agents or switch to a cheaper model)':''}`;
    else if(x.projected_pct!=null) t+=` → ~${Math.min(100,x.projected_pct).toFixed(0)}% at reset`;
    if(x.source==='limit_message') t+=' (from the limit message)';
    return t;
  });
  if(rl.age_s!=null) lines.push(`Updated ${hm(rl.age_s)} ago by the Claude Code statusline`);
  const left=w.resets_at-nowS;
  return {
    show:true, level:w.status,
    val:w.status==='hit'?'LIMIT':`${w.pct.toFixed(0)}%`,
    lbl:`${w.kind==='five_hour'?'5h':'Week'} · ↻ ${hm(left)}`,
    fill:Math.min(100,Math.max(0,w.pct)),
    proj:w.projected_pct!=null&&w.status!=='hit'?Math.min(100,w.projected_pct):null,
    title:lines.join('\n'),
  };
}
function _rateLimitUpdateUI(rl){
  const d=_rateLimitDisplay(rl, Date.now()/1000);
  document.querySelectorAll('.rl-part').forEach(el=>{ el.style.display=d.show?'':'none'; });
  if(!d.show) return;
  const stat=document.getElementById('rl-stat');
  stat.classList.toggle('rl-warn', d.level==='warn');
  stat.classList.toggle('rl-hit', d.level==='hit');
  stat.title=d.title;
  document.getElementById('rl-val').textContent=d.val;
  document.getElementById('rl-lbl').textContent=d.lbl;
  document.getElementById('rl-fill').style.width=d.fill+'%';
  const proj=document.getElementById('rl-proj');
  proj.style.display=d.proj!=null?'block':'none';
  if(d.proj!=null) proj.style.left=`calc(${d.proj}% - 1px)`;
}
function _infraUpdateUI(infra){
  if(!infra) return;
  const wd=infra.watchdog||{}, sn=infra.sentinel||{};
  const badge=document.getElementById('infra-badge');
  const badgeLbl=document.getElementById('infra-badge-lbl');
  const unhealthy=!wd.pid_alive||sn.stale;
  if(badge) badge.style.display=unhealthy?'flex':'none';
  if(badgeLbl) badgeLbl.textContent=!wd.pid_alive?'WATCHDOG DOWN':'SENTINEL STALE';

  const wdDot=document.getElementById('infra-wd-dot');
  const wdStatus=document.getElementById('infra-wd-status');
  const wdDetail=document.getElementById('infra-wd-detail');
  if(wdDot) wdDot.style.background=wd.pid_alive?'var(--g)':'var(--r)';
  if(wdStatus){ wdStatus.textContent=wd.pid_alive?'RUNNING':'NOT RUNNING'; wdStatus.style.color=wd.pid_alive?'var(--g)':'var(--r)'; }
  if(wdDetail){
    const restarts=wd.restarts_recent||0;
    const wdAgo=_fmtEpochAgo(wd.last_log_ts);
    wdDetail.textContent=(restarts>0?restarts+' restart'+(restarts!==1?'s':'')+' recently — ':'')+(wd.last_log_line||'no log yet')+(wdAgo?' ('+wdAgo+')':'');
  }

  const snDot=document.getElementById('infra-sn-dot');
  const snStatus=document.getElementById('infra-sn-status');
  const snDetail=document.getElementById('infra-sn-detail');
  if(snDot) snDot.style.background=sn.stale?'var(--r)':'var(--g)';
  if(snStatus){ snStatus.textContent=sn.stale?'STALE':'OK'; snStatus.style.color=sn.stale?'var(--r)':'var(--g)'; }
  if(snDetail){
    const snAgo=_fmtEpochAgo(sn.last_log_ts);
    snDetail.textContent=(sn.last_log_line||'no log yet')+(snAgo?' ('+snAgo+')':'');
  }

  const uptimeEl=document.getElementById('infra-uptime');
  if(uptimeEl) uptimeEl.textContent=_fmtDurationDHM(infra.monitor_uptime_s);

  const fmtBreakdown=counts=>{
    const total=Object.values(counts||{}).reduce((a,b)=>a+b,0);
    if(!total) return '0';
    const parts=Object.entries(counts).sort((a,b)=>b[1]-a[1]).map(([k,v])=>`${k}:${v}`).join(', ');
    return `${total} (${parts})`;
  };
  const alertEl=document.getElementById('infra-alert-audit');
  if(alertEl) alertEl.textContent=fmtBreakdown(infra.alert_audit_7d);
  const killEl=document.getElementById('infra-kill-audit');
  if(killEl) killEl.textContent=fmtBreakdown(infra.kill_audit_7d);
}
function _renderLicenseUI(){
  const badge=document.getElementById('license-tier-badge');
  const emailNote=document.getElementById('license-email-note');
  const activeRow=document.getElementById('license-active-row');
  const inactiveRow=document.getElementById('license-inactive-row');
  const isPro=!!window._AOC_IS_PRO;
  if(badge){
    badge.textContent=isPro?'PRO':'FREE';
    badge.style.background=isPro?'rgba(76,194,133,.12)':'rgba(255,255,255,.06)';
    badge.style.color=isPro?'rgba(76,194,133,.9)':'var(--t3)';
  }
  if(emailNote) emailNote.textContent=isPro&&window._AOC_LICENSE_EMAIL?window._AOC_LICENSE_EMAIL:'';
  if(activeRow)   activeRow.style.display=isPro?'flex':'none';
  if(inactiveRow) inactiveRow.style.display=isPro?'none':'flex';
  /* Pro-gated controls elsewhere in Settings: lock instead of hide, so a
     Free user can see what they'd unlock rather than wondering if the
     button is just missing */
  const startBtn=document.getElementById('tunnel-start-btn');
  if(startBtn){ startBtn.disabled=startBtn.disabled||!isPro; if(!isPro) startBtn.title='Pro feature -- Settings -> LICENSE to upgrade'; }
  const addMachineBtn=document.getElementById('btn-add-remote-machine');
  if(addMachineBtn){ addMachineBtn.disabled=!isPro; addMachineBtn.title=isPro?'':'Pro feature -- Settings -> LICENSE to upgrade'; addMachineBtn.style.opacity=isPro?'':'.4'; }
}
async function _activateLicense(){
  const input=document.getElementById('license-key-input');
  const msg=document.getElementById('license-msg');
  const key=input?.value?.trim();
  if(!key) return;
  if(msg){ msg.textContent='Activating…'; msg.style.color='var(--t3)'; }
  const r=await fetch('/license',{method:'POST',body:JSON.stringify({key})}).then(r=>r.json()).catch(()=>({ok:false,error:'network error'}));
  if(r.ok){
    window._AOC_IS_PRO=true;
    window._AOC_LICENSE_EMAIL=r.email||null;
    if(input) input.value='';
    if(msg){ msg.textContent='Activated -- reloading…'; msg.style.color='rgba(76,194,133,.9)'; }
    setTimeout(()=>location.reload(), 600);
  } else if(msg){
    msg.textContent='Error: '+(r.error||'invalid key');
    msg.style.color='var(--r)';
  }
}
async function _deactivateLicense(){
  await fetch('/license',{method:'DELETE'});
  location.reload();
}
async function _tunnelStart(){
  const lbl=document.getElementById('tunnel-status-lbl');
  if(lbl) lbl.textContent='Status: STARTING…';
  const errDetail=document.getElementById('tunnel-error-detail');
  if(errDetail) errDetail.style.display='none';
  const r=await fetch('/tunnel/start',{method:'POST',body:JSON.stringify({provider:_tunnelProvider})}).then(r=>r.json()).catch(()=>null);
  /* tunnel auto-provisions a token server-side if none existed; adopt it now so
     this already-open tab keeps authenticating once the localhost bypass is
     disabled for tunnel-exposed traffic (see _check_auth in monitor.py) */
  if(r&&r.token&&r.token!==window._AOC_TOKEN){
    window._AOC_TOKEN=r.token;
    /* the status WebSocket was opened with the old (possibly empty) token in
       its URL — a WebSocket can't have its URL updated in place, so reopen */
    _connectEvents();
  }
}
async function _tunnelStop(){
  await fetch('/tunnel/stop',{method:'POST'});
  if(document.getElementById('tunnel-status-lbl'))
    document.getElementById('tunnel-status-lbl').textContent='Status: OFF';
}
function _tunnelCopyUrl(){
  const u=document.getElementById('tunnel-url-input');
  if(!u) return;
  navigator.clipboard?.writeText(u.value).then(()=>log('Tunnel URL copied','success')).catch(()=>{u.select();document.execCommand('copy');log('URL copied','success');});
}

async function testWebhook(){
  const url=document.getElementById('st-webhook')?.value?.trim();
  const st=document.getElementById('st-wh-status');
  if(!url){ if(st) st.textContent='No URL set'; return; }
  if(st) st.textContent='Sending...';
  try{
    await fetch('/webhook_test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});
    if(st){ st.textContent='✓ Sent'; st.style.color='var(--g)'; }
  }catch(e){
    if(st){ st.textContent='✗ Failed'; st.style.color='var(--r)'; }
  }
}
async function _fireWebhook(event, agent){
  if(!_webhookUrl) return;
  if(!_webhookEvents[event]) return;
  try{
    // Mirror _webhook_notify_worker's agent_payload field-for-field (monitor.py
    // ~1262-1269) -- that's the server-side path that fires when no browser
    // tab is open, and it was enriched with project/model/error_message so an
    // external consumer (Slack/Discord/a bot) doesn't have to open the
    // dashboard to know what an event was even about. This client-only path
    // (fires while a tab IS open) never got the same fields, so the exact
    // same event for the exact same agent produced a strictly worse payload
    // depending purely on whether a tab happened to be open.
    const agentPayload={id:agent.id,name:agent.name,status:agent.status,
      cost:agent.tokens_used?+_agentCost(agent).toFixed(4):0,
      project:agent.session_project||'', model:agent.model||''};
    if(event==='error'&&agent.error_message) agentPayload.error_message=agent.error_message.slice(0,500);
    fetch('/webhook_fire',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      url:_webhookUrl, event, agent:agentPayload
    })});
  }catch(e){}
}
/* Same job as _compute_burn_rates/_is_burn_spike in monitor.py -- kept in
   sync by hand (same as the stuck-detector logic, which also lives
   independently in both languages) rather than sharing code across a
   Python/JS boundary that doesn't otherwise exist in this app. */
function _computeBurnRates(history,now,recentWindowS=180,minHistoryS=300){
  if(history.length<2) return null;
  const [oldestTs,oldestTok]=history[0];
  const [newestTs,newestTok]=history[history.length-1];
  const observedS=newestTs-oldestTs;
  if(observedS<minHistoryS) return null;
  const avgRate=(newestTok-oldestTok)/(observedS/60);
  const recentCutoff=now-recentWindowS;
  const recent=history.filter(([t])=>t>=recentCutoff);
  if(recent.length<2) return null;
  const [rOldestTs,rOldestTok]=recent[0];
  const [rNewestTs,rNewestTok]=recent[recent.length-1];
  const rObservedS=rNewestTs-rOldestTs;
  if(rObservedS<=0) return null;
  const recentRate=(rNewestTok-rOldestTok)/(rObservedS/60);
  return [recentRate,avgRate];
}
function _isBurnSpike(recentRate,avgRate,minFloor=5000,multiplier=3){
  return avgRate>0 && recentRate>multiplier*avgRate && recentRate>minFloor;
}
/* Same job as _is_cost_spike/_project_avg_costs in monitor.py -- kept in
   sync by hand, same reasoning as _computeBurnRates/_isBurnSpike above. */
function _isCostSpike(sessionCost,projectAvgCost,minFloor=1,multiplier=3){
  return projectAvgCost>0 && sessionCost>multiplier*projectAvgCost && sessionCost>minFloor;
}
function _projectAvgCosts(byProject){
  const out={};
  (byProject||[]).forEach(row=>{
    const project=row.project, sessions=row.sessions||0;
    if(!project||sessions<=0) return;
    out[project]=(row.cost||0)/sessions;
  });
  return out;
}
/* Bucket a session's raw (epoch_s, state) activity samples into
   `numBuckets` fixed-width time slices over the last `windowS` seconds,
   for the CLI card's activity sparkline. Each bucket carries forward the
   most recently seen state (samples are sparse relative to a bucket's
   width) -- a bucket with no new sample simply repeats the prior one.
   Buckets before any sample exists are null ("no data yet"). */
function _bucketActivity(history,now,numBuckets=20,windowS=7200){
  const bucketSize=windowS/numBuckets;
  const buckets=new Array(numBuckets).fill(null);
  let lastKnown=null, idx=0;
  for(let i=0;i<numBuckets;i++){
    const bEnd=now-windowS+(i+1)*bucketSize;
    while(idx<history.length && history[idx][0]<bEnd){
      lastKnown=history[idx][1];
      idx++;
    }
    buckets[i]=lastKnown;
  }
  return buckets;
}
async function _fireBurnWebhook(sess,recentRate,avgRate){
  if(!_webhookUrl) return;
  if(!_webhookEvents.burn_spike) return;
  try{
    fetch('/webhook_fire',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      url:_webhookUrl, event:'burn_spike',
      session:{id:sess.id, project:sess.project, display_name:sess.display_name,
               recent_rate:Math.round(recentRate), avg_rate:Math.round(avgRate)}
    })});
  }catch(e){}
}
async function _fireWaitingWebhook(sess){
  if(!_webhookUrl) return;
  if(!_webhookEvents.waiting_nudge) return;
  try{
    fetch('/webhook_fire',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      url:_webhookUrl, event:'waiting_nudge',
      session:{id:sess.id, project:sess.project, display_name:sess.display_name,
               waiting_secs:sess.waiting_secs}
    })});
  }catch(e){}
}
async function _fireCostSpikeWebhook(sess,cost,projectAvgCost){
  if(!_webhookUrl) return;
  if(!_webhookEvents.cost_spike) return;
  try{
    fetch('/webhook_fire',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      url:_webhookUrl, event:'cost_spike',
      session:{id:sess.id, project:sess.project, display_name:sess.display_name,
               cost:+cost.toFixed(4), project_avg_cost:+projectAvgCost.toFixed(4)}
    })});
  }catch(e){}
}
/* init on page load */
(()=>{ _applyAccent(_accentName); _applyDensity(_density); })();

