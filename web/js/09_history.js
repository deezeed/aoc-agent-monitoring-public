/* ── history view ── */
let _histTab='sessions'; // a key of _HIST_TABS
/* History's tabs, grouped by what you came for. A new tab = one row in
   _HIST_TABS + its key in one group of _HIST_GROUPS. Keys are what
   _histTab holds (other views link straight to them), so they never change. */
const _HIST_TABS={
  sessions:   ['SESSIONS', 'Every session: when, what it cost, its agents and files'],
  search:     ['CONVERSATIONS', 'Search what you and Claude wrote in past conversations'],
  analytics:  ['ANALYTICS', 'Spend and activity by day, project, model and agent type'],
  cache:      ['CACHE', 'Where the token spend goes and how well the prompt cache works'],
  commits:    ['COMMITS', 'What each git commit Claude made cost'],
  clients:    ['CLIENTS', 'Monthly Claude Code spend per client, for invoicing'],
  worklog:    ['WORK LOG', 'What got done per project or client -- a standup or a client report in one click'],
  models:     ['MODELS', "Which work each model's spend went to, and what a cheaper model would save"],
  context:    ['CONTEXT', 'What every session loads before your first message (skills, MCP tools, CLAUDE.md) and what re-reading it costs'],
  failures:   ['FAILURES', 'Which tool calls fail most, why, and what that costs'],
  permissions:['PERMISSIONS', 'Which permission prompts hold you up, and safe allow rules that would stop them'],
  memory:     ['MEMORY', "Your CLAUDE.md and memory files: what they cost at every start, what's broken in them, and an editor"],
};
const _HIST_GROUPS=[
  ['past',  'PAST WORK', 'What happened: sessions, conversations, activity, work log', ['sessions','search','analytics','worklog']],
  ['spend', 'SPEND',     'Where the money goes: cache, commits, clients, models',     ['cache','commits','clients','models']],
  ['tune',  'TUNE UP',   'What slows you down or costs extra, and how to fix it',     ['context','failures','permissions','memory']],
];
const _histGroupLast={};   // group -> the tab you last had open in it
/* Pure: the group a tab belongs to (the first group for an unknown tab). */
function _histGroupOf(tab){
  return (_HIST_GROUPS.find(g=>g[3].includes(tab))||_HIST_GROUPS[0]);
}
/* Pure: two rows -- the groups, then the open group's tabs. */
function _histTabsHtml(tab){
  const cur=_histGroupOf(tab);
  const groups=_HIST_GROUPS.map(([k,l,t,tabs])=>`<button class="hg-tab${k===cur[0]?' active':''}" onclick="_histPickGroup(${jsq(k)})" title="${escHtml(t)}">${l}</button>`).join('');
  const tabs=cur[3].filter(k=>_HIST_TABS[k]).map(k=>`<div class="adp-tab ${k===tab?'active':''}" onclick="_histPick(${jsq(k)})" title="${escHtml(_HIST_TABS[k][1])}">${_HIST_TABS[k][0]}</div>`).join('');
  return `<div class="hist-nav"><div class="hg-tabs" role="tablist">${groups}</div><div class="adp-tabs hist-tabs" style="border:none">${tabs}</div></div>`;
}
function _histPick(tab){
  _histTab=tab;
  _histGroupLast[_histGroupOf(tab)[0]]=tab;
  renderHistory();
}
function _histPickGroup(k){
  const g=_HIST_GROUPS.find(x=>x[0]===k);
  if(!g) return;
  _histPick(_histGroupLast[k]&&g[3].includes(_histGroupLast[k])?_histGroupLast[k]:g[3][0]);
}
let _commitSort='new';    // History -> COMMITS ledger: 'new' | 'cost'
let _commitShowAll=false;
let _cacheDays=30;
let _selectedProjectTrend=''; // project name whose own cost trend is pinned open in Analytics, '' = none
function _selectProjectTrend(project){
  _selectedProjectTrend=(_selectedProjectTrend===project)?'':project; // clicking the same project again closes it
  renderHistory();
}
let _selectedModelTrend=''; // same idea as _selectedProjectTrend, one dimension over (model instead of project)
function _selectModelTrend(model){
  _selectedModelTrend=(_selectedModelTrend===model)?'':model;
  renderHistory();
}
let _selectedSubagentTypeTrend=''; // same idea, one more dimension over (subagent_type instead of model)
function _selectSubagentTypeTrend(subagentType){
  _selectedSubagentTypeTrend=(_selectedSubagentTypeTrend===subagentType)?'':subagentType;
  renderHistory();
}
let _selectedFileTrend=''; // same idea, one more dimension over (file path instead of model) -- only ever the top-20 file_hotspots paths, see by_day_file_hotspots' own backend comment
function _selectFileTrend(path){
  _selectedFileTrend=(_selectedFileTrend===path)?'':path;
  renderHistory();
}
/* Each renderHistory() call bumps this; after every await a call checks it is
   still the newest, so a slow /history fetch from an earlier tab can't
   overwrite the tab the user switched to meanwhile. */
