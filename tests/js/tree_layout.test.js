/* Tests _buildTree + _layoutTree, extracted straight from monitor.py. The
 * TREE view used one row counter per depth, so a parent centred on its
 * children could land on the same row as a root leaf placed before it and
 * the two hex nodes were drawn on top of each other. Every node must get
 * its own spot, and a parent must still sit between its first and last
 * child. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, '_buildTree'));
eval(extractFunction(src, '_layoutTree'));

const c = new Checker();

function layout(agents) {
  const { childMap, roots, depth } = _buildTree(agents);
  return _layoutTree(agents, childMap, roots, depth);
}

// Three flat root leaves, then a root with a two-level subtree -- the shape
// that used to overlap (root "p" centred onto row 0/1 next to leaf "a").
const agents = [
  { id: 'a' }, { id: 'b' }, { id: 'c' },
  { id: 'p' },
  { id: 'k1', parent_id: 'p' }, { id: 'k2', parent_id: 'p' },
  { id: 'g1', parent_id: 'k2' }, { id: 'g2', parent_id: 'k2' },
];
const pos = layout(agents);

c.check('every agent gets a position', agents.every(a => pos[a.id]));
const spots = new Set(agents.map(a => `${pos[a.id].x},${pos[a.id].y}`));
c.check('no two nodes share a spot', spots.size === agents.length);

const rootX = pos.a.x;
const rootYs = ['a', 'b', 'c', 'p'].map(id => pos[id].y);
c.check('roots share one column', ['b', 'c', 'p'].every(id => pos[id].x === rootX));
c.check('roots do not overlap vertically (>= one node apart)',
  rootYs.every((y, i) => rootYs.every((y2, j) => i === j || Math.abs(y - y2) >= 56)));
c.check('children sit one level right of their parent', pos.k1.x > pos.p.x && pos.g1.x > pos.k2.x);
c.check('parent is centred between its children',
  pos.p.y === (pos.k1.y + pos.k2.y) / 2 && pos.k2.y === (pos.g1.y + pos.g2.y) / 2);
c.check('subtree starts below the earlier root leaves', pos.k1.y > pos.c.y);

// orphan parent_id (parent not in the list) -> treated as a root, no crash
const orphan = layout([{ id: 'x', parent_id: 'gone' }, { id: 'y' }]);
c.check('orphan parent_id becomes a root', orphan.x && orphan.x.x === orphan.y.x && orphan.x.y !== orphan.y.y);

c.finish();
