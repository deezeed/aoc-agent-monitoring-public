/* People (web/js/21_people.js): Settings -> PEOPLE and who's here,
 * extracted from the shipped dashboard. */
const fs = require('fs');
const path = require('path');
const { readMonitorSource, extractFunction, WEB_DIR } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
const part = fs.readFileSync(path.join(WEB_DIR, 'js', '21_people.js'), 'utf8');
global._PE_ROLES = eval('(' + /^var _PE_ROLES=(\{.*\});$/m.exec(part)[1] + ')');
for (const f of ['escHtml', 'jsq', '_peInitials', '_peAgo', '_pePresenceHtml', '_pePaneHtml']) eval(extractFunction(src, f));
const c = new Checker();
global.location = { origin: 'http://192.168.1.5:5151' };

c.check('initials', _peInitials('Marco Rossi') === 'MR' && _peInitials('marek') === 'M' && _peInitials('') === '?');
c.check('ago', _peAgo(null) === 'never' && _peAgo(5) === 'now' && _peAgo(600) === '10 min ago' && _peAgo(7200) === '2 h ago');
const present = [{ id: 'owner', name: 'Marek', role: 'owner' }, { id: 'marco', name: 'Marco <R>', role: 'member', view: 'orch' }];
const ph = _pePresenceHtml(present, { id: 'owner' });
c.check("who's here: everyone but me, escaped, role + view in the tooltip", !ph.includes('>M<') && ph.includes('Marco &lt;R&gt; (Member) · orch')
  && ph.includes('pe-member'));
c.check('alone: nothing shown', _pePresenceHtml([{ id: 'owner', name: 'Marek', role: 'owner' }], { id: 'owner' }) === '');
const d = { owner_name: 'Marek', users: [{ id: 'marco', name: 'Marco', role: 'member', last_seen: null, invited_by: 'Marek', disabled: false },
  { id: 'eva', name: 'Eva', role: 'viewer', last_seen: null, disabled: true }], invites: [{ role: 'viewer' }], present: [{ id: 'marco', name: 'Marco' }] };
const pane = _pePaneHtml(d, null, '');
c.check('pane: your name, each person with role / disable / remove, open invites, who is here',
  pane.includes('value="Marek"') && pane.includes('_peUpdate(&quot;marco&quot;,{role:this.value})') && pane.includes('<option value="member" selected>')
  && pane.includes('_peUpdate(&quot;eva&quot;,{disabled:false})') && pane.includes('>Enable<') && pane.includes('_peRemove(&quot;marco&quot;,&quot;Marco&quot;)')
  && pane.includes('1 open invite') && pane.includes('Here now: Marco'));
c.check('pane: the owner can\'t be made member/viewer of themselves -- no owner row', !pane.includes('_peUpdate(&quot;owner&quot;'));
const withLink = _pePaneHtml(d, { role: 'member', link: 'https://x.trycloudflare.com/join?code=abc', path: '/join?code=abc' }, '');
c.check('invite link shown with copy', withLink.includes('value="https://x.trycloudflare.com/join?code=abc"') && withLink.includes('copyText(this,')
  && !withLink.includes('No tunnel running'));
c.check('no tunnel: a link on your network, and says so', _pePaneHtml(d, { role: 'viewer', link: '', path: '/join?code=abc' }, '').includes('No tunnel running')
  && _pePaneHtml(d, { role: 'viewer', link: '', path: '/join?code=abc' }, '').includes('value="http://192.168.1.5:5151/join?code=abc"'));
c.check('loading / error', _pePaneHtml(null).includes('Loading') && _pePaneHtml(d, null, 'boom').includes('boom'));
c.finish();
