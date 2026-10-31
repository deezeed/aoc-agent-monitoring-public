/* Tests _paletteFilter, extracted straight from monitor.py. The command
 * palette's matching: case- and diacritic-insensitive, every query word must
 * match (label, then sub/extra words, then letters in order), best first,
 * capped (12 by default). Ranking details live in palette_actions.test.js. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
const _PALETTE_LIMIT = 12;
for (const f of ['_paletteNorm', '_paletteScore', '_paletteFilter']) eval(extractFunction(src, f));

const c = new Checker();

const candidates = [
  { id: 'a', label: 'AOC', sub: 'Session' },
  { id: 'b', label: 'PHANTOM AI', sub: 'Session' },
  { id: 'c', label: 'Security audit of backend', sub: 'Agent · running' },
  { id: 'd', label: 'Settings → Cost', sub: 'Settings' },
  { id: 'e', label: 'Settings → Notify', sub: 'Settings' },
  { id: 'f', label: 'View → Cards', sub: 'View' },
  { id: 'g', label: 'View → Timeline', sub: 'View' },
];

c.check('empty query returns candidates as-is', _paletteFilter(candidates, '').length === candidates.length);
c.check('empty query preserves original order', _paletteFilter(candidates, '')[0].label === 'AOC');
const r1 = _paletteFilter(candidates, 'phantom');
c.check('case-insensitive label match finds the right entry', r1.length === 1 && r1[0].label === 'PHANTOM AI');
c.check('settings: both settings entries first', _paletteFilter(candidates, 'settings').slice(0, 2).map(x => x.id).join() === 'd,e');
c.check('no match returns an empty array', _paletteFilter(candidates, 'xyz-nonexistent').length === 0);
c.check('"view" finds both view entries first', _paletteFilter(candidates, 'view').slice(0, 2).map(x => x.id).join() === 'f,g');
const many = Array.from({ length: 20 }, (_, i) => ({ id: 'i' + i, label: `Item ${i}`, sub: 'Test' }));
c.check('results are capped at 12', _paletteFilter(many, 'item').length === 12);
c.check('explicit limit', _paletteFilter(many, 'item', [], 5).length === 5);
c.check('whitespace-only query treated as empty', _paletteFilter(candidates, '   ').length === candidates.length);
c.check('sub matches too', _paletteFilter(candidates, 'running')[0].id === 'c');

c.finish();
