/* Prompt library UI, extracted from monitor.py: _promptVars finds the
 * {{fields}} a template asks for, _fillPrompt fills them (date / yesterday
 * by themselves) and reports what's missing, _launchFormHtml lists the
 * templates escaped. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_promptVars', '_fillPrompt', '_launchFormHtml']) eval(extractFunction(src, f));

const c = new Checker();
c.check('fields in order, deduped, built-ins skipped',
  JSON.stringify(_promptVars('Review PR #{{number}} on {{ branch }} ({{date}}, {{number}}, {{Yesterday}})')) === '["number","branch"]');
c.check('no fields', _promptVars('plain text').length === 0 && _promptVars('').length === 0);
c.check('not a field: {{1x}} / {{}}', _promptVars('{{1x}} {{}} {x}').length === 0);

const now = new Date(2026, 9, 13, 21, 5);
let r = _fillPrompt('PR #{{number}} on {{branch}} for {{date}} vs {{yesterday}}', { number: '42', branch: 'feat/x' }, now);
c.check('filled', r.text === 'PR #42 on feat/x for 2026-10-13 vs 2026-10-12' && r.missing.length === 0);
r = _fillPrompt('PR #{{number}} and {{number}} on {{branch}}', { number: ' ' }, now);
c.check('missing reported once each, left in place', JSON.stringify(r.missing) === '["number","branch"]' && r.text.includes('{{number}}'));
c.check('value with $& stays literal', _fillPrompt('x {{a}}', { a: '$& $1' }, now).text === 'x $& $1');

const h = _launchFormHtml([], [{ name: '', label: 'This PC' }], '', [
  { id: 't1', name: 'Fix <tests>', used: 3 }, { id: 't2', name: 'Plain', used: 0 }]);
c.check('templates listed escaped with use count', h.includes('<option value="t1">Fix &lt;tests&gt; · 3×</option>') && h.includes('<option value="t2">Plain</option>'));
c.check('write-your-own first, save button, fields box', h.indexOf('— write your own —') < h.indexOf('value="t1"')
  && h.includes('>Save as template<') && h.includes('id="ln-vars"'));
c.check('no templates -> still renders', _launchFormHtml([], [], '').includes('id="ln-tpl"'));
c.finish();
