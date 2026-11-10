/* ── timeline ── */
/* ── timeline zoom helpers ── */
function _tlZoomIn(){ if(_tlZoom<16){ _tlZoom=Math.min(16,_tlZoom*2); if(lastStatus)renderTimeline(lastStatus); } }
function _tlZoomOut(){ if(_tlZoom>1){ _tlZoom=Math.max(1,_tlZoom/2); if(lastStatus)renderTimeline(lastStatus); } }
function _tlZoomReset(){ _tlZoom=1; _tlZoomFocus=0.5; if(lastStatus)renderTimeline(lastStatus); }
function _tlClickAgent(id, frac){
  openAgentDetail(id, null);
  _tlZoomFocus = frac;
  if(_tlZoom===1) _tlZoom=2;
  if(lastStatus) renderTimeline(lastStatus);
}

/* ── Session views: what Timeline / Heat / Graph / Tree draw when no agent
   ran. They were agent-only, so on a day of plain sessions (no subagents)
   all four stayed empty. Items = the open sessions plus today's history,
   with transcript activity per minute and the files each one edited. ── */
/* Pure: [{id, name, start, end (ms), state: 'wait'|'live'|'done', cost,
   model, group, edited: [paths], minutes: [[epoch_s, lines]]}], oldest first. */
function _sessionItems(sr, hist, act, todayStr, nowMs){
  const live=((sr&&sr.sessions_list)||[]).filter(s=>s&&s.session_active!==false);
  const waitIds=new Set(_waitingSessions(live,0).map(s=>s.id));
  const by=new Map();
  const base=p=>String(p||'').split(/[\\/]/).filter(Boolean).pop()||'';
  (Array.isArray(hist)?hist:[]).forEach(h=>{
    if(!h||h.date!==todayStr||!h.started_at) return;
    const start=new Date(`${h.date}T${h.started_at}`).getTime();
    if(!isFinite(start)) return;
    by.set(h.id,{id:h.id, name:h.title||h.project||String(h.id).slice(0,8), start, end:start+(h.duration_s||0)*1000,
      state:'done', cost:h.cost||0, model:'', group:h.project||'', edited:[], minutes:[]});
  });
  live.forEach(s=>{
    const it=by.get(s.id)||{id:s.id, start:Date.parse(s.first_ts)||nowMs, cost:0, edited:[], minutes:[]};
    it.name=s.display_name||it.name||s.project||String(s.id).slice(0,8);
    it.end=nowMs; it.state=waitIds.has(s.id)?'wait':'live';
    it.cost=s.estimated_cost||it.cost||0; it.model=s.model||''; it.group=base(s.cwd)||it.group||s.project||'';
    if(!isFinite(it.start)) it.start=nowMs;
    by.set(s.id,it);
  });
  const a=act||{};
  by.forEach(it=>{ const x=a[it.id]; if(x){ it.minutes=x.minutes||[]; it.edited=x.edited||[];
    if(it.minutes.length) it.start=Math.min(it.start, it.minutes[0][0]*1000); } });
  return [...by.values()].sort((p,q)=>p.start-q.start);
}
const _svNote=n=>`<div class="sv-note"><b>No agents ran</b> — showing your ${n} session${n===1?'':'s'} from today instead. Click one to open it in History.</div>`;
const _svEmpty='<div class="rp-empty" style="padding:60px 14px">No sessions today yet.<small>Agents and sessions show up here as soon as they start.</small></div>';
const _svStateLabel={wait:'Waiting on you', live:'Live', done:'Finished'};
const _svClock=ms=>new Date(ms).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'});
/* Pure: the span shown -- first session start .. now, at least 1 h. */
function _svRange(items, nowMs){
  const min=Math.min(...items.map(i=>i.start), nowMs-3600e3);
  return [min, Math.max(nowMs, min+3600e3)];
}
function _sessTimelineHtml(items, nowMs){
  if(!items.length) return _svEmpty;
  const [t0,t1]=_svRange(items,nowMs), span=t1-t0, pct=t=>((t-t0)/span*100);
  const stepH=span>8*3600e3?2:1, ticks=[];
  const first=new Date(t0); first.setMinutes(0,0,0);
  for(let t=first.getTime()+3600e3; t<t1; t+=stepH*3600e3) ticks.push(t);
  const axis=`<div class="sv-axis">${ticks.map(t=>`<span style="left:${pct(t).toFixed(2)}%">${_svClock(t)}</span>`).join('')}<span class="now" style="left:100%">now</span></div>`;
  const rows=items.map(it=>{
    const bucket=Math.max(60e3, span/160), heat=new Map();
    it.minutes.forEach(([m,n])=>{ const k=Math.floor((m*1000-t0)/bucket); heat.set(k,(heat.get(k)||0)+n); });
    const hmax=Math.max(1,...heat.values());
    const marks=[...heat].filter(([k])=>k>=0).map(([k,n])=>`<i style="left:${((k*bucket)/span*100).toFixed(2)}%;width:${(bucket/span*100).toFixed(2)}%;opacity:${(0.25+0.75*n/hmax).toFixed(2)}"></i>`).join('');
    const dur=_fmtDurShort(Math.max(0,(it.end-it.start)/1000));
    const meta=[_svStateLabel[it.state], dur, it.cost?'$'+it.cost.toFixed(2):'', it.edited.length?`${it.edited.length} file${it.edited.length===1?'':'s'}`:''].filter(Boolean).join(' · ');
    return `<div class="sv-row ${it.state}" onclick="_jumpToSessionHistory(${jsq(it.id)})" title="${escHtml(it.name+' · '+_svClock(it.start)+'–'+(it.state==='done'?_svClock(it.end):'now'))}">
      <div class="sv-name"><b>${escHtml(it.name)}</b><small>${escHtml(meta)}</small></div>
      <div class="sv-track"><span class="sv-bar" style="left:${pct(it.start).toFixed(2)}%;width:${Math.max(0.6,pct(it.end)-pct(it.start)).toFixed(2)}%"></span>${marks}</div></div>`;
  }).join('');
  return `<div class="sv-wrap">${_svNote(items.length)}<div class="sv-tl">${axis}${rows}</div>
    <div class="sv-legend"><span><i class="live"></i>Live</span><span><i class="wait"></i>Waiting on you</span><span><i class="done"></i>Finished</span><span><i class="act"></i>Activity (brighter = busier)</span></div></div>`;
}
/* Pure: heat grid -- one row per session, one column per time bin, cell = transcript lines. */
function _sessHeatHtml(items, nowMs){
  if(!items.length) return _svEmpty;
  const [t0,t1]=_svRange(items,nowMs);
  const binMin=[5,10,15,30,60].find(b=>(t1-t0)/(b*60e3)<=48)||60, bin=binMin*60e3, n=Math.ceil((t1-t0)/bin);
  const grid=items.map(it=>{ const c=new Array(n).fill(0); it.minutes.forEach(([m,v])=>{ const k=Math.floor((m*1000-t0)/bin); if(k>=0&&k<n) c[k]+=v; }); return c; });
  const max=Math.max(1,...grid.flat());
  const every=Math.ceil(n/8);
  const head=`<div class="sv-hname"></div>${Array.from({length:n},(_,k)=>`<div class="sv-hcol">${k%every===0?_svClock(t0+k*bin):''}</div>`).join('')}`;
  const rows=items.map((it,r)=>`<div class="sv-hname ${it.state}" onclick="_jumpToSessionHistory(${jsq(it.id)})" title="${escHtml(it.name)}">${escHtml(it.name)}</div>`
    +grid[r].map((v,k)=>`<div class="sv-cell" style="--a:${v?(0.15+0.85*v/max).toFixed(2):0}" title="${escHtml(`${it.name} · ${_svClock(t0+k*bin)} · ${v} line${v===1?'':'s'}`)}"></div>`).join('')).join('');
  return `<div class="sv-wrap">${_svNote(items.length)}<div class="sv-heat" style="grid-template-columns:minmax(110px,170px) repeat(${n},minmax(8px,1fr))">${head}${rows}</div>
    <div class="sv-legend"><span>${binMin}-minute bins</span><span class="sv-scale">less<i></i>more</span><span>transcript lines (messages + tool calls)</span></div></div>`;
}
/* Pure: the session graph -- nodes = sessions, edges = files both edited
   (number on the edge) or the same folder (dashed). */
