/* Tests _filterErrors, extracted straight from monitor.py. The ERRORS
 * right-panel tab never had a text search before -- this is the pure
 * matching logic behind it: case-insensitive substring match across
 * error_msg/name/project, same "trim + lowercase, empty query = no-op"
 * shape _paletteFilter already uses. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, '_filterErrors'));

const c = new Checker();

const errs = [
  { error_msg: 'Timeout waiting for response', name: 'Explore repo', project: 'AOC' },
  { error_msg: 'FileNotFoundError: config.yaml', name: 'Load config', project: 'PHANTOM AI' },
  { error_msg: 'Connection refused', name: 'API check', project: 'AOC' },
];

// 1. empty query -> unfiltered, original order preserved
c.check('empty query returns all errors unfiltered', _filterErrors(errs, '').length === 3);
c.check('empty query preserves original order', _filterErrors(errs, '')[0].name === 'Explore repo');

// 2. matches on error_msg, case-insensitive
const r1 = _filterErrors(errs, 'timeout');
c.check('case-insensitive match on error_msg', r1.length === 1 && r1[0].name === 'Explore repo');

// 3. matches on name
const r2 = _filterErrors(errs, 'api check');
c.check('match on name field', r2.length === 1 && r2[0].error_msg === 'Connection refused');

// 4. matches on project -- finds every error in that project, not just one
const r3 = _filterErrors(errs, 'aoc');
c.check('match on project field finds all errors in that project', r3.length === 2);

// 5. no match -> empty array
c.check('no match returns an empty array', _filterErrors(errs, 'xyz-nonexistent').length === 0);

// 6. whitespace-only query behaves like empty query
c.check('whitespace-only query treated as empty', _filterErrors(errs, '   ').length === 3);

// 7. missing fields on a row don't crash the match (defensive .toLowerCase on undefined)
const sparse = [{ error_msg: 'Some error' }, { name: 'Named only' }, {}];
c.check('rows with missing fields are handled without crashing', _filterErrors(sparse, 'error').length === 1);
c.check('empty query on sparse rows still returns everything', _filterErrors(sparse, '').length === 3);

c.finish();
