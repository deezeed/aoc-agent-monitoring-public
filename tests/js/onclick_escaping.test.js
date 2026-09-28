/* Tests jsq() (extracted straight from monitor.py) the way a browser uses it:
 * the value goes into onclick="fn(${jsq(v)})", the browser HTML-decodes the
 * attribute, then runs it as JS. It must hand fn() the exact original string
 * for Windows paths (a "\u" in "C:\...\utils" was a JS SyntaxError under the
 * old '${escHtml(p.replace(/'/g,"\\'"))}' pattern), apostrophes, double
 * quotes and markup.
 * Also a regression scan: no on*="..." handler in monitor.py may interpolate
 * a data value inside '...' quotes again -- only the allowlisted constants. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, 'escHtml'));
eval(extractFunction(src, 'jsq'));

const c = new Checker();

// what a browser does with an attribute value before running it
function htmlDecodeAttr(s) {
  return s.replace(/&(#39|quot|lt|gt|amp);/g, (_, e) =>
    ({ '#39': "'", quot: '"', lt: '<', gt: '>', amp: '&' })[e]);
}
// render onclick="fn(ARG)", pull the attribute back out the way an HTML
// parser would (up to the next raw "), decode, run; return what fn received
function clickWith(argSource) {
  const html = `<button onclick="fn(${argSource})">x</button>`;
  const m = html.match(/onclick="([^"]*)"/);
  let got;
  new Function('fn', htmlDecodeAttr(m[1]))(v => { got = v; });
  return { got, html };
}

const values = {
  'Windows path with \\u': 'C:\\Users\\marek\\utils\\x.py',
  'Windows path with \\n and \\t': 'C:\\new\\tmp\\t.py',
  apostrophe: "Marek's laptop",
  'double quote': 'say "hi".md',
  markup: '</button><img src=x onerror=alert(1)>',
  'ampersand entity text': 'a &amp; b &#39; c',
  unicode: 'Počítač/projekt ✓',
  empty: '',
};
for (const [label, v] of Object.entries(values)) {
  let r;
  try { r = clickWith(jsq(v)); } catch (e) { r = { error: e.message }; }
  c.check(`jsq round-trips: ${label}`, r.got === v);
  if (r.html) c.check(`jsq keeps the attribute closed: ${label}`, (r.html.match(/"/g) || []).length === 2);
}
c.check('jsq stringifies non-strings', clickWith(jsq(42)).got === '42');

// the old path pattern really was broken for Windows paths (why this changed)
const oldPattern = p => `'${escHtml(p.replace(/'/g, "\\'"))}'`;
let oldBroke = false;
try { clickWith(oldPattern('C:\\Users\\marek\\utils\\x.py')); } catch (e) { oldBroke = e instanceof SyntaxError; }
c.check("old '${escHtml(p.replace(...))}' pattern throws on a \\u path", oldBroke);

// regression scan over monitor.py's inline handlers
const ALLOWED = new Set([
  'b.f',              // status filter keys (constants)
  'f',                // _classifyError types (constants)
  'pr',               // notify-settings profile names (constants)
  'ev.k',             // notify event keys (constants)
  'id',               // terminal tab ids, 't'+counter
]);
const offenders = [];
const re = /\bon[a-z]+="[^"]*?'\$\{([^}]*)\}'/g;
let m;
while ((m = re.exec(src))) {
  const expr = m[1].trim();
  if (ALLOWED.has(expr)) continue;
  if (/^[\w.]+\?'var\(--\w+\)':'var\(--\w+\)'$/.test(expr.replace(/_\w+\.includes\([\w.]+\)/, 'x'))) continue; // style ternaries
  offenders.push(`line ${src.slice(0, m.index).split('\n').length}: '\${${expr}}'`);
}
c.check('no data value interpolated inside \'...\' in an on*="" handler' +
        (offenders.length ? ' -- found: ' + offenders.join('; ') : ''), offenders.length === 0);

c.finish();
