/* ── multi-machine merge ──
   Pure function: local sr + whatever's cached from configured remotes ->
   one combined object shaped exactly like _build_status_payload()'s own
   output, so every render function above (and every notification-diff
   path) accepts it completely unchanged. No-op when no remotes are
   configured, so single-machine behavior is byte-for-byte identical to
   before this feature existed. */
function _mergeRemoteData(sr){
  if(!_remoteMachines.length) return sr;
  const localName=_localMachineName||'local';
  const sessions_list=(sr.sessions_list||[]).map(s=>({...s, machine:localName, _isLocal:true, _domId:'local_'+s.id}));
  const agents=(sr.agents||[]).map(a=>({...a, machine:localName, _isLocal:true, _domId:'local_'+a.id}));
  /* also merge the raw sessions dict -- checkStaleAgents() looks up
     data.sessions[agent.session_id].last_seen_epoch directly (not via
     sessions_list, which doesn't carry that field) to decide whether a
     2h+ "running" agent is actually abandoned; without this, every remote
     agent would look abandoned immediately since its session_id would
     never be found in the local machine's own sessions dict. */
  const mergedSessionsDict={...(sr.sessions||{})};
  _remoteMachines.forEach((m,idx)=>{
    const cached=_remoteCache[idx];
    if(!cached||!cached.ok||!cached.data) return;
    const name=m.name||('machine'+idx);
    (cached.data.sessions_list||[]).forEach(s=>sessions_list.push({...s, machine:name, _isLocal:false, _domId:'m'+idx+'_'+s.id}));
    (cached.data.agents||[]).forEach(a=>agents.push({...a, machine:name, _isLocal:false, _domId:'m'+idx+'_'+a.id}));
    Object.assign(mergedSessionsDict, cached.data.sessions||{});
  });
  return {...sr, sessions_list, agents, sessions:mergedSessionsDict, sessions_count:sessions_list.length};
}

function _pollRemoteMachines(){
  _remoteMachines.forEach((m,idx)=>{
    if(!m.url) return;
    const url=m.url+'/status'+(m.token?('?token='+encodeURIComponent(m.token)):'');
    fetch(url,{signal:AbortSignal.timeout(4000)})
      .then(r=>r.ok?r.json():Promise.reject(new Error('HTTP '+r.status)))
      .then(data=>{ _remoteCache[idx]={data, ok:true, lastFetch:Date.now()}; })
      .catch(()=>{ _remoteCache[idx]={..._remoteCache[idx], ok:false, lastFetch:Date.now()}; });
  });
}
setInterval(_pollRemoteMachines, 4000);

/* ── routing a mutation (Force Stop / Dismiss) to the right machine ──
   Every merged agent/session card carries a.machine/s.machine (the name
   typed into Settings -> Remote Machines, or _localMachineName for this
   machine's own -- see _mergeRemoteData above). _machineFor resolves
   that name back to the {url,token} needed to reach it; null means
   "local", so _apiUrl's callers don't need their own local/remote
   branch -- a null machine just returns the plain relative path exactly
   as every call site already did before Remote Machines existed. */
function _machineFor(name){
  if(!name || name===_localMachineName) return null;
  return _remoteMachines.find(m=>m.name===name) || null;
}
function _apiUrl(machine, path){
  if(!machine || !machine.url) return path;
  const sep=path.includes('?')?'&':'?';
  return machine.url+path+(machine.token?(sep+'token='+encodeURIComponent(machine.token)):'');
}

/* ── multi-machine merge: History/Analytics ──
   Separate from the live-dashboard merge above on purpose: History and
   Analytics are fetched on-demand only when that tab is actually open
   (same as they already were before remotes existed), not on the constant
   4s poll -- there's no need to keep remote SQLite aggregates warm in the
   background just because a tab isn't showing them. */
async function _fetchRemoteJSON(path){
  return Promise.all(_remoteMachines.map(m=>{
    if(!m.url) return null;
    const sep=path.includes('?')?'&':'?';
    const url=m.url+path+(m.token?(sep+'token='+encodeURIComponent(m.token)):'');
    return fetch(url,{signal:AbortSignal.timeout(4000)}).then(r=>r.ok?r.json():null).catch(()=>null);
  }));
}

