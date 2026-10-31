/* A/B model compare UI (extracted from monitor.py): the form (folder chips,
 * two model pickers, budget, cost tick-box), the estimate line, the result
 * table (live numbers while running, cheaper/faster tags, hit-cap, Keep only
 * when finished and the run changed files, escaping), the earlier-runs list,
 * and the "A/B: compare two models…" link in the New session form. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const k of ['_AB_MODEL_NAMES', '_AB_STATE']) {
  const m = new RegExp(`const ${k}=\\{[^\\n]*\\};`).exec(src);
  if (!m) throw new Error(k + ' not found');
  eval(m[0].replace('const ', 'var '));
}
for (const f of ['escHtml', 'jsq', '_abFormHtml', '_abEstHtml', '_abDur', '_abResultHtml', '_abListHtml', '_launchFormHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
let h = _abFormHtml([{ path: 'C:\\p\\A "x"', name: 'A "x"' }], 'C:\\p\\<repo>', 'fix <b>it</b>');
c.check('form: chip escaped', h.includes('data-path="C:\\p\\A &quot;x&quot;"'));
c.check('form: cwd + prompt prefilled, escaped', h.includes('value="C:\\p\\&lt;repo&gt;"') && h.includes('>fix &lt;b&gt;it&lt;/b&gt;</textarea>'));
c.check('form: A opus, B sonnet by default', /id="ab-m-a"[^]*?value="opus" selected/.test(h) && /id="ab-m-b"[^]*?value="sonnet" selected/.test(h));
c.check('form: budget + cost tick-box', h.includes('id="ab-budget"') && h.includes('id="ab-ok"') && h.includes('billed'));
c.check('form: says the working copy is not touched', h.includes("working copy isn't touched"));

c.check('estimate: needs two models', _abEstHtml(null).includes('two different models'));
const e = _abEstHtml({ runs: [{ label: 'A', model: 'opus', low: 0.31, high: 2, capped: true }, { label: 'B', model: 'haiku', low: 0.02, high: 0.4 }], max_total: 4 });
c.check('estimate line', e.includes('Opus $0.31–$2.00 (capped)') && e.includes('Haiku $0.02–$0.40') && e.includes('<b>$4.00</b>'));

c.check('dur', _abDur(5) === '5s' && _abDur(65) === '1m 05s' && _abDur(null) === '—');

const base = { id: 'c1', prompt: 'do <x>', repo: 'C:\\r', head: 'abcdef1234', budget: 2 };
let r = _abResultHtml({ ...base, state: 'running', runs: [
  { label: 'A', model: 'opus', state: 'running', started: 1000, live: { cost: 0.12, tool_calls: 3, errors: 1, denials: 2 } },
  { label: 'B', model: 'sonnet', state: 'queued' }] }, 1065);
c.check('running: live numbers', r.includes('$0.12') && r.includes('<td>3</td>') && r.includes('+2 blocked'));
c.check('running: elapsed time', r.includes('1m 05s…'));
c.check('running: no Keep, Stop & discard', !r.includes('_abKeep') && r.includes('Stop & discard'));
c.check('prompt escaped, head short', r.includes('do &lt;x&gt;') && r.includes('off abcdef1'));

const done = { ...base, state: 'done', runs: [
  { label: 'A', model: 'opus', state: 'done', wall_s: 90, cost: 0.9, tool_calls: 8, errors: 0, denials: 0, final: 'ok', diff: 'd', diffstat: { files: 2, ins: 10, dels: 1 } },
  { label: 'B', model: 'sonnet', state: 'done', wall_s: 40, cost: 0.3, tool_calls: 5, errors: 2, denials: 0, final: 'ok', diff: '', diffstat: { files: 0, ins: 0, dels: 0 }, stop: 'error_max_budget_usd' }] };
r = _abResultHtml(done, 2000);
c.check('done: cheaper + faster on B', (r.match(/ab-win">cheaper/g) || []).length === 1 && /\$0\.30 <span class="ab-warn">hit cap<\/span> <span class="ab-win">cheaper/.test(r) && r.includes('40s <span class="ab-win">faster'));
c.check('done: Keep only for the run with changes', r.includes(`_abKeep(${jsq('c1')},${jsq('A')})`) && !r.includes(`_abKeep(${jsq('c1')},${jsq('B')})`));
c.check('done: Diff + Answer buttons', r.includes(`_abDiff(${jsq('c1')},${jsq('A')})`) && r.includes(`_abAnswer(${jsq('c1')},${jsq('B')})`) && !r.includes(`_abDiff(${jsq('c1')},${jsq('B')})`));
c.check('done: changes', r.includes('2 files') && r.includes('+10') && r.includes('−1') && r.includes('none'));
c.check('done: Discard both', r.includes('Discard both'));
r = _abResultHtml({ ...done, state: 'kept', kept: 'A', merged: '1234567890' }, 2000);
c.check('kept: no actions, merged sha', !r.includes('_abKeep') && !r.includes('_abDiscard') && r.includes('merged as 1234567') && r.includes('ab-kept">kept'));
r = _abResultHtml({ ...done, state: 'kept', kept: 'A', merge_error: true, runs: [{ ...done.runs[0], branch: 'aoc-ab/x-a-opus' }, done.runs[1]] }, 2000);
c.check('kept with conflict: branch named', r.includes('Merge conflict') && r.includes('aoc-ab/x-a-opus'));
r = _abResultHtml({ ...base, state: 'done', runs: [{ label: 'A', model: 'opus', state: 'error', error: 'not <logged> in' }, { label: 'B', model: 'haiku', state: 'stopped', error: 'stopped' }] }, 0);
c.check('errors escaped, states named', r.includes('not &lt;logged&gt; in') && r.includes('Failed') && r.includes('Stopped'));
c.check('missing comparison', _abResultHtml(null, 0).includes('not found'));

c.check('list: empty -> nothing', _abListHtml([]) === '');
const l = _abListHtml([{ id: 'c"1', prompt: '<p>', state: 'kept', kept: 'B', runs: [{ model: 'opus', cost: 0.5 }, { model: 'haiku', cost: 0.25 }] }]);
c.check('list: row', l.includes('&lt;p&gt;') && l.includes('Opus vs Haiku · $0.75 · kept B') && l.includes(`_abShow(${jsq('c"1')}`));

c.check('New session form links to A/B', _launchFormHtml([], [], '', []).includes('_abFromLaunch(this.form)'));
c.finish();
