/* ── agent detail panel ── */
let _adpAgentId = null;
let _adpTab = 'log';
let _adpCopyData = '';

function openAgentDetail(id, event){
  if(event) event.stopPropagation();
  _adpAgentId = id;
  _adpTab = 'log';
  const ov = document.getElementById('agent-detail-overlay');
  if(ov) ov.classList.add('open');
  ['log','tasks','files'].forEach(t=>{
    const el=document.getElementById('adpt-'+t);
    if(el) el.classList.toggle('active', t==='log');
  });
  _renderAdp();
}

function closeAgentDetail(){
  const ov = document.getElementById('agent-detail-overlay');
  if(ov) ov.classList.remove('open');
  _adpAgentId = null;
}

function setAdpTab(tab){
  _adpTab = tab;
  ['log','tasks','files'].forEach(t=>{
    const el=document.getElementById('adpt-'+t);
    if(el){ el.classList.toggle('active', t===tab); el.setAttribute('aria-selected',t===tab); }
  });
  _renderAdpBody();
}

function _renderAdp(){
  if(!_adpAgentId || !lastStatus) return;
  const a=(lastStatus.agents||[]).find(ag=>ag.id===_adpAgentId);
  if(!a){ closeAgentDetail(); return; }
  const col=sCol(a.status);
  const stLabel=stxt(a);
  let elapsedStr='';
  if(a.started_at){
    const startMs=parseTimeStr(a.started_at);
    let endMs=Date.now();
    if(a.status!=='running'&&a.completed_at) endMs=parseTimeStr(a.completed_at);
    const secs=Math.max(0,Math.floor((endMs-startMs)/1000));
    elapsedStr=secs<60?secs+'s':Math.floor(secs/60)+'m '+(secs%60)+'s';
  }
  // input/output/cache_read/cache_write_tokens are all tracked per agent
  // (same fields the session-card tooltip already surfaces at the session
  // level) but only their sum (tokens_used) was ever shown here -- cache
  // tokens matter for cost, so break it down in a hover tooltip on the
  // existing cost badge. Only present on the hook's tool_response when it
  // happens to include usage+resolvedModel (not guaranteed every time), so
  // check for actual presence rather than defaulting to 0 -- otherwise a
  // missing breakdown would misleadingly render as "Input: 0 · Output: 0 …"
  // instead of just omitting the tooltip. The 4 fields are always set
  // together as one group (see aoc_hook.py's usage_fields / monitor.py's
  // _apply_agent_update), so checking one is enough.
  const hasTokBreakdown=a.input_tokens!=null;
  const tokBreakdownParts=[];
  if(hasTokBreakdown) tokBreakdownParts.push(_tokBreakdownStr(a));
  // tool_use_count is a separate signal from tokens_used (a high token
  // count with few tool calls behaves very differently from a low count
  // with many) and isn't gated behind the same usage+resolvedModel
  // condition as the breakdown above, so it's tracked independently.
  if(a.tool_use_count!=null) tokBreakdownParts.push(`${a.tool_use_count} tool call${a.tool_use_count!==1?'s':''}`);
  const tokBreakdown=tokBreakdownParts.join(' · ');
  const tokStr=a.tokens_used?`<span style="font-size:10px;color:rgba(76,194,133,.8);font-family:var(--font2)"${tokBreakdown?` title="${escHtml(tokBreakdown)}"`:''}>$${_agentCost(a).toFixed(4)}</span>`:'';
  // token_limit already draws a context-window progress bar on the agent
  // card (renderAgents' tokensHtml) but was never surfaced here -- same
  // field, compact badge form for the header row instead of a full bar.
  const ctxPct=_ctxPct(a);
  const ctxStr=ctxPct!==null?`<span style="font-size:9px;color:var(--t3);font-family:var(--font2)" title="Context window used: ${Number(a.tokens_used).toLocaleString()} / ${Number(a.token_limit).toLocaleString()} tokens">${ctxPct}% ctx</span>`:'';
  // Raw model string, same truncate-with-title-tooltip treatment as the
  // COST BY MODEL bar labels in History -> Analytics -- this is the one
  // model field on an agent, it was tracked and costed server-side but
  // never actually shown anywhere on the live agent itself until now.
  const modelStr=a.model?`<span style="font-size:9px;color:var(--t3);font-family:var(--font2);max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(a.model)}">${escHtml(a.model)}</span>`:'';
  // subagent_type (e.g. "general-purpose", "Explore", "Plan") was sitting
  // in tool_input at hook registration time but never captured before --
  // the only way to tell agents apart was their free-text description.
  const subagentTypeStr=a.subagent_type?`<span style="font-size:9px;color:rgba(var(--c-rgb),.75);font-family:var(--font2);background:rgba(var(--c-rgb),.08);border-radius:4px;padding:1px 6px" title="Subagent type">${escHtml(a.subagent_type)}</span>`:'';
  // detected_via==='transcript' (this agent's PreToolUse hook never fired,
  // caught by the transcript fallback instead) already surfaces as a
  // HOOK MISS badge on the card, the AGENTS-tab row, SUMMARY, Compare,
  // the GRAPH/TREE tooltip and History Detail -- this panel was the one
  // per-agent surface still missing it.
  const hookMissStr=a.detected_via==='transcript'?`<span class="hookmiss-badge" title="${escHtml(_hookMissTitle(a))}">⚠ HOOK MISS</span>`:'';
  // parent_id (this agent was itself spawned by another subagent, see
  // TREE view / _extract_child_agent_ids) was only ever consumed by
  // _buildTree's own layout code -- nowhere else told you which agent
  // spawned this one. Clickable straight to the parent's own detail
  // panel, same pattern as the rest of this header's badges.
  const parentAgent=_resolveParentAgent(a);
  const parentStr=parentAgent?`<span style="font-size:9px;color:var(--t3);font-family:var(--font2);cursor:pointer" onclick="openAgentDetail(${jsq(parentAgent.id)},null)" title="Spawned by this agent — click to open">↳ ${escHtml(parentAgent.name)}</span>`:'';
  // Reverse direction of the same thread -- TREE already draws a childCount
  // badge (${childCount}↓) on a parent node, but nothing told you that from
  // an orchestrator agent's own detail panel.
  const childAgents=_resolveChildAgents(a);
  const childStr=childAgents.length?`<span style="font-size:9px;color:var(--t3);font-family:var(--font2);cursor:pointer" onclick="openAgentDetail(${jsq(childAgents[0].id)},null)" title="${escHtml(childAgents.map(c=>c.name).join(', '))}">spawned ${childAgents.length} subagent${childAgents.length!==1?'s':''} →</span>`:'';
  // Same _stuckSecs() signal the card already showed (>300s since this
  // running agent's last task completion) -- the detail panel, the surface
  // you'd actually click into to investigate why an agent looks frozen,
  // never carried it.
  const stuckSecs=_stuckSecs(a);
  const stuckStr=stuckSecs>300?`<div class="stuck-badge">⚠ STUCK — no progress for ${stuckSecs<3600?Math.floor(stuckSecs/60)+'m':Math.floor(stuckSecs/3600)+'h'}</div>`:'';
  const isRunning=a.status==='running'||a.status==='waiting';
  const liveBadge=isRunning?`<div class="live-badge" style="font-size:9px;letter-spacing:.07em;margin-left:auto;margin-right:4px"><div class="dot"></div>LIVE</div>`:'';
  const tasks=a.tasks||[];
  const activeTask=isRunning?tasks.find(t=>!t.done):null;
  const activeTaskHtml=activeTask?`<div style="display:flex;align-items:center;gap:6px;margin-top:6px;padding:5px 9px;border-radius:7px;background:rgba(var(--c-rgb),.06);border:1px solid rgba(var(--c-rgb),.18)">
    <span style="font-size:9px;font-family:var(--font2);color:rgba(var(--c-rgb),.7);letter-spacing:.09em;flex-shrink:0">► NOW</span>
    <span style="font-size:11px;color:var(--c);font-family:var(--font2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(activeTask.label||'')}</span>
  </div>`:'';
  const hdrEl=document.getElementById('adp-hdr');
  if(hdrEl) hdrEl.innerHTML=`
    <div class="unit-badge" style="border-color:${col};color:${col};background:rgba(var(--c-rgb),.07);margin-top:2px">${escHtml(_deriveUnit(a))}</div>
    <div style="flex:1;min-width:0">
      <div style="font-size:13px;font-weight:700;color:${col};margin-bottom:3px;line-height:1.3">${escHtml(a.name)}</div>
      ${a.description?`<div style="font-size:11px;color:var(--t2);line-height:1.45;margin-bottom:5px">${escHtml(a.description)}</div>`:''}
      <div style="display:flex;align-items:center;gap:7px;flex-wrap:wrap">
        <div class="status-pill ${escHtml(a.status)}" style="font-size:9px;padding:2px 7px"><span class="sdot"></span>${escHtml(stLabel)}</div>
        ${elapsedStr?`<span style="font-size:10px;font-family:var(--font2);color:var(--t3)">${elapsedStr}</span>`:''}
        ${tokStr}
        ${ctxStr}
        ${modelStr}
        ${subagentTypeStr}
        ${hookMissStr}
        ${parentStr}
        ${childStr}
      </div>
      ${stuckStr}
      ${activeTaskHtml}
    </div>
    ${liveBadge}
    <button class="adp-btn" onclick="exportAgentDetail()" title="Export agent report to clipboard [E]" style="padding:3px 10px;font-size:9px;margin-right:4px;flex-shrink:0">⎘ MD</button>
    <button class="adp-close" onclick="closeAgentDetail()" aria-label="Close">✕</button>
  `;
  const logCount=(a.log||[]).length;
  const taskCount=(a.tasks||[]).length;
  const fileCount=(a.files_changed||[]).length;
  const lt=document.getElementById('adpt-log'); if(lt) lt.textContent=`LOG (${logCount})`;
  const tt=document.getElementById('adpt-tasks'); if(tt) tt.textContent=`TASKS (${taskCount})`;
  const ft=document.getElementById('adpt-files'); if(ft) ft.textContent=`FILES (${fileCount})`;
  _renderAdpBody();
}