function _mergeAnalytics(local,remoteDataList){
  if(!_remoteMachines.length) return local;
  const sumInto=(dst,src)=>{ for(const k of ['sessions','tokens','cost','agents','done','errors','files','tool_use_count','task_done','task_total','waiting_on_you_s']){ if(src[k]!=null) dst[k]=(dst[k]||0)+src[k]; } };
  const total={...(local.total||{})};
  let hookMisses=local.hook_misses||0;
  let retryCount=local.retry_count||0;
  const retryPatternsMap=new Map((local.retry_patterns||[]).map(r=>[r.name,{...r}]));
  const byDayMap=new Map((local.by_day||[]).map(r=>[r.date,{...r}]));
  const byProjMap=new Map((local.by_project||[]).map(r=>[r.project,{...r}]));
  const byDayProjMap=new Map((local.by_day_project||[]).map(r=>[r.date+'|'+r.project,{...r}]));
  const byModelMap=new Map((local.by_model||[]).map(r=>[r.model,{...r}]));
  const byDayModelMap=new Map((local.by_day_model||[]).map(r=>[r.date+'|'+r.model,{...r}]));
  const bySubagentTypeMap=new Map((local.by_subagent_type||[]).map(r=>[r.subagent_type,{...r}]));
  const byDaySubagentTypeMap=new Map((local.by_day_subagent_type||[]).map(r=>[r.date+'|'+r.subagent_type,{...r}]));
  const byDayHookMap=new Map((local.by_day_hook_reliability||[]).map(r=>[r.date,{...r}]));
  const fileHotspotsMap=new Map((local.file_hotspots||[]).map(r=>[r.path,{...r}]));
  const byDayFileHotspotsMap=new Map((local.by_day_file_hotspots||[]).map(r=>[r.date+'|'+r.path,{...r}]));
  const fileTypeMap=new Map((local.by_file_type||[]).map(r=>[r.ext,{...r}]));
  const tagCloudMap=new Map((local.tag_cloud||[]).map(r=>[r.tag,{...r}]));
  const byTagMap=new Map((local.by_tag||[]).map(r=>[r.tag,{...r}]));
  let slowestAgents=(local.slowest_agents||[]).map(r=>({...r, _isLocal:true}));
  const commonErrorsMap=new Map((local.common_errors||[]).map(r=>[r.error_msg,{...r}]));
  remoteDataList.forEach((rd,idx)=>{
    if(!rd) return;
    sumInto(total, rd.total||{});
    hookMisses+=rd.hook_misses||0;
    retryCount+=rd.retry_count||0;
    (rd.retry_patterns||[]).forEach(r=>{
      const ex=retryPatternsMap.get(r.name);
      if(ex){ ex.retries=(ex.retries||0)+(r.retries||0); ex.sessions=(ex.sessions||0)+(r.sessions||0); }
      else retryPatternsMap.set(r.name,{...r});
    });
    (rd.by_day||[]).forEach(r=>{ const ex=byDayMap.get(r.date); ex?sumInto(ex,r):byDayMap.set(r.date,{...r}); });
    (rd.by_project||[]).forEach(r=>{ const ex=byProjMap.get(r.project); ex?sumInto(ex,r):byProjMap.set(r.project,{...r}); });
    (rd.by_day_project||[]).forEach(r=>{
      const key=r.date+'|'+r.project;
      const ex=byDayProjMap.get(key);
      if(ex){ ex.tokens=(ex.tokens||0)+(r.tokens||0); ex.cost=(ex.cost||0)+(r.cost||0); }
      else byDayProjMap.set(key,{...r});
    });
    (rd.by_model||[]).forEach(r=>{ const ex=byModelMap.get(r.model); ex?sumInto(ex,r):byModelMap.set(r.model,{...r}); });
    (rd.by_day_model||[]).forEach(r=>{
      const key=r.date+'|'+r.model;
      const ex=byDayModelMap.get(key);
      if(ex){ ex.tokens=(ex.tokens||0)+(r.tokens||0); ex.cost=(ex.cost||0)+(r.cost||0); }
      else byDayModelMap.set(key,{...r});
    });
    (rd.by_subagent_type||[]).forEach(r=>{ const ex=bySubagentTypeMap.get(r.subagent_type); ex?sumInto(ex,r):bySubagentTypeMap.set(r.subagent_type,{...r}); });
    (rd.by_day_subagent_type||[]).forEach(r=>{
      const key=r.date+'|'+r.subagent_type;
      const ex=byDaySubagentTypeMap.get(key);
      if(ex){ ex.tokens=(ex.tokens||0)+(r.tokens||0); ex.cost=(ex.cost||0)+(r.cost||0); }
      else byDaySubagentTypeMap.set(key,{...r});
    });
    (rd.by_day_hook_reliability||[]).forEach(r=>{
      const ex=byDayHookMap.get(r.date);
      if(ex){ ex.misses=(ex.misses||0)+(r.misses||0); ex.total=(ex.total||0)+(r.total||0); }
      else byDayHookMap.set(r.date,{...r});
    });
    (rd.file_hotspots||[]).forEach(r=>{
      const ex=fileHotspotsMap.get(r.path);
      if(ex){ ex.changes=(ex.changes||0)+(r.changes||0); ex.sessions=(ex.sessions||0)+(r.sessions||0); ex.total_lines=(ex.total_lines||0)+(r.total_lines||0); }
      else fileHotspotsMap.set(r.path,{...r});
    });
    (rd.by_day_file_hotspots||[]).forEach(r=>{
      const key=r.date+'|'+r.path;
      const ex=byDayFileHotspotsMap.get(key);
      if(ex){ ex.changes=(ex.changes||0)+(r.changes||0); }
      else byDayFileHotspotsMap.set(key,{...r});
    });
    (rd.by_file_type||[]).forEach(r=>{
      const ex=fileTypeMap.get(r.ext);
      if(ex){ ex.changes=(ex.changes||0)+(r.changes||0); ex.sessions=(ex.sessions||0)+(r.sessions||0); ex.total_lines=(ex.total_lines||0)+(r.total_lines||0); }
      else fileTypeMap.set(r.ext,{...r});
    });
    (rd.tag_cloud||[]).forEach(r=>{
      const ex=tagCloudMap.get(r.tag);
      if(ex){ ex.count=(ex.count||0)+(r.count||0); }
      else tagCloudMap.set(r.tag,{...r});
    });
    (rd.by_tag||[]).forEach(r=>{ const ex=byTagMap.get(r.tag); ex?sumInto(ex,r):byTagMap.set(r.tag,{...r}); });
    // A leaderboard of individual agent runs, not a summable aggregate --
    // concat local+remote (each machine's own top 20) and re-sort/re-slice
    // to the merged top 20, rather than the sumInto-by-key pattern every
    // other by_* breakdown above uses. Tagged with the machine name (same
    // idea as _mergeHistorySessions) since a row here identifies one
    // specific agent run, not an aggregate bucket -- unlike _isLocal,
    // machine name has no meaning for a purely local dashboard, so it's
    // only set once remotes actually exist (this whole function already
    // early-returns before reaching here otherwise).
    const machineName=(_remoteMachines[idx]&&_remoteMachines[idx].name)||('machine'+idx);
    slowestAgents=slowestAgents.concat((rd.slowest_agents||[]).map(r=>({...r, _isLocal:false, machine:machineName})));
    (rd.common_errors||[]).forEach(r=>{
      const ex=commonErrorsMap.get(r.error_msg);
      // Occurrence count sums across machines; last_session_id/project keep
      // whichever machine's row is already there rather than trying to
      // compare recency across machines (no timestamp in this payload to
      // compare against) -- click-through still lands on a real, valid
      // occurrence of the error either way.
      if(ex){ ex.occurrences=(ex.occurrences||0)+(r.occurrences||0); }
      else commonErrorsMap.set(r.error_msg,{...r});
    });
  });
  slowestAgents=slowestAgents.sort((a,b)=>(b.duration_s||0)-(a.duration_s||0)).slice(0,20);
  return {
    ...local, total, hook_misses:hookMisses, retry_count:retryCount,
    retry_patterns:[...retryPatternsMap.values()].sort((a,b)=>(b.retries||0)-(a.retries||0)),
    by_day:[...byDayMap.values()].sort((a,b)=>a.date<b.date?1:-1),
    by_project:[...byProjMap.values()].sort((a,b)=>(b.cost||0)-(a.cost||0)),
    by_day_project:[...byDayProjMap.values()].sort((a,b)=>a.date<b.date?-1:1),
    by_model:[...byModelMap.values()].sort((a,b)=>(b.cost||0)-(a.cost||0)),
    by_day_model:[...byDayModelMap.values()].sort((a,b)=>a.date<b.date?-1:1),
    by_subagent_type:[...bySubagentTypeMap.values()].sort((a,b)=>(b.cost||0)-(a.cost||0)),
    by_day_subagent_type:[...byDaySubagentTypeMap.values()].sort((a,b)=>a.date<b.date?-1:1),
    by_day_hook_reliability:[...byDayHookMap.values()].sort((a,b)=>a.date<b.date?-1:1),
    file_hotspots:[...fileHotspotsMap.values()].sort((a,b)=>(b.changes||0)-(a.changes||0)),
    by_day_file_hotspots:[...byDayFileHotspotsMap.values()].sort((a,b)=>a.date<b.date?-1:1),
    by_file_type:[...fileTypeMap.values()].sort((a,b)=>(b.changes||0)-(a.changes||0)),
    tag_cloud:[...tagCloudMap.values()].sort((a,b)=>(b.count||0)-(a.count||0)),
    by_tag:[...byTagMap.values()].sort((a,b)=>(b.cost||0)-(a.cost||0)),
    slowest_agents:slowestAgents,
    common_errors:[...commonErrorsMap.values()].sort((a,b)=>(b.occurrences||0)-(a.occurrences||0)),
  };
}

