/* Every inline <script> of the dashboard page (monitor.py's HTML raw string)
 * parses. The other JS tests eval single extracted functions, so a syntax
 * error anywhere else (e.g. a stray quote in a toast string) would only show
 * up in the browser -- where it takes the whole page down. */
const vm = require('vm');
const { readMonitorSource } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
const start = src.indexOf('HTML = r"""');
const end = src.indexOf('</html>"""', start);
const c = new Checker();
c.check('found the HTML raw string', start > 0 && end > start);
const html = src.slice(start + 'HTML = r"""'.length, end + '</html>'.length);

const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)(?![^>]*type="(?:application\/json|text\/template)")[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
c.check('page has inline scripts', blocks.length > 0);
blocks.forEach((code, i) => {
  let err = null;
  try { new vm.Script(code, { filename: `page-script-${i}.js` }); } catch (e) { err = e; }
  const where = err && err.stack ? String(err.stack).split('\n').slice(0, 3).join(' | ') : '';
  c.check(`inline script #${i} (${code.length} chars) parses${err ? ': ' + err.message + ' @ ' + where : ''}`, !err);
});
c.finish();
