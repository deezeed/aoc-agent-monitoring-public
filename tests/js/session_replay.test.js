/* Session replay UI, extracted from monitor.py: _replayOffset (+m:ss from
 * the start), _replayFilter (the tabs), _replayHtml (facts, spend strip,
 * escaped rows with error / commit / cost marks, details), _replayBtnHtml. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_fmtDurationDHM', '_replayBtnHtml', '_replayFilter', '_replayOffset', '_replayHtml'])
  eval(extractFunction(src, f));
eval(src.match(/const _RP_GLYPH=\{[^}]*\};/)[0].replace('const ', 'var '));

const c = new Checker();
const Q = '&quot;';
c.check('offset m:ss', _replayOffset(1065, 1000) === '+1:05');
c.check('offset h:mm:ss', _replayOffset(1000 + 3723, 1000) === '+1:02:03');
c.check('offset unknown -> empty', _replayOffset(null, 1000) === '' && _replayOffset(5, null) === '');

const steps = [
  { i: 0, t: 1000, kind: 'prompt', text: 'Fix <the> parser' },
  { i: 1, t: 1002, kind: 'text', text: 'Looking.', cost: 0.5, model: 'claude-opus-5-5', tokens: 1200 },
  { i: 2, t: 1002, kind: 'tool', tool: 'Bash', text: 'Bash: pytest -q', detail: 'Run tests', err: true, result: '2 <failed>', secs: 12 },
  { i: 3, t: 1016, kind: 'tool', tool: 'Bash', text: 'Bash: git commit -m fix', err: false, commit: true, cost: 0.02 },
  { i: 4, t: 1020, kind: 'command', text: '/clear' },
];
const top = [1];
const ids = f => _replayFilter(steps, f, top).map(s => s.i).join(',');
c.check('filters', ids('all') === '0,1,2,3,4' && ids('prompts') === '0,1' && ids('tools') === '2,3'
  && ids('errors') === '2' && ids('commits') === '3' && ids('costly') === '1');

const d = { ok: true, steps, truncated: false,
  summary: { start: 1000, end: 1020 + 3600, cost: 0.52, calls: 2, prompts: 1, tools: 2, errors: 1, commits: 1, top } };
let h = _replayHtml(d, 'all');
c.check('facts: duration, cost, counts, failures', h.includes('1h 0m') && h.includes('$0.520') && h.includes('2 API calls')
  && h.includes('rp-bad">1 failed') && h.includes('1 commit<'));
c.check('spend strip: one block per costed step, clickable', (h.match(/class="rp-seg/g) || []).length === 2 && h.includes('_replayGo(1)'));
c.check('prompt escaped', h.includes('Fix &lt;the&gt; parser') && !h.includes('<the>'));
c.check('failed tool: ✖, error details with escaped output', h.includes('class="rp-row k-err"') && h.includes('✖')
  && h.includes('<summary>Error</summary>') && h.includes('2 &lt;failed&gt;') && h.includes('rp-errout'));
c.check('slow tool shows its seconds', h.includes('rp-secs">12s'));
c.check('commit marked', h.includes('class="rp-row k-commit"') && h.includes('⎇'));
c.check('costliest step highlighted', h.includes('rp-cost top') && h.includes('$0.500'));
c.check('offsets on rows', h.includes('>+0:16<'));
c.check('scrub range over all steps', h.includes('max="4"'));
c.check('active tab marked', h.includes(`rp-tab on" onclick="_replaySetFilter(${Q}all${Q})"`));
h = _replayHtml(d, 'commits');
c.check('commits tab -> only that row', (h.match(/class="rp-row /g) || []).length === 1);
c.check('empty filter -> message', _replayHtml({ ...d, steps: [steps[0]] }, 'errors').includes('Nothing of this kind'));
c.check('truncated note', _replayHtml({ ...d, truncated: true }, 'all').includes('last 4,000 steps'));
c.check('error payload -> message', _replayHtml({ ok: false, error: 'no <transcript>' }).includes('no &lt;transcript&gt;'));

h = _replayBtnHtml({ id: 's1', machine: 'laptop', display_name: 'Demo' });
c.check('button opens replay for machine + session', h.includes(`openReplay(${Q}s1${Q},${Q}laptop${Q},${Q}Demo${Q})`));
c.check('no id -> no button', _replayBtnHtml({}) === '' && _replayBtnHtml(null) === '');
c.finish();