function _mergeHistorySessions(local,remoteDataList){
  if(!_remoteMachines.length) return local;
  const merged=(local||[]).map(s=>({...s, machine:_localMachineName||'local', _isLocal:true}));
  remoteDataList.forEach((rd,idx)=>{
    if(!rd) return;
    const name=(_remoteMachines[idx]&&_remoteMachines[idx].name)||('machine'+idx);
    rd.forEach(s=>merged.push({...s, machine:name, _isLocal:false}));
  });
  merged.sort((a,b)=>(b.started_at||'').localeCompare(a.started_at||''));
  return merged;
}

/* ── live updates via SSE — replaces the old 500ms setInterval poll ── */
let _es=null;
let _esReconnectDelay=1000;   /* ms, capped exponential backoff */
let _esReconnectTimer=null;
function _connectEvents(){
  /* WebSocket, not EventSource/SSE — SSE never got forwarded at all through
     the Cloudflare quick tunnel (chunked responses arrived as 0 bytes even
     after 35s), while WebSocket already reliably survives the same tunnel
     (proven by the terminal). Unlike EventSource, WebSocket has no built-in
     retry, so reconnect is hand-rolled below. */
  if(_esReconnectTimer){ clearTimeout(_esReconnectTimer); _esReconnectTimer=null; }
  if(_es){ try{ _es.onclose=null; _es.close(); }catch(e){} }
  const proto=location.protocol==='https:'?'wss':'ws';
  const url=`${proto}://${location.host}/events`+(window._AOC_TOKEN?'?token='+encodeURIComponent(window._AOC_TOKEN):'');
  _es=new WebSocket(url);
  _es.onopen=()=>{ pollFails=0; _setConnStatus(true); _esReconnectDelay=1000; };
  _es.onmessage=(ev)=>{
    try{
      const sr=JSON.parse(ev.data);
      pollFails=0; _setConnStatus(true);
      _applyStatus(sr);
    }catch(e){ log('status WS parse error: '+e.message,'error'); }
  };
  _es.onerror=()=>{ /* onclose fires right after and owns state + reconnect */ };
  _es.onclose=()=>{
    pollFails++;
    if(pollFails>=2) _setConnStatus(false);
    _esReconnectTimer=setTimeout(_connectEvents, _esReconnectDelay);
    _esReconnectDelay=Math.min(_esReconnectDelay*1.5, 15000);
  };
}

