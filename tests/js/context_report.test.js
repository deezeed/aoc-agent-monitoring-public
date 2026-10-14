/* History -> CONTEXT (_contextReportHtml extracted from monitor.py): KPIs,
 * tips with monthly savings (escaped, `code` rendered), the startup parts,
 * skills per plugin with 'never' used, MCP servers (sub-cent rows hidden),
 * memory files, projects, empty and error states. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_fixBtnHtml', '_contextReportHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
const d = { days: 30, sessions: 56, avg_tokens: 54179, max_tokens: 165849, avg_calls: 208.4, cost: 141.52,
  tips: [{ kind: 'mcp', saving_month: 6.86, title: 'Unused MCP server: <Vercel>', detail: 'Disconnect it (`/mcp`).' },
         { kind: 'plugin', saving_month: 5, title: 'Unused plugin: vercel', detail: 'x' }],
  breakdown: [{ part: 'built-in tools & rest', tokens: 39984, share: 73.8 }, { part: 'skills', tokens: 5982, share: 11 }],
  skills: [{ plugin: 'vercel', skills: 31, sessions: 55, tokens: 1951, uses: 0, cost: 5.01 },
           { plugin: '(no plugin)', skills: 15, sessions: 55, tokens: 1725, uses: 8, cost: 4.43 }],
  mcp: [{ server: 'claude.ai Gmail', sessions: 51, tokens: 303, uses: 17, cost: 0.72 },
        { server: 'tiny-server', sessions: 2, tokens: 9, uses: 0, cost: 0.001 }],
  files: [{ path: 'C:\\Users\\m\\.claude\\projects\\x\\memory\\MEMORY.md', kind: 'AutoMem', tokens: 1883, sessions: 30, cost: 1.67 }],
  projects: [{ project: 'shop', sessions: 43, avg_tokens: 53802, calls: 8969, cost: 107.07 }] };
const h = _contextReportHtml(d);
c.check('KPIs: tokens at start, startup cost, savings sum',
  h.includes('~' + (54179).toLocaleString()) && h.includes('$141.52') && h.includes('~$11.86'));
c.check('tips escaped, code rendered, saving shown',
  h.includes('Unused MCP server: &lt;Vercel&gt;') && h.includes('<code>/mcp</code>') && h.includes('~$6.86 / month'));
c.check('parts labelled', h.includes('Claude Code built-in tools &amp; rest') && h.includes('Skills listing') && h.includes('74%'));
c.check('unused plugin -> never, used -> count', h.includes('<span class="ctx-unused">never</span>') && h.includes('8×'));
c.check('no-plugin skills named', h.includes('Built-in &amp; personal'));
c.check('MCP rows, sub-cent hidden', h.includes('claude.ai Gmail') && !h.includes('tiny-server'));
c.check('memory file: short path + kind', h.includes('x/memory/MEMORY.md') && h.includes('AutoMem'));
c.check('projects', h.includes('shop') && h.includes('$107.07'));
c.check('no MCP / files -> sections left out', !_contextReportHtml({ ...d, mcp: [], files: [] }).includes('MCP servers</div>'));
c.check('lean tip -> dash KPI', _contextReportHtml({ ...d, tips: [{ kind: 'fine', saving_month: 0, title: 'lean', detail: '' }] }).includes('<div class="hk-val">—</div>'));
c.check('empty period', _contextReportHtml({ sessions: 0 }).includes('No Claude Code sessions'));
c.check('error', _contextReportHtml({ error: 'x' }).includes('failed'));
c.finish();
