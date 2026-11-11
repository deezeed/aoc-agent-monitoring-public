/* Shared sessions panel (web/js/22_shared.js), extracted from the shipped dashboard. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_peInitials', '_shListHtml', '_shBarHtml']) eval(extractFunction(src, f));
const c = new Checker();
const list = [{ id: 'a1', name: 'Pair <AOC>', cwd: 'C:\\x', kind: 'claude', by: 'Marek', alive: true, people: [{ name: 'Marco', role: 'member' }] },
  { id: 'b2', name: 'Old', cwd: 'C:\\y', kind: 'shell', by: 'Marek', alive: false, people: [] }];
const own = _shListHtml(list, true, [{ path: 'C:\\proj' }], '');
c.check('owner: sessions (escaped), who started, who is there, open + end; start form with known folders',
  own.includes('Pair &lt;AOC&gt;') && own.includes('Claude Code') && own.includes('by Marek') && own.includes('here: Marco')
  && own.includes('_shJoin(&quot;a1&quot;') && own.includes('_shStop(&quot;a1&quot;') && own.includes('Start a shared session')
  && own.includes('<option value="C:\\proj">') && own.includes('>ended<'));
const mem = _shListHtml(list, false, [], '');
c.check('others: open only, no start / end', mem.includes('_shJoin(') && !mem.includes('_shStop(') && !mem.includes('Start a shared session')
  && mem.includes('The owner starts shared sessions'));
c.check('nothing running / loading', _shListHtml([], false, [], '').includes('No shared session running') && _shListHtml(null, false).includes('Loading'));
const bar = _shBarHtml({ name: 'Pair', people: [{ name: 'Marco Rossi', role: 'member' }], typing: 'Marco', me: { can_type: false }, ended: false });
c.check('bar: who is there, who is typing, watching-only', bar.includes('>MR<') && bar.includes('Marco is typing') && bar.includes('watching') && bar.includes('_shLeave()'));
c.check('bar: ended', _shBarHtml({ name: 'P', people: [], typing: '', me: { can_type: true }, ended: true }).includes('ended')
  && !_shBarHtml({ name: 'P', people: [], typing: '', me: { can_type: true }, ended: false }).includes('watching'));
c.check('start form: the folder example shows single backslashes', own.includes('placeholder="folder, e.g. C:\\path\\to\\project"'));
c.finish();
