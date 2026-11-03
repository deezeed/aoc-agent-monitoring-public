/* Ask AOC overlay (extracted from monitor.py): _askAnswerHtml escapes the
 * answer, turns only the [#id8] refs the server sent into Replay buttons,
 * '- ' lines into a list, and shows period + cost / cached; _askBodyHtml
 * shows examples, busy / error notes, earlier questions; the Today header
 * and the palette both open it. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_askAnswerHtml', '_askBodyHtml']) eval(extractFunction(src, f));
eval(src.match(/const _ASK_EXAMPLES=\[[^\]]*\];/)[0].replace('const ', 'var '));

const c = new Checker();
const S1 = 'aaaa1111-0000-0000-0000-000000000001';
const e = { q: 'Koľko?', answer: 'Spolu $7.75 <b>x</b>; najviac [#aaaa1111], potom [#deadbeef].\n- prvá & druhá\n- [#aaaa1111] again',
  sessions: { aaaa1111: { id: S1, title: 'OmniSocial scheduler' } }, cost: 0.0123,
  period: { label: 'this week', from: '2026-10-26', to: '2026-10-30' }, cached: false };
let h = _askAnswerHtml(e);
c.check('escaped', h.includes('$7.75 &lt;b&gt;x&lt;/b&gt;') && !h.includes('<b>x'));
c.check('known ref -> replay button with title', h.includes('onclick="openReplay(&quot;' + S1 + '&quot;,\'\',&quot;OmniSocial scheduler&quot;)"')
  && h.includes('>OmniSocial scheduler</button>'));
c.check('unknown ref left as text', h.includes('[#deadbeef]'));
c.check('bullets -> list', h.includes('<ul><li>prvá &amp; druhá</li><li><button'));
c.check('paragraph', h.includes('<p>Spolu'));
c.check('meta: period range + cost', h.includes('this week (2026-10-26 – 2026-10-30) · $0.012'));
c.check('cached label', _askAnswerHtml({ ...e, cached: true }).includes('cached answer'));
c.check('single day period', _askAnswerHtml({ ...e, period: { label: 'today', from: '2026-10-30', to: '2026-10-30' } }).includes('today (2026-10-30) · $'));
c.check('no answer -> empty', _askAnswerHtml(null) === '' && _askAnswerHtml({}) === '');
const evil = _askAnswerHtml({ answer: '[#aaaa1111]', sessions: { aaaa1111: { id: "x'\"><img>", title: '<img src=x>' } } });
c.check('ref title/id cannot inject html', !evil.includes('<img') && evil.includes('&lt;img src=x&gt;'));
const long = _askAnswerHtml({ answer: '[#aaaa1111]', sessions: { aaaa1111: { id: S1, title: 'x'.repeat(60) } } });
c.check('long title shortened', long.includes('x'.repeat(39) + '…</button>'));

h = _askBodyHtml({});
c.check('examples when nothing asked', _ASK_EXAMPLES.every(q => h.includes(escHtml(q))) && h.includes('_askRun('));
c.check('cost note', h.includes('about 1 cent'));
h = _askBodyHtml({ busy: true, last: null });
c.check('busy note, no examples', h.includes('Gathering facts') && !h.includes('ask-hint"><button class="ask-chip" onclick="_askRun'));
h = _askBodyHtml({ error: 'Still answering <x>' });
c.check('error escaped', h.includes('ask-note err">Still answering &lt;x&gt;'));
h = _askBodyHtml({ last: { ...e, key: 'k1', cached: true }, history: [{ key: 'k1', q: 'Koľko?' }, { key: 'k2', q: 'What failed?' }] });
c.check('answer shown with question', h.includes('Koľko?</div><div class="ask-answer">'));
c.check('cached -> ask again fresh (same prev + model)', h.includes('_askRun(&quot;Koľko?&quot;,true,&quot;&quot;,&quot;haiku&quot;)'));
c.check('follow up + deeper chips', h.includes('onclick="_askFollow()">Follow up</button>')
  && h.includes('_askRun(&quot;Koľko?&quot;,false,&quot;&quot;,\'sonnet\')" title="Ask the same question with Claude Sonnet'));
c.check('earlier questions exclude current', h.includes('_askShow(&quot;k2&quot;)') && !h.includes('_askShow(&quot;k1&quot;)'));

// follow-ups + Sonnet
h = _askBodyHtml({ last: { ...e, key: 'k3', prev: 'k2', thread: ['Koľko dnes?', 'A <b>včera</b>?'], model: 'sonnet' } });
c.check('thread shown, escaped', h.includes('↳ after: Koľko dnes? › A &lt;b&gt;včera&lt;/b&gt;?'));
c.check('sonnet answer: no Deeper chip, meta says Sonnet', !h.includes('Deeper (Sonnet)') && h.includes('· Sonnet · $0.012'));
h = _askBodyHtml({ last: { ...e, key: 'k3', prev: 'k2', model: 'sonnet', cached: true } });
c.check('fresh re-ask keeps prev + model', h.includes('_askRun(&quot;Koľko?&quot;,true,&quot;k2&quot;,&quot;sonnet&quot;)'));
h = _askBodyHtml({ last: { ...e, key: 'k3' }, follow: 'k3' });
c.check('follow mode: banner + way back, no second Follow up chip', h.includes('Follow-up to “Koľko?”') && h.includes('_askNew()')
  && !h.includes('_askFollow()'));
c.check('follow banner hidden for another answer', !_askBodyHtml({ last: { ...e, key: 'k3' }, follow: 'k9' }).includes('Follow-up to'));
c.check('busy note names the model', _askBodyHtml({ busy: true, busyModel: 'sonnet' }).includes('asking Claude Sonnet')
  && _askBodyHtml({ busy: true }).includes('asking Claude Haiku'));
c.check('haiku meta has no model label', !_askAnswerHtml(e).includes('Sonnet'));
c.check('form submits as follow-up when in follow mode', src.includes("_askRun(this.q.value,false,_askState.follow||'')"));
c.check('request carries prev + model', src.includes("JSON.stringify({q, force:!!force, prev:prev||'', model:model||'haiku'})")
  && src.includes('prev=data.get("prev") or ""') && src.includes('model=data.get("model") or "haiku"'));

c.check('Today header has Ask AOC', /onclick="openAsk\(\)"[^>]*>Ask AOC</.test(src));
c.check('palette entry', src.includes("add('act:ask','Ask AOC…'") && src.includes('ask:()=>openAsk(),'));
c.check('routes', src.includes('path_no_qs == "/ask"') && src.includes('path_no_qs == "/ask/history"'));

c.finish();
