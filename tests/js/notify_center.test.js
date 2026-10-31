/* Tests the notification center renderers (_ncHtml, _ncStatusHtml,
 * _ncLogHtml) in Settings -> Notify. Extracted straight from monitor.py. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, 'escHtml'));
eval(extractFunction(src, 'jsq'));
const _NC_ICONS = { toast: '🖥', push: '📱', webhook: '🔗' };
eval(extractFunction(src, '_ncHtml'));
eval(extractFunction(src, '_ncStatusHtml'));
eval(extractFunction(src, '_ncLogHtml'));

const c = new Checker();

const d = {
  types: [
    { key: 'done', group: 'Agents', label: 'Agent finished', quiet: true, channels: { toast: true, push: false, webhook: true } },
    { key: 'cache_expiry', group: 'Sessions', label: 'Cache <cold>', quiet: true, channels: { toast: true, push: true, webhook: null } },
    { key: 'permission', group: 'Sessions', label: 'Held prompt', quiet: false, channels: { toast: null, push: true, webhook: null } },
  ],
  quiet_start: '22:00', quiet_end: '07:00', quiet_now: false, muted_projects: [], webhook_url: true, push_devices: 1,
};
const h = _ncHtml(d);
c.check('one group header per group', (h.match(/class="nc-g"/g) || []).length === 2 && h.includes('AGENTS') && h.includes('SESSIONS'));
c.check('checkbox per available channel', (h.match(/type="checkbox"/g) || []).length === 6);
c.check('dash for unavailable channels', (h.match(/nc-na/g) || []).length === 3);
c.check('checked state follows the data', (h.match(/ checked/g) || []).length === 5);
c.check('labels are escaped', h.includes('Cache &lt;cold&gt;') && !h.includes('<cold>'));
c.check('toggle calls _ncSet with kind + channel', h.includes(`_ncSet(${jsq('done')},${jsq('push')},this.checked,this)`));
c.check('test button per type', (h.match(/_ncTest\(/g) || []).length === 3);
c.check('always tag only on quiet=false types', (h.match(/· always/g) || []).length === 1);
c.check('no webhook url -> webhook boxes disabled', (_ncHtml({ ...d, webhook_url: false }).match(/disabled/g) || []).length === 1);
c.check('webhook url -> none disabled', !h.includes('disabled'));
c.check('empty types', _ncHtml({ types: [] }).includes('No notification types'));

c.check('status: quiet window', _ncStatusHtml(d).includes('Quiet hours 22:00–07:00') && !_ncStatusHtml(d).includes('on now'));
c.check('status: quiet now', _ncStatusHtml({ ...d, quiet_now: true }).includes('on now'));
c.check('status: no quiet hours', _ncStatusHtml({ ...d, quiet_start: '', quiet_end: '' }).includes('No quiet hours'));
c.check('status: devices + webhook', _ncStatusHtml(d).includes('1 phone for push') && _ncStatusHtml(d).includes('webhook set'));
c.check('status: muted count', _ncStatusHtml({ ...d, muted_projects: ['a', 'b'] }).includes('2 muted projects'));
c.check('status: null -> empty', _ncStatusHtml(null) === '');

const now = 10000;
const log = [
  { t: now - 5, kind: 'done', channel: 'toast', title: '[test] ✓ Sample', body: 'ok', test: true },
  { t: now - 120, kind: 'cache_expiry', channel: 'push', title: '❄ <s>', body: '' },
  { t: now - 7200, kind: 'done', channel: 'webhook', title: 'done', body: '' },
];
const lh = _ncLogHtml(log, now);
c.check('log rows', (lh.match(/class="nc-e"/g) || []).length === 3);
c.check('log relative times', lh.includes('just now') && lh.includes('2m ago') && lh.includes('2h ago'));
c.check('log test tag replaces the title prefix', lh.includes('<span>[test]</span> ✓ Sample') && !lh.includes('[test] ✓'));
c.check('log escapes titles', lh.includes('&lt;s&gt;'));
c.check('log channel icons', lh.includes('🖥') && lh.includes('📱') && lh.includes('🔗'));
c.check('log empty state', _ncLogHtml([], now).includes('Nothing sent yet'));
c.check('log capped at 40', (_ncLogHtml(Array.from({ length: 60 }, () => log[0]), now).match(/class="nc-e"/g) || []).length === 40);

c.check('page has the center', ['id="nc-table"', 'id="nc-log"', 'id="nc-status"', "if(name==='notify') _ncLoad();"].every(x => src.includes(x)));
c.check('hidden webhook boxes still exist for saveSettings', ['st-wh-done', 'st-wh-stuck', 'st-wh-ratelimit'].every(x => src.includes(`id="${x}"`)));
c.finish();
