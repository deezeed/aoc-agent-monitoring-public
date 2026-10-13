/* Hard budget cap flag, extracted from monitor.py: _budgetBadgeHtml shows a
 * session whose tool calls AOC denies (s.budget), with the numbers and a
 * continue button routed to the session's machine; Today rows carry it. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_fmtDurationDHM', '_lastMessageInfo', '_waitLabel', '_waitingSessions', '_ctxMeterHtml',
                 '_permAskHtml', '_replyAskHtml', '_budgetBadgeHtml', '_loopBadgeHtml', '_awayBlockHtml', '_changesBtnHtml', '_replayBtnHtml',
                 '_queueHtml', '_todayHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
const Q = '&quot;';
c.check('no cap -> empty', _budgetBadgeHtml({}) === '' && _budgetBadgeHtml(null) === '' && _budgetBadgeHtml({ budget: {} }) === '');

let h = _budgetBadgeHtml({ id: 's1', machine: 'laptop', budget: { kind: 'session', spent: 5.2, cap: 5 } });
c.check('session cap: numbers', h.includes('$5.20 of its $5.00 cap') && h.includes('tools blocked'));
c.check('session cap: Let it continue for this machine + session', h.includes(`_budgetContinue(${Q}laptop${Q},${Q}s1${Q},false)`) && h.includes('>Let it continue<'));
c.check('clicks do not open the card', h.includes('onclick="event.stopPropagation()"'));
h = _budgetBadgeHtml({ id: 's2', budget: { kind: 'day', spent: 41.5, cap: 40 } });
c.check('daily cap: wording + lift for today', h.includes('Today $41.50 of the $40.00 daily cap') && h.includes(`${Q}s2${Q},true)`) && h.includes('>Lift for today<'));
h = _budgetBadgeHtml({ id: 'x"),alert(1)//', budget: { kind: 'session', spent: 1, cap: 1 } });
c.check('quote in id cannot break out', !h.includes('x"),alert'));

const now = 1791900000;
const sess = { id: 's1', project: 'p', display_name: 'Demo', session_active: true, waiting_on_you: true, waiting_kind: 'turn',
  waiting_secs: 900, last_message: 'Stopped: budget cap.', budget: { kind: 'session', spent: 6, cap: 5 } };
h = _todayHtml({ sessions_list: [sess], agents: [] }, {}, null, now, '2026-10-13', {}, 'auto');
c.check('Today row shows the cap flag', h.includes('budget-flag') && h.includes('$6.00 of its $5.00 cap'));
c.finish();
