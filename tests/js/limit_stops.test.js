/* Stopped by the usage limit (_limitStopRowHtml / _limitStopsHtml, the Inbox
 * 'limit' kind and the Today section, extracted from monitor.py): Continue
 * only once the limit reset and the session is closed, an explicit opt-in
 * "Auto-continue at <reset>" before it, "type pokračuj there" for a session
 * still open in its terminal, escaping, and no duplicate Inbox card for an
 * open session. Times are compared against the same locale formatting the
 * page uses (no 24h / LF assumptions). */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(src.match(/const _INBOX_KINDS=\{[\s\S]*?\};/)[0].replace(/^const /, 'var '));
eval(src.match(/const _LIMIT_PROMPT='[^']*';/)[0].replace(/^const /, 'var '));
for (const f of ['escHtml', 'jsq', '_fmtDurationDHM', '_lastMessageInfo', '_waitLabel', '_permAskHtml', '_replyAskHtml',
                 '_budgetBadgeHtml', '_loopBadgeHtml', '_ctxSaveHtml', '_ctxMeterHtml', '_inboxItems', '_inboxHtml',
                 '_bgStrandedLeft', '_bgAgentsHtml', '_limitClock', '_limitStopRowHtml', '_limitStopsHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
const now = 1_800_000_000;
const clock = s => new Date(s * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
const L = (id, o) => Object.assign({ id, cwd: 'C:\\proj\\shop', title: '', kind: 'five_hour', resets_at: now + 1800, at: now - 600,
                                     ready: false, open: false, auto: false, task: null }, o);

c.check('prompt is pokračuj', _LIMIT_PROMPT === 'pokračuj');
let h = _limitStopRowHtml(L('a'), now);
c.check('before reset: status names the reset time', h.includes('Stopped by usage limit — resets ' + escHtml(clock(now + 1800))));
c.check('before reset: no Continue, an opt-in Auto-continue at <reset> instead',
  !h.includes(`'continue')`) && h.includes(`_limitStopAct(${jsq('a')},'auto_on')`) && h.includes('Auto-continue at ' + escHtml(clock(now + 1800))));
c.check('folder name when no title, window label', h.includes('>shop<') && h.includes('5-hour'));
c.check('always a dismiss', h.includes(`_limitStopAct(${jsq('a')},'dismiss')`));

h = _limitStopRowHtml(L('b', { ready: true, title: 'Shop <redesign>', kind: 'seven_day' }), now);
c.check('after reset: Continue + ready wording', h.includes(`_limitStopAct(${jsq('b')},'continue')`) && h.includes('ready to continue') && h.includes('ls-row ready'));
c.check('after reset: no auto-continue toggle', !h.includes("'auto_on'"));
c.check('title escaped, weekly label', h.includes('Shop &lt;redesign&gt;') && h.includes('weekly'));

h = _limitStopRowHtml(L('c', { auto: true }), now);
c.check('auto on: says it continues by itself and costs plan, offers cancel',
  h.includes('Continues by itself at ' + escHtml(clock(now + 1800))) && h.includes('uses your plan') && h.includes(`'auto_off'`) && !h.includes("'auto_on'"));
h = _limitStopRowHtml(L('c2', { auto: true, resets_at: null }), now);
c.check('auto on, reset unknown: once the limit allows', h.includes('once the limit allows') && !h.includes('resets '));

h = _limitStopRowHtml(L('d', { open: true, ready: true }), now);
c.check('open in its terminal: Terminal + type it there, never Continue / auto',
  h.includes(`_focusSession(${jsq('d')})`) && h.includes('type <b>pokračuj</b> there') && !h.includes("'continue')") && !h.includes("'auto_on'"));
c.check('open before reset: "after the reset"', _limitStopRowHtml(L('e', { open: true }), now).includes('there after the reset'));

h = _limitStopRowHtml(L("x'\"<y>"), now);
c.check('ids go through jsq in handlers', h.includes(`_limitStopAct(${jsq("x'\"<y>")},'dismiss')`) && !h.includes(`'x'"<y>'`));
const later = now + 3 * 86400;
c.check('reset on another day shows the weekday', _limitStopRowHtml(L('w', { resets_at: later }), now)
  .includes(escHtml(new Date(later * 1000).toLocaleDateString([], { weekday: 'short' }) + ' ' + clock(later))));
c.check('list = one row each', (_limitStopsHtml([L('1'), L('2')], now).match(/class="ls-row/g) || []).length === 2 && _limitStopsHtml(null, now) === '');

/* Inbox */
const S = (id, o) => Object.assign({ id, display_name: id, session_active: true }, o);
const sr = {
  sessions_list: [S('open-stop', { waiting_on_you: true, waiting_secs: 120, last_message: "You've hit your session limit" }),
                  S('ask', { waiting_on_you: true, waiting_secs: 60, last_message: 'Should I push it?' }),
                  S('plain', { waiting_on_you: true, waiting_secs: 900, last_message: 'Done.' })],
  limit_stops: [L('open-stop', { open: true }), L('closed-stop', { title: 'Memebot', at: now - 3600 })],
};
const items = _inboxItems(sr, now);
const kinds = items.map(i => (i.s ? i.s.id : i.ls.id) + ':' + i.kind);
c.check('inbox: open stopped session becomes a limit card (not plain waiting), closed one added; order after questions',
  JSON.stringify(kinds) === JSON.stringify(['ask:question', 'closed-stop:limit', 'open-stop:limit', 'plain:waiting']));
h = _inboxHtml(items, now);
c.check('inbox: one card per item, limit cards carry the row', (h.match(/class="td-card ib-item/g) || []).length === 4
  && (h.match(/ib-limit/g) || []).length === 2 && h.includes('Memebot') && h.includes('1h 0m ago'));
c.check('inbox: count includes stops', h.includes('4 things need you'));
c.check('inbox without limit_stops unchanged', _inboxItems({ sessions_list: sr.sessions_list }, now).every(i => i.kind !== 'limit'));

/* Today: the section sits in the left column (source check -- the view needs many helpers) */
c.check('Today renders a "Stopped by usage limit" section from sr.limit_stops',
  /sec\(`Stopped by usage limit[^`]*`, _limitStopsHtml\(sr\.limit_stops, nowS\)/.test(src));
c.check('auto_on asks first (it costs plan)', /action==='auto_on'&&!confirm\(/.test(src));

c.finish();
