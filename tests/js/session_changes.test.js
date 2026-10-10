/* Session Changes UI (extracted from monitor.py): the "Δ n" button,
 * the panel listing repos / files (kind tag, +/-, escaped, each file
 * opening its diff on the right machine), the empty and footer states, and
 * the shared diff line colouring. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_diffLinesHtml', '_changesBtnHtml', '_sessionChangesHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
c.check('no edits -> no button', _changesBtnHtml({ id: 's' }) === '' && _changesBtnHtml(null) === '');
let h = _changesBtnHtml({ id: 's1', machine: 'pc', display_name: 'AOC <x>', edited_count: 3 });
c.check('button with count, routed', h.includes('>Δ 3<') && h.includes('openSessionChanges(&quot;s1&quot;,&quot;pc&quot;,&quot;AOC &lt;x&gt;&quot;)'));
c.check('button plural tooltip', h.includes('the 3 files this session edited'));

const d = { ok: true, clean: 2, outside: 1, repos: [{ name: 'AOC <repo>', branch: 'master', root: 'C:/p/AOC', files: [
  { path: 'C:/p/AOC/monitor.py', rel: 'monitor.py', kind: 'modified', ins: 12, dels: 3 },
  { path: 'C:/p/AOC/new <f>.js', rel: 'new <f>.js', kind: 'untracked', ins: 40, dels: 0 },
  { path: 'C:/p/AOC/old.py', rel: 'old.py', kind: 'deleted', ins: 0, dels: 9 } ] }] };
h = _sessionChangesHtml(d, 's1', 'pc');
c.check('repo + branch, escaped', h.includes('<b>AOC &lt;repo&gt;</b>') && h.includes('⎇ master'));
c.check('kind tags', h.includes('k-modified">M<') && h.includes('k-untracked">NEW<') && h.includes('k-deleted">D<'));
c.check('+/- counts, zero dels hidden', h.includes('+12') && h.includes('−3') && !h.includes('−0'));
c.check('file opens its diff on the machine', h.includes('_showSessionFileDiff(&quot;s1&quot;,&quot;pc&quot;,&quot;C:/p/AOC/monitor.py&quot;,&quot;monitor.py&quot;)'));
c.check('file names escaped', h.includes('new &lt;f&gt;.js') && !h.includes('new <f>.js'));
c.check('footer: committed + outside', h.includes('2 edited files are committed or unchanged · 1 outside any git repo'));
h = _sessionChangesHtml({ ok: true, repos: [], clean: 1, outside: 0 }, 's', '');
c.check('nothing uncommitted', h.includes('everything this session edited is committed') && h.includes('1 edited file is committed'));
c.check('error state', _sessionChangesHtml({ ok: false, error: 'boom <b>' }).includes('boom &lt;b&gt;') && _sessionChangesHtml(null).includes('Could not load'));

h = _diffLinesHtml('--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old <a>\n+new\n same');
c.check('diff colouring + escaping', h.includes('<span class="diff-del">-old &lt;a&gt;</span>') && h.includes('<span class="diff-add">+new</span>')
  && h.includes('<span class="diff-hunk">') && h.includes('<span class="diff-meta">--- a/x</span>'));
c.finish();
