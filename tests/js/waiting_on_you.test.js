/* Tests the "waiting on you" pieces, extracted straight from monitor.py:
 * _lastMessageInfo (the tail of Claude's last message, question detection,
 * markdown stripped), _waitingSessions (who has waited >= N s, longest
 * first), the strip above the views and the quote on cards/bays -- incl.
 * that transcript text can't inject HTML. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_fmtDurationDHM', '_lastMessageInfo', '_waitLabel', '_waitingSessions', '_permAskHtml', '_woyQuoteHtml', '_waitingStripHtml'])
  eval(extractFunction(src, f));

const c = new Checker();

let i = _lastMessageInfo('Done. I pushed the fix.\n\nShould I also **publish** the release?');
c.check('question in the last paragraph -> asked, markdown stripped', i.asked && i.text === 'Should I also publish the release?');
i = _lastMessageInfo('Hotovo — všetko je pushnuté a CI je zelené.');
c.check('statement -> not asked', !i.asked && i.text.startsWith('Hotovo'));
c.check('empty -> null', _lastMessageInfo('') === null && _lastMessageInfo(null) === null);
i = _lastMessageInfo('x '.repeat(300) + 'which option do you want?', 60);
c.check('long text keeps its tail with a leading ellipsis', i.text.startsWith('…') && i.text.endsWith('which option do you want?') && i.text.length <= 61);
i = _lastMessageInfo('Run this:\n\n```\nnpm test\n```');
c.check('code fences dropped', !i.text.includes('npm test') && !i.text.includes('```'));
c.check('full text kept for the tooltip', _lastMessageInfo('a\n\nb?').full === 'a b?');

const S = (id, secs, extra = {}) => ({ id, display_name: 'S' + id, session_active: true, waiting_on_you: true, waiting_secs: secs, ...extra });
const list = [S('1', 900), S('2', 7200), S('3', 100), S('4', 5000, { session_active: false }), S('5', 4000, { waiting_on_you: false })];
const w = _waitingSessions(list, 600);
c.check('only active + waiting + >= threshold, longest first', w.map(s => s.id).join(',') === '2,1');
c.check('no list -> []', _waitingSessions(null, 600).length === 0);

c.check('strip empty when nobody waits', _waitingStripHtml([]) === '');
let h = _waitingStripHtml([S('2', 7200, { last_message: 'Mám pokračovať s <script>alert(1)</script> deployom?' }), S('1', 900)]);
c.check('strip head counts sessions', h.includes('2 WAITING ON YOU'));
c.check('strip shows wait time', h.includes('2h'));
c.check('strip shows the question, escaped', h.includes('❓') && h.includes('&lt;script&gt;') && !h.includes('<script>'));
c.check('strip item without a message still renders', (h.match(/class="woy-item"/g) || []).length === 2);
h = _waitingStripHtml([1, 2, 3, 4, 5, 6].map(n => S(String(n), 1000 * n)));
c.check('strip caps at 4 + "more"', (h.match(/class="woy-item"/g) || []).length === 4 && h.includes('+2 more'));

h = _woyQuoteHtml({ last_message: 'Which one should I keep?' });
c.check('quote: asked label', h.includes('Claude asks') && h.includes('woy-quote asked'));
h = _woyQuoteHtml({ last_message: 'All done.' });
c.check('quote: statement label', h.includes('Claude said') && !h.includes(' asked"'));
c.check('quote: nothing to show -> empty', _woyQuoteHtml({}) === '');

c.finish();