function _renderAdpBody(){
  if(!_adpAgentId || !lastStatus) return;
  const a=(lastStatus.agents||[]).find(ag=>ag.id===_adpAgentId);
  if(!a) return;
  const bodyEl=document.getElementById('adp-body');
  if(!bodyEl) return;
  const scrollTop=bodyEl.scrollTop;

  if(_adpTab==='log'){
    const entries=a.log||[];
    _adpCopyData=entries.join('\n');
    if(!entries.length){
      bodyEl.innerHTML='<div style="color:var(--t3);font-family:var(--font2);font-size:11px;text-align:center;padding:50px 0;letter-spacing:.08em">// no log entries</div>';
      return;
    }
    bodyEl.innerHTML=`<div class="adp-sec">LOG MESSAGES<button class="adp-btn" onclick="copyText(this,_adpCopyData)">COPY ALL</button></div>`+
      entries.slice().reverse().map(e=>`<div class="adp-log-line">${escHtml(String(e))}</div>`).join('');
  } else if(_adpTab==='tasks'){
    const tasks=a.tasks||[];
    const done=tasks.filter(t=>t.done).length;
    if(!tasks.length){
      bodyEl.innerHTML='<div style="color:var(--t3);font-family:var(--font2);font-size:11px;text-align:center;padding:50px 0;letter-spacing:.08em">// no tasks defined</div>';
      return;
    }
    const errHtml=(a.status==='error'&&a.error_message)?`<div style="margin-bottom:8px;padding:7px 10px;border-radius:8px;background:rgba(239,106,87,.06);border:1px solid rgba(239,106,87,.2);border-left:3px solid var(--r)"><div style="font-size:9px;font-family:var(--font2);color:var(--r);letter-spacing:.08em;margin-bottom:3px">ERROR DETAIL</div><div style="font-size:11px;color:rgba(240,164,151,.85);font-family:var(--font2);line-height:1.5;word-break:break-all">${escHtml(a.error_message)}</div></div>`:'';
    bodyEl.innerHTML=`<div class="adp-sec">TASKS — ${done}/${tasks.length} COMPLETE</div>`+errHtml+
      tasks.map(t=>`<div class="adp-task-row ${t.done?'done':'pend'}">
        <span style="flex-shrink:0">${t.done?'✓':'·'}</span>
        <span style="flex:1">${escHtml(t.label||'')}</span>
        ${t.completed_at?`<span style="font-size:9px;font-family:var(--font2);color:var(--t3);flex-shrink:0">${t.completed_at}</span>`:''}
      </div>`).join('');
  } else if(_adpTab==='files'){
    const files=a.files_changed||[];
    if(!files.length){
      bodyEl.innerHTML='<div style="color:var(--t3);font-family:var(--font2);font-size:11px;text-align:center;padding:50px 0;letter-spacing:.08em">// no file changes</div>';
      return;
    }
    bodyEl.innerHTML=`<div class="adp-sec">FILES CHANGED — ${files.length}</div>`+
      files.map(f=>`<div class="fe ${f.type==='new'?'new':'changed'}" onclick="showDiff(${jsq(f.path)});closeAgentDetail()" style="cursor:pointer" title="Click for diff — closes panel">
        <span class="fe-badge">${f.type==='new'?'NEW':'MOD'}</span>
        <span class="fe-name" title="${escHtml(f.path)}">${escHtml(f.path)}</span>
        ${f.lines?`<span class="fe-lines">${escHtml(f.lines)}L</span>`:''}
      </div>`).join('');
  }

  bodyEl.scrollTop=scrollTop;
}

