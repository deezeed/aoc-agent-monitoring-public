/* _fitTopbar steps the topbar down (icon-only search, no clock, fewer
 * buttons) until nothing collides with the centred clock or spills past the
 * right edge. Fake elements stand in for the DOM: the "layout" here is a
 * function of which step classes are on the bar. Also pins the strip-card
 * wording: a running agent with every task ticked is "Still working…". */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, '_fitTopbar'));
const c = new Checker();

function makeBar(widthFor) {
  const cls = new Set();
  const classList = { add: (...a) => a.forEach(x => cls.add(x)), remove: (...a) => a.forEach(x => cls.delete(x)), has: x => cls.has(x) };
  const el = (rect, hiddenWhen) => ({
    get offsetParent() { return hiddenWhen && cls.has(hiddenWhen) ? null : {}; },
    getBoundingClientRect: () => rect(cls),
  });
  const clock = el(() => ({ left: 700, right: 900, width: 200 }), 'tb-noclock');
  const right = el(cl => { const w = widthFor(cl); return { left: 1000 - w + 300, right: w > 800 ? 1400 : 1300, width: w }; });
  const bar = { classList, getBoundingClientRect: () => ({ right: 1300 }),
    querySelector: s => (s === '.clock' ? clock : null), querySelectorAll: () => [right, clock] };
  return { bar, cls };
}
global.getComputedStyle = () => ({ display: 'block' });

let t = makeBar(() => 300);
global.document = { querySelector: () => t.bar };
_fitTopbar();
c.check('roomy bar: no step applied', t.cls.size === 0);

t = makeBar(cl => (cl.has('tb-tight') ? 300 : 500));
_fitTopbar();
c.check('collision with clock: steps until it fits, no further', t.cls.has('tb-lbl') && t.cls.has('tb-tight') && !t.cls.has('tb-noclock'));

t = makeBar(() => 900);
_fitTopbar();
c.check('spills past the right edge: all four steps applied', ['tb-lbl', 'tb-tight', 'tb-noclock', 'tb-squeeze'].every(x => t.cls.has(x)));

c.check('_applyStatus re-fits the topbar', /renderKpi\(sr\);\s*_fitTopbar\(\);/.test(src));
c.check("running agent with all tasks ticked says 'Still working…'", src.includes("a.status==='running'?'Still working…':'All tasks done'"));
c.check('old 11px tick boxes hidden in favour of the bar', src.includes('.sc-ticks { display:none; }'));
c.finish();
