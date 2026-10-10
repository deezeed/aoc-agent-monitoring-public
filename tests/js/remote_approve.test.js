/* Remote approve UI, extracted from monitor.py: _permAskHtml shows the
 * held permission prompt with Allow / Deny / Answer in terminal (escaped,
 * routed to the session's machine), and the waiting quote / Today row use
 * it instead of the plain "Needs your OK" text while a prompt is held. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_fmtDurationDHM', '_lastMessageInfo', '_waitLabel', '_permAskHtml', '_woyQuoteHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
const Q = '&quot;';
const req = (id, extra = {}) => ({ id, tool_name: 'Bash', summary: 'Bash: rm -rf <build>', detail: 'Clean "build"', age_s: 12, ...extra });
const S = (extra = {}) => ({ id: 's1', machine: 'laptop', session_active: true, waiting_on_you: true,
  waiting_kind: 'permission', waiting_message: 'Bash: rm -rf <build>', perm_requests: [], ...extra });

c.check('no held prompt -> empty', _permAskHtml(S()) === '' && _permAskHtml({}) === '' && _permAskHtml(null) === '');

let h = _permAskHtml(S({ perm_requests: [req('r1')] }));
c.check('summary escaped', h.includes('Bash: rm -rf &lt;build&gt;') && !h.includes('<build>'));
c.check('three actions', h.includes('>Allow<') && h.includes('>Deny…<') && h.includes('>Answer in terminal<'));
c.check('allow routed to the machine + request', h.includes(`_permDecide(${Q}laptop${Q},${Q}r1${Q},'allow')`));
c.check('deny + terminal wired', h.includes(`${Q}r1${Q},'deny')`) && h.includes(`${Q}r1${Q},'terminal')`));
c.check('details collapsible, escaped', h.includes('<details class="perm-detail">') && h.includes('Clean &quot;build&quot;'));
c.check('clicks do not open the card', h.includes('onclick="event.stopPropagation()"'));
c.check('single prompt -> no "more"', !h.includes('more after this'));

h = _permAskHtml(S({ perm_requests: [req('r1'), req('r2'), req('r3')] }));
c.check('oldest first, rest counted', h.includes(`${Q}r1${Q},'allow'`) && !h.includes(`${Q}r2${Q}`) && h.includes('+2 more after this'));
c.check('no detail -> no details box', !_permAskHtml(S({ perm_requests: [req('r1', { detail: '' })] })).includes('<details'));
h = _permAskHtml(S({ perm_requests: [req('x"),alert(1)//')] }));
c.check('quote in id cannot break out of the attribute', !h.includes('x"),alert') && h.includes('x\\&quot;),alert'));

h = _woyQuoteHtml(S({ perm_requests: [req('r1')] }));
c.check('waiting quote becomes the ask box', h.includes('perm-ask') && h.includes('>Allow<'));
h = _woyQuoteHtml(S());
c.check('not held -> plain Needs your OK quote', h.includes('woy-quote') && !h.includes('perm-ask'));

c.finish();
