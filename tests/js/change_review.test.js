/* Change review UI (extracted from monitor.py): per-file "reviewed" toggle,
 * Discard (with the file's sig from when the panel was shown), the review bar
 * with "Stage reviewed", the "Discarded - can be put back" list with Restore,
 * and that none of it appears for a remote machine or a non-local view. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_sessionChangesHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
const files = () => [
  { path: 'C:/p/a.py', rel: 'a.py', kind: 'modified', ins: 1, dels: 1, seen: true, sig: '10:123' },
  { path: 'C:/p/n <x>.js', rel: 'n <x>.js', kind: 'untracked', ins: 4, dels: 0, seen: false, sig: '4:9' },
  { path: 'C:/p/r.py', rel: 'r.py', kind: 'renamed', ins: 0, dels: 0, seen: false, sig: '1:1' } ];
const mk = (extra) => Object.assign({ ok: true, local: true, clean: 0, outside: 0,
  repos: [{ name: 'p', branch: 'master', root: 'C:/p', files: files() }], discards: [] }, extra || {});

let h = _sessionChangesHtml(mk(), 's1', '');
c.check('progress n/m reviewed', h.includes('1/3 reviewed'));
c.check('stage reviewed button with only seen paths', h.includes('Stage reviewed (1)') && h.includes('_chgStage(&quot;s1&quot;,[&quot;C:/p/a.py&quot;])'));
c.check('seen row dimmed + toggle on, unmark on click', h.includes('chg-row seen') && h.includes('chg-seen on') && h.includes('_chgSeen(&quot;s1&quot;,&quot;C:/p/a.py&quot;,false)'));
c.check('unseen toggle marks', h.includes('_chgSeen(&quot;s1&quot;,&quot;C:/p/n &lt;x&gt;.js&quot;,true)'));
c.check('discard passes kind + working + sig', h.includes('_chgDiscard(&quot;s1&quot;,&quot;C:/p/a.py&quot;,&quot;a.py&quot;,&quot;modified&quot;,false,&quot;10:123&quot;)'));
c.check('renamed files: no discard', (h.match(/_chgDiscard\(/g) || []).length === 2 && !h.includes('&quot;r.py&quot;,&quot;renamed&quot;'));
c.check('new-file discard tooltip says it is kept', h.includes('Remove this new file'));
c.check('no working warning when idle', !h.includes('still working'));
h = _sessionChangesHtml(mk({ working: true }), 's1', '');
c.check('working warning + passed to discard', h.includes('still working') && h.includes('&quot;modified&quot;,true,'));

h = _sessionChangesHtml(mk({ discards: [{ id: '20261030-101500-abcdef', path: 'C:/p/b <y>.py', rel: 'b <y>.py', kind: 'modified', at: 1761819300 }] }), 's1', '');
c.check('discard list with restore', h.includes('Discarded — can be put back') && h.includes('b &lt;y&gt;.py')
  && h.includes('_chgUndo(&quot;s1&quot;,&quot;20261030-101500-abcdef&quot;,&quot;b &lt;y&gt;.py&quot;)'));

const plain = (x) => !x.includes('_chgSeen') && !x.includes('_chgDiscard') && !x.includes('_chgStage') && !x.includes('reviewed');
c.check('remote machine: read-only', plain(_sessionChangesHtml(mk(), 's1', 'pc2')));
c.check('not local (tunnel/phone): read-only', plain(_sessionChangesHtml(mk({ local: false }), 's1', '')));
h = _sessionChangesHtml(mk({ local: false, discards: [{ id: 'x', rel: 'q', kind: 'modified' }] }), 's1', '');
c.check('not local: no restore list either', !h.includes('_chgUndo'));
h = _sessionChangesHtml(mk({ repos: [] }), 's1', '');
c.check('nothing uncommitted: no review bar', h.includes('everything this session edited is committed') && !h.includes('reviewed'));
const ns = mk(); delete ns.repos[0].files[0].sig;
c.check('no sig -> real null, not the string "null"', _sessionChangesHtml(ns, 's1', '').includes('&quot;modified&quot;,false,null)'));
const z = mk(); z.repos[0].files.forEach(f => { f.seen = false; });
c.check('nothing reviewed: no stage button', !_sessionChangesHtml(z, 's1', '').includes('Stage reviewed'));
c.finish();
