/* Tests _cacheInsights / _cacheReportHtml / _fmtMoney, extracted straight
 * from monitor.py: the findings that explain where the spend goes, the
 * empty/indexing states, the cost-split bar, and escaping of session names. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_fmtMoney', '_fmtTokShort', '_cacheInsights', '_cacheReportHtml']) eval(extractFunction(src, f));
eval(src.match(/const _CACHE_SPLIT=\[[\s\S]*?\n\];/)[0].replace('const ', 'var '));
global.document = { body: { classList: { contains: () => false } } };

const c = new Checker();

c.check('money: big -> whole dollars', _fmtMoney(1234.5) === '$1,235');
c.check('money: dollars -> cents', _fmtMoney(56.7) === '$56.70');
c.check('money: sub-dollar -> 4 decimals', _fmtMoney(0.01234) === '$0.0123');
c.check('money: null -> dash', _fmtMoney(null) === '—');
c.check('tokens short', _fmtTokShort(277086) === '277k' && _fmtTokShort(2.6e9) === '2.6B' && _fmtTokShort(12) === '12');

const t = { n: 9429, inp: 18870, out: 6333460, cw: 26556917, cr: 2586068306, cost: 786.17, cold_n: 44, cold_cw: 12260618,
  cold_idle_n: 44, saved: 6983.09, cold_extra: 77.8, cost_read: 517.21, cost_write: 168.92, cost_out: 99.97, cost_in: 0.06,
  hit_rate: 0.99, avg_context: 277086 };
const ins = _cacheInsights(t);
c.check('insight 1: re-reading share + context size', ins[0].text.startsWith('66% of the spend is Claude re-reading') && ins[0].text.includes('277k'));
c.check('insight 2: re-caches after idle with extra cost', ins[1].text.includes('44 times') && ins[1].text.includes('(all after an hour') && ins[1].text.includes('$77.80'));
c.check('no low-hit-rate warning at 99%', !ins.some(i => i.text.startsWith('Only')));
c.check('saved insight last', ins[ins.length - 1].text.includes('saved about $6,983'));
c.check('low hit rate flagged', _cacheInsights({ ...t, hit_rate: 0.6 }).some(i => i.text.startsWith('Only 60%')));
c.check('no spend -> no insights', _cacheInsights({ cost: 0 }).length === 0);

let h = _cacheReportHtml({ index: { building: true }, stats: null });
c.check('indexing state', h.includes('Still reading your Claude Code transcripts'));
h = _cacheReportHtml({ index: { updated_at: 1 }, stats: { total: { n: 0 } } });
c.check('empty period', h.includes('No Claude Code calls in this period'));
h = _cacheReportHtml({ error: 'x' });
c.check('error state', h.includes('failed'));

const sess = { session_id: 'sid-1', title: '<b>Phantom</b>', cost: 90.5, cost_read: 70, avg_context: 300000, hit_rate: 0.995, cold_n: 1, cold_extra: 2.54, cwd: '' };
h = _cacheReportHtml({ index: { updated_at: 1 }, stats: { total: t, sessions: [sess], by_model: [{ model: 'claude-opus-5-5', n: 4515, cost: 431.5, hit_rate: 0.9867, avg_context: 290000, cold_n: 27 }] } });
c.check('KPIs', h.includes('$786.17') && h.includes('>99%<') && h.includes('$6,983') && h.includes('>44<'));
c.check('bar: one segment per non-zero category, widths as % of spend',
  (h.match(/class="cache-seg"/g) || []).length === 4 && h.includes('width:65.79%'));
c.check('legend lists every category with value and %', h.includes('Cache reads') && h.includes('$517.21') && h.includes('65.8%') && h.includes('Uncached input'));
c.check('session name escaped', h.includes('&lt;b&gt;Phantom') && !h.includes('<b>Phantom'));
c.check('session re-reading share + re-caches', h.includes('>77%<') && h.includes('1 <span class="cache-extra">+$2.54'));
c.check('open button via jsq', h.includes(`showHistoryDetail(${jsq('sid-1')})`));
c.check('model row', h.includes('claude-opus-5-5') && h.includes('4,515'));

c.finish();