let _histRenderGen=0;
async function renderHistory(){
  const gen=++_histRenderGen;
  _histLastRender=Date.now();
  const el=document.getElementById('history-area');
  if(!el) return;
  const fmtCost=_fmtCost;
  const fmtTok=t=>t?Number(t).toLocaleString():'—';
  const fmtDur=_fmtDurShort;

  el.innerHTML=`<div style="max-width:900px;width:100%;margin:0 auto;display:flex;flex-direction:column;height:100%;min-height:0">
    <div class="hist-toolbar">
      <span class="hist-title">SESSION HISTORY</span>
      ${_histTabsHtml(_histTab)}
      ${_histTab==='worklog'?`<div class="adp-tabs cache-range" style="border:none;margin-left:10px">${[['yesterday','YESTERDAY'],['today','TODAY'],['week','THIS WEEK'],['custom','CUSTOM']].map(([k,l])=>`<div class="adp-tab ${_wlRange===k?'active':''}" onclick="_wlSetRange(${jsq(k)})">${l}</div>`).join('')}</div><div class="adp-tabs cache-range" style="border:none;margin-left:10px">${[['project','BY PROJECT'],['client','BY CLIENT']].map(([k,l])=>`<div class="adp-tab ${_wlGroup===k?'active':''}" onclick="_wlGroup=${jsq(k)};renderHistory()">${l}</div>`).join('')}</div>`:''}
      ${_histTab==='models'?`<div class="adp-tabs cache-range" style="border:none;margin-left:10px">${[[7,'7 DAYS'],[30,'30 DAYS'],[90,'90 DAYS']].map(([d,l])=>`<div class="adp-tab ${_adviceDays===d?'active':''}" onclick="_adviceDays=${d};renderHistory()">${l}</div>`).join('')}</div>`:''}
      ${_histTab==='failures'?`<div class="adp-tabs cache-range" style="border:none;margin-left:10px">${[[7,'7 DAYS'],[30,'30 DAYS'],[90,'90 DAYS']].map(([d,l])=>`<div class="adp-tab ${_failDays===d?'active':''}" onclick="_failDays=${d};renderHistory()">${l}</div>`).join('')}</div>`:''}
      ${_histTab==='context'?`<div class="adp-tabs cache-range" style="border:none;margin-left:10px">${[[7,'7 DAYS'],[30,'30 DAYS'],[90,'90 DAYS']].map(([d,l])=>`<div class="adp-tab ${_ctxDays===d?'active':''}" onclick="_ctxDays=${d};renderHistory()">${l}</div>`).join('')}</div>`:''}
      ${_histTab==='permissions'?`<div class="adp-tabs cache-range" style="border:none;margin-left:10px">${[[7,'7 DAYS'],[30,'30 DAYS'],[90,'90 DAYS']].map(([d,l])=>`<div class="adp-tab ${_permDays===d?'active':''}" onclick="_permDays=${d};renderHistory()">${l}</div>`).join('')}</div>`:''}
      ${_histTab==='cache'||_histTab==='commits'?`<div class="adp-tabs cache-range" style="border:none;margin-left:10px">${[[7,'7 DAYS'],[30,'30 DAYS'],[0,'ALL']].map(([d,l])=>`<div class="adp-tab ${_cacheDays===d?'active':''}" onclick="_cacheDays=${d};renderHistory()">${l}</div>`).join('')}</div>`:''}
      ${_histTab==='search'?`<input type="search" id="conv-search-input" class="conv-input" placeholder="Search past conversations…" value="${escHtml(_convQuery)}" oninput="setConvSearch(this.value)" autocomplete="off">`:''}
      ${_histTab==='sessions'?`<input type="text" id="hist-search-input" placeholder="Filter by name, project, id, or tag..." value="${escHtml(_histSearchQuery)}" oninput="setHistSearch(this.value)" style="font-family:var(--font2);font-size:11px;padding:4px 10px;border-radius:7px;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.03);color:var(--t);margin-left:10px;width:150px">
      <input type="date" id="hist-search-from" value="${escHtml(_histSearchFrom)}" oninput="setHistDateFrom(this.value)" title="From date" style="font-family:var(--font2);font-size:11px;padding:4px 8px;border-radius:7px;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.03);color:var(--t);margin-left:6px">
      <input type="date" id="hist-search-to" value="${escHtml(_histSearchTo)}" oninput="setHistDateTo(this.value)" title="To date" style="font-family:var(--font2);font-size:11px;padding:4px 8px;border-radius:7px;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.03);color:var(--t);margin-left:6px">`:''}
      ${_histTab==='analytics'?(()=>{
        /* href, not fetch() -- so the token has to ride in the query string
           the same way the tunnel URL / WebSocket URL already do (see
           window._AOC_TOKEN usages above), or this 401s the moment it's
           opened through a cloudflared tunnel instead of localhost */
        const tok=window._AOC_TOKEN?encodeURIComponent(window._AOC_TOKEN):'';
        const monthFrom=new Date().toISOString().slice(0,8)+'01';
        const today=new Date().toISOString().slice(0,10);
        const q=(extra)=>tok?extra+(extra.includes('?')?'&':'?')+'token='+tok:extra;
        if(!window._AOC_IS_PRO){
          return `<span style="font-family:var(--font2);font-size:9px;color:var(--t3);margin-left:10px" title="CSV export is a Pro feature -- Settings -> LICENSE to upgrade">🔒 CSV export (PRO)</span>`;
        }
        return `
        <a href="${q(`/export_costs.csv?from=${monthFrom}&to=${today}`)}" class="adp-btn" style="text-decoration:none;font-size:9px;padding:2px 10px;margin-left:10px" title="Download this month's cost-by-project report as CSV">⬇ THIS MONTH</a>
        <a href="${q('/export_costs.csv')}" class="adp-btn" style="text-decoration:none;font-size:9px;padding:2px 10px;margin-left:6px" title="Download all-time cost-by-project report as CSV">⬇ ALL TIME</a>
        <span style="font-family:var(--font2);font-size:9px;color:var(--t3);margin-left:10px">BY MODEL</span>
        <a href="${q(`/export_costs_by_model.csv?from=${monthFrom}&to=${today}`)}" class="adp-btn" style="text-decoration:none;font-size:9px;padding:2px 10px;margin-left:6px" title="Download this month's cost-by-model report as CSV">⬇ THIS MONTH</a>
        <a href="${q('/export_costs_by_model.csv')}" class="adp-btn" style="text-decoration:none;font-size:9px;padding:2px 10px;margin-left:6px" title="Download all-time cost-by-model report as CSV">⬇ ALL TIME</a>
        <span style="font-family:var(--font2);font-size:9px;color:var(--t3);margin-left:10px">BY AGENT TYPE</span>
        <a href="${q(`/export_costs_by_subagent_type.csv?from=${monthFrom}&to=${today}`)}" class="adp-btn" style="text-decoration:none;font-size:9px;padding:2px 10px;margin-left:6px" title="Download this month's cost-by-agent-type report as CSV">⬇ THIS MONTH</a>
        <a href="${q('/export_costs_by_subagent_type.csv')}" class="adp-btn" style="text-decoration:none;font-size:9px;padding:2px 10px;margin-left:6px" title="Download all-time cost-by-agent-type report as CSV">⬇ ALL TIME</a>`;
      })():''}
      <div style="flex:1"></div>
      ${lastStatus&&lastStatus.last_autosave?`<span style="font-family:var(--font2);font-size:9px;color:var(--t3);letter-spacing:.06em" title="Auto-saved every 5 min">⟳ ${(()=>{const d=Math.round((Date.now()/1000-lastStatus.last_autosave)/60);return d<1?'just now':d+'m ago';})()}</span>`:''}
      <button class="adp-btn" onclick="fetch('/history/save_current',{method:'GET'}).then(()=>renderHistory())" style="font-size:9px;padding:2px 10px">⊕ SAVE NOW</button>
    </div>
    <div class="hist-body" id="hist-body"><div style="text-align:center;padding:40px;color:var(--t3);font-family:var(--font2);font-size:10px;letter-spacing:.1em">Loading...</div></div>
  </div>`;

  const body=document.getElementById('hist-body');
  if(_histTab==='failures'){
    body.innerHTML='<div class="conv-empty">Reading your Claude Code transcripts…</div>';
    try{
      const [d]=await Promise.all([fetch('/failure_map?days='+_failDays).then(r=>r.json()), _fixesLoad()]);
      if(gen===_histRenderGen) body.innerHTML=_failureMapHtml(d, _fixesApplied, _fixLocal());
    }catch(e){ body.innerHTML='<div class="conv-empty">Could not load the failure map.</div>'; }
    return;
  }
  if(_histTab==='permissions'){
    body.innerHTML='<div class="conv-empty">Reading your permission prompts and settings…</div>';
    try{
      const [d]=await Promise.all([fetch('/permission_advice?days='+_permDays).then(r=>r.json()), _fixesLoad()]);
      if(gen===_histRenderGen) body.innerHTML=_permAdviceHtml(d, _fixesApplied, _fixLocal());
    }catch(e){ body.innerHTML='<div class="conv-empty">Could not load the permission advisor.</div>'; }
    return;
  }
  if(_histTab==='memory'){
    if(!_memData) body.innerHTML='<div class="conv-empty">Reading your CLAUDE.md and memory files…</div>';
    else _memPaint();   // keep an open draft on screen while the list refreshes
    try{
      if(!_memDirty()) await _memLoad();
      else _memData=await fetch('/memory_lint').then(r=>r.json());
      if(gen===_histRenderGen) _memPaint();
    }catch(e){ if(gen===_histRenderGen) body.innerHTML='<div class="conv-empty">Could not read the memory files.</div>'; }
    return;
  }
  if(_histTab==='context'){
    body.innerHTML='<div class="conv-empty">Reading your Claude Code transcripts…</div>';
    try{
      const [d]=await Promise.all([fetch('/context_report?days='+_ctxDays).then(r=>r.json()), _fixesLoad()]);
      if(gen===_histRenderGen) body.innerHTML=_contextReportHtml(d, _fixesApplied, _fixLocal());
    }catch(e){ body.innerHTML='<div class="conv-empty">Could not load the context report.</div>'; }
    return;
  }
  if(_histTab==='models'){
    body.innerHTML='<div class="conv-empty">Reading your Claude Code transcripts…</div>';
    try{
      const d=await fetch('/model_advice?days='+_adviceDays).then(r=>r.json());
      if(gen===_histRenderGen) body.innerHTML=_modelAdviceHtml(d);
    }catch(e){ body.innerHTML='<div class="conv-empty">Could not load the model report.</div>'; }
    return;
  }
  if(_histTab==='worklog'){
    if(!_wlData) body.innerHTML='<div class="conv-empty">Reading your Claude Code sessions…</div>';
    else _wlPaint();
    try{
      const [f,t]=_worklogRange(_wlRange, _localDateStr(), _wlFrom, _wlTo);
      const d=await fetch(`/worklog?from=${f}&to=${t}&group=${_wlGroup}`).then(r=>r.json());
      if(gen===_histRenderGen){ _wlData=d; _wlPaint(); }
    }catch(e){ if(gen===_histRenderGen) body.innerHTML='<div class="conv-empty">Could not load the work log.</div>'; }
    return;
  }
  if(_histTab==='clients'){
    try{
      const d=await fetch('/clients'+(_clientsMonth?'?month='+_clientsMonth:'')).then(r=>r.json());
      if(gen===_histRenderGen){ _clientsData=d; _clientsMonth=d.month; body.innerHTML=_clientsHtml(d, _clientsEdit, !!window._AOC_IS_PRO, window._AOC_TOKEN||'', document.documentElement.lang); }
    }catch(e){ body.innerHTML='<div class="conv-empty">Could not load the client report.</div>'; }
    return;
  }
  if(_histTab==='commits'){
    try{
      const d=await fetch('/commit_costs?days='+_cacheDays).then(r=>r.json());
      if(gen===_histRenderGen){ _lastCommitData=d; body.innerHTML=_commitReportHtml(d, _commitSort, _commitShowAll); }
    }catch(e){ body.innerHTML='<div class="conv-empty">Could not load the commit report.</div>'; }
    return;
  }
  if(_histTab==='cache'){
    try{
      const d=await fetch('/cache_stats?days='+_cacheDays).then(r=>r.json());
      if(gen===_histRenderGen) body.innerHTML=_cacheReportHtml(d);
    }catch(e){ body.innerHTML='<div class="conv-empty">Could not load the cache report.</div>'; }
    return;
  }
  if(_histTab==='search'){
    const inp=document.getElementById('conv-search-input');
    if(inp){ inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); }
    _runConvSearch();
    return;
  }
  if(_histTab==='analytics'){
    try{
      const localAnalytics=await fetch('/analytics').then(r=>r.json());
      const remoteAnalytics=await _fetchRemoteJSON('/analytics');
      if(gen!==_histRenderGen) return;
      const d=_mergeAnalytics(localAnalytics,remoteAnalytics);
      const t=d.total||{};
      const byDay=d.by_day||[];
      const byProj=d.by_project||[];
      const byDayProj=d.by_day_project||[];
      const byModel=d.by_model||[];
      const byDayModel=d.by_day_model||[];
      const bySubagentType=d.by_subagent_type||[];
      const byTag=d.by_tag||[];
      const byDaySubagentType=d.by_day_subagent_type||[];
      const byDayHook=d.by_day_hook_reliability||[];
      const fileHotspots=d.file_hotspots||[];
      const byDayFileHotspots=d.by_day_file_hotspots||[];
      const byFileType=d.by_file_type||[];
      const tagCloud=d.tag_cloud||[];
      const slowestAgents=d.slowest_agents||[];
      const commonErrors=d.common_errors||[];
      const hookMisses=d.hook_misses||0;
      const retryCount=d.retry_count||0;
      const retryPatterns=d.retry_patterns||[];
      const hmc=d.hook_miss_concurrency||{};
      const maxProjCost=byProj.reduce((m,r)=>Math.max(m,r.cost||0),0)||1;
      const maxModelCost=byModel.reduce((m,r)=>Math.max(m,r.cost||0),0)||1;
      const maxSubagentTypeCost=bySubagentType.reduce((m,r)=>Math.max(m,r.cost||0),0)||1;
      const maxTagCost=byTag.reduce((m,r)=>Math.max(m,r.cost||0),0)||1;
      const maxFileChanges=fileHotspots.reduce((m,r)=>Math.max(m,r.changes||0),0)||1;
      const maxFileTypeChanges=byFileType.reduce((m,r)=>Math.max(m,r.changes||0),0)||1;
      const maxTagCount=tagCloud.reduce((m,r)=>Math.max(m,r.count||0),0)||1;
      const maxAgentDuration=slowestAgents.reduce((m,r)=>Math.max(m,r.duration_s||0),0)||1;
      const maxErrorOccurrences=commonErrors.reduce((m,r)=>Math.max(m,r.occurrences||0),0)||1;
      const maxRetries=retryPatterns.reduce((m,r)=>Math.max(m,r.retries||0),0)||1;
      const {costSoFar:_costSoFar,projectedMonthEnd:_projectedMonthEnd}=_computeMonthProjection(byDay);
      body.innerHTML=`
        <div class="hist-analytics">
          <div class="hist-kpi"><div class="hk-val">${t.sessions||0}</div><div class="hk-lbl">Sessions</div></div>
          <div class="hist-kpi"><div class="hk-val" style="color:rgba(76,194,133,.9)">${fmtCost(t.cost)}</div><div class="hk-lbl">Total cost</div></div>
          <div class="hist-kpi"><div class="hk-val">${fmtTok(t.tokens)}</div><div class="hk-lbl">Total tokens</div></div>
          <div class="hist-kpi" title="${t.done||0} completed successfully"><div class="hk-val">${t.agents||0}</div><div class="hk-lbl">Agents run</div></div>
          <div class="hist-kpi"><div class="hk-val" style="color:${t.errors>0?'var(--r)':'var(--t3)'}">${t.errors||0}</div><div class="hk-lbl">Errors</div></div>
          <div class="hist-kpi"><div class="hk-val">${t.files||0}</div><div class="hk-lbl">Files changed</div></div>
          <div class="hist-kpi" title="Agents whose start was only ever caught by the transcript fallback -- Claude Code's own PreToolUse hook never fired for them"><div class="hk-val" style="color:${hookMisses>0?'rgba(224,161,58,.95)':'var(--t3)'}">${hookMisses}</div><div class="hk-lbl">Hook misses</div></div>
          <div class="hist-kpi" title="An agent errored, then a same-named agent ran again in that same session -- exact-name match, same session only"><div class="hk-val" style="color:${retryCount>0?'rgba(224,161,58,.95)':'var(--t3)'}">${retryCount}</div><div class="hk-lbl">Retries</div></div>
        </div>
        ${_buildInsights({totalAgents:t.agents||0, hookMisses, commonErrors, retryPatterns}).map(i=>`
        <div style="font-family:var(--font2);font-size:10px;color:var(--t2);padding:4px 8px;margin-bottom:4px;border-radius:7px;background:rgba(224,161,58,.06);border:1px solid rgba(224,161,58,.18)">${i.icon} ${escHtml(i.text)}</div>`).join('')}
        ${hmc.hook_miss&&hmc.normal?`
        <div style="font-family:var(--font2);font-size:10px;color:var(--t3);padding:2px 2px 8px" title="Tests whether the hook drops PreToolUse more often when multiple CLI sessions are active at once">
          Avg concurrent sessions when an agent started: <span style="color:rgba(224,161,58,.9)">${hmc.hook_miss.avg_concurrent_sessions} (hook miss, n=${hmc.hook_miss.n})</span> vs <span style="color:var(--t2)">${hmc.normal.avg_concurrent_sessions} (normal, n=${hmc.normal.n})</span>
        </div>`:''}
        ${_costSoFar>0?`
        <div style="font-family:var(--font2);font-size:10px;color:var(--t3);padding:2px 2px 8px" title="Simple run-rate projection: (cost so far this month / days elapsed) x days remaining, added to cost so far">
          This month so far: <span style="color:var(--t2)">${fmtCost(_costSoFar)}</span> — projected month-end: <span style="color:rgba(224,161,58,.9)">${fmtCost(_projectedMonthEnd)}</span>
        </div>`:''}
        ${byDay.length?`
        <div class="trend-wrap" id="trend-tokens"></div>
        <div class="trend-wrap" id="trend-cost"></div>
        <div class="trend-wrap" id="trend-errors"></div>
        <div class="trend-wrap" id="trend-duration"></div>
        <div class="trend-wrap" id="trend-waiting"></div>`:''}
        ${byDayHook.length?`
        <div class="trend-wrap" id="trend-hook-reliability"></div>`:''}
        ${byDay.length?`
        <div class="trend-wrap" id="trend-task-completion"></div>`:''}
        ${byProj.length?`
        <div class="hist-section">COST BY PROJECT <span style="font-weight:400;color:var(--t3);font-size:8px;letter-spacing:0;text-transform:none">(click a project for its own trend)</span></div>
        <div class="hist-bar-wrap">${byProj.map(r=>`
          <div class="hist-bar-row" onclick="_selectProjectTrend(${jsq(r.project||'')})" style="cursor:pointer;${_selectedProjectTrend===r.project?'background:rgba(var(--c-rgb),.08);border-radius:6px':''}">
            <span class="hist-bar-lbl" style="max-width:110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r.project||'')}">${escHtml(r.project||'—')}</span>
            <div class="hist-bar-track"><div class="hist-bar-fill" style="width:${((r.cost||0)/maxProjCost*100).toFixed(1)}%;background:rgba(76,194,133,.65)"></div></div>
            <span class="hist-bar-val">${fmtCost(r.cost)}</span>
            <span style="font-family:var(--font2);font-size:9px;color:var(--t3);min-width:64px;text-align:right" title="${r.done||0} done, ${r.errors||0} errors${_taskCompletionPct(r)!=null?` · ${_taskCompletionPct(r)}% tasks done`:''}${r.waiting_on_you_s?` · Claude waited on you ${fmtDur(r.waiting_on_you_s)} total`:''}">${r.sessions}sess / ${r.agents}ag${r.errors?` · <span style="color:var(--r)">${r.errors}err</span>`:''}</span>
          </div>`).join('')}</div>
        ${_selectedProjectTrend&&byDayProj.some(r=>r.project===_selectedProjectTrend)?`
        <div class="trend-wrap" id="trend-selected-proj"></div>
        <div class="trend-wrap" id="trend-selected-proj-tokens"></div>`:''}`:''}
        ${byModel.length?`
        <div class="hist-section">COST BY MODEL <span style="font-weight:400;color:var(--t3);font-size:8px;letter-spacing:0;text-transform:none">(click a model for its own trend)</span></div>
        <div class="hist-bar-wrap">${byModel.map(r=>`
          <div class="hist-bar-row" onclick="_selectModelTrend(${jsq(r.model||'')})" style="cursor:pointer;${_selectedModelTrend===r.model?'background:rgba(var(--c-rgb),.08);border-radius:6px':''}">
            <span class="hist-bar-lbl" style="max-width:110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r.model||'')}">${escHtml(r.model||'—')}</span>
            <div class="hist-bar-track"><div class="hist-bar-fill" style="width:${((r.cost||0)/maxModelCost*100).toFixed(1)}%;background:rgba(76,194,133,.65)"></div></div>
            <span class="hist-bar-val">${fmtCost(r.cost)}</span>
            <span style="font-family:var(--font2);font-size:9px;color:var(--t3);min-width:88px;text-align:right" title="${r.done||0} done, ${r.errors||0} errors${_tokPerCall(r)!=null?` · ${_tokPerCall(r).toLocaleString()} tok/call`:''}${r.avg_duration_s!=null?` · avg ${fmtDur(r.avg_duration_s)}`:''}">${r.agents}ag / ${fmtTok(r.tokens)}${_computeSuccessRate(r.agents,r.done)!=null?` · ${_computeSuccessRate(r.agents,r.done)}%`:''}${r.errors?` · <span style="color:var(--r)">${r.errors}err</span>`:''}</span>
          </div>`).join('')}</div>
        ${_selectedModelTrend&&byDayModel.some(r=>r.model===_selectedModelTrend)?`
        <div class="trend-wrap" id="trend-selected-model"></div>
        <div class="trend-wrap" id="trend-selected-model-tokens"></div>`:''}`:''}
        ${bySubagentType.length?`
        <div class="hist-section">COST BY AGENT TYPE <span style="font-weight:400;color:var(--t3);font-size:8px;letter-spacing:0;text-transform:none">(click a type for its own trend)</span></div>
        <div class="hist-bar-wrap">${bySubagentType.map(r=>`
          <div class="hist-bar-row" onclick="_selectSubagentTypeTrend(${jsq(r.subagent_type||'')})" style="cursor:pointer;${_selectedSubagentTypeTrend===r.subagent_type?'background:rgba(var(--c-rgb),.08);border-radius:6px':''}">
            <span class="hist-bar-lbl" style="max-width:110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r.subagent_type||'')}">${escHtml(r.subagent_type||'—')}</span>
            <div class="hist-bar-track"><div class="hist-bar-fill" style="width:${((r.cost||0)/maxSubagentTypeCost*100).toFixed(1)}%;background:rgba(76,194,133,.65)"></div></div>
            <span class="hist-bar-val">${fmtCost(r.cost)}</span>
            <span style="font-family:var(--font2);font-size:9px;color:var(--t3);min-width:64px;text-align:right" title="${r.done||0} done, ${r.errors||0} errors${_tokPerCall(r)!=null?` · ${_tokPerCall(r).toLocaleString()} tok/call`:''}">${r.agents}ag / ${fmtTok(r.tokens)}${r.errors?` · <span style="color:var(--r)">${r.errors}err</span>`:''}</span>
          </div>`).join('')}</div>
        ${_selectedSubagentTypeTrend&&byDaySubagentType.some(r=>r.subagent_type===_selectedSubagentTypeTrend)?`
        <div class="trend-wrap" id="trend-selected-subagent-type"></div>
        <div class="trend-wrap" id="trend-selected-subagent-type-tokens"></div>`:''}`:''}
        ${byTag.length?`
        <div class="hist-section">COST BY TAG <span style="font-weight:400;color:var(--t3);font-size:8px;letter-spacing:0;text-transform:none">(a session with multiple tags counts toward each)</span></div>
        <div class="hist-bar-wrap">${byTag.map(r=>`
          <div class="hist-bar-row">
            <span class="hist-bar-lbl" style="max-width:110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r.tag||'')}">${escHtml(r.tag||'')}</span>
            <div class="hist-bar-track"><div class="hist-bar-fill" style="width:${((r.cost||0)/maxTagCost*100).toFixed(1)}%;background:rgba(76,194,133,.65)"></div></div>
            <span class="hist-bar-val">${fmtCost(r.cost)}</span>
            <span style="font-family:var(--font2);font-size:9px;color:var(--t3);min-width:64px;text-align:right" title="${r.done||0} done, ${r.errors||0} errors">${r.sessions}sess / ${r.agents}ag${r.errors?` · <span style="color:var(--r)">${r.errors}err</span>`:''}</span>
          </div>`).join('')}</div>`:''}
        ${fileHotspots.length?`
        <div class="hist-section">TOP FILES <span style="font-weight:400;color:var(--t3);font-size:8px;letter-spacing:0;text-transform:none">(click a file for its trend, ⇄ for its diff)</span></div>
        <div class="hist-bar-wrap">${fileHotspots.map(r=>`
          <div class="hist-bar-row" onclick="_selectFileTrend(${jsq(r.path||'')})" style="cursor:pointer;${_selectedFileTrend===r.path?'background:rgba(var(--c-rgb),.08);border-radius:6px':''}">
            <span class="hist-bar-lbl" style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;direction:rtl;text-align:left" title="${escHtml(r.path||'')}">${escHtml(r.path||'—')}</span>
            <div class="hist-bar-track"><div class="hist-bar-fill" style="width:${((r.changes||0)/maxFileChanges*100).toFixed(1)}%;background:rgba(76,194,133,.65)"></div></div>
            <span class="hist-bar-val">${r.changes} change${r.changes!==1?'s':''}</span>
            <span style="font-family:var(--font2);font-size:9px;color:var(--t3);min-width:88px;text-align:right" title="${r.sessions} distinct session${r.sessions!==1?'s':''}">${r.sessions}sess · ${r.total_lines||0}L</span>
            <span onclick="showDiff(${jsq(r.path||'')});event.stopPropagation()" title="View diff" style="cursor:pointer;padding:0 2px;opacity:.6">⇄</span>
          </div>`).join('')}</div>
        ${_selectedFileTrend&&byDayFileHotspots.some(r=>r.path===_selectedFileTrend)?`
        <div class="trend-wrap" id="trend-selected-file"></div>`:''}`:''}
        ${byFileType.length?`
        <div class="hist-section">FILE TYPES</div>
        <div class="hist-bar-wrap">${byFileType.map(r=>`
          <div class="hist-bar-row">
            <span class="hist-bar-lbl" style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r.ext||'')}">${r.ext==='(no extension)'?escHtml(r.ext):'.'+escHtml(r.ext||'')}</span>
            <div class="hist-bar-track"><div class="hist-bar-fill" style="width:${((r.changes||0)/maxFileTypeChanges*100).toFixed(1)}%;background:rgba(76,194,133,.65)"></div></div>
            <span class="hist-bar-val">${r.changes} change${r.changes!==1?'s':''}</span>
            <span style="font-family:var(--font2);font-size:9px;color:var(--t3);min-width:88px;text-align:right" title="${r.sessions} distinct session${r.sessions!==1?'s':''}">${r.sessions}sess · ${r.total_lines||0}L</span>
          </div>`).join('')}</div>`:''}
        ${tagCloud.length?`
        <div class="hist-section">TAGS <span style="font-weight:400;color:var(--t3);font-size:8px;letter-spacing:0;text-transform:none">(click a tag to filter Sessions by it)</span></div>
        <div class="hist-bar-wrap">${tagCloud.map(r=>`
          <div class="hist-bar-row" onclick="_selectTagFilter(${jsq(r.tag||'')})" style="cursor:pointer">
            <span class="hist-bar-lbl" style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r.tag||'')}">${escHtml(r.tag||'')}</span>
            <div class="hist-bar-track"><div class="hist-bar-fill" style="width:${((r.count||0)/maxTagCount*100).toFixed(1)}%;background:rgba(76,194,133,.65)"></div></div>
            <span class="hist-bar-val">${r.count} session${r.count!==1?'s':''}</span>
          </div>`).join('')}</div>`:''}
        ${slowestAgents.length?`
        <div class="hist-section">SLOWEST AGENTS <span style="font-weight:400;color:var(--t3);font-size:8px;letter-spacing:0;text-transform:none">(click to open its session)</span></div>
        <div class="hist-bar-wrap">${slowestAgents.map(r=>`
          <div class="hist-bar-row" ${r._isLocal!==false
            ?`onclick="_jumpToSessionHistory(${jsq(r.session_id||'')})" style="cursor:pointer"`
            :`style="cursor:default"`}>
            <span class="hist-bar-lbl" style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r.name||r.agent_id||'')}">${escHtml(r.name||r.agent_id||'—')}</span>
            <div class="hist-bar-track"><div class="hist-bar-fill" style="width:${((r.duration_s||0)/maxAgentDuration*100).toFixed(1)}%;background:${r.status==='error'?'rgba(239,106,87,.6)':'rgba(76,194,133,.65)'}"></div></div>
            <span class="hist-bar-val">${fmtDur(r.duration_s)}</span>
            <span style="font-family:var(--font2);font-size:9px;color:var(--t3);min-width:64px;text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r.project||'')}${r.machine?' · '+escHtml(r.machine):''}">${r.subagent_type?escHtml(r.subagent_type)+' · ':''}${escHtml(r.project||'—')}${r.status==='error'?' · <span style="color:var(--r)">err</span>':''}</span>
          </div>`).join('')}</div>`:''}
        ${commonErrors.length?`
        <div class="hist-section">COMMON ERRORS <span style="font-weight:400;color:var(--t3);font-size:8px;letter-spacing:0;text-transform:none">(exact-text matches; click to open the most recent occurrence)</span></div>
        <div class="hist-bar-wrap">${commonErrors.map(r=>`
          <div class="hist-bar-row" ${r.last_session_id
            ?`onclick="_jumpToSessionHistory(${jsq(r.last_session_id||'')})" style="cursor:pointer"`
            :`style="cursor:default"`}>
            <span class="hist-bar-lbl" style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r.error_msg||'')}">${escHtml((r.error_msg||'').slice(0,80))}</span>
            <div class="hist-bar-track"><div class="hist-bar-fill" style="width:${((r.occurrences||0)/maxErrorOccurrences*100).toFixed(1)}%;background:rgba(239,106,87,.55)"></div></div>
            <span class="hist-bar-val">${r.occurrences}×</span>
            <span style="font-family:var(--font2);font-size:9px;color:var(--t3);min-width:64px;text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r.project||'')}">${escHtml(r.project||'—')}</span>
          </div>`).join('')}</div>`:''}
        ${retryPatterns.length?`
        <div class="hist-section">RETRIED TASKS <span style="font-weight:400;color:var(--t3);font-size:8px;letter-spacing:0;text-transform:none">(exact-name match, same session)</span></div>
        <div class="hist-bar-wrap">${retryPatterns.map(r=>`
          <div class="hist-bar-row">
            <span class="hist-bar-lbl" style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r.name||'')}">${escHtml(r.name||'')}</span>
            <div class="hist-bar-track"><div class="hist-bar-fill" style="width:${((r.retries||0)/maxRetries*100).toFixed(1)}%;background:rgba(224,161,58,.55)"></div></div>
            <span class="hist-bar-val">${r.retries}×</span>
            <span style="font-family:var(--font2);font-size:9px;color:var(--t3);min-width:64px;text-align:right" title="${r.sessions} distinct session${r.sessions!==1?'s':''}${r.last_error?` · ${escHtml(r.last_error)}`:''}">${r.sessions}sess</span>
          </div>`).join('')}</div>`:''}
        ${byProj.length&&byDayProj.length?`
        <div class="hist-section">TOP PROJECTS — COST TREND</div>
        ${byProj.slice(0,3).map((p,i)=>`<div class="trend-wrap" id="trend-proj-${i}"></div>`).join('')}`:''}
      `;
      if(byDay.length){
        // by_day is ORDER BY date DESC from the backend -- reverse to
        // chronological (oldest→newest, left-to-right) for the trend charts.
        const chrono=byDay.slice().reverse();
        // Config-array + loop, matching the same shape the small-multiples
        // per-project trend loop below already uses for repeated charts.
        [
          {id:'trend-tokens',   field:'tokens',        label:'TOKENS BY DAY',                color:'var(--c)', fmt:v=>fmtTok(v)},
          {id:'trend-cost',     field:'cost',           label:'COST BY DAY',                   color:'var(--g)', fmt:v=>fmtCost(v)},
          {id:'trend-errors',   field:'errors',         label:'ERRORS BY DAY',                 color:'var(--r)', fmt:v=>String(v)},
          {id:'trend-duration', field:'avg_duration_s', label:'AVG SESSION DURATION BY DAY',   color:'var(--o)', fmt:v=>fmtDur(v)},
          {id:'trend-waiting',  field:'waiting_on_you_s', label:'TIME CLAUDE WAITED ON YOU BY DAY', color:'rgba(224,161,58,.9)', fmt:v=>fmtDur(v)},
        ].forEach(cfg=>{
          _renderTrendChart(document.getElementById(cfg.id),
            chrono.map(r=>({date:r.date, value:r[cfg.field]||0})),
            {label:`${cfg.label} (last ${chrono.length} days)`, color:cfg.color, formatValue:cfg.fmt});
        });
      }
      if(byDayHook.length){
        // Day-level view of the same hook_misses/hook_miss_concurrency data
        // already shown above as an all-time total -- shows whether the
        // PreToolUse hook's reliability is trending better or worse, not
        // just its cumulative miss count.
        const chronoHook=byDayHook.slice().reverse();
        _renderTrendChart(document.getElementById('trend-hook-reliability'),
          chronoHook.map(r=>({date:r.date, value:r.total>0?(r.misses||0)/r.total*100:0})),
          {label:`HOOK MISS RATE BY DAY (last ${chronoHook.length} days)`, color:'rgba(224,161,58,.9)', formatValue:v=>v.toFixed(1)+'%'});
      }
      if(byDay.length){
        // task_done/task_total were already summed into by_day for the
        // session-list rows' own task-completion chips, but never divided
        // against each other into a trend -- same derived-percentage shape
        // as the hook-reliability trend just above.
        const chronoTasks=byDay.slice().reverse();
        _renderTrendChart(document.getElementById('trend-task-completion'),
          chronoTasks.map(r=>({date:r.date, value:(r.task_total||0)>0?(r.task_done||0)/r.task_total*100:0})),
          {label:`TASK COMPLETION RATE BY DAY (last ${chronoTasks.length} days)`, color:'rgba(76,194,133,.9)', formatValue:v=>v.toFixed(1)+'%'});
      }
      if(_selectedProjectTrend){
        // byDayProj already covers every project with no cap (only the
        // small-multiples view below is capped to the top 3) -- clicking
        // any COST BY PROJECT row can pin open that project's own trend,
        // not just whichever 3 happen to be the biggest spenders.
        const el=document.getElementById('trend-selected-proj');
        if(el){
          const rows=byDayProj.filter(r=>r.project===_selectedProjectTrend); // already date ASC
          _renderTrendChart(el, rows.map(r=>({date:r.date, value:r.cost||0})),
            {label:`${_selectedProjectTrend} — COST TREND (selected)`, color:'var(--c)', formatValue:v=>fmtCost(v)});
          // by_day_project's SQL row carries `tokens` alongside `cost` (same
          // query, same rows) but only the cost side ever got a chart here --
          // tokens survives the multi-machine merge in _mergeAnalytics just
          // fine, it just had no render slot.
          const elTok=document.getElementById('trend-selected-proj-tokens');
          if(elTok) _renderTrendChart(elTok, rows.map(r=>({date:r.date, value:r.tokens||0})),
            {label:`${_selectedProjectTrend} — TOKENS TREND (selected)`, color:'var(--o)', formatValue:v=>fmtTok(v)});
        }
      }
      if(_selectedModelTrend){
        const el=document.getElementById('trend-selected-model');
        if(el){
          const rows=byDayModel.filter(r=>r.model===_selectedModelTrend); // already date ASC
          _renderTrendChart(el, rows.map(r=>({date:r.date, value:r.cost||0})),
            {label:`${_selectedModelTrend} — COST TREND (selected)`, color:'var(--c)', formatValue:v=>fmtCost(v)});
          const elTok=document.getElementById('trend-selected-model-tokens');
          if(elTok) _renderTrendChart(elTok, rows.map(r=>({date:r.date, value:r.tokens||0})),
            {label:`${_selectedModelTrend} — TOKENS TREND (selected)`, color:'var(--o)', formatValue:v=>fmtTok(v)});
        }
      }
      if(_selectedSubagentTypeTrend){
        const el=document.getElementById('trend-selected-subagent-type');
        if(el){
          const rows=byDaySubagentType.filter(r=>r.subagent_type===_selectedSubagentTypeTrend); // already date ASC
          _renderTrendChart(el, rows.map(r=>({date:r.date, value:r.cost||0})),
            {label:`${_selectedSubagentTypeTrend} — COST TREND (selected)`, color:'var(--c)', formatValue:v=>fmtCost(v)});
          const elTok=document.getElementById('trend-selected-subagent-type-tokens');
          if(elTok) _renderTrendChart(elTok, rows.map(r=>({date:r.date, value:r.tokens||0})),
            {label:`${_selectedSubagentTypeTrend} — TOKENS TREND (selected)`, color:'var(--o)', formatValue:v=>fmtTok(v)});
        }
      }
      if(_selectedFileTrend){
        const el=document.getElementById('trend-selected-file');
        if(el){
          const rows=byDayFileHotspots.filter(r=>r.path===_selectedFileTrend); // already date ASC
          _renderTrendChart(el, rows.map(r=>({date:r.date, value:r.changes||0})),
            {label:`${_selectedFileTrend} — CHANGES TREND (selected)`, color:'var(--c)', formatValue:v=>String(v)});
        }
      }
      if(byProj.length&&byDayProj.length){
        // Small multiples rather than one multi-line chart -- keeps each
        // project's own scale readable and avoids needing a 4th+ categorical
        // hue beyond the app's 3 established brand accent colors.
        // Deliberately cost-only, unlike the single-selection trend above --
        // giving each of the top 3 a 2nd tokens chart would double this
        // preview section to 6 charts for a glance-only overview; tokens are
        // one click away via _selectProjectTrend on any of these projects.
        const projColors=['var(--c)','var(--g)','var(--o)'];
        byProj.slice(0,3).forEach((p,i)=>{
          const rows=byDayProj.filter(r=>r.project===p.project); // already date ASC
          _renderTrendChart(document.getElementById(`trend-proj-${i}`),
            rows.map(r=>({date:r.date, value:r.cost||0})),
            {label:`${p.project} — cost trend`, color:projColors[i], formatValue:v=>fmtCost(v)});
        });
      }
    }catch(e){ body.innerHTML='<div style="color:var(--r);padding:20px;font-family:var(--font2);font-size:10px">Error loading analytics</div>'; }
  } else {
    try{
      const localSessions=await fetch('/history').then(r=>r.json());
      const remoteSessions=await _fetchRemoteJSON('/history');
      if(gen!==_histRenderGen) return;
      _histSessionsCache=_mergeHistorySessions(localSessions,remoteSessions);
      _renderHistSessionsBody();
    }catch(e){ body.innerHTML='<div style="color:var(--r);padding:20px;font-family:var(--font2);font-size:10px">Error loading history</div>'; }
  }
}