/* Builds one agent's Markdown block -- shared by exportAgentDetail (a
   standalone report, headingPrefix defaults to its original "# AGENT: "
   H1) and exportSessionDetail (embedded under a session's own H1, passes
   "### " instead) so this formatting logic isn't duplicated a third
   time. The internal Tasks/Error/Files/Log sub-headings stay fixed at
   "## " either way -- keeps exportAgentDetail's own output byte-for-byte
   unchanged after this refactor, at the minor cost of those sub-sections
   not perfectly nesting under a session doc's own headings. */
// parentName is pre-resolved by the caller (exportAgentDetail/
// exportSessionDetail, where the full agents list is in scope) rather
// than looked up in here off a.parent_id -- this function stays a pure
// function of its own arguments, same as every other field it reads,
// so it's testable in isolation without needing lastStatus defined.
function _agentToMarkdown(a,headingPrefix='# AGENT: ',parentName=null,childNames=null){
  const tasksDone=(a.tasks||[]).filter(t=>t.done).length;
  const tasksTotal=(a.tasks||[]).length;
  const pct=tasksTotal>0?Math.round(tasksDone/tasksTotal*100):0;
  const cost=a.tokens_used?_agentCost(a).toFixed(4):null;
  const stLabel=stxt(a);
  let md=`${headingPrefix}${a.name}\n`;
  md+=`**Status:** ${stLabel}\n`;
  if(a.session_project) md+=`**Project:** ${a.session_project}\n`;
  if(a.started_at) md+=`**Started:** ${a.started_at}\n`;
  if(a.completed_at) md+=`**Completed:** ${a.completed_at}\n`;
  if(a.started_at){
    const startMs=parseTimeStr(a.started_at);
    let endMs=Date.now();
    if(a.status!=='running'&&a.completed_at) endMs=parseTimeStr(a.completed_at);
    const secs=Math.max(0,Math.floor((endMs-startMs)/1000));
    md+=`**Duration:** ${secs<60?secs+'s':Math.floor(secs/60)+'m '+(secs%60)+'s'}\n`;
  }
  if(a.tokens_used) md+=`**Tokens:** ${Number(a.tokens_used).toLocaleString()}${cost?' ($'+cost+')':''}\n`;
  if(a.token_limit&&a.tokens_used) md+=`**Context used:** ${_ctxPct(a)}% (${Number(a.tokens_used).toLocaleString()} / ${Number(a.token_limit).toLocaleString()})\n`;
  if(a.model) md+=`**Model:** ${a.model}\n`;
  if(a.subagent_type) md+=`**Subagent type:** ${a.subagent_type}\n`;
  if(a.tool_use_count!=null) md+=`**Tool calls:** ${a.tool_use_count}\n`;
  if(a.detected_via==='transcript') md+=`**Detected via:** transcript fallback (hook miss)${a.concurrent_sessions!=null?` — ${a.concurrent_sessions} other session${a.concurrent_sessions!==1?'s':''} active at the time`:''}\n`;
  const stuckSecs=_stuckSecs(a);
  if(stuckSecs>300) md+=`**Stuck:** no progress for ${stuckSecs<3600?Math.floor(stuckSecs/60)+'m':Math.floor(stuckSecs/3600)+'h'}\n`;
  if(parentName) md+=`**Spawned by:** ${parentName}\n`;
  if(childNames&&childNames.length) md+=`**Spawned subagents:** ${childNames.join(', ')}\n`;
  if(a.description) md+=`**Description:** ${a.description}\n`;
  md+='\n';
  if(tasksTotal){
    md+=`## Tasks (${tasksDone}/${tasksTotal} — ${pct}%)\n`;
    (a.tasks||[]).forEach(t=>{
      md+=`- [${t.done?'x':' '}] ${t.label||''}`;
      if(t.completed_at) md+=` _(${t.completed_at})_`;
      md+='\n';
    });
    md+='\n';
  }
  if(a.status==='error'&&a.error_message){
    md+=`## Error\n\`\`\`\n${a.error_message}\n\`\`\`\n\n`;
  }
  const files=a.files_changed||[];
  if(files.length){
    md+=`## Files Changed (${files.length})\n`;
    files.forEach(f=>{ md+=`- ${f.type==='new'?'NEW':'MOD'} \`${f.path}\`${f.lines?' ('+f.lines+'L)':''}\n`; });
    md+='\n';
  }
  const logEntries=a.log||[];
  if(logEntries.length){
    md+=`## Log (${logEntries.length} entries)\n\`\`\`\n`;
    logEntries.forEach(e=>{ md+=String(e)+'\n'; });
    md+='```\n';
  }
  return md;
}
function _copyMarkdownToClipboard(md,label){
  const _doToast=()=>showToast('done','EXPORTED',label+' copied to clipboard');
  navigator.clipboard.writeText(md).then(_doToast).catch(()=>{
    const ta=document.createElement('textarea');
    ta.value=md; ta.style.cssText='position:fixed;top:-9999px;left:-9999px';
    document.body.appendChild(ta); ta.select(); document.execCommand('copy');
    document.body.removeChild(ta); _doToast();
  });
}
function exportAgentDetail(){
  if(!_adpAgentId || !lastStatus) return;
  const a=(lastStatus.agents||[]).find(ag=>ag.id===_adpAgentId);
  if(!a) return;
  const parent=_resolveParentAgent(a);
  const children=_resolveChildAgents(a).map(ag=>ag.name);
  _copyMarkdownToClipboard(_agentToMarkdown(a,undefined,parent?.name,children),'Agent report');
}
function exportSessionDetail(sessionId){
  if(!lastStatus) return;
  const sess=(lastStatus.sessions_list||[]).find(s=>s.id===sessionId);
  const agents=(lastStatus.agents||[]).filter(a=>a.session_id===sessionId);
  const label=sess?(sess.display_name||sess.project||sessionId.slice(-6)):sessionId.slice(-6);
  const tasksDone=agents.reduce((s,a)=>s+((a.tasks||[]).filter(t=>t.done).length),0);
  const tasksTotal=agents.reduce((s,a)=>s+((a.tasks||[]).length),0);
  const totalTokens=agents.reduce((s,a)=>s+(a.tokens_used||0),0);
  const totalCost=agents.reduce((s,a)=>s+_agentCost(a),0);
  let md=`# SESSION: ${label}\n`;
  if(sess){
    if(sess.project) md+=`**Project:** ${sess.project}\n`;
    if(sess.git_branch) md+=`**Branch:** ${sess.git_branch}\n`;
    if(sess.cc_version) md+=`**CC Version:** ${sess.cc_version}\n`;
    if(sess.note) md+=`**Note:** ${sess.note}\n`;
  }
  md+=`**Agents:** ${agents.length}\n`;
  if(tasksTotal) md+=`**Tasks:** ${tasksDone}/${tasksTotal}\n`;
  if(totalTokens>0) md+=`**Tokens:** ${Number(totalTokens).toLocaleString()} (~$${totalCost.toFixed(4)})\n`;
  md+='\n';
  if(agents.length){
    md+=`## Agents\n\n`;
    agents.forEach(a=>{
      const parent=_resolveParentAgent(a);
      const children=_resolveChildAgents(a).map(ag=>ag.name);
      md+=_agentToMarkdown(a,'### ',parent?.name,children)+'\n';
    });
  }else{
    md+='_No agents recorded for this session._\n';
  }
  _copyMarkdownToClipboard(md,'Session report');
}

