/* Tests _rateLimitDisplay and _rateLimitUpdateUI, extracted straight from
 * monitor.py: the topbar plan-limit meter picks the most severe window,
 * formats value/label/tooltip, and hides when there's nothing to show. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, '_rateLimitDisplay'));
eval(extractFunction(src, '_rateLimitUpdateUI'));

const c = new Checker();
const NOW = 1800000000;
const win = (kind, pct, status, extra = {}) => ({ kind, pct, status, resets_at: NOW + 4500, ...extra });

c.check('null -> hidden', _rateLimitDisplay(null, NOW).show === false);
c.check('only reset windows -> hidden',
  _rateLimitDisplay({ windows: [{ kind: 'five_hour', pct: null, status: 'reset', resets_at: NOW - 1 }] }, NOW).show === false);

let d = _rateLimitDisplay({ age_s: 90, windows: [win('five_hour', 42.4, 'ok'), win('seven_day', 18, 'ok', { resets_at: NOW + 3 * 86400 })] }, NOW);
c.check('ok: 5h shown first', d.show && d.val === '42%' && d.lbl === '5h · ↻ 1h 15m' && d.level === 'ok');
c.check('ok: fill = pct', d.fill === 42.4);
c.check('tooltip lists both windows + age', d.title.includes('5-hour: 42% used') && d.title.includes('Weekly: 18% used · resets in 3d 0h')
  && d.title.includes('Updated 1m ago'));

d = _rateLimitDisplay({ windows: [win('five_hour', 30, 'ok'), win('seven_day', 86, 'warn', { resets_at: NOW + 2 * 86400 })] }, NOW);
c.check('weekly warn outranks ok 5h', d.val === '86%' && d.lbl.startsWith('Week') && d.level === 'warn');

d = _rateLimitDisplay({ windows: [win('five_hour', 100, 'hit', { source: 'limit_message' })] }, NOW);
c.check('hit -> LIMIT, no projection tick', d.val === 'LIMIT' && d.level === 'hit' && d.proj === null);
c.check('hit from limit message noted', d.title.includes('from the limit message'));

d = _rateLimitDisplay({ windows: [win('five_hour', 61, 'warn', { pace_pct_per_h: 20, projected_pct: 110, eta_full_at: NOW + 7020 })] }, NOW);
c.check('pace + eta in tooltip', d.title.includes('pace 20.0%/h') && d.title.includes('hits 100% at'));
c.check('projection tick capped at 100', d.proj === 100);
d = _rateLimitDisplay({ windows: [win('five_hour', 50, 'ok', { pace_pct_per_h: 2, projected_pct: 52.5 })] }, NOW);
c.check('slow pace -> ~% at reset', d.title.includes('~53% at reset') && d.proj === 52.5);

// DOM wiring
const mk = () => ({ style: {}, textContent: '', title: '', classList: { s: new Set(), toggle(k, on) { on ? this.s.add(k) : this.s.delete(k); } } });
const els = { 'rl-stat': mk(), 'rl-val': mk(), 'rl-lbl': mk(), 'rl-fill': mk(), 'rl-proj': mk() };
const parts = [mk(), mk()];
global.document = { getElementById: id => els[id] || null, querySelectorAll: () => parts };
const realNow = Date.now;
Date.now = () => NOW * 1000;
_rateLimitUpdateUI({ windows: [win('five_hour', 85, 'warn', { projected_pct: 95 })] });
c.check('UI: parts visible', parts.every(p => p.style.display === ''));
c.check('UI: warn class + value', els['rl-stat'].classList.s.has('rl-warn') && els['rl-val'].textContent === '85%');
c.check('UI: fill + projection tick', els['rl-fill'].style.width === '85%' && els['rl-proj'].style.display === 'block'
  && els['rl-proj'].style.left === 'calc(95% - 1px)');
_rateLimitUpdateUI(null);
c.check('UI: no data -> hidden', parts.every(p => p.style.display === 'none'));
Date.now = realNow;

c.finish();
