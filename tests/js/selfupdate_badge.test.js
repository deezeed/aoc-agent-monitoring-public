/* Tests _selfUpdateUpdateUI, extracted straight from monitor.py, against a
 * minimal fake DOM: a git checkout gets the "run git pull" badge, an
 * installer build (mode "release") gets a clickable badge that opens the
 * new installer's URL, and switching back resets the click handler. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, '_fmtEpochAgo'));
eval(extractFunction(src, '_selfUpdateUpdateUI'));

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
_selfUpdateUpdateUI({ stale: true, mode: 'release', latest: '1.0.200', installed: '1.0.150', url, checked_at: null });
c.check('release -> badge shown', badge.style.display === 'flex');
c.check('release -> label names the new version', lbl.textContent === 'UPDATE 1.0.200');
c.check('release -> title names both versions', badge.title.includes('1.0.200') && badge.title.includes('1.0.150'));
c.check('release -> clickable', badge.style.cursor === 'pointer' && typeof badge.onclick === 'function');
badge.onclick();
c.check('click opens the installer URL in a new tab', opened.length === 1 && opened[0][0] === url && opened[0][1] === '_blank');

// 3. git checkout -> old behavior, click handler cleared
_selfUpdateUpdateUI({ stale: true, behind_by: 3, checked_at: null });
c.check('git -> generic label', lbl.textContent === 'UPDATE AVAILABLE');
c.check('git -> git pull hint', badge.title.startsWith('3 commits behind origin/master'));
c.check('git -> not clickable', badge.onclick === null && badge.style.cursor === 'default');

c.finish();
