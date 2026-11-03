/* Health -> CLAUDE CODE SETUP card (_hooksHealthHtml) and the status pill's
 * CHECK HOOKS state (_ccPillState), extracted from monitor.py. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', '_waitingSessions', '_hooksHealthHtml', '_ccPillState']) eval(extractFunction(src, f));
const c = new Checker();

const ok = (id, label) => ({ id, label, ok: true, detail: '' });
const allOk = { checks: [ok('activity', 'Activity hooks'), ok('danger', 'Dangerous-command guard')], at: 1793640000, error: '', bad: [] };
let h = _hooksHealthHtml(allOk);
c.check('all fine: says so, no Repair button', h.includes('Everything AOC needs is set up') && !h.includes('Repair</button>'));
c.check('all fine: Re-check offered', h.includes('Re-check'));

const broken = { checks: [ok('activity', 'Activity hooks'),
  { id: 'danger', label: 'Dangerous-command guard', ok: false, detail: 'PreToolUse Bash|PowerShell hook <missing>' }],
  at: 1793640000, error: '', bad: ['Dangerous-command guard'] };
h = _hooksHealthHtml(broken);
c.check('broken: counts the missing part', h.includes('1 part of AOC not set up'));
c.check('broken: Repair button', h.includes('_hooksHealthRun(true)') && h.includes('>Repair<'));
c.check('broken: detail escaped', h.includes('hook &lt;missing&gt;') && !h.includes('<missing>'));
c.check('broken row marked bad', /hh-row bad[^]*Dangerous-command guard/.test(h));
h = _hooksHealthHtml(broken, 'repair');
c.check('while repairing: buttons disabled, label changes', h.includes('Repairing…') && (h.match(/disabled/g) || []).length === 2);
h = _hooksHealthHtml({ checks: null, error: 'setup.py <boom>' });
c.check('no result: error shown escaped', h.includes("Couldn't check the install: setup.py &lt;boom&gt;"));
c.check('nothing yet: placeholder', _hooksHealthHtml(null).includes('not checked yet'));
h = _hooksHealthHtml({ ...allOk, error: 'TimeoutExpired' });
c.check('last run error shown under the list', h.includes('Last run: TimeoutExpired'));

const sr = (ih) => ({ sessions_list: [], agents: [], infra_health: ih });
c.check('pill: hooks missing -> CHECK HOOKS', _ccPillState(sr({ watchdog: { pid_alive: true }, hooks_bad: ['Budget caps'] }), 0)[1] === 'CHECK HOOKS');
c.check('pill: hooks fine -> OPTIMAL', _ccPillState(sr({ watchdog: { pid_alive: true }, hooks_bad: [] }), 0)[1] === 'OPTIMAL');
c.check('pill: unknown hooks -> OPTIMAL', _ccPillState(sr({ watchdog: { pid_alive: true } }), 0)[1] === 'OPTIMAL');
c.check('pill: dead watchdog still wins', _ccPillState(sr({ watchdog: { pid_alive: false }, hooks_bad: ['x'] }), 0)[1] === 'DEGRADED');
c.finish();