function _sessGraphHtml(items){
  if(!items.length) return _svEmpty;
  const norm=p=>String(p).replace(/\\/g,'/').toLowerCase();
  const W=680, n=items.length, H=n>4?500:400, cx=W/2, cy=H/2-10, R=n===1?0:Math.min(cx-110, cy-70, 170);
  const pos=items.map((_,i)=>{ const a=2*Math.PI*i/n-Math.PI/2; return [cx+R*Math.cos(a), cy+R*Math.sin(a)]; });
  const sets=items.map(it=>new Set(it.edited.map(norm)));
  const edges=[];
  for(let i=0;i<n;i++) for(let j=i+1;j<n;j++){
    let shared=0; sets[i].forEach(p=>{ if(sets[j].has(p)) shared++; });
    const same=items[i].group&&items[i].group===items[j].group;
    if(shared||same) edges.push({i,j,shared,same});
  }
  const col={wait:'var(--o)', live:'var(--c)', done:'var(--t3)'};
  const maxCost=Math.max(0.01,...items.map(i=>i.cost||0));
  const edgeSvg=edges.map(e=>{ const [x1,y1]=pos[e.i], [x2,y2]=pos[e.j];
    return `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" style="stroke:var(--c)" stroke-opacity="${e.shared?0.65:0.3}" stroke-width="${e.shared?Math.min(6,1.5+e.shared/2):1.2}"${e.shared?'':' stroke-dasharray="5 4"'}/>`
      +(e.shared?`<text x="${((x1+x2)/2).toFixed(1)}" y="${((y1+y2)/2-6).toFixed(1)}" text-anchor="middle" class="sv-elab">${e.shared} shared file${e.shared===1?'':'s'}</text>`:''); }).join('');
  const nodeSvg=items.map((it,i)=>{ const [x,y]=pos[i], r=16+14*Math.sqrt((it.cost||0)/maxCost);
    return `<g class="sv-node" onclick="_jumpToSessionHistory(${jsq(it.id)})"><title>${escHtml(`${it.name} · ${_svStateLabel[it.state]} · $${(it.cost||0).toFixed(2)} · ${it.edited.length} files`)}</title>
      <circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${(r+6).toFixed(1)}" style="fill:${col[it.state]}" fill-opacity=".12"/>
      <circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${r.toFixed(1)}" style="fill:${col[it.state]};stroke:${col[it.state]}" fill-opacity=".25" stroke-width="2"/>
      <text x="${x.toFixed(1)}" y="${(y+4).toFixed(1)}" text-anchor="middle" class="sv-nval">${it.edited.length}</text>
      <text x="${x.toFixed(1)}" y="${(y+r+18).toFixed(1)}" text-anchor="middle" class="sv-nlab">${escHtml(it.name.length>24?it.name.slice(0,23)+'…':it.name)}</text>
      <text x="${x.toFixed(1)}" y="${(y+r+32).toFixed(1)}" text-anchor="middle" class="sv-nsub">${escHtml(`${_svStateLabel[it.state]} · $${(it.cost||0).toFixed(2)}`)}</text></g>`; }).join('');
  return `<div class="sv-wrap">${_svNote(n)}<svg class="sv-graph" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">${edgeSvg}${nodeSvg}</svg>
    <div class="sv-legend"><span><i class="live"></i>Live</span><span><i class="wait"></i>Waiting</span><span><i class="done"></i>Finished</span><span>number = files edited · size = cost</span><span>line = files both edited · dashed = same folder</span></div></div>`;
}
/* Pure: folder -> sessions, with what each one did. */
function _sessTreeHtml(items){
  if(!items.length) return _svEmpty;
  const groups=new Map();
  items.forEach(it=>{ const g=it.group||'Other'; if(!groups.has(g)) groups.set(g,[]); groups.get(g).push(it); });
  const body=[...groups].map(([g,list])=>{
    const cost=list.reduce((s,i)=>s+(i.cost||0),0), files=list.reduce((s,i)=>s+i.edited.length,0);
    return `<div class="sv-tg"><div class="sv-tgh"><b>${escHtml(g)}</b><small>${list.length} session${list.length===1?'':'s'} · ${files} file${files===1?'':'s'} · $${cost.toFixed(2)}</small></div>
      ${list.map(it=>`<div class="sv-tn ${it.state}" onclick="_jumpToSessionHistory(${jsq(it.id)})"><i></i><div><b>${escHtml(it.name)}</b>
        <small>${escHtml([_svStateLabel[it.state], _svClock(it.start)+'–'+(it.state==='done'?_svClock(it.end):'now'), it.model?_ccModelName(it.model):'', it.edited.length?`${it.edited.length} files`:'', '$'+(it.cost||0).toFixed(2)].filter(Boolean).join(' · '))}</small>
        <small class="sv-tsub">No subagents</small></div></div>`).join('')}</div>`;
  }).join('');
  return `<div class="sv-wrap">${_svNote(items.length)}<div class="sv-tree"><div class="sv-troot">Today</div>${body}</div></div>`;
}
const _svPainted=new WeakMap();
/* No agents: draw the session version of `view` into `el`. Fetches /history
   and /session_activity (cached) and redraws once when fresh data lands. */
