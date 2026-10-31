/* History -> WORK LOG (_worklogRange, _worklogMarkdown, _worklogHtml
 * extracted from monitor.py): range buttons (week = Monday..today, custom
 * validated/swapped), Markdown export, the tab's states, KPIs, summary
 * card (local-only Summarize, cached text, rewrite), print + CSV links
 * carrying the token (CSV locked without Pro), escaping. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_localDateStr', '_worklogRange', '_wlHrs', '_worklogMarkdown', '_worklogHtml'])
  eval(extractFunction(src, f));

const c = new Checker();

// ── ranges ──
c.check('today', JSON.stringify(_worklogRange('today', '2026-10-30')) === '["2026-10-30","2026-10-30"]');
c.check('yesterday across a month', JSON.stringify(_worklogRange('yesterday', '2026-11-01')) === '["2026-10-31","2026-10-31"]');
c.check('week starts Monday (Fri 10-30 -> Mon 10-26)', JSON.stringify(_worklogRange('week', '2026-10-30')) === '["2026-10-26","2026-10-30"]');
c.check('week on a Sunday goes back 6 days', JSON.stringify(_worklogRange('week', '2026-11-01')) === '["2026-10-26","2026-11-01"]');
c.check('custom swapped', JSON.stringify(_worklogRange('custom', '2026-10-30', '2026-10-20', '2026-10-10')) === '["2026-10-10","2026-10-20"]');
c.check('custom garbage -> today', JSON.stringify(_worklogRange('custom', '2026-10-30', 'x', '')) === '["2026-10-30","2026-10-30"]');
c.check('hours format', _wlHrs(5400) === '1.5 h' && _wlHrs(90) === '2 min' && _wlHrs(0) === '0 min');

const rep = { from: '2026-10-26', to: '2026-10-30', group: 'project',
  total: { cost: 12.5, tokens: 5000, sessions: 2, commits: 1, active_s: 4500 },
  groups: [
    { name: 'AOC <x>', cost: 10, tokens: 4000, active_s: 3600,
      commits: [{ sha: 'abc1234def', subject: 'Work <log>', repo: 'AOC', day: '2026-10-29' }],
      sessions: [{ id: 's1', title: 'Build <tab>', cost: 10, tokens: 4000, active_s: 3600, commits: 1,
                   first_day: '2026-10-28', last_day: '2026-10-29', asks: ['Pridaj tab'], last_reply: 'ok' }] },
    { name: '', cost: 2.5, tokens: 1000, active_s: 900, commits: [],
      sessions: [{ id: 's2', title: 'Chat', cost: 2.5, tokens: 1000, active_s: 900, commits: 0,
                   first_day: '2026-10-30', last_day: '2026-10-30', asks: [], last_reply: '' }] } ] };

// ── markdown ──
let md = _worklogMarkdown(rep, { text: 'Done:\n- tab' });
c.check('markdown: title + summary first', md.startsWith('# Work log 2026-10-26 – 2026-10-30\n\nDone:\n- tab\n'));
c.check('markdown: group, commit, session', md.includes('## AOC <x>') && md.includes('- `abc1234` Work <log> (AOC)') && md.includes('- Build <tab> (1.0 h)'));
c.check('markdown: unassigned + plurals', md.includes('## Unassigned') && md.includes('1 session · 0 commits'));
c.check('markdown: no report -> empty', _worklogMarkdown(null) === '');

// ── html ──
const d = { report: rep, summaries: { standup: { text: '<b>Done</b>', cost: 0.012, generated_at: 1790000000 }, client: null } };
let h = _worklogHtml(d, 'standup', false, true, 'tok&1', 'week', '', '', true);
c.check('KPIs', h.includes('>1.3 h<') && h.includes('>$12.50<') && h.includes('<div class="hk-lbl">Commits</div>'));
c.check('cached summary escaped + Rewrite', h.includes('&lt;b&gt;Done&lt;/b&gt;') && !h.includes('<b>Done</b>') && h.includes('↻ Rewrite') && h.includes('$0.012'));
c.check('print link with token', h.includes('/worklog/print?from=2026-10-26&amp;to=2026-10-30&amp;group=project&amp;mode=standup&amp;token=tok%261'));
c.check('CSV link when Pro', h.includes('/worklog/export.csv?from=2026-10-26'));
c.check('names/titles/commits escaped', h.includes('AOC &lt;x&gt;') && h.includes('Build &lt;tab&gt;') && h.includes('Work &lt;log&gt;') && !h.includes('Build <tab>'));
c.check('OPEN goes to the history detail', h.includes('showHistoryDetail(&quot;s1&quot;)'));
c.check('date range on a session row', h.includes('10-28 – 10-29'));
c.check('no custom pickers outside custom', !h.includes('type="date"'));

h = _worklogHtml(d, 'client', false, true, '', 'custom', '2026-10-01', '2026-10-05', false);
c.check('client mode, nothing cached -> Summarize + help', h.includes('✦ Summarize') && h.includes('A short report for your client'));
c.check('no Pro -> CSV locked, print still there', h.includes('🔒 CSV (PRO)') && !h.includes('/worklog/export.csv') && h.includes('/worklog/print?'));
c.check('custom pickers carry values', h.includes('value="2026-10-01"') && h.includes('value="2026-10-05"'));
c.check('busy -> Writing', _worklogHtml(d, 'standup', true, true, '', 'today').includes('Writing…'));
c.check('remote -> no Summarize button', _worklogHtml(d, 'standup', false, false, '', 'today').includes('Summarize on the AOC PC')
  && !_worklogHtml(d, 'standup', false, false, '', 'today').includes('_wlSummarize('));

// ── states ──
c.check('index not ready', _worklogHtml({ report: null }, 'standup', false, true, '', 'today').includes('Still reading'));
c.check('error', _worklogHtml({ error: 'x' }, 'standup', false, true, '', 'today').includes('failed'));
c.check('empty period', _worklogHtml({ report: { ...rep, groups: [] } }, 'standup', false, true, '', 'today').includes('No Claude Code work recorded 2026-10-26 – 2026-10-30'));

c.finish();