/* ── auto-remove done agents; error agents stay sticky ── */
const _removingIds = new Set();
const _fadingStartedAt = {};  /* agentId → epoch ms when fade began */
const _MIN_VISIBLE_MS = 15000; /* agent stays visible at least 15s from started_at */
function scheduleRemove(agentId, status){
  if(status==='error') return;
  if(_removingIds.has(agentId)) return;
  _removingIds.add(agentId);
  _fadingStartedAt[agentId] = Date.now();  /* JS-computed fade; persists across innerHTML rebuilds */
  /* respect minimum visibility — delay removal so agent was visible for at least 15s */
  const agent=(lastStatus?.agents||[]).find(a=>a.id===agentId);
  const startMs=agent?.started_at?parseTimeStr(agent.started_at):Date.now();
  const alreadyVisibleMs=Date.now()-startMs;
  const delay=Math.max(10000, _MIN_VISIBLE_MS-alreadyVisibleMs);
  setTimeout(async ()=>{
    /* cancel removal if agent went back to running */
    const cur=(lastStatus?.agents||[]).find(a=>a.id===agentId);
    if(cur && cur.status==='running'){
      _removingIds.delete(agentId);
      delete _fadingStartedAt[agentId];
      return;
    }
    try{
      await fetch('/history/save_current');
      await fetch('/remove',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:agentId})});
    }catch(e){}
    _removingIds.delete(agentId);
    delete _fadingStartedAt[agentId];
  }, delay);
}

