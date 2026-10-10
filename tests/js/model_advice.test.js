/* History -> MODELS (_modelAdviceHtml extracted from monitor.py): KPIs,
 * tips with monthly savings (escaped, `code` rendered), the work x model
 * table with Sonnet / Haiku re-pricing (dashes where it's already that
 * model or cheaper), empty and error states. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', '_modelAdviceHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
const d = { total: 470, days: 30,
  by_model: [{ label: 'Opus 5.5', cost: 370, share: 78.7 }, { label: 'Sonnet 5', cost: 100, share: 21.3 }],
  tips: [{ id: 'main', saving_month: 75, title: 'Sonnet for routine <sessions>', detail: 'Use `/model sonnet`.' },
         { id: 'explore', saving_month: 38, title: 'Explore', detail: 'x' }],
  rows: [{ where: 'main', label: 'Opus 5.5', kind: 'shell', calls: 3000, cost: 300, on_sonnet: 240, on_haiku: 120 },
         { where: 'subagent', label: 'Sonnet 5', kind: 'explore', calls: 1200, cost: 10, on_sonnet: 10, on_haiku: 5 },
         { where: 'main', label: 'Haiku 4.5', kind: 'text', calls: 3, cost: 1, on_sonnet: 2, on_haiku: 1 },
         { where: 'main', label: 'Opus 5.5', kind: 'text', calls: 1, cost: 0.001, on_sonnet: 0, on_haiku: 0 }] };
let h = _modelAdviceHtml(d);
c.check('KPIs: spent, main model, savings sum', h.includes('$470.00') && h.includes('Opus 5.5 79%') && h.includes('~$113.00'));
c.check('tips escaped, code rendered, savings shown', h.includes('Sonnet for routine &lt;sessions&gt;') && h.includes('<code>/model sonnet</code>') && h.includes('~$75.00 / month'));
c.check('work labels + subagent tag', h.includes('Running commands') && h.includes('Reading &amp; searching<span class="cc-repo">subagent</span>'));
c.check('re-priced columns', h.includes('$240.00') && h.includes('$120.00'));
c.check('already Sonnet -> dash in the Sonnet column', /Sonnet 5<\/td>[\s\S]*?<span class="adv-dim">—<\/span>/.test(h));
c.check('Haiku row -> dashes in both columns', (h.split('Haiku 4.5</td>')[1] || '').split('</tr>')[0].split('adv-dim').length === 3);
c.check('rows under a cent hidden', !h.includes('$0.00</td>'));
c.check('calls formatted', h.includes((3000).toLocaleString()));
c.check('no savings -> dash KPI', _modelAdviceHtml({ ...d, tips: [{ id: 'fine', saving_month: 0, title: 'lean', detail: '' }] }).includes('<div class="hk-val">—</div>'));
c.check('empty period', _modelAdviceHtml({ total: 0 }).includes('No Claude Code usage'));
c.check('error', _modelAdviceHtml({ error: 'x' }).includes('failed'));
c.finish();