/* ── notification history panel ── */
function _updateNotifBadge(){
  const badge=document.getElementById('notif-badge');
  if(!badge) return;
  if(_notifUnread>0){
    badge.style.display='flex';
    badge.textContent=_notifUnread>99?'99+':String(_notifUnread);
  } else {
    badge.style.display='none';
  }
}

function _renderNotifPanel(){
  const listEl=document.getElementById('notif-list');
  const cntEl=document.getElementById('notif-hdr-count');
  if(!listEl) return;
  const n=_notifHistory.length;
  if(cntEl) cntEl.textContent=n?`${n} event${n!==1?'s':''}`:' ';
  if(!n){
    listEl.innerHTML='<div style="text-align:center;padding:60px 20px;color:var(--t3);font-family:var(--font2);font-size:11px;letter-spacing:.08em">// no events yet</div>';
    return;
  }
  listEl.innerHTML=[..._notifHistory].reverse().map(e=>
    `<div class="notif-entry ${e.type}">
      <span class="notif-entry-time">${e.t}</span>
      ${e.tag?`<span class="notif-entry-tag">${escHtml(e.tag)}</span>`:''}
      <span class="notif-entry-msg">${escHtml(e.msg)}</span>
    </div>`
  ).join('');
}

function openNotifPanel(){
  _notifOpen=true;
  _notifUnread=0;
  _updateNotifBadge();
  const ov=document.getElementById('notif-overlay');
  if(ov) ov.classList.add('open');
  _renderNotifPanel();
}

