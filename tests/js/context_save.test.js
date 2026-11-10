/* Save state when the context fills up (_ctxSaveHtml + Inbox kind, from
 * monitor.py): offer / armed / sent strips under the context meter, buttons
 * only for local sessions, ids escaped; the Inbox lists an offer (not an
 * armed or sent one) between "cache going cold" and "looks stuck"; the
 * Settings block, the notification type and the routes exist. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(src.match(/const _INBOX_KINDS=\{[\s\S]*?\};/)[0].replace(/^const /, 'var '));
for (const f of ['escHtml', 'jsq', '_fmtDurationDHM', '_lastMessageInfo', '_waitLabel', '_permAskHtml', '_replyAskHtml',
                 '_budgetBadgeHtml', '_loopBadgeHtml', '_ctxSaveHtml', '_ctxMeterHtml', '_inboxItems', '_inboxHtml', '_bgStrandedLeft', '_bgAgentsHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
const now = 1_800_000_000;
const ctx = { pct: 92, window: 200000, tokens: 184000 };
const S = (id, cs, o) => Object.assign({ id, display_name: id, session_active: true, context: ctx, ctx_save: cs }, o);

c.check('no ctx_save -> nothing', _ctxSaveHtml(S('a', null)) === '' && _ctxSaveHtml(null) === '');
c.check('closed session -> nothing', _ctxSaveHtml(S('a', { state: 'offer', threshold: 90 }, { session_active: false })) === '');
const offer = _ctxSaveHtml(S("it's", { state: 'offer', threshold: 90 }));
c.check('offer: threshold + Save state button, id escaped',
  offer.includes('Context over 90 %') && offer.includes('>Save state</button>') && offer.includes(`_ctxSaveSend(${jsq("it's")})`));
const armed = _ctxSaveHtml(S('a', { state: 'armed', threshold: 90 }));
c.check('armed: says when + Cancel', armed.includes('Saves state when this turn ends') && armed.includes('_ctxSaveCancel(') && armed.includes('cs-flag armed'));
const sent = _ctxSaveHtml(S('a', { state: 'sent', threshold: 90, sent_at: now }));
c.check('sent: no button', sent.includes('State save sent') && !sent.includes('<button'));
c.check('remote session: no buttons', !_ctxSaveHtml(S('a', { state: 'offer', threshold: 90 }, { _isLocal: false })).includes('<button'));
c.check('meter carries the strip', _ctxMeterHtml(S('a', { state: 'offer', threshold: 90 }), now).includes('class="cs-flag"')
  && !_ctxMeterHtml(S('a', null), now).includes('cs-flag'));

const sessions = [
  S('loopy', null, { loop: { text: 'Bash failed 5 times' } }),
  S('full', { state: 'offer', threshold: 90 }),
  S('armed', { state: 'armed', threshold: 90 }),
  S('sent', { state: 'sent', threshold: 90, sent_at: now }),
  S('plain', null, { waiting_on_you: true, waiting_secs: 60, last_message: 'Done.' }),
];
const kinds = _inboxItems({ sessions_list: sessions }, now).map(i => i.s.id + ':' + i.kind);
c.check('inbox: an offer ranks before looks-stuck; armed/sent not listed',
  JSON.stringify(kinds) === JSON.stringify(['full:ctxsave', 'loopy:loop', 'plain:waiting']));
const waitingFull = _inboxItems({ sessions_list: [S('w', { state: 'offer', threshold: 90 }, { waiting_on_you: true, last_message: 'Should I?' })] }, now);
c.check('a question still wins over the context offer', waitingFull[0].kind === 'question');
const h = _inboxHtml(_inboxItems({ sessions_list: [sessions[1]] }, now), now);
c.check('inbox card: label + Save state button', h.includes('Context almost full') && h.includes('_ctxSaveSend('));

c.check('settings block + loader', ['id="st-cs-mode"', 'id="st-cs-thr"', 'id="st-cs-prompt"', '_csLoad();'].every(x => src.includes(x)));
c.check('notification type + routes', src.includes('"key": "ctx_save"') && src.includes('"/context_save/send"') && src.includes('"/context_save/cancel"'));
c.finish();
