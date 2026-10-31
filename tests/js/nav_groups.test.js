/* Navigation cleanup: History's tabs grouped by purpose (_HIST_TABS +
 * _HIST_GROUPS, drawn by _histTabsHtml), and Timeline/Graph/Heat/Tree
 * behind one sidebar entry with a switcher (_VIZ_VIEWS, _vizSwitchHtml).
 * Tab keys and view names must not change: other views link to them
 * (_histTab='commits';setView('history')), hotkeys call setView('graph'),
 * and saved prefs hold them. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
const c = new Checker();

function sliceConst(name, open, close) {
  const i = src.indexOf(`const ${name}=${open}`);
  if (i < 0) throw new Error('missing const ' + name);
  const j = src.indexOf(close, i);
  return src.slice(i, j + close.length).replace(/^const /, 'var ');
}

function escHtml(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }
eval(extractFunction(src, 'jsq'));
eval(sliceConst('_HIST_TABS', '{', '\n};'));
eval(sliceConst('_HIST_GROUPS', '[', '\n];'));
eval(sliceConst('_VIZ_VIEWS', '[', '];'));
var _histTab = 'sessions';
var _histGroupLast = {};
let renders = 0;
function renderHistory() { renders++; }
for (const n of ['_histGroupOf', '_histTabsHtml', '_histPick', '_histPickGroup', '_isVizView', '_vizSwitchHtml'])
  eval(extractFunction(src, n));

// --- groups cover every tab exactly once ---
const keys = Object.keys(_HIST_TABS);
const grouped = _HIST_GROUPS.flatMap(g => g[3]);
c.check('every tab is in exactly one group', keys.every(k => grouped.filter(x => x === k).length === 1));
c.check('groups only name real tabs', grouped.every(k => k in _HIST_TABS));
c.check('three groups, none empty', _HIST_GROUPS.length === 3 && _HIST_GROUPS.every(g => g[3].length > 0));

// --- every tab key the page uses still exists ---
const used = new Set();
for (const m of src.matchAll(/_histTab\s*={1,3}\s*\\?'([a-z]+)\\?'/g)) used.add(m[1]);
const unknown = [...used].filter(k => !(k in _HIST_TABS));
c.check('all _histTab keys used in the page are tabs (' + unknown.join(',') + ')', unknown.length === 0 && used.size >= 10);
c.check('old tab keys kept', ['sessions', 'search', 'analytics', 'cache', 'commits', 'clients', 'models', 'context', 'failures', 'permissions', 'memory'].every(k => k in _HIST_TABS));

// --- _histTabsHtml ---
let h = _histTabsHtml('failures');
c.check('open group marked', /hg-tab active"[^>]*>TUNE UP</.test(h));
c.check('open tab marked', /adp-tab active"[^>]*>FAILURES</.test(h));
c.check('only the open group\'s tabs drawn', h.includes('PERMISSIONS') && !h.includes('>COMMITS<') && !h.includes('>SESSIONS<'));
c.check('unknown tab falls back to the first group', _histGroupOf('nope')[0] === 'past' && _histTabsHtml('nope').includes('>SESSIONS<'));
c.check('tab tooltips escaped', !/title="[^"]*'[^"]*"/.test(_histTabsHtml('models')));

// --- picking ---
_histPick('commits');
c.check('pick sets tab + renders', _histTab === 'commits' && renders === 1);
_histPickGroup('past');
c.check('new group opens its first tab', _histTab === 'sessions');
_histPickGroup('spend');
c.check('group reopens the tab you left it on', _histTab === 'commits');
_histPickGroup('bogus');
c.check('unknown group ignored', _histTab === 'commits' && renders === 3);

// --- live views ---
c.check('four live views', _VIZ_VIEWS.map(v => v[0]).join() === 'timeline,graph,heat,tree');
c.check('_isVizView', _isVizView('heat') && !_isVizView('history') && !_isVizView(undefined));
const s = _vizSwitchHtml('graph');
c.check('switcher marks the current view', /viz-tab active"[^>]*>Graph</.test(s) && (s.match(/viz-tab active/g) || []).length === 1);
c.check('switcher buttons call setView with the old names', ['timeline', 'graph', 'heat', 'tree'].every(v => s.includes(`setView(&quot;${v}&quot;)`)));

// --- page wiring ---
c.check('sidebar has one Views entry', src.includes('id="vt-views"') && !/id="vt-(tl|graph|heat|tree)"/.test(src));
c.check('hotkeys still open each live view', ["setView('timeline')", "setView('graph')", "setView('heat')", "setView('tree')"].every(x => src.includes(`: ${x}`) || src.includes(x)));
c.check('every live view keeps its area', ['timeline', 'graph', 'heat', 'tree'].every(v => src.includes(`id="${v}-area"`)));
c.check('switcher container present', src.includes('id="viz-switch"'));
c.check('renderHistory draws the grouped tabs', extractFunction(src, 'renderHistory').includes('_histTabsHtml(_histTab)'));

c.finish();
