/* Tests _ctxMeterHtml, extracted from monitor.py: the context-window meter on
 * CLI cards and session bays (fill %, tokens of window, colour level, the
 * "compact soon" hint) and the prompt-cache countdown shown only while a
 * session waits on you. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', '_fmtDurationDHM', '_ctxSaveHtml', '_ctxMeterHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
const NOW = 1_800_000_000;
const ctx = (extra = {}) => ({ pct: 41.2, window: 1000000, tokens: 412000, level: 'ok',
  cache_expires_at: NOW + 1500, cache_ttl: '1h', recache_tokens: 405000, ...extra });
const S = (extra = {}) => ({ id: 's', session_active: true, waiting_on_you: false, context: ctx(), ...extra });

c.check('no context -> empty', _ctxMeterHtml({ id: 's' }, NOW) === '' && _ctxMeterHtml(null, NOW) === '');
c.check('bad context -> empty', _ctxMeterHtml(S({ context: { pct: 'x', window: 200000 } }), NOW) === ''
  && _ctxMeterHtml(S({ context: { pct: 5, window: 0 } }), NOW) === '');

let h = _ctxMeterHtml(S(), NOW);
c.check('shows rounded % and tokens/window', h.includes('41% · 412k/1M'));
c.check('bar width = pct', h.includes('width:41.2%'));
c.check('ok level class', h.includes('ctx-ok'));
c.check('not waiting -> no cache note', !h.includes('ctx-cache'));
c.check('tooltip has exact numbers', h.includes('412,000 of 1,000,000'));

h = _ctxMeterHtml(S({ context: ctx({ pct: 88, level: 'high', tokens: 176000, window: 200000 }) }), NOW);
c.check('high -> red class + compact soon', h.includes('ctx-high') && h.includes('compact soon') && h.includes('176k/200k'));
h = _ctxMeterHtml(S({ context: ctx({ pct: 75, level: 'warn' }) }), NOW);
c.check('warn class, no compact hint', h.includes('ctx-warn') && !h.includes('compact soon'));
h = _ctxMeterHtml(S({ context: ctx({ level: '"><script>' }) }), NOW);
c.check('unknown level -> ok, no injection', h.includes('ctx-ok') && !h.includes('<script>'));
c.check('pct clamped to 100', _ctxMeterHtml(S({ context: ctx({ pct: 130 }) }), NOW).includes('width:100.0%'));

h = _ctxMeterHtml(S({ waiting_on_you: true }), NOW);
c.check('waiting + warm -> minutes left', h.includes('cache warm') && h.includes('25m left'));
c.check('warm tooltip mentions re-cache size', h.includes('405k tok re-cached'));
h = _ctxMeterHtml(S({ waiting_on_you: true, context: ctx({ cache_expires_at: NOW - 10 }) }), NOW);
c.check('waiting + expired -> cache cold', h.includes('ctx-cache cold') && h.includes('cache cold'));
h = _ctxMeterHtml(S({ waiting_on_you: true, context: ctx({ cache_expires_at: null }) }), NOW);
c.check('no expiry known -> no cache note', !h.includes('ctx-cache'));
h = _ctxMeterHtml(S({ waiting_on_you: true, session_active: false }), NOW);
c.check('closed session -> no cache note', !h.includes('ctx-cache'));

// dollar figures (server adds recache_usd / warm_usd)
const priced = extra => S({ waiting_on_you: true, context: ctx({ recache_usd: 3.24, warm_usd: 0.08, ...extra }) });
h = _ctxMeterHtml(priced({}), NOW);
c.check('25 min left: calm, prices only in the tooltip', !h.includes(' soon') && !h.includes('if cold')
  && h.includes('405k tok re-cached, $3.24') && h.includes('~$0.08 to read'));
h = _ctxMeterHtml(priced({ cache_expires_at: NOW + 540 }), NOW);
c.check('1h cache, 9 min left: amber + cold price', h.includes('ctx-cache soon') && h.includes('$3.24 if cold'));
h = _ctxMeterHtml(priced({ cache_ttl: '5m', cache_expires_at: NOW + 180 }), NOW);
c.check('5m cache, 3 min left: not yet', !h.includes(' soon'));
h = _ctxMeterHtml(priced({ cache_ttl: '5m', cache_expires_at: NOW + 90 }), NOW);
c.check('5m cache, 90 s left: amber', h.includes('ctx-cache soon'));
h = _ctxMeterHtml(priced({ cache_expires_at: NOW - 5 }), NOW);
c.check('cold: next message price', h.includes('cache cold · next msg ~$3.24'));
h = _ctxMeterHtml(S({ waiting_on_you: true, context: ctx({ cache_expires_at: NOW + 60 }) }), NOW);
c.check('no prices from server -> no $ text', !h.includes('$') && h.includes('ctx-cache soon'));

c.finish();
