/* History -> FAILURES (_failureMapHtml / _failToolName extracted from
 * monitor.py): KPIs, tips, the top-failures table with target chip, reason,
 * example and a replay button opening the 'errors' filter (escaped via
 * jsq), by-reason / by-tool tables, denied rows, empty and error states. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_failToolName', '_failureMapHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
c.check('MCP tool names shortened', _failToolName('mcp__claude_ai_Gmail__create_draft') === 'Gmail · create_draft'
  && _failToolName('mcp__plugin_design_figma__authenticate') === 'figma · authenticate' && _failToolName('Edit') === 'Edit');
const g = (o) => ({ tool: 'Bash', target: 'git push', reason: 'Exit code 1', count: 5, sessions: 3, secs: 125, cost: 1.5,
  last_sid: "abc'12345", last_label: 'My <session>', example: 'fatal: <rejected>', ...o });
const d = { days: 30, calls: 14737, failed: 389, denied: 68, rate: 2.6, secs: 5385, cost: 24.6, sessions: 51,
  tips: [{ reason: 'Missing OAuth scope', tool: 'mcp__claude_ai_Gmail__create_draft', target: '', count: 9, detail: 'Start in the `project` folder.', cost_month: 3.48 }],
  top: [g(), g({ tool: 'mcp__claude_ai_Gmail__create_draft', target: '', reason: 'Missing OAuth scope', count: 1 })],
  by_reason: [g({ reason: 'File does not exist', count: 120 })], by_tool: [g({ tool: 'Read', count: 69 })],
  denied_rows: [g({ reason: 'Auto-mode check unavailable', count: 56 })] };
const h = _failureMapHtml(d);
c.check('KPIs', h.includes('389 <span class="fm-rate">2.6%</span>') && h.includes('1.5 h') && h.includes('$24.60') && h.includes('>68<'));
c.check('tip: short tool name in title, code, saving', h.includes('<b>Missing OAuth scope (Gmail · create_draft): 9×</b>') && h.includes('<code>project</code>') && h.includes('~$3.48 / month'));
c.check('top row: tool, target chip, reason, escaped example', h.includes('<b>Bash</b><span class="cc-repo">git push</span>')
  && h.includes('fatal: &lt;rejected&gt;') && h.includes('2 min'));
c.check('replay opens the errors filter, args escaped', h.includes(`openReplay(${escHtml(JSON.stringify("abc'12345"))}`) && h.includes(",'errors')"));
c.check('MCP tool shown short', h.includes('Gmail · create_draft'));
c.check('by reason / by tool', h.includes('File does not exist') && h.includes('<td class="cache-name">Read</td>'));
c.check('denied section', h.includes('Denied (not failures)') && h.includes('Auto-mode check unavailable'));
c.check('no denials -> section left out', !_failureMapHtml({ ...d, denied_rows: [] }).includes('Denied (not failures)'));
c.check('no failures', _failureMapHtml({ ...d, failed: 0 }).includes('No failed tool calls'));
c.check('no calls', _failureMapHtml({ calls: 0 }).includes('No tool calls'));
c.check('error', _failureMapHtml({ error: 'x' }).includes('failed —'));
c.finish();
