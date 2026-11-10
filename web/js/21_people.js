/* ── PEOPLE: several people on one AOC (server/18_users.py) ──
   Settings → PEOPLE (owner): invite someone (a one-time link through the
   tunnel, member or viewer), change roles, disable or remove people. The top
   bar shows who's here; a viewer sees a read-only strip. Globals are `var`
   (boot runs before this part, see web/js/19). */
var _peState={me:null, data:null, invite:null, err:''};
var _PE_ROLES={owner:'Owner', member:'Member', viewer:'Viewer'};

function _peInitials(name){ return String(name||'?').split(/\s+/).filter(Boolean).slice(0,2).map(w=>w[0].toUpperCase()).join('')||'?'; }
function _peAgo(s){ return s==null?'never':s<60?'now':s<3600?Math.round(s/60)+' min ago':s<86400?Math.round(s/3600)+' h ago':Math.round(s/86400)+' d ago'; }

/* Pure: the top bar's "who's here" (everyone but me, newest first). */
function _pePresenceHtml(present, me){
  const others=(present||[]).filter(p=>!me||p.id!==me.id);
  if(!others.length) return '';
  return others.slice(0,5).map(p=>`<span class="pe-av pe-${escHtml(p.role)}" title="${escHtml(p.name)} (${escHtml(_PE_ROLES[p.role]||p.role)})${p.view?' · '+escHtml(p.view):''}">${escHtml(_peInitials(p.name))}</span>`).join('')
    +(others.length>5?`<span class="pe-av">+${others.length-5}</span>`:'');
}

/* Pure: Settings → PEOPLE. */
function _pePaneHtml(d, invite, err){
  if(!d) return '<div class="or-note">Loading…</div>';
  const now=Date.now()/1000;
  const people=(d.users||[]).map(u=>`<div class="pe-row${u.disabled?' off':''}">
      <span class="pe-av pe-${escHtml(u.role)}">${escHtml(_peInitials(u.name))}</span>
      <div class="pe-who"><b>${escHtml(u.name)}</b><span class="or-note">${u.disabled?'disabled · ':''}last here ${_peAgo(u.last_seen?now-u.last_seen:null)}${u.invited_by?' · invited by '+escHtml(u.invited_by):''}</span></div>
      <select class="settings-input" onchange="_peUpdate(${jsq(u.id)},{role:this.value})">${['member','viewer'].map(r=>`<option value="${r}"${u.role===r?' selected':''}>${_PE_ROLES[r]}</option>`).join('')}</select>
      <button class="adp-btn" onclick="_peUpdate(${jsq(u.id)},{disabled:${u.disabled?'false':'true'}})">${u.disabled?'Enable':'Disable'}</button>
      <button class="adp-btn" onclick="_peRemove(${jsq(u.id)},${jsq(u.name)})">Remove</button></div>`).join('');
  const link=invite?`<div class="pe-link"><span>Send this link (one use, 7 days, ${escHtml(_PE_ROLES[invite.role]||invite.role)}):</span>
      <input class="settings-input" readonly value="${escHtml(invite.link||(location.origin+invite.path))}" onclick="this.select()">
      <button class="adp-btn" onclick="copyText(this,this.previousElementSibling.value)">Copy</button>
      ${invite.link?'':'<span class="or-warn">No tunnel running: this link only works on your network. Start the tunnel (TUNNEL tab) to invite people from anywhere.</span>'}</div>`:'';
  return `<div class="settings-section"><div class="settings-section-title">PEOPLE ON THIS AOC</div>
    <div class="or-note">They join through your tunnel with their own link and token. Members can do everything except people and AOC's own settings
      (and nothing on this PC itself); viewers only look. What they approve, start or merge is logged with their name.</div>
    <div class="ln-row"><label class="ln-lbl" for="pe-owner">Your name</label><input id="pe-owner" class="settings-input" value="${escHtml(d.owner_name||'')}" onchange="_peUpdate('',{owner_name:this.value})"></div>
    ${people||'<div class="or-note">Nobody else yet.</div>'}
    <form class="ln-row pe-inv" onsubmit="event.preventDefault();_peInvite(this)"><label class="ln-lbl">Invite</label>
      <input name="name" class="settings-input" placeholder="their name (optional)"><select name="role" class="settings-input"><option value="member">Member</option><option value="viewer">Viewer</option></select>
      <button class="adp-btn ln-go">Create invite link</button></form>
    ${link}${err?`<div class="or-warn">${escHtml(err)}</div>`:''}
    ${(d.invites||[]).length?`<div class="ln-row"><span class="or-note">${d.invites.length} open invite(s)</span><button class="adp-btn" onclick="_peCancelInvites()">Cancel them</button></div>`:''}
    ${(d.present||[]).length?`<div class="or-note">Here now: ${d.present.map(p=>escHtml(p.name)).join(', ')}</div>`:''}</div>`;
}

function _peRender(){ const el=document.getElementById('people-area'); if(el) el.innerHTML=_pePaneHtml(_peState.data, _peState.invite, _peState.err); }
async function _peLoad(){
  try{ const r=await fetch('/users').then(r=>r.json()); _peState.data=r.ok?r:null; _peState.err=r.ok?'':(r.error||''); }
  catch(e){ _peState.err=e.message; }
  _peRender();
}
async function _pePost(path, body){
  try{ return await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(r=>r.json()); }
  catch(e){ return {ok:false, error:e.message}; }
}
async function _peInvite(form){
  const r=await _pePost('/users/invite', {role:form.role.value, name:form.name.value});
  if(!r.ok){ _peState.err=r.error||'failed'; _peRender(); return; }
  _peState.invite=Object.assign({role:form.role.value}, r); _peState.err='';
  await _peLoad();
}
async function _peUpdate(id, patch){
  const r=await _pePost('/users/update', Object.assign({id}, patch));
  if(!r.ok) showToast('error','People',r.error||'failed');
  await _peLoad();
}
async function _peRemove(id, name){
  if(!confirm(`Remove ${name}? Their link and token stop working at once.`)) return;
  await _peUpdate(id, {remove:true});
}
async function _peCancelInvites(){ await _pePost('/users/cancel_invites', {}); _peState.invite=null; await _peLoad(); }

/* who am I: the owner manages people; a viewer gets a read-only strip */
async function _peMe(){
  try{ _peState.me=await fetch('/me').then(r=>r.json()); }catch(e){ return; }
  const me=_peState.me||{};
  const tab=document.querySelector('.settings-tab[data-pane="people"]');
  if(tab) tab.style.display=me.role==='owner'?'':'none';
  let strip=document.getElementById('pe-strip');
  if(me.role&&me.role!=='owner'){
    if(!strip){ strip=document.createElement('div'); strip.id='pe-strip'; document.body.prepend(strip); }
    strip.className='pe-strip pe-'+me.role;
    strip.textContent=me.role==='viewer'?`Signed in as ${me.name} -- viewer: you can look, not change anything.`:`Signed in as ${me.name} (member).`;
  }
}
function _pePresenceTick(){
  const el=document.getElementById('pe-presence');
  if(!el) return;
  const html=_pePresenceHtml((typeof lastStatus!=='undefined'&&lastStatus&&lastStatus.presence)||[], _peState.me);
  if(el.innerHTML!==html) el.innerHTML=html;
}
_peMe();
setInterval(_pePresenceTick, 3000);