let _histSessionsCache=null;
let _histSearchQuery='';
let _histSearchFrom='';
let _histSearchTo='';
let _histSearchGen=0;
let _histDetailTags=[]; // currently-displayed session detail's tags, so _historyTagAdd/Remove don't need to scrape the DOM
let _histUntaggedOnly=false;
function _toggleUntaggedFilter(){ _histUntaggedOnly=!_histUntaggedOnly; _renderHistSessionsBody(); }
async function _renderHistSessionsBody(){
  // Separate from renderHistory() on purpose: renderHistory() rebuilds the
  // whole toolbar (including the search input itself) every call, which
  // would reset the input's cursor/focus on every keystroke if the search
  // handler called it directly -- same reason the main agent search
  // (setSearch) re-renders only #agents, never its own toolbar.
  const body=document.getElementById('hist-body');
  if(!body) return;
  const q=_histSearchQuery;
  // A query or date range reaches past the local cache (which only ever
  // holds /history's 100-most-recent rows) via the server-side
  // /history/search endpoint instead of filtering that cache -- an
  // additive capability, not a replacement of the fast "browsing the
  // most recent sessions, no filter" path below.
  const hasFilter=!!(q||_histSearchFrom||_histSearchTo);
  const myGen=++_histSearchGen;
  let sessions;
  if(hasFilter){
    try{
      const params=new URLSearchParams();
      if(q) params.set('q',q);
      if(_histSearchFrom) params.set('from',_histSearchFrom);
      if(_histSearchTo) params.set('to',_histSearchTo);
      const qstr='/history/search?'+params.toString();
      // Same-shaped merge as the unfiltered /history path above (line
      // ~6554-6555) -- without this, a search would only ever cover this
      // machine's own DB, silently dropping remote-machine history that
      // the unfiltered view already includes.
      const [localSessions,remoteSessions]=await Promise.all([
        fetch(qstr).then(r=>r.json()).catch(()=>[]),
        _fetchRemoteJSON(qstr),
      ]);
      sessions=_mergeHistorySessions(localSessions,remoteSessions);
    }catch(e){ sessions=[]; }
    if(myGen!==_histSearchGen) return; // a newer search superseded this one mid-flight
  }else{
    sessions=_histSessionsCache||[];
  }
  if(!sessions.length){
    body.innerHTML=hasFilter
      ?'<div style="color:var(--t3);font-family:var(--font2);font-size:11px;text-align:center;padding:60px 0;letter-spacing:.1em">// no sessions match your search</div>'
      :'<div style="color:var(--t3);font-family:var(--font2);font-size:11px;text-align:center;padding:60px 0;letter-spacing:.1em">// no history yet — sessions are saved on reset</div>';
    return;
  }
  const untaggedCount=sessions.filter(s=>!(s.tags&&s.tags.length)).length;
  if(_histUntaggedOnly) sessions=sessions.filter(s=>!(s.tags&&s.tags.length));
  const untaggedBar=untaggedCount>0?`
    <div style="font-size:10px;font-family:var(--font2);color:var(--t3);padding:0 0 8px;display:flex;align-items:center;gap:6px">
      <span>🏷 ${untaggedCount} untagged session${untaggedCount!==1?'s':''}</span>
      <button class="adp-btn" onclick="_toggleUntaggedFilter()" style="font-size:9px;padding:1px 8px">${_histUntaggedOnly?'SHOW ALL':'SHOW ONLY THESE'}</button>
    </div>`:'';
  if(!sessions.length){
    body.innerHTML=untaggedBar+'<div style="color:var(--t3);font-family:var(--font2);font-size:11px;text-align:center;padding:60px 0;letter-spacing:.1em">// no untagged sessions</div>';
    return;
  }
  const fmtCost=_fmtCost;
  const fmtTok=t=>t?Number(t).toLocaleString():'—';
  const fmtDur=_fmtDurShort;
  // Retrospective counterpart to the live cost_spike webhook: that one only
  // ever fires a one-time alert while a session is still running, so once
  // it closes there was no lasting record of which past sessions were
  // outliers. Reuses the exact same predicate/baseline (_isCostSpike,
  // _projectAvgCosts) against whatever KPI-bar analytics fetch already
  // happens to be cached -- local-only (not merged across Remote Machines,
  // unlike the session list itself), so this only ever marks local rows;
  // skipped entirely (no badge, not a crash) if that cache hasn't
  // populated yet.
  const projectAvgCosts=_projectAvgCosts((_kpiAnalytics||{}).by_project);
  const grouped={};
  sessions.forEach(s=>{ const d=s.date||'—'; if(!grouped[d]) grouped[d]=[]; grouped[d].push(s); });
  body.innerHTML=untaggedBar+Object.entries(grouped).map(([date,rows])=>`
    <div class="hist-section">${date}</div>
    ${rows.map(s=>{
      const isRemote=s._isLocal===false;
      const rm=isRemote?(_remoteMachines.find(m=>m.name===s.machine)||{}):null;
      const onclick=isRemote
        ?`showHistoryDetail(${jsq(s.id)},${jsq(rm.url||'')},${jsq(rm.token||'')})`
        :`showHistoryDetail(${jsq(s.id)})`;
      const projectAvg=isRemote?0:(projectAvgCosts[s.project]||0);
      const isOutlier=_isCostSpike(s.cost||0,projectAvg);
      return `
      <div class="hist-row" onclick="${onclick}">
        <span class="hr-date">${s.started_at||'—'} – ${s.ended_at||'—'}</span>
        <span class="hr-project">${s.title?`${escHtml(s.title)}${s.project?` <span class="hr-proj2">// ${escHtml(s.project)}</span>`:''}`:escHtml(s.project||'(no project)')}${isRemote?` <span style="color:var(--c);opacity:.7">⌘ ${escHtml(s.machine)}</span>`:''}${(s.tags||[]).map(t=>`<button class="le-tag" style="margin-left:6px" onclick="event.stopPropagation();setHistSearch(${jsq(t)})" title="Filter History by this tag">${escHtml(t)}</button>`).join('')}</span>
        <span class="hr-chips">
          ${s.agents>0?`<span class="hist-chip ok">${s.done||0}✓ ${s.agents}ag</span>`:''}
          ${s.task_total>0?`<span class="hist-chip cost">${s.task_done||0}/${s.task_total}t</span>`:''}
          ${s.errors>0?`<span class="hist-chip err">${s.errors}✗</span>`:''}
          ${isOutlier?`<span class="hookmiss-badge" title="${fmtCost(s.cost)} vs. this project's own ${fmtCost(projectAvg)} average session cost">⚡ OUTLIER</span>`:''}
          ${s.cost?`<span class="hist-chip cost">${fmtCost(s.cost)}</span>`:''}
          ${s.tokens?`<span class="hist-chip tok">${fmtTok(s.tokens)}</span>`:''}
          <span style="font-family:var(--font2);font-size:9px;color:var(--t3)">${fmtDur(s.duration_s)}</span>
        </span>
      </div>`;}).join('')}
  `).join('');
}
/* ── prompt-cache report (History → CACHE) ── */
const _CACHE_SPLIT=[  /* categorical order validated for both surfaces (dataviz validator) */
  {k:'cost_read', label:'Cache reads', dark:'#3987e5', light:'#2a78d6',
   tip:'Every call sends the whole conversation again; cached tokens are cheap but there are a lot of them'},
  {k:'cost_write', label:'Cache writes', dark:'#d95926', light:'#eb6834',
   tip:'New messages added to the cache, plus full re-caches'},
  {k:'cost_out', label:'Output', dark:'#199e70', light:'#1baf7a', tip:"Claude's replies and tool calls"},
  {k:'cost_in', label:'Uncached input', dark:'#c98500', light:'#eda100', tip:'Prompt tokens sent without the cache'},
];
/* $1,234 / $56.70 / $0.0123 -- whole dollars once it's big, cents below
   that, 4 decimals only for sub-dollar amounts */
function _fmtMoney(c){
  if(c==null||isNaN(c)) return '—';
  c=+c;
  return c>=1000?'$'+Math.round(c).toLocaleString('en-US'):c>=1?'$'+c.toFixed(2):'$'+c.toFixed(4);
}
function _fmtTokShort(n){
  n=Number(n)||0;
  return n>=1e9?(n/1e9).toFixed(1)+'B':n>=1e6?(n/1e6).toFixed(1)+'M':n>=1e3?Math.round(n/1e3)+'k':String(n);
}
/* Pure: /cache_stats response -> plain-language findings, most important first. */
function _cacheInsights(t){
  const out=[];
  if(!t||!t.cost) return out;
  const readShare=t.cost_read/t.cost;
  if(readShare>=0.4) out.push({icon:'📚', text:`${Math.round(readShare*100)}% of the spend is Claude re-reading the conversation: every call sends the whole context again — about ${_fmtTokShort(t.avg_context)} tokens per call here. Long sessions get more expensive with every message; when a task is done, start a fresh session or run /compact.`});
  if(t.cold_n>0) out.push({icon:'🧊', text:`${t.cold_n} time${t.cold_n===1?'':'s'} a session's whole context had to be cached again${t.cold_idle_n?` (${t.cold_idle_n===t.cold_n?'all':t.cold_idle_n} after an hour or more away — Claude Code's cache lasts 1 hour)`:''}, costing ${_fmtMoney(t.cold_extra)} more than a warm cache would have. Coming back within the hour, or starting fresh with a short summary, avoids it.`});
  if(t.hit_rate!=null&&t.hit_rate<0.8) out.push({icon:'⚠', text:`Only ${Math.round(t.hit_rate*100)}% of prompt tokens came from the cache — usually a sign of frequent model switches or context being rewritten.`});
  if(t.saved>0) out.push({icon:'✓', text:`The cache saved about ${_fmtMoney(t.saved)}: the ${_fmtTokShort(t.cr)} cached tokens would have cost that much more as plain input.`});
  return out;
}
/* Pure: /cache_stats response -> report HTML. */
function _cacheReportHtml(d){
  d=d||{};
  const idx=d.index||{};
  if(d.error) return `<div class="conv-empty">The cache report failed — see Health → background errors.</div>`;
  const st=d.stats, t=st&&st.total;
  if(!t||!t.n){
    return `<div class="conv-empty">${idx.building||!idx.updated_at
      ?'Still reading your Claude Code transcripts — the report appears once the index is built (a few minutes after AOC starts).'
      :'No Claude Code calls in this period.'}</div>`;
  }
  const light=document.body&&document.body.classList.contains('light');
  const pct=v=>t.cost?v/t.cost*100:0;
  const segs=_CACHE_SPLIT.filter(x=>t[x.k]>0);
  const bar=segs.map(x=>`<span class="cache-seg" style="width:${pct(t[x.k]).toFixed(2)}%;background:${light?x.light:x.dark}" title="${escHtml(x.label)}: ${_fmtMoney(t[x.k])} (${pct(t[x.k]).toFixed(1)}%)"></span>`).join('');
  const legend=_CACHE_SPLIT.map(x=>`<div class="cache-leg" title="${escHtml(x.tip)}"><i style="background:${light?x.light:x.dark}"></i><span class="cache-leg-l">${escHtml(x.label)}</span><span class="cache-leg-v">${_fmtMoney(t[x.k])}</span><span class="cache-leg-p">${pct(t[x.k]).toFixed(1)}%</span></div>`).join('');
  const kpi=(v,l,tip)=>`<div class="hist-kpi" title="${escHtml(tip)}"><div class="hk-val">${v}</div><div class="hk-lbl">${l}</div></div>`;
  const kpis=[
    kpi(_fmtMoney(t.cost),'Spend','Estimated API-price cost of every Claude Code call in this period (on a subscription you pay the plan, not this)'),
    kpi(t.hit_rate==null?'—':Math.round(t.hit_rate*100)+'%','From cache','Share of prompt tokens read from the cache instead of being sent again at full price'),
    kpi(_fmtMoney(t.saved),'Saved by cache','What the cached tokens would have cost as plain input, minus what they did cost'),
    kpi(String(t.cold_n),'Re-caches','Calls that had to write most of the context to the cache again (cache expired, model switched, or context rewritten)'),
    kpi(_fmtTokShort(t.avg_context),'Avg context','Prompt tokens per call — the whole conversation is sent every time'),
  ].join('');
  const ins=_cacheInsights(t).map(i=>`<div class="cache-insight"><span>${i.icon}</span><span>${escHtml(i.text)}</span></div>`).join('');
  const sess=(st.sessions||[]).map(x=>{
    const folder=(x.cwd||'').split(/[\\/]/).filter(Boolean).pop()||'';
    const name=x.title||x.project||folder||x.session_id.slice(0,8);
    return `<tr>
      <td class="cache-name"><span title="${escHtml(x.session_id)}">${escHtml(name)}</span></td>
      <td class="num">${_fmtMoney(x.cost)}</td>
      <td class="num">${x.cost?Math.round(x.cost_read/x.cost*100):0}%</td>
      <td class="num cache-opt">${_fmtTokShort(x.avg_context)}</td>
      <td class="num cache-opt">${x.hit_rate==null?'—':Math.round(x.hit_rate*100)+'%'}</td>
      <td class="num">${x.cold_n?`${x.cold_n} <span class="cache-extra">+${_fmtMoney(x.cold_extra)}</span>`:'—'}</td>
      <td><button class="adp-btn conv-btn" onclick="showHistoryDetail(${jsq(x.session_id)})">OPEN</button></td>
    </tr>`;
  }).join('');
  const models=(st.by_model||[]).map(m=>`<tr><td>${escHtml(m.model||'?')}</td><td class="num">${m.n.toLocaleString()}</td><td class="num">${_fmtMoney(m.cost)}</td><td class="num">${m.hit_rate==null?'—':Math.round(m.hit_rate*100)+'%'}</td><td class="num">${_fmtTokShort(m.avg_context)}</td><td class="num">${m.cold_n||'—'}</td></tr>`).join('');
  return `<div class="cache-report">
    <div class="hist-analytics">${kpis}</div>
    <div class="hist-section">WHERE THE MONEY GOES</div>
    <div class="cache-bar" role="img" aria-label="${escHtml(_CACHE_SPLIT.map(x=>x.label+' '+pct(t[x.k]).toFixed(0)+'%').join(', '))}">${bar}</div>
    <div class="cache-legend">${legend}</div>
    ${ins}
    <div class="hist-section">MOST EXPENSIVE SESSIONS</div>
    <div class="cache-table-wrap"><table class="cache-table">
      <thead><tr><th>Session</th><th class="num">Spend</th><th class="num" title="Share of the session's spend that was re-reading context">Re-reading</th><th class="num cache-opt">Avg context</th><th class="num cache-opt">From cache</th><th class="num">Re-caches</th><th></th></tr></thead>
      <tbody>${sess}</tbody></table></div>
    <div class="hist-section">BY MODEL</div>
    <div class="cache-table-wrap"><table class="cache-table">
      <thead><tr><th>Model</th><th class="num">Calls</th><th class="num">Spend</th><th class="num">From cache</th><th class="num">Avg context</th><th class="num">Re-caches</th></tr></thead>
      <tbody>${models}</tbody></table></div>
    <div class="conv-status" style="margin-top:6px">Estimated at API prices from the Claude Code transcripts on this machine (subagent transcripts not included). ${idx.building?'Still indexing — numbers will grow.':''}</div>
  </div>`;
}

/* ── cost per commit (History → COMMITS) ── */
let _lastCommitData=null;
function _setCommitView(sort, all){
  if(sort) _commitSort=sort;
  if(all!==undefined) _commitShowAll=all;
  const body=document.getElementById('hist-body');
  if(body&&_lastCommitData) body.innerHTML=_commitReportHtml(_lastCommitData, _commitSort, _commitShowAll);
}
/* Pure: /commit_costs response -> report HTML. */
