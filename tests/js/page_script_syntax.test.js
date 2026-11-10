/* The dashboard's JS parses: the web/js/ parts one by one and joined (what
 * monitor.py inlines into the page at startup), plus any inline <script>
 * left in web/index.html. The other JS tests eval single extracted
 * functions, so a syntax error anywhere else (e.g. a stray quote in a toast
 * string) would only show up in the browser -- where it takes the whole
 * page down. Each part parsing alone keeps the cuts between whole
 * functions/blocks, never inside one. */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { WEB_DIR, webJsParts } = require('./lib/extract');
const { Checker } = require('./lib/check');

const c = new Checker();
const html = fs.readFileSync(path.join(WEB_DIR, 'index.html'), 'utf8');
const parts = webJsParts();
const appJs = parts.map(p => fs.readFileSync(p, 'utf8')).join('');
c.check('index.html has the one app.js script line the parts go into', html.includes('<script src="app.js"></script>'));
c.check('no stray web/app.js next to web/js/', !fs.existsSync(path.join(WEB_DIR, 'app.js')));
c.check('web/js/ parts numbered 01..N', parts.map(p => path.basename(p).slice(0, 2))
  .every((n, i) => Number(n) === i + 1) && parts.length > 1);

const blocks = parts.map(p => [`web/js/${path.basename(p)}`, fs.readFileSync(p, 'utf8')]).concat([['web/js joined', appJs]],
  [...html.matchAll(/<script(?![^>]*\bsrc=)(?![^>]*type="(?:application\/json|text\/template)")[^>]*>([\s\S]*?)<\/script>/g)]
    .map((m, i) => [`index.html inline #${i}`, m[1]]));
c.check('app.js is not empty', appJs.length > 100000);
blocks.forEach(([name, code]) => {
  let err = null;
  try { new vm.Script(code, { filename: name }); } catch (e) { err = e; }
  const where = err && err.stack ? String(err.stack).split('\n').slice(0, 3).join(' | ') : '';
  c.check(`${name} (${code.length} chars) parses${err ? ': ' + err.message + ' @ ' + where : ''}`, !err);
});
c.finish();
