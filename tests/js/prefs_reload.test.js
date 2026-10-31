/* Tests _loadPrefs + _savePrefs, extracted straight from monitor.py.
 * _loadPrefs used to call setView() -- which itself calls _savePrefs() --
 * before restoring pinned agents, collapsed cards/bays and the collapsed
 * side panel, so it wrote them back to localStorage empty: they survived
 * one reload and were gone on the next. Loading must leave storage as it
 * found it, and collapsed session bays must round-trip. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
const c = new Checker();

// Globals the two functions touch, stubbed.
const store = {};
global.localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } };
const rootClasses = new Set();
global.document = {
  querySelector: () => ({ classList: { add: x => rootClasses.add(x) } }),
  getElementById: () => ({ textContent: '' }),
};
var currentView = 'agents', listMode = false, statusFilter = 'all', _soundMuted = false, _theme = 'dark';
var _collapsedIds = new Set(), _pinnedIds = new Set(), _collapsedBays = new Set(), _panelCollapsed = false;
var _vizView = 'timeline';
function _isVizView(v) { return ['timeline', 'graph', 'heat', 'tree'].includes(v); }
function _applyMuteUI() {}
function _applyTheme() {}
function setView(v) { currentView = v; _savePrefs(); }  // the real one saves too

eval(extractFunction(src, '_savePrefs'));
eval(extractFunction(src, '_loadPrefs'));

const saved = {
  view: 'history', list: false, sf: 'all', muted: false, theme: 'dark',
  collapsed: ['agent-1'], pinned: ['agent-2'], bays: ['|sess-1', 'laptop|sess-2'], panelCollapsed: true, today: 1, cc: 1,
};
store.aoc_prefs = JSON.stringify(saved);

_loadPrefs();
const after = JSON.parse(store.aoc_prefs);

c.check('view restored', currentView === 'history');
c.check('pinned restored in memory', _pinnedIds.has('agent-2'));
c.check('collapsed bays restored in memory', _collapsedBays.has('|sess-1') && _collapsedBays.has('laptop|sess-2'));
c.check('panel restored', _panelCollapsed === true && rootClasses.has('panel-collapsed'));
c.check('loading does not wipe stored pinned', JSON.stringify(after.pinned) === '["agent-2"]');
c.check('loading does not wipe stored collapsed cards', JSON.stringify(after.collapsed) === '["agent-1"]');
c.check('loading does not wipe stored bays', JSON.stringify(after.bays) === '["|sess-1","laptop|sess-2"]');
c.check('loading does not wipe stored panel state', after.panelCollapsed === true);

// COMMAND (Command Center) is the home view: no prefs -> Command; prefs
// saved before it existed (no cc flag) open on Command once, and from then
// on the saved view is respected again.
store.aoc_prefs = JSON.stringify({ view: 'cli', today: 1 });
_loadPrefs();
c.check('old prefs -> Command Center once', currentView === 'command' && JSON.parse(store.aoc_prefs).cc === 1);
store.aoc_prefs = JSON.stringify({ view: 'cli', today: 1, cc: 1 });
_loadPrefs();
c.check('...then the saved view again', currentView === 'cli');
delete store.aoc_prefs;
_loadPrefs();
c.check('first run -> Command Center', currentView === 'command');

// Recent palette actions round-trip (junk dropped, capped at 8).
var _paletteRecent = [];
store.aoc_prefs = JSON.stringify({ view: 'cli', today: 1, cc: 1, pal: ['view:today', 3, null, ...Array.from({ length: 10 }, (_, i) => 'x' + i)] });
_loadPrefs();
c.check('palette recents restored, junk dropped, capped', _paletteRecent.length === 8 && _paletteRecent[0] === 'view:today');
c.check('palette recents saved back', JSON.stringify(JSON.parse(store.aoc_prefs).pal) === JSON.stringify(_paletteRecent));

// Garbage in the bays list is dropped, not turned into keys.
store.aoc_prefs = JSON.stringify({ bays: ['ok|1', 7, null, { x: 1 }] });
_collapsedBays = new Set();
_loadPrefs();
c.check('non-string bay keys ignored', [..._collapsedBays].join() === 'ok|1');

// The sidebar's Views entry reopens the live view you used last (graph/heat/...)
store.aoc_prefs = JSON.stringify({ view: 'history', viz: 'heat', today: 1, cc: 1 });
_vizView = 'timeline';
_loadPrefs();
c.check('last live view restored', _vizView === 'heat' && JSON.parse(store.aoc_prefs).viz === 'heat');
store.aoc_prefs = JSON.stringify({ view: 'history', viz: 'nope', today: 1, cc: 1 });
_vizView = 'timeline';
_loadPrefs();
c.check('unknown saved live view ignored', _vizView === 'timeline');

c.finish();