/* ── auto-remove stale "running" agents (started > 20min ago, never finished) ── */
function checkStaleAgents(data){
  const now=Date.now();
  (data.agents||[]).filter(a=>!String(a.id||'').startsWith('hook_')).forEach(a=>{
    if(a.status!=='running') return;
    if(!a.started_at) return;
    const [_sh,_sm,_ss]=(a.started_at||'').split(':').map(Number);
    const startMs=new Date().setHours(_sh||0,_sm||0,_ss||0,0);
    if(isNaN(startMs)) return;
    const age=(now-startMs)/1000;
    if(age<=7200) return;  /* not old enough to even consider yet */
    /* A 2h+ "running" agent isn't necessarily stuck — it might just be a slow
       task under a session the user is still actively working in. Only treat
       it as abandoned if its OWNING SESSION also looks dead (no heartbeat in
       30min): that's the actual signal something got orphaned (e.g. AOC
       restarted mid-task and will never see the real completion event),
       not merely "this took a while". Without this check, any long-running
       agent got force-marked done/removed purely by age, even while its CLI
       was still live and the agent still genuinely working. */
    const sess=a.session_id?(data.sessions||{})[a.session_id]:null;
    const sessLastSeen=sess?sess.last_seen_epoch:null;
    const sessLooksAbandoned=!sessLastSeen||(now/1000-sessLastSeen)>1800;
    if(sessLooksAbandoned) scheduleRemove(a.id,'done');
  });
}

/* ── stuck detection ── track last task-progress time per agent */
const _lastProgressAt = {};  /* agentId → epoch ms of last task completion */
const _stuckNotified = new Set(); /* agents already notified about being stuck */
function _stuckSecs(a){ const lp=_lastProgressAt[a.id]; return lp&&a.status==='running'?Math.floor((Date.now()-lp)/1000):0; }

/* ── token-burn anomaly detection ── mirrors the server-side check in
   _webhook_notify_worker so a tab-open user gets the badge/sound
   immediately, not just whenever webhooks happen to be configured. */
