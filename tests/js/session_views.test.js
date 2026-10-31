/* Session views extracted from monitor.py: what Timeline / Heat / Graph /
 * Tree draw when no agent ran -- _sessionItems merges the open sessions with
 * today's history and the per-minute activity, then one html builder each. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_waitingSessions', '_waitLabel', '_fmtDurShort', '_ccModelName',
  '_sessionItems', '_svRange', '_sessTimelineHtml', '_sessHeatHtml', '_sessGraphHtml', '_sessTreeHtml'])
  eval(extractFunction(src, f));
// CI checks monitor.py out with CRLF line endings, so match ';' + \r?\n
const grab = name => { const a = src.indexOf('const ' + name + '='); const m = /;\r?\n/g; m.lastIndex = a; return src.slice(a, m.exec(src).index + 1).replace(/^const /, 'var '); };
['_svNote', '_svEmpty', '_svStateLabel', '_svClock'].forEach(n => (0, eval)(grab(n)));

const c = new Checker();
const today = '2026-10-28', now = new Date('2026-10-28T20:00:00').getTime(), m0 = Math.floor(new Date('2026-10-28T17:30:00').getTime() / 1000);
const sr = { sessions_list: [
  { id: 'a', display_name: 'AOC <x>', session_active: true, cwd: 'C:\\P\\aoc', first_ts: '2026-10-28T16:23:00Z', estimated_cost: 4, model: 'claude-opus-5-5' },
  { id: 'b', display_name: 'Memebot', session_active: true, cwd: 'C:\\P\\aoc', waiting_on_you: true, waiting_secs: 60, estimated_cost: 1 },
  { id: 'z', display_name: 'Closed', session_active: false }] };
const hist = [{ id: 'a', date: today, started_at: '17:23:00', duration_s: 60, title: 'AOC old title', cost: 2 },
  { id: 'h', date: today, started_at: '09:00:00', duration_s: 1800, title: 'Morning', project: 'notes', cost: 0.5 },
  { id: 'y', date: '2026-10-27', started_at: '10:00:00', duration_s: 60, title: 'Yesterday' }];
const act = { a: { minutes: [[m0, 5], [m0 + 60, 9]], edited: ['C:\\P\\aoc\\monitor.py', 'C:\\P\\aoc\\README.md'] },
  b: { minutes: [[m0 + 600, 3]], edited: ['c:/p/aoc/monitor.py'] } };
const it = _sessionItems(sr, hist, act, today, now);
c.check('items: open sessions + today\'s history, oldest first, closed and yesterday left out', it.map(i => i.id).join() === 'h,a,b');
c.check('items: live data wins (name, cost, end = now, folder), history kept for finished', it[1].name === 'AOC <x>' && it[1].cost === 4
  && it[1].end === now && it[1].group === 'aoc' && it[1].state === 'live' && it[0].state === 'done' && it[0].end === it[0].start + 1800e3);
c.check('items: waiting state, activity and edited files attached', it[2].state === 'wait' && it[1].minutes.length === 2 && it[1].edited.length === 2);
c.check('items: no status / history -> empty', _sessionItems(null, null, null, today, now).length === 0);

const tl = _sessTimelineHtml(it, now);
c.check('timeline: a row per session, escaped, opens History, note + legend', (tl.match(/class="sv-row /g) || []).length === 3
  && tl.includes('AOC &lt;x&gt;') && tl.includes('_jumpToSessionHistory(&quot;a&quot;)') && tl.includes('No agents ran') && tl.includes('Waiting on you'));
c.check('timeline: activity marks inside the bar', (tl.match(/<i style="left:/g) || []).length >= 2 && tl.includes('2 files'));
const ht = _sessHeatHtml(it, now);
c.check('heat: grid with one row per session and the busiest cell full', (ht.match(/class="sv-hname /g) || []).length === 3
  && ht.includes('--a:1.00') && ht.includes('15-minute bins') && ht.includes(escHtml(_svClock(m0 * 1000)) + ' · 14 lines'));
const gr = _sessGraphHtml(it);
c.check('graph: node per session, shared-file edge (path case / slashes ignored), same-folder edge', (gr.match(/class="sv-node"/g) || []).length === 3
  && gr.includes('1 shared file') && !gr.includes('stroke-dasharray'));
c.check('graph: escaped labels', gr.includes('AOC &lt;x&gt;') && !gr.includes('AOC <x>'));
const tr = _sessTreeHtml(it);
c.check('tree: grouped by folder with totals', tr.includes('<b>aoc</b>') && tr.includes('2 sessions · 3 files · $5.00') && tr.includes('<b>notes</b>')
  && tr.includes('Opus 5.5'));
c.check('empty -> friendly empty state everywhere', [_sessTimelineHtml, _sessHeatHtml, _sessGraphHtml, _sessTreeHtml].every(f => f([], now).includes('No sessions today yet')));
c.finish();