function closeNotifPanel(){
  _notifOpen=false;
  const ov=document.getElementById('notif-overlay');
  if(ov) ov.classList.remove('open');
}

function clearNotifHistory(){
  _notifHistory=[];
  _notifUnread=0;
  _updateNotifBadge();
  _renderNotifPanel();
}

/* ── heatmap view ── */
let _heatMode = 'tasks'; /* 'tasks' | 'tokens' */
let _heatBinSec = 30;

function _heatBinOptions(rangeSec){
  if(rangeSec<=120) return [15,30];
  if(rangeSec<=600) return [30,60];
  if(rangeSec<=1800) return [60,120];
  return [120,300];
}

function renderHeatmap(data){
  const el=document.getElementById('heat-area');
  if(!el) return;
  const agents=(data.agents||[]).filter(a=>!String(a.id||'').startsWith('hook_')&&a.started_at);
  if(!agents.length){ _sessFallback('heat', el, data); return; }
  _svPainted.delete(el);
  const now=Date.now();
  let minT=Infinity, maxT=-Infinity;
  agents.forEach(a=>{
    const s=parseTimeStr(a.started_at); if(s&&s<minT) minT=s;
    const e=parseTimeStr(a.completed_at)||now; if(e>maxT) maxT=e;
  });
  maxT=Math.max(maxT,now);
  const rangeSec=Math.max((maxT-minT)/1000,60);

  /* auto-pick bin size if needed */
  const binOptions=_heatBinOptions(rangeSec);
  if(!binOptions.includes(_heatBinSec)) _heatBinSec=binOptions[0];
  const binMs=_heatBinSec*1000;
  const totalBins=Math.min(Math.ceil((maxT-minT)/binMs),80);
  const effectiveBinMs=Math.ceil((maxT-minT)/totalBins);

  /* per-agent per-bin values */
  const rows=agents.map(a=>{
    const sT=parseTimeStr(a.started_at)||minT;
    const eT=parseTimeStr(a.completed_at)||(a.status==='running'||a.status==='waiting'?now:sT+5000);
    const bins=[];
    for(let b=0;b<totalBins;b++){
      const bStart=minT+b*effectiveBinMs;
      const bEnd=bStart+effectiveBinMs;
      const active=sT<bEnd&&eT>bStart;
      if(!active){ bins.push({v:0,active:false,tasks:[]}); continue; }
      if(_heatMode==='tasks'){
        const tasksDone=(a.tasks||[]).filter(t=>{
          if(!t.done||!t.completed_at) return false;
          const tt=parseTimeStr(t.completed_at);
          return tt&&tt>=bStart&&tt<bEnd;
        });
        bins.push({v:tasksDone.length,active:true,tasks:tasksDone.map(t=>t.label||'task')});
      } else {
        /* tokens: approximate proportional to time overlap */
        const overlap=Math.min(eT,bEnd)-Math.max(sT,bStart);
        const dur=Math.max(eT-sT,1);
        const tokEst=Math.round((a.tokens_used||0)*(overlap/dur));
        bins.push({v:tokEst,active:true,tasks:[]});
      }
    }
    return {a,bins,sT,eT};
  });

  /* compute max value across all bins for normalization */
  const allVals=rows.flatMap(r=>r.bins.map(b=>b.v));
  const maxVal=Math.max(...allVals,1);

  /* cell color function */
  function cellColor(b){
    if(!b.active) return 'transparent';
    if(b.v===0) return 'rgba(255,255,255,.04)';
    const t=b.v/maxVal;
    if(_heatMode==='tasks'){
      const a=0.15+t*0.85;
      return `rgba(var(--c-rgb),${a.toFixed(2)})`;
    } else {
      const a=0.12+t*0.85;
      return `rgba(76,194,133,${a.toFixed(2)})`;
    }
  }

  /* axis labels: show every Nth bin */
  const labelEvery=Math.max(1,Math.ceil(totalBins/10));
  const axisLabels=Array.from({length:totalBins},(_,b)=>{
    if(b%labelEvery!==0) return '';
    const t=new Date(minT+b*effectiveBinMs);
    return `${String(t.getHours()).padStart(2,'0')}:${String(t.getMinutes()).padStart(2,'0')}`;
  });

  /* bin size selector */
  const binOptHtml=binOptions.map(s=>`<button class="heat-mode-btn${_heatBinSec===s?' active':''}" onclick="_heatSetBin(${s})">${s<60?s+'s':Math.round(s/60)+'m'}</button>`).join('');
  const modeHtml=`
    <button class="heat-mode-btn${_heatMode==='tasks'?' active':''}" onclick="_heatSetMode('tasks')">TASKS</button>
    <button class="heat-mode-btn${_heatMode==='tokens'?' active':''}" onclick="_heatSetMode('tokens')">TOKENS</button>
    <div style="flex:1"></div>
    <span style="color:var(--t3);margin-right:4px">BIN</span>${binOptHtml}
    <span style="color:var(--t3);margin-left:8px">${totalBins} bins · ${_heatBinSec<60?_heatBinSec+'s':Math.round(_heatBinSec/60)+'m'} each</span>`;

  /* legend scale */
  const scaleSteps=6;
  const scaleHtml=Array.from({length:scaleSteps},(_,i)=>{
    const t=i/(scaleSteps-1);
    const col=_heatMode==='tasks'?`rgba(var(--c-rgb),${(0.15+t*0.85).toFixed(2)})`:`rgba(76,194,133,${(0.12+t*0.85).toFixed(2)})`;
    return `<div class="heat-legend-cell" style="background:${col}"></div>`;
  }).join('');

  /* tooltip helper via title attribute — simple approach */
  const gridHtml=rows.map(row=>{
    const col=sCol(row.a.status);
    const cellsHtml=row.bins.map((b,bi)=>{
      const bg=cellColor(b);
      const bStart=new Date(minT+bi*effectiveBinMs);
      const bEnd=new Date(minT+(bi+1)*effectiveBinMs);
      const timeStr=`${String(bStart.getHours()).padStart(2,'0')}:${String(bStart.getMinutes()).padStart(2,'0')}:${String(bStart.getSeconds()).padStart(2,'0')}–${String(bEnd.getHours()).padStart(2,'0')}:${String(bEnd.getMinutes()).padStart(2,'0')}:${String(bEnd.getSeconds()).padStart(2,'0')}`;
      const tipContent=b.active?(b.tasks.length?`${timeStr}\\n${b.tasks.map(t=>'✓ '+t).join('\\n')}`:b.v>0?`${timeStr}\\n~${b.v.toLocaleString()} tokens`:`${timeStr}\\nrunning (no tasks)`):timeStr+' (inactive)';
      const statusBorder=row.a.status==='error'&&parseTimeStr(row.a.completed_at)>=minT+bi*effectiveBinMs&&parseTimeStr(row.a.completed_at)<minT+(bi+1)*effectiveBinMs?`outline:1px solid var(--r);`:'';
      const doneBorder=row.a.status==='done'&&parseTimeStr(row.a.completed_at)>=minT+bi*effectiveBinMs&&parseTimeStr(row.a.completed_at)<minT+(bi+1)*effectiveBinMs?`outline:1px solid var(--g);`:'';
      return `<div class="heat-cell" style="background:${bg};${statusBorder||doneBorder}" title="${escHtml(tipContent)}"></div>`;
    }).join('');
    return `<div class="heat-row">
      <div class="heat-label" style="color:${col}" onclick="openAgentDetail(${jsq(row.a.id)},null)" title="${escHtml(row.a.name)}">${escHtml(row.a.name.length>18?row.a.name.slice(0,17)+'…':row.a.name)}</div>
      <div class="heat-cells">${cellsHtml}</div>
    </div>`;
  }).join('');

  const axisHtml=`<div class="heat-axis">${axisLabels.map(l=>`<div class="heat-axis-lbl">${l}</div>`).join('')}</div>`;

  el.innerHTML=`<div style="max-width:960px;width:100%">
  <div class="heat-toolbar">${modeHtml}</div>
  ${axisHtml}
  <div class="heat-grid">${gridHtml}</div>
  <div class="heat-legend">
    <span>LOW</span>
    <div class="heat-legend-scale">${scaleHtml}</div>
    <span>HIGH</span>
    <span style="margin-left:16px;color:var(--t3)">${_heatMode==='tasks'?'color = tasks completed per bin':'color = estimated tokens per bin'}</span>
  </div>
  </div>`;
}

