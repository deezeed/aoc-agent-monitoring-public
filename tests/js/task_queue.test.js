/* Task queue UI, extracted from monitor.py: _queueBody turns the New session
 * form's "Start" choice into the POST /queue body; _queueHtml renders the
 * Queued card on Today (what each task waits for, escaped, cancel wired);
 * _todayHtml shows the card only when there is a queue. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_fmtDurationDHM', '_lastMessageInfo', '_waitLabel', '_waitingSessions', '_ctxMeterHtml',
                 '_permAskHtml', '_replyAskHtml', '_budgetBadgeHtml', '_loopBadgeHtml', '_awayBlockHtml', '_changesBtnHtml', '_replayBtnHtml', '_queueBody', '_queueHtml', '_todayHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
const Q = '&quot;';

// ── form -> body ──
c.check('needs a first message', _queueBody('C:/p', '  ', '', 'limits').error === 'A queued task needs a first message.');
let b = _queueBody('C:/p', 'Do it', 'sonnet', 'limits');
c.check('limits choice -> no time, limits on', b.at === null && b.limits === true && b.model === 'sonnet' && b.cwd === 'C:/p');
c.check('time choice without a time -> error', _queueBody('C:/p', 'x', '', 'at', '', true).error === 'Pick a start time.');
b = _queueBody('C:/p', 'x', '', 'at', '2026-10-14T02:30', false);
c.check('time choice -> epoch seconds of the local time, limits as ticked',
        b.at === Math.round(new Date(2026, 9, 14, 2, 30).getTime() / 1000) && b.limits === false);

// ── Queued card ──
const now = Math.round(new Date(2026, 9, 13, 20, 0).getTime() / 1000);
const T = (extra) => ({ id: 't1', cwd: 'C:\\Users\\m\\proj<x>', prompt: 'Fix "the" <bug>', model: '', at: null, limits: true,
  created: now - 60, state: 'queued', waiting_on: 'limits', ...extra });
let h = _queueHtml([T()], now);
c.check('folder name from the path, escaped', h.includes('>proj&lt;x&gt;<'));
c.check('prompt escaped', h.includes('Fix &quot;the&quot; &lt;bug&gt;') && !h.includes('<bug>'));
c.check('waits for plan limits', h.includes('waits for plan limits'));
c.check('Cancel wired with escaped id', h.includes(`_queueCancel(${Q}t1${Q})`) && h.includes('>Cancel<'));
h = _queueHtml([T({ waiting_on: 'time', at: now + 2 * 3600 + 1800, limits: true })], now);
c.check('timed: shows the clock + limits note', /at \d\d?:\d\d.*if limits allow/.test(h));
h = _queueHtml([T({ waiting_on: 'time', at: now + 3 * 86400, limits: false })], now);
c.check('another day: weekday shown, no limits note', !h.includes('if limits allow') && /at \S+ \d/.test(h));
h = _queueHtml([T({ state: 'launched', finished_at: now - 300, waiting_on: '' })], now);
c.check('launched: green "started", x removes', h.includes('q-when done') && h.includes('started ') && h.includes('>✕<'));
h = _queueHtml([T({ state: 'failed', error: 'Claude Code <not> found', waiting_on: '' })], now);
c.check('failed: red with escaped reason', h.includes('q-when bad') && h.includes('failed · Claude Code &lt;not&gt; found'));
h = _queueHtml([T({ model: 'haiku' })], now);
c.check('model shown', h.includes('>haiku<'));
c.check('quote in id cannot break out', !_queueHtml([T({ id: 'x"),alert(1)//' })], now).includes('x"),alert'));

// ── on Today ──
const sr = (queue) => ({ sessions_list: [], agents: [], queue });
c.check('no queue -> no Queued card', !_todayHtml(sr([]), {}, null, now, '2026-10-13', {}, 'auto').includes('>Queued'));
h = _todayHtml(sr([T(), T({ id: 't2', state: 'launched', finished_at: now, waiting_on: '' })]), {}, null, now, '2026-10-13', {}, 'auto');
c.check('queue -> Queued card counting only waiting tasks', h.includes('Queued <span class="td-count">1</span>') && h.includes('q-row'));

c.finish();