function _sessFallback(view, el, sr){
  const before=_ccData.histAt+_ccData.actAt;
  const pending=Promise.all([_ccFetch('hist','/history',60000), _ccFetch('act','/session_activity?hours=24',30000)]);
  if(_ccData.histAt+_ccData.actAt!==before)
    pending.then(()=>{ if(currentView===view&&lastStatus) ({timeline:renderTimeline, heat:renderHeatmap, graph:renderGraph, tree:renderTree})[view](lastStatus); });
  const now=Math.floor(Date.now()/60e3)*60e3; // to the minute, so a status push alone doesn't redraw
  const items=_sessionItems(sr, _ccData.hist, _ccData.act, _localDateStr(), now);
  const html=({timeline:_sessTimelineHtml, heat:_sessHeatHtml, graph:_sessGraphHtml, tree:_sessTreeHtml})[view](items, now);
  if(_svPainted.get(el)!==html){ el.innerHTML=html; _svPainted.set(el,html); }
}
function renderTimeline(data){
  const tl=document.getElementById('timeline-area');
  const agents=(data.agents||[]).filter(a=>!String(a.id||'').startsWith('hook_')&&a.started_at);
  if(!agents.length){ _sessFallback('timeline', tl, data); return; }
  _svPainted.delete(tl);
  const now=Date.now();
  let minT=Infinity, maxT=-Infinity;
  agents.forEach(a=>{
    const s=parseTimeStr(a.started_at); if(s&&s<minT) minT=s;
    const e=parseTimeStr(a.completed_at)||now; if(e>maxT) maxT=e;
  });
  if(!isFinite(minT)) minT=now-60000;
  maxT=Math.max(maxT,now); /* extend to now so running agents reach edge */
  const originalRange=Math.max(maxT-minT,10000);
  const fmtMs=ms=>{ const s=Math.round(ms/1000); return s<60?s+'s':Math.floor(s/60)+'m'+(s%60?(' '+(s%60)+'s'):''); };
  const hasRunning=agents.some(a=>a.status==='running'||a.status==='waiting');

  /* ── zoom window ── */
  const zoomedRange = originalRange / _tlZoom;
  const focusMs = minT + _tlZoomFocus * originalRange;
  let dispMin = focusMs - zoomedRange * _tlZoomFocus;
  let dispMax = dispMin + zoomedRange;
  /* clamp to original bounds */
  if(dispMin < minT){ dispMin=minT; dispMax=dispMin+zoomedRange; }
  if(dispMax > maxT){ dispMax=maxT; dispMin=dispMax-zoomedRange; }
  dispMin=Math.max(dispMin,minT);
  dispMax=Math.min(dispMax,maxT);
  const dispRange=Math.max(dispMax-dispMin,1000);

  const nowPct=((now-dispMin)/dispRange*100).toFixed(2);

  /* compute tick interval: aim for ~4-6 ticks */
  const rangeSec=dispRange/1000;
  let tickSec=10;
  if(rangeSec>600) tickSec=120;
  else if(rangeSec>300) tickSec=60;
  else if(rangeSec>120) tickSec=30;
  else if(rangeSec>60) tickSec=15;
  const tickMs=tickSec*1000;
  const firstTick=Math.ceil(dispMin/tickMs)*tickMs;
  const ticks=[];
  for(let t=firstTick;t<=dispMax;t+=tickMs) ticks.push(t);

  const tickSvg=ticks.map(t=>{
    const left=((t-dispMin)/dispRange*100).toFixed(2);
    const label=fmtMs(t-minT);
    return `<div style="position:absolute;left:${left}%;top:0;height:100%;pointer-events:none">
      <div style="position:absolute;top:0;left:0;width:1px;height:100%;background:rgba(255,255,255,.05)"></div>
      <div style="position:absolute;top:0;left:2px;font-size:8px;font-family:var(--font2);color:var(--t3)">${label}</div>
    </div>`;
  }).join('');

  /* ── zoom toolbar ── */
  const zoomLbl=Number.isInteger(_tlZoom)?`${_tlZoom}×`:`${_tlZoom}×`;
  const zoomInfo=_tlZoom>1?`<span style="color:var(--t3);font-size:9px">showing ${fmtMs(dispRange)} of ${fmtMs(originalRange)}</span>`:'';
  const toolbar=`<div class="tl-toolbar">
    <button class="density-btn" onclick="_tlZoomOut()" title="Zoom out (-)">−</button>
    <span class="tl-zoom-lbl">${zoomLbl}</span>
    <button class="density-btn" onclick="_tlZoomIn()" title="Zoom in (+)">+</button>
    <button class="density-btn" onclick="_tlZoomReset()" title="Reset zoom (0)" style="margin-left:2px">FIT</button>
    ${zoomInfo}
    <span style="flex:1"></span>
    <span style="color:var(--t3)">START <span style="color:var(--t2)">${new Date(minT).toLocaleTimeString()}</span></span>
    <span style="color:var(--t3)">RANGE <span style="color:var(--c)">${fmtMs(originalRange)}</span></span>
    ${hasRunning?`<span style="display:flex;align-items:center;gap:4px;color:var(--c)"><span style="width:6px;height:6px;border-radius:50%;background:var(--c);animation:pulse 1.5s infinite;display:inline-block"></span>LIVE</span>`:''}
  </div>`;

  tl.innerHTML= toolbar +
    `<div style="position:relative;height:20px;margin-bottom:4px;overflow:visible">
      ${tickSvg}
      ${hasRunning&&now>=dispMin&&now<=dispMax?`<div style="position:absolute;left:${nowPct}%;top:0;height:calc(100% + ${agents.length*32}px);width:1px;background:rgba(var(--c-rgb),.3);pointer-events:none;z-index:1"><div style="position:absolute;top:-14px;left:3px;font-size:8px;font-family:var(--font2);color:var(--c)">NOW</div></div>`:''}
    </div>` +
    agents.map(a=>{
      const sT=parseTimeStr(a.started_at)||minT;
      const eT=parseTimeStr(a.completed_at)||(a.status==='running'||a.status==='waiting'?now:sT+5000);
      /* clamp bar to display window */
      const clampedS=Math.max(sT,dispMin);
      const clampedE=Math.min(eT,dispMax);
      if(clampedS>=clampedE && sT>=dispMax) return ''; /* fully outside right */
      if(clampedS>=clampedE && eT<=dispMin) return ''; /* fully outside left */
      const left=Math.max(0,((clampedS-dispMin)/dispRange*100)).toFixed(2);
      const width=Math.max(((Math.max(clampedE,clampedS+1)-clampedS)/dispRange*100),.3).toFixed(2);
      /* original bar metrics for labels */
      const origLeft=((sT-dispMin)/dispRange*100).toFixed(2);
      const origWidth=Math.max(((eT-sT)/dispRange*100),.3).toFixed(2);
      const col=sCol(a.status);
      const dur=fmtMs(eT-sT);
      const tokStr=a.tokens_used?` · $${_agentCost(a).toFixed(4)}`:'';
      /* center fraction of this agent's bar in the original range (for click-to-focus) */
      const barMid=sT+(eT-sT)/2;
      const frac=Math.max(0,Math.min(1,(barMid-minT)/originalRange));
      const clickAgent=`_tlClickAgent(${jsq(a.id)},${frac.toFixed(4)})`;
      const inlineTicksInTrack=ticks.map(t=>`<div style="position:absolute;left:${((t-dispMin)/dispRange*100).toFixed(2)}%;top:0;bottom:0;width:1px;background:rgba(255,255,255,.04);pointer-events:none"></div>`).join('');
      return `<div class="tl-row">
        <div class="tl-name clickable" style="color:${col}" title="${escHtml(a.description||a.name)}" onclick="${clickAgent}">${escHtml(a.name.slice(0,22))}</div>
        <div class="tl-track" style="position:relative">
          <div class="tl-bg"></div>
          ${inlineTicksInTrack}
          <div class="tl-bar" style="left:${left}%;width:${width}%;background:${col};opacity:.8;box-shadow:0 0 6px ${col}55;border-radius:3px;cursor:pointer" title="${escHtml(a.name)} — ${dur}${tokStr}" onclick="${clickAgent}"></div>
          ${a.status==='running'?`<div class="tl-bar" style="left:${left}%;width:${width}%;background:linear-gradient(90deg,transparent 60%,${col}44);animation:sweep 2s linear infinite;border-radius:3px;pointer-events:none"></div>`:''}
          ${a.status==='done'&&parseFloat(origLeft)+parseFloat(origWidth)>=0&&parseFloat(origLeft)+parseFloat(origWidth)<=100?`<div style="position:absolute;left:calc(${origLeft}% + ${origWidth}%);top:3px;font-size:8px;font-family:var(--font2);color:${col};white-space:nowrap;opacity:.7">${dur}</div>`:''}
        </div>
        <div class="tl-elapsed" style="color:${col}">${a.status==='running'?fmtMs(now-sT):dur}</div>
      </div>`;
    }).join('');
}

