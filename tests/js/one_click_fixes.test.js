/* One-click fixes (_fixBtnHtml / _fixPlanHtml extracted from monitor.py): the
 * tip button states (Fix… / ✓ Fixed + Undo / manual steps / not on this PC),
 * escaping of the fix payload in onclick, and the preview's change list. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_fixBtnHtml', '_fixPlanHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
const fix = { kind: 'mcp', target: "claude.ai O'Brien" };
c.check('no fix -> nothing', _fixBtnHtml(null, {}, true) === '' && _fixBtnHtml({}, {}, true) === '');
const go = _fixBtnHtml(fix, {}, true);
c.check('Fix… button, payload escaped', go.includes('Fix…') && go.includes(`_fixOpen(${jsq(JSON.stringify({ kind: 'mcp', target: "claude.ai O'Brien" }))})`)
  && !go.includes("O'Brien\"})"));
c.check('remote -> no button', !_fixBtnHtml(fix, {}, false).includes('_fixOpen') && _fixBtnHtml(fix, {}, false).includes('Fix on the PC'));
const done = _fixBtnHtml(fix, { "mcp:claude.ai O'Brien": { id: 'fx1', title: 'Turn off <x>' } }, true);
c.check('applied -> Fixed + Undo', done.includes('✓ Fixed') && done.includes(`_fixUndo(${jsq('fx1')})`) && done.includes('Turn off &lt;x&gt;'));
c.check('manual -> steps', _fixBtnHtml({ kind: 'manual', target: 'design', steps: 'Go to <claude.ai>' }, {}, true).includes('How to turn off'));

const plan = { ok: true, title: 'Turn off <MCP>', files: ['C:\\Users\\m\\.claude.json'],
  changes: [{ file: 'claude_json', op: 'list_add', path: ['projects', 'C:/Users/m', 'disabledMcpServers'], before: null, item: 'claude.ai Vercel' },
            { file: 'settings', op: 'set', path: ['enabledPlugins', 'vercel@x'], before: true, after: false },
            { file: 'claude_md', op: 'line_add', path: [], text: '- use <files>' }] };
const h = _fixPlanHtml(plan);
c.check('title + files', h.includes('Turn off &lt;MCP&gt;') && h.includes('.claude.json'));
c.check('list add row', h.includes('projects["C:/Users/m"].disabledMcpServers') && h.includes('(not set) → add <code>claude.ai Vercel</code>'));
c.check('set row', h.includes('<code>enabledPlugins.vercel@x</code>: true → <code>false</code>'));
c.check('line row escaped', h.includes('<code>- use &lt;files&gt;</code>'));
c.check('apply button', h.includes('_fixApply(this)'));
c.check('error plan', _fixPlanHtml({ ok: false, error: 'already off' }).includes('already off'));
c.finish();
