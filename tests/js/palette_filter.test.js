/* Tests _paletteFilter, extracted straight from monitor.py. This is the
 * command palette's matching logic -- a substring match (case-
 * insensitive) over label+sub, capped to 8 results. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, '_paletteFilter'));

const c = new Checker();

const candidates = [
  { label: 'AOC', sub: 'Session' },
  { label: 'PHANTOM AI', sub: 'Session' },
  { label: 'Security audit of backend', sub: 'Agent · running' },
  { label: 'Settings → Cost', sub: 'Settings' },
  { label: 'Settings → Notify', sub: 'Settings' },
  { label: 'View → Cards', sub: 'View' },
  { label: 'View → Timeline', sub: 'View' },
];

// 1. empty query returns the first 8 candidates unfiltered
c.check('empty query returns candidates as-is (capped to 8)', _paletteFilter(candidates, '').length === candidates.length);
c.check('empty query preserves original order', _paletteFilter(candidates, '')[0].label === 'AOC');

// 2. substring match on label, case-insensitive
const r1 = _paletteFilter(candidates, 'phantom');
c.check('case-insensitive label match finds the right entry', r1.length === 1 && r1[0].label === 'PHANTOM AI');

// 3. substring match on sub too
const r2 = _paletteFilter(candidates, 'settings');
c.check('matching on sub field finds both settings entries', r2.length === 2);

// 4. no match returns empty array
c.check('no match returns an empty array', _paletteFilter(candidates, 'xyz-nonexistent').length === 0);

// 5. partial word match works (substring, not whole-word)
const r3 = _paletteFilter(candidates, 'view');
c.check('substring match on "view" finds both view entries', r3.length === 2);

// 6. result cap: more than 8 matches still returns at most 8
const many = Array.from({ length: 20 }, (_, i) => ({ label: `Item ${i}`, sub: 'Test' }));
c.check('results are capped at 8 even with many matches', _paletteFilter(many, 'item').length === 8);

// 7. whitespace-only query behaves like empty query
c.check('whitespace-only query treated as empty', _paletteFilter(candidates, '   ').length === candidates.length);

c.finish();
