/* Tests _convResultsHtml / _convSnippetHtml, extracted straight from
 * monitor.py: index status lines, empty/no-match states, session cards,
 * match marks, and that transcript text can never inject HTML. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, 'escHtml'));
eval(extractFunction(src, 'jsq'));
eval(extractFunction(src, '_convSnippetHtml'));
eval(extractFunction(src, '_convResultsHtml'));

const c = new Checker();
const NOW = Date.parse('2026-10-06T12:00:00Z');
const idx = { files: 40, indexed: 40, messages: 3930, building: false, updated_at: 1 };

c.check('snippet: marks swapped in after escaping',
  _convSnippetHtml('a \x02<b>bug</b>\x03 here') === 'a <mark>&lt;b&gt;bug&lt;/b&gt;</mark> here');
c.check('snippet: newlines flattened', _convSnippetHtml('one\n\ntwo') === 'one two');

let h = _convResultsHtml({ query: '', sessions: [], index: idx }, NOW);
c.check('empty query: hint + index size', h.includes('Type words you remember') && h.includes('3,930 messages from 40 conversations'));
h = _convResultsHtml({ query: 'x', sessions: [], index: { files: 40, indexed: 12, building: true } }, NOW);
c.check('building: progress + "still indexing" on no match', h.includes('12 of 40') && h.includes('still indexing'));
h = _convResultsHtml({ query: 'x', sessions: [], index: {} }, NOW);
c.check('index not started yet: says so', h.includes('built a few minutes after AOC starts'));
h = _convResultsHtml({ query: '<img src=x onerror=alert(1)>', sessions: [], index: idx }, NOW);
c.check('no match: query echoed escaped', h.includes('&lt;img') && !h.includes('<img'));
h = _convResultsHtml({ query: 'x', error: 'search failed', index: idx }, NOW);
c.check('error state', h.includes('Search failed'));

const sess = {
  session_id: 'abc12345-0000', title: 'Checkout <script>alert(1)</script>', project: 'shop',
  cwd: 'C:\\work\\shop', first_ts: '2026-10-05T08:00:00Z', last_ts: '2026-10-05T09:00:00Z', n_hits: 5,
  hits: [{ role: 'user', ts: '2026-10-05T08:00:00Z', snippet: 'why does \x02checkout\x03 fail' },
         { role: 'assistant', ts: '2026-10-05T08:01:00Z', snippet: 'the \x02checkout\x03 webhook…' }],
};
h = _convResultsHtml({ query: 'checkout', sessions: [sess], index: idx }, NOW);
c.check('title escaped, no script tag', h.includes('Checkout &lt;script&gt;') && !h.includes('<script>'));
c.check('project shown', h.includes('// shop'));
c.check('relative day', h.includes('yesterday'));
c.check('roles labelled', h.includes('>YOU<') && h.includes('>CLAUDE<'));
c.check('matches marked', (h.match(/<mark>checkout<\/mark>/g) || []).length === 2);
c.check('"+3 more matches"', h.includes('+3 more matches'));
c.check('open + resume buttons carry id and cwd via jsq',
  h.includes(`showHistoryDetail(${jsq('abc12345-0000')})`) && h.includes(`_copyResumeCmd(${jsq('abc12345-0000')},${jsq('C:\\work\\shop')})`));

h = _convResultsHtml({ query: 'q', sessions: [{ session_id: 'deadbeef-1', title: '', project: '', cwd: '/home/me/proj-x', last_ts: '', n_hits: 1,
  hits: [{ role: 'user', ts: '', snippet: 'q' }] }], index: idx }, NOW);
c.check('no title/project -> cwd folder as title, no where-line', h.includes('>proj-x<') && !h.includes('// '));
h = _convResultsHtml({ query: 'q', sessions: [{ session_id: 'deadbeef-1', n_hits: 1, hits: [{ role: 'user', snippet: 'q' }] }], index: idx }, NOW);
c.check('nothing at all -> short session id', h.includes('>deadbeef<'));

c.finish();
