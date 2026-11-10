/* Tests _limitForecast (plan-limit forecast line) and where it shows:
 * Today's limit card, the COMMAND overview row and the topbar tooltip.
 * Extracted straight from monitor.py; clock strings are computed with the
 * same local Date the page uses, so no 24h-locale assumption. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', '_limitForecast', '_rateLimitDisplay'])
  eval(extractFunction(src, f));

const c = new Checker();
const NOW = 1800000000;
const clock = e => { const d = new Date(e * 1000); return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); };
const win = extra => ({ kind: 'five_hour', pct: 80, status: 'warn', resets_at: NOW + 7200, ...extra });

c.check('no window -> null', _limitForecast(null, NOW) === null);
c.check('no eta -> null', _limitForecast(win({ eta_full_at: null }), NOW) === null);
c.check('hit -> null', _limitForecast(win({ status: 'hit', eta_full_at: NOW + 60 }), NOW) === null);

let f = _limitForecast(win({ eta_full_at: NOW + 1500, soon: true }), NOW);
c.check('soon: minutes + reset clock', f.text === `At this pace you hit the limit in ~25 min · resets ${clock(NOW + 7200)}`);
c.check('soon: advice', f.soon === true && f.advice === 'Pause new agents or switch to a cheaper model');
f = _limitForecast(win({ eta_full_at: NOW + 4200, soon: false }), NOW);
c.check('later: hours + minutes, no advice', f.inTxt === '~1 h 10 min' && f.advice === '' && f.soon === false);
c.check('whole hours', _limitForecast(win({ eta_full_at: NOW + 7200 }), NOW).inTxt === '~2 h');
c.check('eta already passed -> at least ~1 min', _limitForecast(win({ eta_full_at: NOW - 30, soon: true }), NOW).inTxt === '~1 min');
c.check('seconds round up', _limitForecast(win({ eta_full_at: NOW + 24 * 60 + 1 }), NOW).inTxt === '~25 min');

// the topbar tooltip adds the advice only when soon
let d = _rateLimitDisplay({ windows: [win({ eta_full_at: NOW + 1500, soon: true, pace_pct_per_h: 48 })] }, NOW);
c.check('tooltip: soon -> advice', d.title.includes('pause new agents or switch to a cheaper model'));
d = _rateLimitDisplay({ windows: [win({ eta_full_at: NOW + 5000, soon: false, pace_pct_per_h: 10 })] }, NOW);
c.check('tooltip: not soon -> no advice', !d.title.includes('pause new agents'));

// wiring: Today card + COMMAND row use it
const today = extractFunction(src, '_todayHtml');
c.check('Today renders the forecast line', today.includes('_limitForecast(w, nowS)') && today.includes('td-lim-fc'));
const cmd = extractFunction(src, '_commandParts');
c.check('COMMAND plan-limit row says "limit in"', cmd.includes('_limitForecast(w5, nowS)') && cmd.includes('limit in ${fc5.inTxt}'));
c.check('soon line is amber', /\.td-lim-fc\.soon \{[^}]*224,161,58/.test(src));

c.finish();
