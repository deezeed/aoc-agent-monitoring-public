/* "Looks stuck" flag on a CLI session (_loopBadgeHtml, extracted from
 * monitor.py): shown only when the transcript scanner's loop signal is set,
 * text escaped, tooltip says what clears it. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', '_loopBadgeHtml'])
  eval(extractFunction(src, f));

const c = new Checker();
c.check('no loop -> nothing', _loopBadgeHtml({}) === '' && _loopBadgeHtml({ loop: null }) === '' && _loopBadgeHtml(null) === '');
c.check('loop without text -> nothing', _loopBadgeHtml({ loop: { kind: 'errors' } }) === '');
const h = _loopBadgeHtml({ loop: { kind: 'same_fail', count: 3, text: 'The same call failed 3×: Bash: test <x> && "y"' } });
c.check('flag + label', h.includes('class="loop-flag"') && h.includes('Looks stuck'));
c.check('text escaped in body and tooltip', h.includes('&lt;x&gt; &amp;&amp; &quot;y&quot;') && !h.includes('<x>'));
c.check('tooltip says what clears it', h.includes('successful commit clears this'));
c.finish();