const _tokenHistory = {};   /* sessionId → [[epoch_s, cumulative_tokens], ...], trimmed to last 10 min */
const _burnNotified = new Set(); /* sessions already notified about a burn spike */
const _waitingNotified = new Set(); /* sessions already notified about waiting too long */
const _costSpikeNotified = new Set(); /* sessions already notified about a cost spike */

/* ── session activity sparkline ── purely live/in-memory, same choice as
   the burn-spike detector above: no persisted history exists for this,
   and this is a lightweight visualization, not a record worth persisting. */
const _activityHistory = {}; /* sessionId → [[epoch_s, 'active'|'waiting'|'idle'], ...], trimmed to last 2h */

/* extend diffStatus to schedule removal + audio + conflict */
const _origDiff = diffStatus;
const _prevStatuses = {};
const _prevSessionActive = {};
const _sessionAutoDismissTimers = {};
function diffStatusWithRemove(prev, next){
  _origDiff(prev, next);
  /* auto-dismiss sessions that just closed -- NOT for remote (merged-in)
     sessions, matching the same _isLocal gate the manual dismiss buttons
     already use (lines ~3890/5415): a remote session's id doesn't exist
     in this machine's own sessions dict, so dismissing it here would
     create a bogus local entry keyed by a foreign id. */
  (next.sessions_list||[]).forEach(s=>{
    if(s._isLocal===false) return;
    const wasActive=_prevSessionActive[s.id];
    const nowActive=s.session_active!==false;
    if(wasActive===true && !nowActive && !_sessionAutoDismissTimers[s.id]){
      /* 90s, not 5s: a session can look momentarily "inactive" from
         nothing more than a >5min gap between hook heartbeats (one long
         tool call, a long subagent task, stepping away briefly) -- both
         _build_status_payload's live session_active computation and
         _transcript_scanner_worker's own staleness check treat that as
         inactive by design. The old 5s window gave a same-session
         reactivation (next scanner tick at 30s, or the next real
         heartbeat) essentially no chance to arrive before the dismiss
         fired -- this is what caused every "why did this session
         randomly get dismissed while I was still using it" recurrence. */
      _sessionAutoDismissTimers[s.id]=setTimeout(()=>{
        delete _sessionAutoDismissTimers[s.id];
        _dismissSession(s.id);
        if(sessionFilter===s.id) setSessionFilter(null);
      }, 90000);
    } else if(nowActive && _sessionAutoDismissTimers[s.id]){
      /* session came back before the timer fired -- cancel it. Mirrors
         scheduleRemove's own "re-check before committing" pattern
         (~6220-6245); this timer previously had no equivalent guard at
         all, so it always fired regardless of what happened in between. */
      clearTimeout(_sessionAutoDismissTimers[s.id]);
      delete _sessionAutoDismissTimers[s.id];
    }
    _prevSessionActive[s.id]=nowActive;

    const nowS=Date.now()/1000;
    // activity sparkline sample -- taken regardless of active/inactive
    // (unlike the token-burn tracking below, which only matters while
    // active) so the sparkline can actually show when a session went idle.
    const activityState=!nowActive?'idle':(s.waiting_on_you?'waiting':'active');
    const ahist=_activityHistory[s.id]=_activityHistory[s.id]||[];
    ahist.push([nowS,activityState]);
    const acutoff=nowS-7200;
    while(ahist.length&&ahist[0][0]<acutoff) ahist.shift();

    if(!nowActive){ delete _tokenHistory[s.id]; _burnNotified.delete(s.id); _waitingNotified.delete(s.id); _costSpikeNotified.delete(s.id); return; }

    // Waiting-too-long nudge: mirrors the server-side check in
    // _webhook_notify_worker, kept independent of the burn-rate block
    // below so it isn't accidentally skipped for a session with under
    // 5min of token-history samples.
    if((s.waiting_secs||0)>7200){
      if(!_waitingNotified.has(s.id)){
        _waitingNotified.add(s.id);
        if(!_isNotifySuppressed(s.project)){ _playSound('waiting_nudge'); _fireWaitingWebhook(s); }
      }
    }else{
      _waitingNotified.delete(s.id);
    }

    // Cost-spike: mirrors the server-side check in _webhook_notify_worker
    // (_is_cost_spike/_project_avg_costs), same independent placement as
    // waiting_nudge above -- not gated behind the burn-rate history check
    // below. _kpiAnalytics.by_project is already fetched every 30s by
    // renderKpi for the KPI bar, reused here rather than fetching again.
    const projectAvgCost=_projectAvgCosts((_kpiAnalytics||{}).by_project)[s.project]||0;
    const sessionCost=s.estimated_cost||0;
    if(_isCostSpike(sessionCost,projectAvgCost)){
      if(!_costSpikeNotified.has(s.id)){
        _costSpikeNotified.add(s.id);
        if(!_isNotifySuppressed(s.project)){ _playSound('cost_spike'); _fireCostSpikeWebhook(s,sessionCost,projectAvgCost); }
      }
    }else{
      _costSpikeNotified.delete(s.id);
    }

    const total=(s.input_tokens||0)+(s.output_tokens||0);
    const hist=_tokenHistory[s.id]=_tokenHistory[s.id]||[];
    hist.push([nowS,total]);
    const cutoff=nowS-600;
    while(hist.length&&hist[0][0]<cutoff) hist.shift();
    const rates=_computeBurnRates(hist,nowS);
    if(rates){
      const [recentRate,avgRate]=rates;
      if(_isBurnSpike(recentRate,avgRate)){
        if(!_burnNotified.has(s.id)){
          _burnNotified.add(s.id);
          if(!_isNotifySuppressed(s.project)){ _playSound('burn_spike'); _fireBurnWebhook(s,recentRate,avgRate); }
        }
      }else{
        _burnNotified.delete(s.id);
      }
    }
  });
  const agents=(next.agents||[]).filter(a=>!String(a.id||'').startsWith('hook_'));
  const prevAgents=Object.fromEntries((prev&&prev.agents||[]).map(a=>[a.id,a]));
  agents.forEach(a=>{
    const wasStatus=_prevStatuses[a.id];
    const suppressed=_isNotifySuppressed(a.session_project);
    if(wasStatus && wasStatus!==a.status){
      if(a.status==='done'){
        if(!suppressed){
          _playSound('done');
          showToast('done','MISSION COMPLETE',a.name);
          _browserNotify('AOC — Mission Complete', a.name);
          _fireWebhook('done', a);
        }
      }
      if(a.status==='error'){
        if(!suppressed){
          _playSound('error');
          showToast('error','SYSTEM FAILURE',a.error_message||a.name);
          _browserNotify('AOC — System Failure', a.name+(a.error_message?': '+a.error_message.slice(0,60):''));
          _fireWebhook('error', a);
        }
      }
      if(a.status==='running'&&wasStatus&&wasStatus!=='running'){
        if(!suppressed) _playSound('running');
      }
    }
    /* track task progress for stuck detection */
    const prevA=prevAgents[a.id];
    if(prevA){
      const prevDone=((prevA.tasks||[]).filter(t=>t.done).length);
      const curDone=((a.tasks||[]).filter(t=>t.done).length);
      if(curDone>prevDone) _lastProgressAt[a.id]=Date.now();
    }
    if(a.status==='running' && !_lastProgressAt[a.id]){
      _lastProgressAt[a.id]=parseTimeStr(a.started_at)||Date.now();
    }
    /* stuck sound — fire once when agent crosses 5min no-progress threshold */
    if(a.status==='running' && _lastProgressAt[a.id] && !_stuckNotified.has(a.id)){
      if(Date.now()-_lastProgressAt[a.id]>300000){
        _stuckNotified.add(a.id);
        if(!suppressed){ _playSound('stuck'); _fireWebhook('stuck', a); }
      }
    }
    if(a.status!=='running') _stuckNotified.delete(a.id);
    _prevStatuses[a.id]=a.status;
    if(a.status==='done'||a.status==='error') scheduleRemove(a.id, a.status);
  });
  renderConflicts(agents);
}

