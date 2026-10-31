/* Dangerous-command guard UI, extracted from monitor.py: a held dangerous
 * command shows as a orange "Dangerous command" prompt with "Run it", and
 * Settings lists the built-in rules as checkboxes (checked = guarded). */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_permAskHtml', '_dgRulesHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
const S = (q) => ({ id: 's1', machine: 'laptop', perm_requests: [q] });
const req = { id: 'r1', tool_name: 'Bash', summary: 'Bash: git push --force', detail: '', age_s: 3 };

let h = _permAskHtml(S({ ...req, danger: 'Force push' }));
c.check('danger prompt labelled + styled', h.includes('perm-ask danger') && h.includes('⚠ Dangerous command · Force push'));
c.check('danger allow button says Run it', h.includes('>Run it<') && !h.includes('>Allow<'));
c.check('deny + terminal still offered', h.includes('>Deny…<') && h.includes('>Answer in terminal<'));
h = _permAskHtml(S({ ...req, danger: '<b>x</b>' }));
c.check('danger label escaped', h.includes('&lt;b&gt;x&lt;/b&gt;') && !h.includes('<b>x</b>'));
h = _permAskHtml(S(req));
c.check('normal prompt unchanged', h.includes('🔐 Needs your OK') && h.includes('>Allow<') && !h.includes(' danger'));

const rules = [{ id: 'force_push', label: 'Force push', why: 'rewrites <history>' }, { id: 'rm_broad', label: 'Broad rm', why: 'x' }];
const r = _dgRulesHtml(rules, ['rm_broad']);
c.check('one checkbox per rule', (r.match(/type="checkbox"/g) || []).length === 2);
c.check('switched-off rule unchecked, others checked',
  /data-rule="force_push" checked/.test(r) && !/data-rule="rm_broad" checked/.test(r));
c.check('rule text escaped', r.includes('rewrites &lt;history&gt;'));
c.check('no rules -> empty', _dgRulesHtml(null, null) === '');
c.check('settings has the guard section + load on open',
  ['DANGEROUS COMMANDS', 'id="st-dg-mode"', 'id="st-dg-rules"', '_dgLoad();'].every(x => src.includes(x)));
c.finish();
