/* Health -> RELEASE (_releaseToolsHtml, _releaseConfirmText): Publish release
 * and Sync public mirror, "check first, then confirm". Extracted from the
 * shipped dashboard; nothing is fetched. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_releaseToolsHtml', '_releaseConfirmText']) eval(extractFunction(src, f));
const c = new Checker();
const base = { available: true, kind: '', state: 'idle', step: '', error: '', plan: null, result: null };
const SHA = 'ab'.repeat(32);

c.check('not available (installed copy / not this PC) -> nothing', _releaseToolsHtml({ ...base, available: false }) === '' && _releaseToolsHtml(null) === '');
let h = _releaseToolsHtml(base);
c.check('idle: both buttons + run id field, enabled',
  h.includes("_releaseAct('publish','prepare')") && h.includes("_releaseAct('sync','prepare')") && h.includes('id="rel-run-id"') && !h.includes('disabled'));
c.check('idle: no confirm button anywhere', !h.includes("'confirm'"));

h = _releaseToolsHtml({ ...base, kind: 'publish', state: 'running', step: 'dry run (downloading the CI installer)' });
c.check('running: step shown, buttons disabled', h.includes('Publish release: dry run (downloading the CI installer)…') && (h.match(/disabled/g) || []).length === 3);

const plan = { version: '1.0.410', sha256: SHA, run_id: '42274868424', commit: '6ed1896', changes: 3, since: 'v1.0.384',
  notes: 'AOC 1.0.410\n\n- <b>History</b> -> Clients', warnings: [] };
h = _releaseToolsHtml({ ...base, kind: 'publish', state: 'ready', plan });
c.check('ready: version, run, commit, sha, change count', h.includes('Ready to publish AOC 1.0.410') && h.includes('CI run 42274868424')
  && h.includes('commit 6ed1896') && h.includes(SHA) && h.includes('3 change(s) since v1.0.384'));
c.check('ready: notes escaped in a <pre>', h.includes('&lt;b&gt;History&lt;/b&gt; -&gt; Clients') && !h.includes('<b>History'));
c.check('ready: Publish + Cancel, no "publish anyway" without warnings',
  h.includes("_releaseAct('publish','confirm')") && h.includes('Publish v1.0.410') && h.includes("_releaseAct('publish','cancel')") && !h.includes('rel-allow-internal'));
h = _releaseToolsHtml({ ...base, kind: 'publish', state: 'ready', plan: { ...plan, warnings: ['Watchdog: <reload> only after a commit'] } });
c.check('warnings listed (escaped) + the publish-anyway tick', h.includes('1 line(s) read like developer notes') && h.includes('&lt;reload&gt;') && h.includes('id="rel-allow-internal"'));

h = _releaseToolsHtml({ ...base, kind: 'publish', state: 'done', plan, result: { version: '1.0.410', url: 'https://github.com/x/releases/tag/v1.0.410', verified: true } });
c.check('done + verified', h.includes('Published AOC 1.0.410') && h.includes('href="https://github.com/x/releases/tag/v1.0.410"') && h.includes('matches the SHA-256'));
h = _releaseToolsHtml({ ...base, kind: 'publish', state: 'done', plan, result: { version: '1.0.410', url: 'u', verified: false, verify_error: 'download: 404' } });
c.check('done but not verified says so, with the reason', h.includes('could not be verified') && h.includes('download: 404') && h.includes('hh-row bad'));

h = _releaseToolsHtml({ ...base, kind: 'publish', state: 'error', error: 'release v1.0.410 already exists' });
c.check('error: message + OK (back to idle)', h.includes('Publish release failed') && h.includes('already exists') && h.includes(`_releaseAct(&quot;publish&quot;,'cancel')`));

const sp = { mirror: 'C:\\pub', changed: 2, status: ['M  README.md', 'A  server/01_core.py'], summary: '2 files changed, 3 insertions(+)',
  private_sha: '6ed1896', private_dirty: 0, message: 'AOC public mirror 2026-11-06 @6ed1896' };
h = _releaseToolsHtml({ ...base, kind: 'sync', state: 'ready', plan: sp });
c.check('sync ready: count, diffstat, files, commit message, Commit & push',
  h.includes('2 file(s) change in the public mirror') && h.includes('2 files changed') && h.includes('A  server/01_core.py')
  && h.includes('AOC public mirror 2026-11-06 @6ed1896') && h.includes("_releaseAct('sync','confirm')"));
c.check('sync ready, clean checkout: no dirty warning', !h.includes('uncommitted file(s)'));
h = _releaseToolsHtml({ ...base, kind: 'sync', state: 'ready', plan: { ...sp, private_dirty: 3 } });
c.check('dirty private checkout is warned about', h.includes('3 uncommitted file(s) in this checkout'));
h = _releaseToolsHtml({ ...base, kind: 'sync', state: 'ready', plan: { ...sp, changed: 0, status: [] } });
c.check('nothing changed: no Commit & push, just OK', !h.includes("'sync','confirm'") && h.includes('>OK<'));
h = _releaseToolsHtml({ ...base, kind: 'sync', state: 'done', result: { commit: 'f00ba12', message: sp.message } });
c.check('sync done', h.includes('Public mirror pushed (f00ba12)'));

c.check('confirm text: publish names version, run, sha', /Publish AOC 1\.0\.410[^]*42274868424[^]*abab/.test(_releaseConfirmText('publish', { plan })));
c.check('confirm text: sync names file count + message', /Commit 2 file\(s\)[^]*@6ed1896/.test(_releaseConfirmText('sync', { plan: sp })));
c.check('Health renders the card from /release/status', src.includes("fetch('/release/status')") && src.includes('<div id="release-tools">${_releaseToolsHtml(rel)}</div>'));
c.finish();
