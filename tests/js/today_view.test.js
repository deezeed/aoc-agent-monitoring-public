/* Tests _todayHtml (the TODAY home view), extracted from monitor.py: the
 * day's facts (spend vs a usual day, open sessions, commits), "Needs you"
 * (blocked first, Terminal only for local sessions), "Working", plan
 * limits, spend by project for today only, today's commits, empty states
 * and escaping. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_fmtDurationDHM', '_lastMessageInfo', '_waitLabel', '_waitingSessions', '_ctxMeterHtml', '_permAskHtml', '_loopBadgeHtml', '_awayBlockHtml', '_todayHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
const NOW = Date.UTC(2026, 9, 9, 12) / 1000, TODAY = '2026-10-09';
const S = (id, extra = {}) => ({ id, display_name: 'S-' + id, session_active: true, waiting_on_you: false, host_pid: 1, model: 'claude-opus-5-5', estimated_cost: 2.5, ...extra });

const sr = {
  sessions_list: [
    S('w', { waiting_on_you: true, waiting_kind: 'turn', waiting_secs: 900, last_message: 'Shall I push the fix?' }),
    S('p', { waiting_on_you: true, waiting_kind: 'permission', waiting_secs: 40, waiting_message: 'Claude needs your permission to use <Bash>' }),
    S('r', { context: { pct: 50, window: 200000, tokens: 100000, level: 'ok' } }),
    S('remote', { waiting_on_you: true, waiting_kind: 'turn', waiting_secs: 100, _isLocal: false }),
    S('closed', { session_active: false }),
  ],
  agents: [{ id: 'a1', session_id: 'r', status: 'running' }, { id: 'a2', session_id: 'r', status: 'done' }],
  rate_limits: { windows: [{ kind: 'five_hour', pct: 83, status: 'warn', resets_at: NOW + 5400, projected_pct: 97 },
                           { kind: 'seven_day', pct: 20, status: 'ok', resets_at: NOW + 3 * 86400 }] },
};
const an = {
  today: { cost: 30 },
  by_day: [{ date: '2026-10-09', cost: 30 }, { date: '2026-10-08', cost: 14 }, { date: '2026-10-02', cost: 7 }, { date: '2026-09-01', cost: 900 }],
  by_day_project: [{ date: TODAY, project: 'AOC', cost: 20 }, { date: TODAY, project: '<x>', cost: 10 }, { date: '2026-10-08', project: 'Old', cost: 99 }],
};
const cm = { total: { commits: 2, cost: 5 }, commits: [
  { sha: 'abcdef1234', subject: 'Fix <it>', repo: 'aoc', cost: 3, epoch: NOW - 600 },
  { sha: '1234567', subject: 'Docs', repo: 'aoc', cost: 2, epoch: NOW - 60 }] };

let h = _todayHtml(sr, an, cm, NOW, TODAY);
c.check('spend today', h.includes('<b>$30.00</b> spent today'));
c.check('vs a usual day: (14+7)/7 = 3 -> +900 %', h.includes('▲ 900% vs a usual day'));
c.check('open sessions count (closed excluded)', h.includes('<b>4</b> sessions open'));
c.check('commit count + cost', h.includes('<b>2</b> commits for $5.00'));
const needs = h.slice(h.indexOf('Needs you'), h.indexOf('>Working<'));
c.check('needs you: 3 waiting, blocked first', (needs.match(/class="td-row/g) || []).length === 3 && needs.indexOf('S-p') < needs.indexOf('S-w'));
c.check('permission message shown, escaped', needs.includes('Needs your OK') && needs.includes('&lt;Bash&gt;'));
c.check('turn shows the question', needs.includes('Shall I push the fix?'));
c.check('Terminal button for local sessions only', needs.includes('_focusSession(&quot;p&quot;)') && !needs.includes('_focusSession(&quot;remote&quot;)'));
const work = h.slice(h.indexOf('>Working<'), h.indexOf('Plan limits'));
c.check('working: the busy session with its running agents', work.includes('S-r') && work.includes('1 agent') && !work.includes('S-w'));
c.check('working: context meter', work.includes('ctx-meter') && work.includes('50%'));
c.check('plan limits: both windows, warn level, projection', h.includes('5-hour') && h.includes('Weekly') && h.includes('ctx-warn') && h.includes('~97% by then'));
c.check('spend by project: today only, escaped', h.includes('AOC') && h.includes('&lt;x&gt;') && !h.includes('Old'));
c.check('commits listed with short sha + link to all', h.includes('abcdef1') && !h.includes('abcdef1234') && h.includes('Fix &lt;it&gt;') && h.includes("_histTab='commits'"));

h = _todayHtml({ sessions_list: [] }, {}, null, NOW, TODAY);
c.check('empty day: friendly empty states', h.includes('Nothing is waiting on you.') && h.includes('No Claude Code session is open.')
  && h.includes('Nothing spent yet today.') && h.includes('No commits yet today.') && !h.includes('Plan limits'));
c.check('no history -> no delta', !h.includes('vs a usual day'));
c.check('null inputs do not crash', typeof _todayHtml(null, null, null, NOW, '') === 'string');

c.finish();
