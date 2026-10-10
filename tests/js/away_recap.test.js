/* Today -> "since your message" recap (extracted from monitor.py):
 * _awayBlockHtml shows counts since your last message, the Haiku recap as
 * bullets (escaped; **bold** and `code` rendered), loading / error notes
 * and the Summarize / Refresh button routed to the session's machine;
 * _todayHtml puts it inside that session's own row (Needs you / Working),
 * never as a second list. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();

for (const f of ['escHtml', 'jsq', '_fmtDurationDHM', '_lastMessageInfo', '_waitLabel', '_waitingSessions', '_ctxMeterHtml',
                 '_permAskHtml', '_loopBadgeHtml', '_awayBlockHtml', '_todayHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
const now = 1_800_000_000;
const S = (id, mins, extra = {}) => ({ id, display_name: 'S' + id, machine: 'pc', session_active: true,
  away: { since: now - mins * 60, tools: 37, errors: 3, commits: 2, files: 5 }, ...extra });

c.check('no away -> nothing', _awayBlockHtml({ id: 'x' }, undefined, now, 'auto') === '' && _awayBlockHtml(null) === '');
let h = _awayBlockHtml(S('1', 42), undefined, now, 'auto');
c.check('age of your message', h.includes('Since your message 42m ago'));
c.check('counts', h.includes('37 tool calls · 2 commits · 3 failed · 5 files edited'));
c.check('no recap yet -> Summarize button routed to machine',
  h.includes('>Summarize<') && h.includes('_recapLoad(&quot;1&quot;,&quot;pc&quot;,true)'));
h = _awayBlockHtml(S('1', 42, { away: { since: now - 3600, tools: 1, errors: 0, commits: 0, files: 0 } }), undefined, now, 'auto');
c.check('singular, zero counts hidden', h.includes('<span>1 tool call</span>'));

h = _awayBlockHtml(S('1', 42), { text: '- Fixed <b>tests</b>\n- Committed & pushed' }, now, 'auto');
c.check('bullets -> list, escaped', h.includes('<ul class="td-recap"><li>Fixed &lt;b&gt;tests&lt;/b&gt;</li><li>Committed &amp; pushed</li></ul>'));
c.check('has recap -> Refresh', h.includes('>Refresh<'));
h = _awayBlockHtml(S('1', 42), { text: '- **(1) DONE**: hook in `aoc_permission.py`\n- next' }, now, 'auto');
c.check('**bold** and `code` rendered', h.includes('<b>(1) DONE</b>: hook in <code>aoc_permission.py</code>'));
h = _awayBlockHtml(S('1', 42), { text: '- **<img src=x onerror=1>**' }, now, 'auto');
c.check('markdown cannot smuggle html', h.includes('<b>&lt;img src=x onerror=1&gt;</b>'));
h = _awayBlockHtml(S('1', 42), { text: 'One plain sentence.\nAnd another.' }, now, 'auto');
c.check('non-bullet text -> paragraph', h.includes('<div class="td-recap">One plain sentence. And another.</div>'));
h = _awayBlockHtml(S('1', 42), { loading: true }, now, 'auto');
c.check('loading note + disabled button', h.includes('Summarizing…') && h.includes(' disabled>'));
h = _awayBlockHtml(S('1', 42), { error: 'Claude Code (claude) not found' }, now, 'auto');
c.check('error note', h.includes('td-recap-note err') && h.includes('not found'));
c.check('mode off -> counts, no button', !_awayBlockHtml(S('1', 42), undefined, now, 'off').includes('<button')
  && _awayBlockHtml(S('1', 42), undefined, now, 'off').includes('37 tool calls'));

const sr = { sessions_list: [S('work', 20), S('wait', 90, { waiting_on_you: true, waiting_kind: 'turn', waiting_secs: 600 }),
  { id: 'plain', display_name: 'Splain', session_active: true }] };
h = _todayHtml(sr, {}, null, now, '2027-01-15', { 'pc|wait': { text: '- Waiting one done' } }, 'auto');
c.check('no separate list', !h.includes('While you were away'));
c.check('one away block per away session', (h.match(/class="td-away"/g) || []).length === 2);
const rowOf = name => h.slice(h.indexOf('>' + name + '<'), h.indexOf('</div>\n      </div>', h.indexOf('>' + name + '<')) + 400);
c.check('recap inside its own row', h.indexOf('Waiting one done') > h.indexOf('>Swait<') && h.indexOf('Waiting one done') < h.indexOf('>Swork<'));
c.check('session without away -> no block', !rowOf('Splain').includes('td-away'));
c.finish();