function _heatSetMode(m){ _heatMode=m; if(lastStatus) renderHeatmap(lastStatus); }
function _heatSetBin(s){ _heatBinSec=s; if(lastStatus) renderHeatmap(lastStatus); }

/* ── TREE VIEW ── */
function _buildTree(agents){
  const childMap = {};
  const agentById = {};
  agents.forEach(a => { agentById[a.id]=a; childMap[a.id]=[]; });
  agents.forEach(a => {
    if(a.parent_id && agentById[a.parent_id]) childMap[a.parent_id].push(a.id);
  });
  const roots = agents.filter(a => !a.parent_id || !agentById[a.parent_id]);
  const depth = {};
  const queue = roots.map(r => ({id:r.id, d:0}));
  while(queue.length){
    const {id,d} = queue.shift();
    depth[id] = d;
    (childMap[id]||[]).forEach(cid => queue.push({id:cid, d:d+1}));
  }
  return {childMap, agentById, roots, depth};
}

function _layoutTree(agents, childMap, roots, depth){
  const NODE_H = 80;
  const LEVEL_W = 200;
  const NODE_R = 28;
  const pos = {};
  // One row counter for every leaf, whatever its depth. A per-depth counter
  // let a parent (centred on its children) land on the same row as a
  // shallower leaf placed earlier, drawing the two nodes on top of each other.
  let nextY = 0;
  function assignPos(id, d){
    const children = childMap[id]||[];
    if(!children.length){
      const y = nextY;
      nextY += NODE_H;
      pos[id] = {x: d*LEVEL_W + NODE_R + 20, y: y + NODE_R + 10};
      return;
    }
    children.forEach(cid => assignPos(cid, d+1));
    const childYs = children.map(cid => pos[cid].y);
    pos[id] = {x: d*LEVEL_W + NODE_R + 20, y: (Math.min(...childYs)+Math.max(...childYs))/2};
  }
  roots.forEach(r => assignPos(r.id, depth[r.id]||0));
  return pos;
}

