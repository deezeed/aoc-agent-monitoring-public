/* New session form (_launchFormHtml extracted from monitor.py): folder
 * chips + datalist from /launch/dirs (escaped), machine picker only when
 * there is more than one machine, model choices, Ctrl+Enter, empty state;
 * and the Today header's "+ New session" button. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_fmtDurationDHM', '_lastMessageInfo', '_waitLabel', '_waitingSessions', '_ctxMeterHtml',
                 '_permAskHtml', '_loopBadgeHtml', '_awayBlockHtml', '_changesBtnHtml', '_todayHtml', '_launchFormHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
const dirs = [{ path: 'C:\\p\\AOC "x"', name: 'AOC "x"', last: 3 }, { path: 'C:\\p\\Omni', name: 'Omni', last: 2 }];
let h = _launchFormHtml(dirs, [{ name: '', label: 'This PC' }], '');
c.check('chips with paths, escaped', h.includes('data-path="C:\\p\\AOC &quot;x&quot;"') && h.includes('>AOC &quot;x&quot;</button>'));
c.check('datalist options', h.includes('<option value="C:\\p\\Omni"></option>'));
c.check('single machine -> no picker', !h.includes('id="ln-machine"'));
c.check('models', ['value="opus"', 'value="sonnet"', 'value="haiku"', '<option value="">Default</option>'].every(x => h.includes(x)));
c.check('folder required, Ctrl+Enter submits', h.includes('id="ln-cwd"') && h.includes(' required>') && h.includes("event.key==='Enter'&&(event.ctrlKey"));
h = _launchFormHtml([], [{ name: '', label: 'This PC' }, { name: 'laptop <2>', label: 'laptop <2>' }], 'laptop <2>');
c.check('machines -> picker, selected, escaped', h.includes('id="ln-machine"') && h.includes('<option value="laptop &lt;2&gt;" selected>laptop &lt;2&gt;</option>'));
c.check('no dirs -> hint', h.includes('Folders Claude Code worked in lately'));
c.check('more than 8 dirs -> 8 chips', (_launchFormHtml(Array.from({ length: 12 }, (_, i) => ({ path: 'C:\\d' + i, name: 'd' + i })), [], '').match(/class="ln-chip"/g) || []).length === 8);
const t = _todayHtml({ sessions_list: [] }, {}, null, 1_800_000_000, '2027-01-15', {}, 'auto');
c.check('Today has + New session', t.includes('onclick="openLaunch()"') && t.includes('+ New session'));
c.finish();
