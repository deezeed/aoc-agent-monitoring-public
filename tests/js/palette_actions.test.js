/* Ctrl+K palette actions (extracted from monitor.py): _paletteItems builds
 * plain-data items (session actions, permission allow/deny, project
 * continue/resume, one-click fixes, views, History tabs, settings),
 * _paletteFilter ranks them (diacritics folded, fuzzy, recent first),
 * _paletteRemember caps the recent list, _paletteListHtml escapes, and every
 * item's run[0] names a real entry of _PALETTE_ACTIONS. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
const _PALETTE_RECENT_MAX = 8, _PALETTE_LIMIT = 12, _PALETTE_SOURCES = [];
for (const f of ['escHtml', '_paletteItems', '_paletteNorm', '_paletteScore', '_paletteFilter', '_paletteRemember', '_paletteListHtml'])
  eval(extractFunction(src, f));
const icoM = src.match(/const _PALETTE_ICON=(\{[^\n]*\});/);
const _PALETTE_ICON = eval('(' + icoM[1] + ')');
const actM = src.match(/const _PALETTE_ACTIONS=\{([\s\S]*?)\n\};/);
const actionNames = new Set([...actM[1].matchAll(/^\s*(\w+):/gm)].map(m => m[1]));

const c = new Checker();
const sr = {
  sessions_list: [
    { id: 's1', display_name: 'AOC pokračovanie', project: 'aoc', session_active: true, host_pid: 11, edited_count: 3,
      perm_requests: [{ id: 'p1', tool_name: 'Bash', summary: 'git push <origin>' }] },
    { id: 's2', display_name: 'Old one', session_active: false },
    { id: 's3', display_name: 'Laptop work', machine: 'laptop', session_active: true },
  ],
  agents: [{ id: 'a1', name: 'Explore', status: 'running' }, { id: 'hook_x', name: 'hidden' }],
};
const ctx = {
  local: true,
  resume: [{ name: 'Memebot', cwd: 'C:\\p\\meme', prompt: 'pokračuj v Memebot', resume_id: 'r1', open: [] },
           { name: 'AOC', cwd: 'C:\\p\\aoc', prompt: 'pokračuj v AOC', resume_id: 'r2', open: ['s1'] }],
  fixes: [{ title: 'Turn off Vercel plugin', fix: { kind: 'plugin', target: 'vercel' } },
          { title: 'Manual one', fix: { kind: 'manual', steps: 'x' } },
          { title: 'Done already', fix: { kind: 'env', target: 'PYTHONIOENCODING' } }],
  applied: { 'env:PYTHONIOENCODING': { id: 'f1' } },
};
const items = _paletteItems(sr, ctx);
const ids = new Set(items.map(i => i.id));
const byId = id => items.find(i => i.id === id);
c.check('ids unique', ids.size === items.length);
c.check('every run names a real action', items.every(i => actionNames.has(i.run[0])));
c.check('every kind has an icon', items.every(i => _PALETTE_ICON[i.kind]));
c.check('permission allow/deny, urgent', byId('perm:allow:|s1:p1').run.join() === 'perm,,p1,allow' && byId('perm:deny:|s1:p1').urgent);
c.check('local open session -> terminal focus; remote -> none', ids.has('sess:focus:|s1') && !ids.has('sess:focus:laptop|s3'));
c.check('replay carries machine', byId('sess:replay:laptop|s3').run.join() === 'replay,s3,laptop,Laptop work');
c.check('changes only with edits', ids.has('sess:changes:|s1') && !ids.has('sess:changes:laptop|s3'));
c.check('closed -> dismiss, open -> force stop', ids.has('sess:dismiss:|s2') && !ids.has('sess:stop:|s2') && ids.has('sess:stop:|s1') && ids.has('sess:stop:laptop|s3'));
c.check('hook_ agents hidden', ids.has('agent:a1') && !ids.has('agent:hook_x'));
c.check('project: continue / switch when open', byId('resume:go:Memebot').run.join() === 'launch,C:\\p\\meme,pokračuj v Memebot' && byId('resume:switch:AOC').run.join() === 'focusSession,s1' && !ids.has('resume:go:AOC'));
c.check('project: reopen last conversation', byId('resume:last:Memebot').run.join() === 'launchResume,C:\\p\\meme,r1');
c.check('fixes: not manual, not applied', ids.has('fix:plugin:vercel') && ![...ids].some(i => i.startsWith('fix:manual') || i.startsWith('fix:env')));
c.check('fixes hidden off this PC', !_paletteItems(sr, { ...ctx, local: false }).some(i => i.kind === 'fix'));
c.check('history tabs incl. memory/permissions', ['hist:cache', 'hist:commits', 'hist:clients', 'hist:models', 'hist:context', 'hist:failures', 'hist:permissions', 'hist:memory'].every(i => ids.has(i)));
c.check('settings + views + actions', ids.has('settings:notify') && ids.has('view:command') && ids.has('act:new') && ids.has('act:theme') && ids.has('act:focus'));
c.check('no ctx / no status -> still the static items', _paletteItems(null, null).some(i => i.id === 'act:new') && !_paletteItems(null, null).some(i => i.kind === 'project'));

const F = (q, recent) => _paletteFilter(items, q, recent).map(i => i.id);
c.check('empty query: recent, then urgent', F('', ['view:today']).slice(0, 3).join() === 'view:today,perm:allow:|s1:p1,perm:deny:|s1:p1');
c.check('empty query capped', F('').length === 12);
c.check('diacritics folded', F('pokracuj memebot')[0] === 'resume:go:Memebot' && F('AOC pokračovanie').includes('sess:replay:|s1'));
c.check('word start beats middle', F('cache')[0] === 'hist:cache');
c.check('multi-word: all must match', F('replay laptop')[0] === 'sess:replay:laptop|s3' && F('replay zzz').length === 0);
c.check('fuzzy letters in order', F('hstmem').includes('hist:memory'));
c.check('extra words searchable', F('kill').includes('sess:stop:|s1'));
c.check('recent boosts ties', F('settings', ['settings:tunnel'])[0] === 'settings:tunnel');
c.check('no match -> empty', F('qqqqxxxx').length === 0);

let r = [];
for (let i = 0; i < 12; i++) r = _paletteRemember(r, 'x' + i);
r = _paletteRemember(r, 'x5');
c.check('remember: newest first, unique, capped', r.length === 8 && r[0] === 'x5' && r.filter(x => x === 'x5').length === 1 && r[1] === 'x11');
c.check('remember: junk dropped', _paletteRemember([1, null, 'a'], 'b').join() === 'b,a');

const h = _paletteListHtml([byId('perm:allow:|s1:p1'), byId('sess:stop:|s1')], 1, ['sess:stop:|s1']);
c.check('list escapes labels', h.includes('git push &lt;origin&gt;') && !h.includes('<origin>'));
c.check('list: active row, kinds, recent tag', h.includes('k-permission') && /k-danger active/.test(h) && h.includes('pi-recent'));
c.check('list: empty', _paletteListHtml([], 0, []).includes('No matches'));

_PALETTE_SOURCES.push(() => [{ id: 'inbox:x', label: 'Open inbox', run: ['view', 'inbox'] }, { label: 'bad' }]);
c.check('extra sources plug in (bad items dropped)', _paletteItems(sr, ctx).filter(i => i.id === 'inbox:x' || i.label === 'bad').map(i => i.id).join() === 'inbox:x');
c.finish();