function renderTree(data){
  const el = document.getElementById('tree-area');
  if(!el) return;
  const tc=_graphThemeColors();
  const agents = (data&&data.agents||[]).filter(a => !String(a.id||'').startsWith('hook_'));
  if(!agents.length){ _sessFallback('tree', el, data||{}); return; }
  _svPainted.delete(el);
  const {childMap, agentById, roots, depth} = _buildTree(agents);
  const pos = _layoutTree(agents, childMap, roots, depth);
  const allPos = Object.values(pos);
  if(!allPos.length){
    el.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100%;font-family:var(--font2);font-size:13px;color:${tc.emptyText};letter-spacing:.08em">NO AGENTS</div>`;
    return;
  }
  const W = Math.max(...allPos.map(p=>p.x)) + 80;
  const H = Math.max(...allPos.map(p=>p.y)) + 80;
  const edgeSvg = agents.flatMap(a =>
    (childMap[a.id]||[]).map(cid => {
      const p = pos[a.id], c = pos[cid];
      if(!p||!c) return '';
      const midX = (p.x+c.x)/2;
      const col = sCol(agentById[cid]&&agentById[cid].status||'done');
      return `<path d="M${p.x} ${p.y} C${midX} ${p.y} ${midX} ${c.y} ${c.x} ${c.y}" stroke="${col}" stroke-width="1.5" fill="none" opacity=".45" stroke-dasharray="5 3"/>`;
    })
  ).join('');
  const nodeSvg = agents.map(a => {
    const px = (pos[a.id]||{x:0,y:0}).x;
    const py = (pos[a.id]||{x:0,y:0}).y;
    const rgba = sColRgba(a.status);
    const lbl = _deriveUnit(a).slice(0,4);
    const name = a.name.length>16 ? a.name.slice(0,15)+'…' : a.name;
    const pct = a.tasks&&a.tasks.length ? Math.round(a.tasks.filter(t=>t.done).length/a.tasks.length*100) : 0;
    const r = 24;
    const isActive = _adpAgentId === a.id;
    const pts = Array.from({length:6},(_,k)=>{
      const ag = k*Math.PI/3-Math.PI/6;
      return `${(px+r*Math.cos(ag)).toFixed(0)},${(py+r*Math.sin(ag)).toFixed(0)}`;
    }).join(' ');
    const arcR = r+7;
    const arcC = (2*Math.PI*arcR).toFixed(1);
    const arcD = (pct/100*2*Math.PI*arcR).toFixed(1);
    const childCount = (childMap[a.id]||[]).length;
    return `<g onclick="openAgentDetail(${jsq(a.id)},null)" onmouseover="_treeNodeHover(event,${jsq(a.id)})" onmouseout="_graphNodeOut()" style="cursor:pointer${isActive?';filter:brightness(1.2)':''}">
  ${a.status==='running'?`<circle cx="${px}" cy="${py}" r="${arcR+5}" fill="none" stroke="${rgba}" stroke-width="1" opacity=".2" style="animation:pulse 2s infinite"/>`:``}
  <polygon points="${pts}" fill="${isActive?rgba.replace('.85)',',.15)').replace(',1)',',.15)'):tc.nodeFill}" stroke="${rgba}" stroke-width="${isActive?2.5:1.6}"/>
  <circle cx="${px}" cy="${py}" r="${arcR}" fill="none" stroke="${tc.nodeStroke}" stroke-width="2"/>
  <circle cx="${px}" cy="${py}" r="${arcR}" fill="none" stroke="${rgba}" stroke-width="2" opacity="${isActive?.9:.5}" stroke-dasharray="${arcD} ${arcC}" stroke-linecap="round" transform="rotate(-90 ${px} ${py})"/>
  <text x="${px}" y="${py-4}" text-anchor="middle" font-family="'Red Hat Mono',Consolas,monospace" font-size="10" font-weight="700" fill="${rgba}">${escHtml(lbl)}</text>
  <text x="${px}" y="${py+8}" text-anchor="middle" font-family="'Red Hat Mono',Consolas,monospace" font-size="8" fill="${tc.pctText}">${pct}%</text>
  <text x="${px}" y="${py+40}" text-anchor="middle" font-family="'Atkinson Hyperlegible',sans-serif" font-size="9" fill="${tc.nodeText}">${escHtml(name)}</text>
  ${childCount>0?`<text x="${px+r}" y="${py-r+4}" text-anchor="middle" font-family="'Red Hat Mono',Consolas,monospace" font-size="7" fill="${rgba}" opacity=".8">${childCount}↓</text>`:''}
</g>`;
  }).join('');
  const hasHierarchy = agents.some(a => a.parent_id && agentById[a.parent_id]);
  // Stale wording predates feature 102 (parent_id is now derived
  // automatically from nested subagent transcript scans, not something
  // a dev "adds" -- see _extract_child_agent_ids in hooks/aoc_hook.py),
  // back when this really was an unfinished, opt-in protocol field. This
  // flat view is the correct, expected state whenever no subagent in the
  // session spawned another subagent itself, not a broken one.
  // Explicit width/height = drawn at 1:1. With only a viewBox the SVG
  // stretched to the panel width, so a small tree (W~400) blew up 2-3x into
  // giant hex nodes; a large one now scrolls instead of shrinking.
  const vw = Math.max(W,400), vh = Math.max(H,200)+30;
  // Centred on the drawn width, not W: a flat tree's W is ~130, which put
  // the hint's middle near the left edge and clipped its first half.
  const hint = !hasHierarchy ? `<text x="${(vw/2).toFixed(0)}" y="${H+20}" text-anchor="middle" font-family="'Red Hat Mono',Consolas,monospace" font-size="8" fill="${tc.legendHint}" letter-spacing=".08em">no nested subagent delegation in this session</text>` : '';
  el.innerHTML = `<div style="overflow:auto;width:100%"><svg width="${vw}" height="${vh}" viewBox="0 0 ${vw} ${vh}" style="display:block;margin:0 auto;background:${tc.panelBg};border-radius:14px;border:1px solid ${tc.panelBorder}">${edgeSvg}${nodeSvg}${hint}</svg></div>`;
}