/* ── dynamic favicon ── */
(function(){
  const _cv=document.createElement('canvas');
  _cv.width=32; _cv.height=32;
  let _lastFavStatus='';
  // Canvas fillStyle can't parse var(--c) directly (it needs a resolved
  // color string), so 'running' reads the live --c-rgb custom property at
  // draw time instead of a hardcoded hex -- this is what makes the favicon
  // track the user's chosen accent color instead of always being cyan.
  function _favColors(status){
    if(status==='running'){
      const rgb=getComputedStyle(document.documentElement).getPropertyValue('--c-rgb').trim() || '91,155,224';
      return {solid:`rgb(${rgb})`, faint:`rgba(${rgb},0.2)`};
    }
    const solid={done:'#4cc285',error:'#ef6a57',idle:'#34463f'}[status]||'#34463f';
    return {solid, faint:solid+'33'};
  }
  function _drawFavicon(status){
    if(status===_lastFavStatus) return;
    _lastFavStatus=status;
    const ctx=_cv.getContext('2d');
    ctx.clearRect(0,0,32,32);
    const {solid:col, faint}=_favColors(status);
    ctx.beginPath();
    for(let i=0;i<6;i++){
      const a=Math.PI/180*(i*60-30);
      const x=16+13*Math.cos(a), y=16+13*Math.sin(a);
      i===0?ctx.moveTo(x,y):ctx.lineTo(x,y);
    }
    ctx.closePath();
    ctx.fillStyle=faint;
    ctx.fill();
    ctx.strokeStyle=col;
    ctx.lineWidth=2;
    ctx.stroke();
    /* center dot */
    ctx.beginPath();
    ctx.arc(16,16,4,0,Math.PI*2);
    ctx.fillStyle=col;
    ctx.fill();
    /* update favicon */
    let link=document.querySelector("link[rel='icon']");
    if(!link){ link=document.createElement('link'); link.rel='icon'; document.head.appendChild(link); }
    link.href=_cv.toDataURL('image/png');
  }
  function _updateFavicon(){
    if(!lastStatus){ _drawFavicon('idle'); return; }
    const agents=(lastStatus.agents||[]).filter(a=>!String(a.id||'').startsWith('hook_'));
    const running=agents.some(a=>a.status==='running'||a.status==='waiting');
    const errors=agents.some(a=>a.status==='error');
    const done=agents.length>0&&agents.every(a=>a.status==='done');
    _drawFavicon(errors?'error':running?'running':done?'done':'idle');
  }
  setInterval(_updateFavicon,3000);
  _updateFavicon();
})();

/* ── PWA installability ── manifest link is set dynamically (not a static
   <link> tag) so a tunneled/remote session's token rides along into the
   manifest fetch, and from there into the icon fetch and the installed
   icon's start_url -- same "carry the token through every resource URL
   when tunneled" approach already used elsewhere in this app. */
(function(){
  const tokSuffix=window._AOC_TOKEN?('?token='+encodeURIComponent(window._AOC_TOKEN)):'';
  const link=document.createElement('link');
  link.rel='manifest';
  link.href='/manifest.json'+tokSuffix;
  document.head.appendChild(link);
  if('serviceWorker' in navigator){
    navigator.serviceWorker.register('/sw.js'+tokSuffix).catch(()=>{});
  }
})();

/* ── browser notifications ── */
let _notifPermission = (typeof Notification !== 'undefined') ? Notification.permission : 'denied';
function _requestNotifPermission(){
  if(typeof Notification === 'undefined') return;
  if(_notifPermission==='default') Notification.requestPermission().then(p=>{ _notifPermission=p; });
}
function _browserNotify(title, body){
  if(!('Notification' in window) || Notification.permission !== 'granted') return;
  if(!document.hidden) return; /* only when window not focused */
  try{ new Notification(title, {body, silent:false}); }catch(e){}
}

