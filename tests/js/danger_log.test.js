/* Settings -> DANGEROUS COMMANDS, round 7: the self-test verdict
 * (_dgTestHtml) and the recently held list (_dgLogHtml), extracted from
 * monitor.py. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', '_dgTestHtml', '_dgLogHtml']) eval(extractFunction(src, f));
const c = new Checker();

const hit = { id: 'force_push', label: 'Force push', why: 'x', part: 'git push --force <origin>' };
let h = _dgTestHtml({ ok: true, hits: [hit], would_hold: true, mode: 'hold' });
c.check('held: says so and names the rule', h.includes('Would be held for your OK') && h.includes('<b>Force push</b>'));
c.check('held: matched part escaped', h.includes('git push --force &lt;origin&gt;'));
h = _dgTestHtml({ ok: true, hits: [hit], would_hold: false, mode: 'off' });
c.check('guard off: warns it would run', h.includes('the guard is off') && !h.includes('Would be held'));
c.check('harmless: runs normally', _dgTestHtml({ ok: true, hits: [], would_hold: false }).includes('Runs normally'));
c.check('failed request', _dgTestHtml(null).includes('Could not check') && _dgTestHtml({ ok: false }).includes('Could not check'));

c.check('empty log renders nothing', _dgLogHtml([]) === '' && _dgLogHtml(null) === '');
const now = Date.now() / 1000;
const log = Array.from({ length: 12 }, (_, i) => ({ t: now - i * 60, cwd: 'C:\\proj\\shop', cmd: `git push --force <${i}>`,
  outcome: i % 2 ? 'denied' : 'allowed' }));
h = _dgLogHtml(log);
c.check('shows the last 10 of 12', (h.match(/dg-log-row/g) || []).length === 10 && h.includes('last 10 of 12'));
c.check('project from cwd', h.includes('<i>shop</i>'));
c.check('command escaped', h.includes('git push --force &lt;0&gt;') && !h.includes('<0>'));
c.check('outcome coloured', h.includes('color:var(--g)">allowed') && h.includes('color:var(--r)">denied'));
h = _dgLogHtml([{ t: now - 86400 * 3, cwd: '', cmd: 'rm -rf /', outcome: 'cancelled' }]);
c.check('older entry shows a date, no project, neutral colour', !h.includes('<i>') && h.includes('color:var(--t3)">cancelled') && h.includes('(1)'));
c.finish();
