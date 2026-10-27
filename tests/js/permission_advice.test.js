/* History -> PERMISSIONS (_permAdviceHtml extracted from monitor.py): KPIs,
 * notes (hook missing / log just started), suggested rules with tier chip,
 * Allow… / ✓ Allowed (applied, already covered, not from this PC), the
 * blocked list with why, risky rules with Remove… only for the two files
 * AOC can edit, escaping, empty and error states; _fixBtnHtml labels and the
 * list_remove line in _fixPlanHtml. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_fixBtnHtml', '_fixPlanHtml', '_failToolName', '_permAdviceHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
const g = (o) => ({ rule: 'Bash(npm run test *)', tier: 'project', why: 'runs your project', family: 'npm run test', tool: 'Bash',
  asked: 4, rejected: 0, denied: 2, saved: 3, secs: 125, sessions: 2, example: 'npm run test -- <x>', last_sid: "sid'1", last_label: 'Proj', covered: false, ...o });
const now = Date.now() / 1000;
const d = { days: 30, prompts: 12, waited: 4000, denied: 7, saved: 785, one_offs: 673, hook: true, log_since: now - 30 * 86400,
  suggest: [g(), g({ rule: 'Bash(grep *)', tier: 'read', asked: 0, last_sid: '' }), g({ rule: 'Bash(ls *)', tier: 'read', covered: true })],
  blocked: [g({ rule: '', tier: 'no', family: 'python', why: 'runs any code or program', asked: 3, rejected: 1, example: 'python <x>.py' }),
            g({ rule: '', tier: 'no', tool: 'mcp__claude_ai_Gmail__send_message', family: 'Gmail · send_message', why: 'acts in Gmail' })],
  risky: [{ file: 'settings_local', path: 'C:\\Users\\me\\.claude\\settings.local.json', rule: 'PowerShell(Remove-Item *)', why: 'deletes or moves files' },
          { file: 'project', path: 'C:\\w\\p\\.claude\\settings.json', rule: 'Bash(node *)', why: 'runs any code' }] };
const h = _permAdviceHtml(d, {}, true);
c.check('KPIs', h.includes('>12<') && h.includes('1.1 h') && h.includes('>7<') && h.includes('>785<') && h.includes('673 of them'));
c.check('no note when the log is old enough and the hook is in', !h.includes('pa-note'));
c.check('suggested: rule, tier chip, counts, escaped example', h.includes('<span class="pa-rule">Bash(npm run test *)</span>')
  && h.includes('project scripts') && h.includes('read-only') && h.includes('4×') && h.includes('npm run test -- &lt;x&gt;') && h.includes('2 min'));
c.check('Allow… button with the rule', h.includes('>Allow…</button>') && h.includes(escHtml(JSON.stringify(JSON.stringify({ kind: 'allow', target: 'Bash(grep *)' })))));
c.check('covered -> ✓ Allowed, no button', (h.match(/Allow…<\/button>/g) || []).length === 2 && h.includes('Already allowed in ~/.claude/settings.json'));
c.check('replay only with a session, id escaped', h.includes(`openReplay(${escHtml(JSON.stringify("sid'1"))}`)
  && (h.match(/rp-btn/g) || []).length === 4);
c.check('blocked: family, short tool, why, asked incl. rejected', h.includes('<b>python</b>') && h.includes('runs any code or program')
  && h.includes('Gmail · send_message') && h.includes('<td class="num">4×</td>'));
c.check('risky: Remove… for the local file, by hand for a project file', h.includes('>Remove…</button>') && h.includes('by hand')
  && h.includes('.claude/settings.local.json') && h.includes('Broad rules you already have (2)')
  && h.includes(escHtml(JSON.stringify(JSON.stringify({ kind: 'unallow', target: 'settings_local|PowerShell(Remove-Item *)' })))));
const applied = { 'allow:Bash(grep *)': { id: 'fx1', title: 't' }, 'unallow:settings_local|PowerShell(Remove-Item *)': { id: 'fx2', title: 't' } };
const ha = _permAdviceHtml(d, applied, true);
c.check('applied -> ✓ Allowed / ✓ Removed + Undo', ha.includes('✓ Allowed</span><button') && ha.includes('✓ Removed') && (ha.match(/>Undo</g) || []).length === 2);
c.check('not on this PC -> no buttons', !_permAdviceHtml(d, {}, false).includes('Allow…</button>') && _permAdviceHtml(d, {}, false).includes('Fix on the PC'));
c.check('hook missing note', _permAdviceHtml({ ...d, hook: false }, {}, true).includes('python setup.py'));
c.check('log not started note', _permAdviceHtml({ ...d, log_since: null }, {}, true).includes('logged from now on'));
c.check('young log note', _permAdviceHtml({ ...d, log_since: now - 86400 }, {}, true).includes('counts grow'));
c.check('sections left out when empty', !_permAdviceHtml({ ...d, blocked: [], risky: [] }, {}, true).includes('no rule suggested')
  && !_permAdviceHtml({ ...d, risky: [] }, {}, true).includes('Broad rules'));
c.check('nothing at all', _permAdviceHtml({ days: 30, prompts: 0, suggest: [], blocked: [], risky: [] }, {}, true).includes('No permission prompts'));
c.check('error', _permAdviceHtml({ error: 'x' }).includes('failed —'));
c.check('default fix labels unchanged', _fixBtnHtml({ kind: 'env', target: 'X' }, {}, true).includes('>Fix…<')
  && _fixBtnHtml({ kind: 'env', target: 'X' }, { 'env:X': { id: 'a' } }, true).includes('✓ Fixed'));
const plan = _fixPlanHtml({ ok: true, title: 'Remove the rule x', files: ['f'], changes: [{ file: 'settings_local', op: 'list_remove', path: ['permissions', 'allow'], item: 'Bash(node *)' }] });
c.check('plan shows list_remove', plan.includes('<code>permissions.allow</code>: remove <code>Bash(node *)</code>'));
c.finish();
