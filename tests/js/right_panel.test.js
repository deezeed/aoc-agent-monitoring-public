/* Right panel extracted from monitor.py: FILES (session edits + agent files),
 * AUDIT (escaped rows, POLL heartbeats only counted) and ERRORS grouping. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_rpFilesParts', '_rpAuditParts', '_groupErrors'])
  eval(extractFunction(src, f));

const c = new Checker();
const f = _rpFilesParts({
  sessions_list: [{ id: 's1', display_name: 'Small', session_active: true, edited_count: 1 },
    { id: 's2', display_name: 'Big <B>', session_active: true, edited_count: 4, machine: 'pc2' },
    { id: 's3', display_name: 'Closed', session_active: false, edited_count: 9 },
    { id: 's4', display_name: 'Nothing', session_active: true, edited_count: 0 }],
  agents: [{ id: 'a1', name: 'Explore', files_changed: [{ path: 'src/a.py', type: 'new', lines: 12 }, { path: 'src/b.py' }] },
    { id: 'a2', name: 'Plan', files_changed: [{ path: 'src/a.py' }] }, { id: 'hook_x', files_changed: [{ path: 'skip.py' }] }],
});
c.check('files: count = session edits + unique agent files', f.count === 1 + 4 + 2);
c.check('files: open sessions with edits only, most edits first, escaped', f.html.indexOf('Big &lt;B&gt;') < f.html.indexOf('Small')
  && !f.html.includes('Closed') && !f.html.includes('Nothing') && f.html.includes('4 files') && f.html.includes('1 file<'));
c.check('files: session row opens its changes with its machine', f.html.includes('openSessionChanges(&quot;s2&quot;,&quot;pc2&quot;,'));
c.check('files: agent files deduped, hook_ agents skipped, NEW badge', (f.html.match(/class="fe /g) || []).length === 2
  && !f.html.includes('skip.py') && f.html.includes('>NEW<') && f.html.includes('12L'));
const e = _rpFilesParts({});
c.check('files: empty state', e.count === 0 && e.html.includes('No file changes yet'));

const a = _rpAuditParts(['2026-10-28T19:33:47 | SESSION_START  | project=<img src=x onerror=alert(1)> | agents=0',
  '2026-10-28T19:33:52 | POLL           | poll_count=3', '2026-10-28T19:33:57 | POLL | poll_count=6',
  '2026-10-28T19:40:00 | AGENT_START | name=Explore | project=']);
c.check('audit: html in a line is escaped', !a.html.includes('<img') && a.html.includes('&lt;img'));
c.check('audit: POLL only counted, newest first, time only', a.count === 2 && a.html.includes('2 heartbeat polls hidden')
  && a.html.indexOf('AGENT START') < a.html.indexOf('SESSION START') && a.html.includes('>19:40:00<'));
c.check('audit: empty "key=" details dropped', a.html.includes('name=Explore</small>'));
c.check('audit: only polls -> empty state', _rpAuditParts(['t | POLL | x']).html.includes('No events in this session yet'));

const g = _groupErrors([{ error_msg: 'down' }, { error_msg: 'boom' }, { error_msg: 'down ' }, { error_msg: '' }]);
c.check('errors: same message grouped, order kept', g.length === 3 && g[0].length === 2 && g[1][0].error_msg === 'boom' && g[2].length === 1);
c.finish();
