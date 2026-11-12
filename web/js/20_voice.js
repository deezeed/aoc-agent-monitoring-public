/* ── VOICE: the voice assistant (server/18_voice.py) ──
   The 🎙 button in the top bar opens a panel: tap the mic and speak (the
   browser's speech recognition, Web Speech API), or type. AOC answers out
   loud (speechSynthesis) and does what you asked -- approving, pausing an
   entity, opening a page... Things that start paid work, merge or touch the
   PC ask for a yes first (say "áno" / "yes", or tap). Off until you turn it
   on. Globals are `var`: boot runs before this part (see web/js/19). */
var _voState={settings:null, open:false, listening:false, busy:false, history:[], log:[], pending:null, interim:'', rec:null, err:''};
var _VO_LANGS=[['sk-SK','Slovenčina'],['cs-CZ','Čeština'],['en-US','English']];

function _voSupported(){ return !!(window.SpeechRecognition||window.webkitSpeechRecognition); }

/* Pure: a spoken / typed answer to "shall I?" -> true (yes), false (no), null (something else). */
function _voYesNo(text){
  const t=String(text||'').toLowerCase().replace(/[.!?,]/g,' ').trim();
  if(/^(áno|ano|hej|jasne|potvrď|potvrd|potvrdzujem|urob to|sprav to|yes|yeah|yep|sure|ok|okay|do it|confirm)(\s|$)/.test(t)) return true;
  if(/^(nie|ne|zruš|zrus|nerob|stop|no|nope|cancel|don't|dont)(\s|$)/.test(t)) return false;
  return null;
}

/* Pure: the panel. */
function _voPanelHtml(st, supported){
  const s=st.settings||{};
  const head=`<div class="vo-h"><b>🎙 Voice assistant</b><span style="flex:1"></span><button class="adp-btn" onclick="voiceClose()" title="Close">✕</button></div>`;
  if(!s.enabled) return head+`<div class="vo-off"><p>Ask AOC out loud what's going on, approve plans, run or pause entities, open a page, set a timer, take a note.
      Things that start paid work, merge or touch the PC ask for your yes first, and only work at the PC itself.</p>
      <p class="or-note">It uses your browser's speech recognition${supported?'':' (not available in this browser -- you can type instead)'} and the same Claude account as Orchestrate ("Runs on").</p>
      <button class="adp-btn ln-go" onclick="_voSet({enabled:true})">Turn the voice assistant on</button></div>`;
  const log=st.log.slice(-10).map(m=>`<div class="vo-m vo-${m.who}"><span>${m.who==='you'?'You':'AOC'}</span><div>${escHtml(m.text)}${(m.done||[]).length?`<div class="vo-done">${m.done.map(d=>escHtml(d)).join(' · ')}</div>`:''}</div></div>`).join('');
  const pend=st.pending?`<div class="vo-confirm"><span>${escHtml(st.pending.what.join('; '))}</span>
      <span class="vo-yn"><button class="adp-btn ln-go" onclick="_voConfirm(true)">Yes</button><button class="adp-btn" onclick="_voConfirm(false)">No</button></span></div>`:'';
  const mic=supported?`<button class="vo-mic${st.listening?' live':''}" onclick="${st.listening?'_voStop()':'_voListen()'}" ${st.busy?'disabled':''} title="${st.listening?'Stop':'Speak'}">${st.listening?'■':'🎙'}</button>`:'';
  const opt=(cur,list)=>list.map(([v,l])=>`<option value="${v}"${cur===v?' selected':''}>${l}</option>`).join('');
  return head+`<div class="vo-log" id="vo-log">${log||'<div class="or-note">Try: “Čo práve robia agenti?” · “Schváľ plán.” · “Spusti entitu AI News.” · “Nastav minútku na 10 minút.”</div>'}</div>
    ${st.interim?`<div class="vo-interim">${escHtml(st.interim)}…</div>`:''}${st.busy?'<div class="or-note">Thinking…</div>':''}${st.err?`<div class="or-warn">${escHtml(st.err)}</div>`:''}
    ${pend}
    <form class="vo-in" onsubmit="event.preventDefault();_voAsk(this.q.value);this.q.value=''">${mic}
      <input name="q" class="settings-input" placeholder="${supported?'or type…':'Type your question…'}" autocomplete="off" ${st.busy?'disabled':''}></form>
    <div class="vo-set"><select class="settings-input" onchange="_voSet({lang:this.value})">${opt(s.lang,_VO_LANGS)}</select>
      <label class="ln-chk"><input type="checkbox"${s.speak?' checked':''} onchange="_voSet({speak:this.checked})"> speak answers</label>
      <select class="settings-input" onchange="_voSet({model:this.value})" title="Haiku answers faster; Sonnet understands more">${opt(s.model,[['haiku','Haiku'],['sonnet','Sonnet']])}</select>
      <span style="flex:1"></span><button class="adp-btn" onclick="_voSet({enabled:false})">Turn off</button></div>`;
}

function _voRender(){
  let el=document.getElementById('voice-panel');
  if(!_voState.open){ if(el) el.style.display='none'; return; }
  if(!el){ el=document.createElement('div'); el.id='voice-panel'; el.className='vo-panel'; document.body.appendChild(el); }
  el.style.display='flex';
  const a=document.activeElement, typing=a&&el.contains(a)&&a.tagName==='INPUT';
  const val=typing?a.value:'';
  el.innerHTML=_voPanelHtml(_voState, _voSupported());
  if(typing){ const i=el.querySelector('.vo-in input'); if(i){ i.value=val; i.focus(); } }
  const lg=document.getElementById('vo-log'); if(lg) lg.scrollTop=lg.scrollHeight;
  const b=document.getElementById('btn-voice');
  if(b){ b.classList.toggle('muted', !(_voState.settings&&_voState.settings.enabled)); b.classList.toggle('vo-live', _voState.listening); }
}
async function _voLoad(){
  try{ _voState.settings=await fetch('/voice/settings').then(r=>r.json()); }catch(e){}
  _voRender();
}
function voiceToggle(){ _voState.open=!_voState.open; if(_voState.open&&!_voState.settings) _voLoad(); _voRender(); }
function voiceClose(){ _voStop(); _voState.open=false; _voRender(); }
async function _voSet(patch){
  try{ const r=await fetch('/voice/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(patch)}).then(r=>r.json());
    if(r.ok) _voState.settings=r; }catch(e){ _voState.err='Could not save: '+e.message; }
  _voRender();
}
function _voSpeak(text){
  const s=_voState.settings||{};
  if(!s.speak||!text||!window.speechSynthesis) return;
  try{
    speechSynthesis.cancel();
    const u=new SpeechSynthesisUtterance(text);
    u.lang=s.lang||'sk-SK';
    const v=speechSynthesis.getVoices().find(v=>v.lang===u.lang)||speechSynthesis.getVoices().find(v=>v.lang&&v.lang.slice(0,2)===u.lang.slice(0,2));
    if(v) u.voice=v;
    speechSynthesis.speak(u);
  }catch(e){}
}
function _voListen(){
  const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
  if(!SR) return;
  try{ if(window.speechSynthesis) speechSynthesis.cancel(); }catch(e){}
  const rec=new SR();
  rec.lang=(_voState.settings||{}).lang||'sk-SK';
  rec.interimResults=true; rec.continuous=false; rec.maxAlternatives=1;
  rec.onresult=e=>{
    let fin='', mid='';
    for(let i=e.resultIndex;i<e.results.length;i++){ const t=e.results[i][0].transcript; if(e.results[i].isFinal) fin+=t; else mid+=t; }
    _voState.interim=mid;
    if(fin){ _voState.interim=''; _voState.listening=false; _voAsk(fin); }
    else _voRender();
  };
  rec.onerror=e=>{ _voState.err=e.error==='not-allowed'?'The browser blocked the microphone -- allow it for this page.':e.error==='no-speech'?'I didn\'t hear anything.':'Speech recognition: '+e.error; };
  rec.onend=()=>{ _voState.listening=false; _voState.interim=''; _voRender(); };
  _voState.rec=rec; _voState.listening=true; _voState.err='';
  try{ rec.start(); }catch(e){ _voState.listening=false; _voState.err=String(e.message||e); }
  _voRender();
}
function _voStop(){ try{ if(_voState.rec) _voState.rec.stop(); }catch(e){} _voState.listening=false; _voRender(); }
async function _voAsk(text){
  text=String(text||'').trim();
  if(!text||_voState.busy) return;
  /* a yes / no to the question that waits answers it instead of asking anew */
  if(_voState.pending){ const yn=_voYesNo(text); if(yn!==null){ _voState.log.push({who:'you', text}); return _voConfirm(yn); } }
  _voState.log.push({who:'you', text}); _voState.busy=true; _voState.err=''; _voRender();
  let r;
  try{ r=await fetch('/voice/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text, history:_voState.history.slice(-6)})}).then(r=>r.json()); }
  catch(e){ r={ok:false, error:e.message}; }
  _voState.busy=false;
  if(!r.ok){ _voState.err=r.error||'failed'; _voRender(); return; }
  _voState.log.push({who:'aoc', text:r.say, done:r.done});
  _voState.history.push({role:'user', text}, {role:'assistant', text:r.say});
  _voState.pending=r.confirm||null;
  _voRender(); _voSpeak(r.say); _voAfter();
}
async function _voConfirm(yes){
  const p=_voState.pending; if(!p) return;
  _voState.pending=null; _voState.busy=true; _voRender();
  let r;
  try{ r=await fetch('/voice/confirm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:p.token, yes:!!yes})}).then(r=>r.json()); }
  catch(e){ r={ok:false, error:e.message}; }
  _voState.busy=false;
  const say=r.ok?(yes?'Hotovo.':'Dobre, nerobím to.'):(r.error||'failed');
  _voState.log.push({who:'aoc', text:say, done:r.done||[]});
  _voRender(); _voSpeak(say); _voAfter();
}
/* what changed shows at once in the view you're on */
function _voAfter(){ try{ if(currentView==='orch'&&typeof _orRefresh==='function') _orRefresh(); }catch(e){} }

_voLoad();