function _treeNodeHover(event, agentId){
  _graphNodeHover(event, agentId);
}

/* ── fullscreen ── */
function toggleFullscreen(){
  const btn=document.getElementById('btn-fs');
  if(!document.fullscreenElement){
    document.documentElement.requestFullscreen().catch(()=>{});
    if(btn) btn.textContent='⛶';
  } else {
    document.exitFullscreen().catch(()=>{});
  }
}
document.addEventListener('fullscreenchange',()=>{
  const btn=document.getElementById('btn-fs');
  if(btn) btn.textContent=document.fullscreenElement?'⊡':'⛶';
  if(btn) btn.title=document.fullscreenElement?'Exit fullscreen [Z]':'Fullscreen [Z]';
  if(btn) btn.style.color=document.fullscreenElement?'var(--c)':'';
});

/* ── session notes ── */
let _notesOpen=false;
let _notesSessionId=null;  // null = editing the global scratchpad note; else a per-session note
function openNotesPanel(sessionId){
  _notesOpen=true;
  _notesSessionId=sessionId||null;
  document.getElementById('notes-overlay').classList.add('open');
  setTimeout(()=>{ const ta=document.getElementById('notes-ta'); if(ta) ta.focus(); },220);
  const titleLbl=document.getElementById('notes-title-lbl');
  const subLbl=document.getElementById('notes-subtitle-lbl');
  const ta=document.getElementById('notes-ta');
  if(_notesSessionId){
    const sess=(lastStatus&&lastStatus.sessions_list||[]).find(s=>s.id===_notesSessionId);
    if(ta) ta.value=(sess&&sess.note)||'';
    if(titleLbl) titleLbl.textContent='SESSION NOTE';
    if(subLbl) subLbl.textContent=(sess&&(sess.display_name||sess.project))||'this CLI session';
  }else{
    if(lastStatus&&lastStatus.session_note!=null){ if(ta) ta.value=lastStatus.session_note||''; }
    if(titleLbl) titleLbl.textContent='SESSION NOTES';
    if(subLbl) subLbl.textContent='saved to session history';
  }
}
function closeNotesPanel(){
  _notesOpen=false;
  document.getElementById('notes-overlay').classList.remove('open');
}
async function saveNotes(){
  const ta=document.getElementById('notes-ta');
  const note=ta?ta.value:'';
  try{
    if(_notesSessionId){
      await fetch('/session_note',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:_notesSessionId,note})});
      if(lastStatus){
        const sess=(lastStatus.sessions_list||[]).find(s=>s.id===_notesSessionId);
        if(sess) sess.note=note;
      }
      renderAgents(lastStatus);  // refresh the card's note preview snippet
    }else{
      await fetch('/notes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({note})});
      if(lastStatus) lastStatus.session_note=note;
    }
    const msg=document.getElementById('notes-saved-msg');
    if(msg){ msg.style.display='block'; setTimeout(()=>msg.style.display='none',2000); }
    log('Note saved','info');
  }catch(e){ log('Failed to save note: '+e.message,'error'); }
}
function clearNotes(){
  const ta=document.getElementById('notes-ta');
  if(ta) ta.value='';
  saveNotes();
}

