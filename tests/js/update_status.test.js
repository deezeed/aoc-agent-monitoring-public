/* Health -> VERSION & UPDATES (_updateStatusHtml) and the restart confirm
 * text (_restartConfirmText), extracted from monitor.py. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', '_updateStatusHtml', '_restartConfirmText']) eval(extractFunction(src, f));
const c = new Checker();
const now = 1_800_000_000;

c.check('nothing -> empty', _updateStatusHtml(null, now) === '');
let h = _updateStatusHtml({ mode: 'release', installed: '1.0.374', self_update: { stale: true, latest: '1.0.380', checked_at: now - 60 },
  job: { state: 'idle' }, history: [], log: null }, now);
c.check('installed: version + newer available', h.includes('AOC 1.0.374') && h.includes('1.0.380 available'));
c.check('no log -> no log button; empty history note', !h.includes('Installer log') && h.includes('No updates recorded yet'));
h = _updateStatusHtml({ mode: 'release', installed: '1.0.374', self_update: { stale: false, checked_at: now - 7200 }, job: { state: 'idle' },
  history: [{ t: now - 3 * 86400, outcome: 'updated', from: '1.0.360', to: '1.0.374', via: 'one-click update' },
            { t: now - 4 * 86400, outcome: 'failed', to: '1.0.370', error: 'checksum <mismatch>' }],
  log: { path: 'C:\\x\\install.log', mtime: now } }, now);
c.check('up to date with check age', h.includes('up to date (checked 2 h ago)'));
c.check('history: success row', h.includes('1.0.360 → 1.0.374') && h.includes('one-click update · 3 d ago'));
c.check('history: failure row escaped', h.includes('Update to 1.0.370 failed') && h.includes('checksum &lt;mismatch&gt;'));
c.check('log button when a log exists', h.includes('_updateLogShow()'));
h = _updateStatusHtml({ mode: 'release', installed: '1.0.374', self_update: {}, job: { state: 'downloading', pct: 42.4, version: '1.0.380' }, history: [] }, now);
c.check('running update shown with progress', h.includes('Updating to 1.0.380: downloading 42%'));
h = _updateStatusHtml({ mode: 'release', installed: '1.0.374', self_update: {}, job: { state: 'error', error: 'no <hash>' }, history: [] }, now);
c.check('failed job shown escaped', h.includes('Update failed') && h.includes('no &lt;hash&gt;'));
h = _updateStatusHtml({ mode: 'git', installed: '', self_update: { stale: true, behind_by: 3, checked_at: now }, job: {}, history: [] }, now);
c.check('dev checkout: commits behind, no empty-history note', h.includes('Dev checkout') && h.includes('3 commit(s) behind') && !h.includes('No updates'));

const t = _restartConfirmText(['1 prompt(s) waiting for your OK', 'remote tunnel is open']);
c.check('restart confirm names what is dropped', t.includes('1 prompt(s) waiting for your OK; remote tunnel is open') && t.includes('dropped'));
c.finish();
