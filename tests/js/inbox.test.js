/* Inbox view (_inboxItems / _inboxHtml extracted from monitor.py): which
 * sessions need you and why, urgency order (held/blocked permission, question,
 * budget cap, cache about to go cold, looping, plain waiting), one-tap
 * actions reuse the Allow/Deny + Reply blocks, data is escaped, closed or
 * idle sessions stay out, and the empty state. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(src.match(/const _INBOX_KINDS=\{[\s\S]*?\};/)[0].replace(/^const /, 'var '));
for (const f of ['escHtml', 'jsq', '_fmtDurationDHM', '_lastMessageInfo', '_waitLabel', '_permAskHtml', '_replyAskHtml',
                 '_budgetBadgeHtml', '_loopBadgeHtml', '_ctxSaveHtml', '_ctxMeterHtml', '_inboxItems', '_inboxHtml', '_bgStrandedLeft', '_bgAgentsHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
const now = 1_800_000_000;
const S = (id, o) => Object.assign({ id, display_name: id, session_active: true }, o);
const sessions = [
  S('plain', { waiting_on_you: true, waiting_secs: 900, last_message: 'Done, tests pass.' }),
  S('loopy', { loop: { text: 'Bash failed 5 times in a row' } }),
  S('ask', { waiting_on_you: true, waiting_secs: 60, last_message: 'Should I push it?' }),
  S('held', { waiting_on_you: true, waiting_secs: 40, waiting_kind: 'permission', machine: "lap'top",
              perm_requests: [{ id: 'p<1>', summary: 'Bash: rm -rf "build"', detail: '' }] }),
  S('cache', { waiting_on_you: true, waiting_secs: 3000, last_message: 'Ready.',
               context: { pct: 20, window: 1e6, tokens: 2e5, cache_expires_at: now + 300, cache_ttl: '1h', recache_usd: 1.5, warm_usd: 0.1 } }),
  S('cheapcache', { waiting_on_you: true, waiting_secs: 3000, last_message: 'Ready.',
                    context: { pct: 2, window: 1e6, tokens: 2e4, cache_expires_at: now + 300, cache_ttl: '1h', recache_usd: 0.2, warm_usd: 0.02 } }),
  S('capped', { budget: { spent: 5.2, cap: 5, kind: 'session' } }),
  S('dlg', { waiting_on_you: true, waiting_secs: 31, waiting_kind: 'question', waiting_message: 'Pick <one>' }),
  S('busy', { waiting_on_you: false }),
  S('closed', { session_active: false, waiting_on_you: true, waiting_secs: 9999 }),
];
const items = _inboxItems({ sessions_list: sessions }, now);
const kinds = items.map(i => i.s.id + ':' + i.kind);
c.check('order: permission, questions (longest wait first), budget, cache, loop, waiting',
  JSON.stringify(kinds) === JSON.stringify(['held:permission', 'ask:question', 'dlg:question', 'capped:budget', 'cache:cache', 'loopy:loop', 'cheapcache:waiting', 'plain:waiting']));
c.check('working + closed sessions left out', !kinds.some(k => /^(busy|closed):/.test(k)));
c.check('cache: needs >= $0.25 extra, else plain waiting', items.find(i => i.s.id === 'cheapcache').kind === 'waiting' && items.find(i => i.s.id === 'cache').cacheLeft === 300);
c.check('cache far from expiry -> waiting', _inboxItems({ sessions_list: [S('x', Object.assign({}, sessions[4], { id: 'x', context: Object.assign({}, sessions[4].context, { cache_expires_at: now + 1200 }) }))] }, now)[0].kind === 'waiting');
c.check('held prompt without waiting flag still counts', _inboxItems({ sessions_list: [S('h', { perm_requests: [{ id: 'a', summary: 's' }] })] }, now)[0].kind === 'permission');
c.check('null / empty status', _inboxItems(null, now).length === 0 && _inboxItems({}, now).length === 0);

const h = _inboxHtml(items, now);
c.check('one card per item', (h.match(/class="td-card ib-item/g) || []).length === items.length);
c.check('Allow/Deny with escaped machine + id', h.includes(`_permDecide(${jsq("lap'top")},${jsq('p<1>')},'allow')`) && h.includes('Bash: rm -rf &quot;build&quot;'));
c.check('blocked dialog text escaped', h.includes('Pick &lt;one&gt;'));
c.check('cache line: warm vs cold cost', h.includes('to keep the cache warm (~$0.10)') && h.includes('re-caches for ~$1.50'));
c.check('budget + loop badges', h.includes('⛔ Budget cap') && h.includes('⟳ Looks stuck'));
c.check('Terminal button uses jsq', h.includes(`_focusSession(${jsq('plain')})`));
c.check('remote session: no Terminal button', !_inboxHtml(_inboxItems({ sessions_list: [S('r', { _isLocal: false, waiting_on_you: true, last_message: 'x' })] }, now), now).includes('_focusSession'));
c.check('count in header', h.includes(`${items.length} things need you`));
const reply = _inboxHtml(_inboxItems({ sessions_list: [S('rp', { waiting_on_you: true, last_message: 'Ship it?', reply_request: { id: 'r1', message: 'Ship it?' } })] }, now), now);
c.check('reply ask reused', reply.includes('_replyOpen(') && reply.includes('Claude asks'));
const empty = _inboxHtml([], now);
c.check('empty state', empty.includes('All clear') && empty.includes('Nothing needs you right now.') && !empty.includes('ib-item'));
c.check('page has Inbox nav, area, hotkey, palette', ['id="vt-inbox"', 'id="inbox-area"', 'id="mnav-inbox"', "case 'J': setView('inbox')", "['inbox','Inbox']"].every(x => src.includes(x)));
c.finish();
