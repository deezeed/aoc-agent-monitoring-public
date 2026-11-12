/* Health -> CLAUDE CODE SETUP card (_hooksHealthHtml) and the status pill's
 * CHECK HOOKS state (_ccPillState), extracted from monitor.py. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', '_waitingSessions', '_hooksHealthHtml', '_ccPillState', '_repairResultSentence', '_repairResultHtml']) eval(extractFunction(src, f));
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
h = _hooksHealthHtml({ ...allOk, orch_hint: 'Hooks outdated -- reinstall <Repair>' });
c.check('hooks from before the Orchestrate worker tag: the hint, escaped', h.includes('Hooks outdated -- reinstall &lt;Repair&gt;'));
c.check('no hint -> no hint line', !_hooksHealthHtml(allOk).includes('Hooks outdated'));

// the last Repair click's outcome stays on the card
const lr = (state, extra) => ({ at: 1793640000, state, fixed: [], left: [], error: '', ...extra });
h = _hooksHealthHtml({ ...allOk, last_repair: lr('fixed', { fixed: ['Hook scripts in ~/.claude/hooks'] }) });
c.check('repair fixed: green line names what it fixed', /hh-repair ok[^]*✓ Repair at [^:]*\d[^]*fixed Hook scripts in ~\/\.claude\/hooks/.test(h));
c.check('repair fixed: says open sessions need a restart', h.includes('after a restart.'));
h = _hooksHealthHtml({ ...broken, last_repair: lr('unchanged', { left: ['Dangerous-command guard'] }) });
c.check('repair did nothing: red line says so', h.includes('hh-repair bad') && h.includes('ran, but nothing changed -- still not set up: Dangerous-command guard'));
h = _hooksHealthHtml({ ...broken, last_repair: lr('partial', { fixed: ['Budget caps'], left: ['Dangerous-command guard'] }) });
c.check('repair partial: both lists', h.includes('fixed Budget caps; still not set up: Dangerous-command guard'));
h = _hooksHealthHtml({ ...broken, error: 'setup <x>', last_repair: lr('failed', { error: 'setup <x>' }) });
c.check('repair failed: error escaped, shown once', h.includes('failed: setup &lt;x&gt;') && !h.includes('Last run:'));
h = _hooksHealthHtml({ ...broken, last_repair: lr('failed', { error: 'repair can only be started on this PC' }) });
c.check('repair refused remotely: says why', h.includes('failed: repair can only be started on this PC'));
c.check('no repair yet: no repair line', !_hooksHealthHtml(broken).includes('hh-repair'));
c.check('sentence for the toast matches', _repairResultSentence(lr('unchanged', { left: ['A', 'B'] })) === 'ran, but nothing changed -- still not set up: A, B.');
c.check('no result -> empty sentence', _repairResultSentence(null) === '' && _repairResultHtml(undefined) === '');

const sr = (ih) => ({ sessions_list: [], agents: [], infra_health: ih });
c.check('pill: hooks missing -> CHECK HOOKS', _ccPillState(sr({ watchdog: { pid_alive: true }, hooks_bad: ['Budget caps'] }), 0)[1] === 'CHECK HOOKS');
c.check('pill: hooks fine -> OPTIMAL', _ccPillState(sr({ watchdog: { pid_alive: true }, hooks_bad: [] }), 0)[1] === 'OPTIMAL');
c.check('pill: unknown hooks -> OPTIMAL', _ccPillState(sr({ watchdog: { pid_alive: true } }), 0)[1] === 'OPTIMAL');
c.check('pill: dead watchdog still wins', _ccPillState(sr({ watchdog: { pid_alive: false }, hooks_bad: ['x'] }), 0)[1] === 'DEGRADED');
// auto-repair switch + an automatic repair's result line
c.check('auto-repair switch shown, on by default', /id="hh-auto-repair" checked/.test(_hooksHealthHtml(allOk)));
c.check('auto-repair switch off when the server says so', !/id="hh-auto-repair" checked/.test(_hooksHealthHtml({ ...allOk, auto_repair: false })));
const autoLr = { ...allOk, last_repair: { at: 1793640000, state: 'fixed', fixed: ['Hook scripts'], left: [], error: '', source: 'auto' } };
c.check('automatic repair named as such', _hooksHealthHtml(autoLr).includes('Automatic repair after the update'));
c.check('a click is still just Repair', !_hooksHealthHtml({ ...autoLr, last_repair: { ...autoLr.last_repair, source: undefined } }).includes('Automatic repair'));
c.finish();
