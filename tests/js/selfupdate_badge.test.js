/* Tests _selfUpdateUpdateUI, extracted straight from monitor.py, against a
 * minimal fake DOM: a git checkout gets the "run git pull" badge, an
 * installer build (mode "release") gets a clickable badge that starts the
 * one-click update, shows its progress / failure, and switching back resets
 * the click handler. Also _whatsNewHtml (escaped release-note bullets). */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, '_fmtEpochAgo'));
eval(extractFunction(src, '_selfUpdateUpdateUI'));
eval(extractFunction(src, 'escHtml'));
eval(extractFunction(src, '_whatsNewHtml'));
const installs = [];
global._selfUpdateInstall = su => installs.push(su);

const c = new Checker();

const badge = { style: { display: 'none', cursor: 'default' }, title: '', onclick: null };
const lbl = { textContent: 'UPDATE AVAILABLE' };
global.document = { getElementById: id => ({ 'selfupdate-badge': badge, 'selfupdate-badge-lbl': lbl })[id] || null };
const opened = [];
global.window = { open: (...a) => opened.push(a) };

// 1. not stale -> hidden
_selfUpdateUpdateUI({ stale: false });
c.check('not stale -> badge hidden', badge.style.display === 'none');
_selfUpdateUpdateUI(null);
c.check('missing status -> badge hidden', badge.style.display === 'none');

// 2. installer build with a newer release
const url = 'https://github.com/deezeed/aoc-agent-monitoring-public/releases/download/v1.0.200/AOC-Setup-1.0.200.exe';
const rel = { stale: true, mode: 'release', latest: '1.0.200', installed: '1.0.150', url, checked_at: null };
_selfUpdateUpdateUI(rel);
c.check('release -> badge shown', badge.style.display === 'flex');
c.check('release -> label names the new version', lbl.textContent === 'UPDATE 1.0.200');
c.check('release -> title names both versions', badge.title.includes('1.0.200') && badge.title.includes('1.0.150'));
c.check('release -> clickable', badge.style.cursor === 'pointer' && typeof badge.onclick === 'function');
c.check('release -> title says click to update', badge.title.includes('click to update'));
badge.onclick();
c.check('click starts the one-click update (no new tab)', installs.length === 1 && installs[0].latest === '1.0.200' && opened.length === 0);

// 2b. update job progress -> label follows, not clickable meanwhile
_selfUpdateUpdateUI({ ...rel, job: { state: 'downloading', pct: 42, version: '1.0.200' } });
c.check('downloading -> percent label', lbl.textContent === 'UPDATING 42%');
c.check('downloading -> not clickable', badge.onclick === null && badge.style.cursor === 'default');
_selfUpdateUpdateUI({ ...rel, job: { state: 'verifying', version: '1.0.200' } });
c.check('verifying -> label', lbl.textContent === 'VERIFYING' && badge.title.includes('SHA-256'));
_selfUpdateUpdateUI({ ...rel, job: { state: 'installing', version: '1.0.200' } });
c.check('installing -> label + reconnect hint', lbl.textContent === 'INSTALLING' && badge.title.includes('reconnects'));
_selfUpdateUpdateUI({ ...rel, job: { state: 'error', error: 'checksum mismatch' } });
c.check('error -> UPDATE FAILED + reason in title', lbl.textContent === 'UPDATE FAILED' && badge.title.startsWith('Update failed: checksum mismatch'));
c.check('error -> click retries', typeof badge.onclick === 'function' && badge.style.cursor === 'pointer');
_selfUpdateUpdateUI({ ...rel, job: { state: 'idle' } });
c.check('idle job -> normal label', lbl.textContent === 'UPDATE 1.0.200');

// 3. git checkout -> old behavior, click handler cleared
_selfUpdateUpdateUI({ stale: true, behind_by: 3, checked_at: null });
c.check('git -> generic label', lbl.textContent === 'UPDATE AVAILABLE');
c.check('git -> git pull hint', badge.title.startsWith('3 commits behind origin/master'));
c.check('git -> not clickable', badge.onclick === null && badge.style.cursor === 'default');

// 4. What's new dialog body
c.check('no items -> nothing', _whatsNewHtml({}) === '' && _whatsNewHtml(null) === '' && _whatsNewHtml({ items: [] }) === '');
const h = _whatsNewHtml({ version: '1.0.360', items: ['Inbox <b>new</b>', 'Ask "AOC"'] });
c.check('title names the version', h.includes("WHAT'S NEW IN AOC 1.0.360"));
c.check('one li per item', (h.match(/<li>/g) || []).length === 2);
c.check('items escaped', h.includes('Inbox &lt;b&gt;new&lt;/b&gt;') && !h.includes('<b>new') && h.includes('&quot;AOC&quot;'));
c.check('version escaped', !_whatsNewHtml({ version: '<img>', items: ['x'] }).includes('<img>'));
c.check('has a close button', h.includes('_whatsNewClose()'));

c.finish();
