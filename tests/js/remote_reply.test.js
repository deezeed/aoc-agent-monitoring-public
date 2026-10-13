/* Reply from the dashboard, extracted from monitor.py: _replyAskHtml shows a
 * finished turn AOC is holding (s.reply_request) with Reply… / Done for now,
 * escaped and routed to the session's machine; the waiting quote uses it,
 * and a held permission prompt still wins over it. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_fmtDurationDHM', '_lastMessageInfo', '_waitLabel', '_permAskHtml', '_replyAskHtml', '_woyQuoteHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
const Q = '&quot;';
const S = (extra = {}) => ({ id: 's1', machine: 'laptop', session_active: true, waiting_on_you: true, waiting_kind: 'turn',
  last_message: 'Done.', perm_requests: [], reply_request: null, ...extra });
const R = (message, id = 'r1') => ({ id, message, age_s: 40 });

c.check('nothing held -> empty', _replyAskHtml(S()) === '' && _replyAskHtml({}) === '' && _replyAskHtml(null) === '');

let h = _replyAskHtml(S({ reply_request: R('All tests pass.\n\nShall I push <now>?') }));
c.check('question -> "Claude asks"', h.includes('Claude asks · reply from here'));
c.check('last paragraph shown, escaped', h.includes('Shall I push &lt;now&gt;?') && !h.includes('<now>') && !h.includes('All tests pass'));
c.check('Reply… opens the form for this machine + session', h.includes(`_replyOpen(${Q}laptop${Q},${Q}s1${Q})`) && h.includes('>Reply…<'));
c.check('Done for now releases this request', h.includes(`_replySend(${Q}laptop${Q},${Q}r1${Q},'done')`) && h.includes('>Done for now<'));
c.check('clicks do not open the card', h.includes('onclick="event.stopPropagation()"'));

h = _replyAskHtml(S({ reply_request: R('Pushed to master.') }));
c.check('statement -> "Turn finished"', h.includes('Turn finished · reply from here'));
h = _replyAskHtml(S({ reply_request: R('', 'r2'), last_message: 'Fallback text?' }));
c.check('empty held message -> falls back to last_message', h.includes('Fallback text?'));
h = _replyAskHtml(S({ reply_request: R('', 'r3'), last_message: '' }));
c.check('no text at all -> buttons only', !h.includes('reply-sum') && h.includes('>Reply…<'));
h = _replyAskHtml(S({ reply_request: R('ok', 'x"),alert(1)//') }));
c.check('quote in id cannot break out of the attribute', !h.includes('x"),alert') && h.includes('x\\&quot;),alert'));

h = _woyQuoteHtml(S({ reply_request: R('Go on?') }));
c.check('waiting quote becomes the reply box', h.includes('reply-ask') && h.includes('>Reply…<'));
h = _woyQuoteHtml(S({ reply_request: R('Go on?'), perm_requests: [{ id: 'p1', tool_name: 'Bash', summary: 'Bash: ls', detail: '', age_s: 1 }] }));
c.check('a held permission prompt wins', h.includes('>Allow<') && !h.includes('reply-ask'));

c.finish();
