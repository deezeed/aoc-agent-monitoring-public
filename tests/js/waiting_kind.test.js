/* Tests the "needs your OK" pieces, extracted from monitor.py: _waitLabel
 * (turn / permission / question), _waitingSessions putting blocked sessions
 * first and listing them after 30 s, the quote showing the permission
 * message, and the strip switching to the terminal for local sessions. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_fmtDurationDHM', '_lastMessageInfo', '_waitLabel', '_waitingSessions', '_woyQuoteHtml', '_waitingStripHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
const S = (id, secs, extra = {}) => ({ id, display_name: 'S' + id, session_active: true, waiting_on_you: true,
  waiting_secs: secs, waiting_kind: 'turn', host_pid: 100, ...extra });

c.check('turn -> WAITING, not blocked', _waitLabel(S('1', 0)).word === 'WAITING' && !_waitLabel(S('1', 0)).blocked);
c.check('missing kind -> WAITING', _waitLabel({ waiting_on_you: true }).word === 'WAITING');
const p = _waitLabel(S('1', 0, { waiting_kind: 'permission', waiting_message: 'Claude needs your permission to use Bash' }));
c.check('permission -> NEEDS OK, blocked, message in tip', p.word === 'NEEDS OK' && p.blocked && p.tip.includes('use Bash'));
c.check('question -> QUESTION, blocked', _waitLabel(S('1', 0, { waiting_kind: 'question' })).word === 'QUESTION');

const list = [S('a', 900), S('b', 45, { waiting_kind: 'permission' }), S('c', 20, { waiting_kind: 'permission' }), S('d', 100)];
c.check('blocked first, listed after 30 s; turns after 600 s', _waitingSessions(list, 600).map(s => s.id).join() === 'b,a');
c.check('lower threshold than 30 keeps it', _waitingSessions(list, 10).map(s => s.id).join() === 'b,c,a,d');

let h = _woyQuoteHtml(S('1', 0, { waiting_kind: 'permission', waiting_message: 'Claude needs your permission to use <Bash>', last_message: 'Running tests.' }));
c.check('permission quote shows the message, escaped', h.includes('Needs your OK') && h.includes('&lt;Bash&gt;') && !h.includes('Running tests'));
h = _woyQuoteHtml(S('1', 0, { last_message: 'Shall I push?' }));
c.check('turn quote unchanged', h.includes('Claude asks') && h.includes('Shall I push?'));

h = _waitingStripHtml([S('b', 45, { waiting_kind: 'permission', waiting_message: 'Allow Bash?' })]);
c.check('strip: local session -> focus its terminal', h.includes('_focusSession(&quot;b&quot;)') && h.includes('🔐') && h.includes('blocked'));
h = _waitingStripHtml([S('r', 900, { _isLocal: false })]);
c.check('strip: remote session -> CLI view', h.includes("setView('cli')") && !h.includes('_focusSession'));
h = _waitingStripHtml([S('n', 900, { host_pid: null })]);
c.check('strip: no host pid -> CLI view', !h.includes('_focusSession'));

c.finish();
