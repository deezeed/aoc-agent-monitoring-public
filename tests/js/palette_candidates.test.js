/* Tests _paletteCandidates, extracted straight from monitor.py. This
 * builds the full unfiltered list the command palette's own
 * _paletteFilter (tests/js/palette_filter.test.js) then narrows by query
 * -- had no coverage of its own: a session/agent/settings-pane/view
 * dropped from this list would silently vanish from the palette with
 * nothing catching it (palette_filter.test.js only ever exercises its
 * own hand-built candidate fixtures, never the real generator). */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

let lastStatus = null; // _paletteCandidates' free-variable dependency

const src = readMonitorSource();
eval(extractFunction(src, '_paletteCandidates'));

const c = new Checker();

// 1. no live status at all -> still returns the static settings/view
// entries, just no session/agent ones (guards each lastStatus access with
// `lastStatus && ...`)
lastStatus = null;
const bare = _paletteCandidates();
c.check('null lastStatus -> no session/agent entries, doesn\'t crash', !bare.some(x => x.sub === 'Session' || x.sub.startsWith('Agent')));
c.check('null lastStatus -> settings/view entries still present', bare.some(x => x.sub === 'Settings') && bare.some(x => x.sub === 'View'));

// 2. sessions_list entries become "Session" candidates, labeled by
// display_name falling back to project falling back to a generic label
lastStatus = {
  sessions_list: [
    { id: 's1', display_name: 'My Session', project: 'AOC' },
    { id: 's2', project: 'PHANTOM AI' },
    { id: 's3' },
  ],
  agents: [
    { id: 'ag_real1', name: 'Explore repo', status: 'running' },
    { id: 'ag_real2', name: '', status: 'done' },
    { id: 'hook_abc123', name: 'should be excluded', status: 'running' },
  ],
};
const out = _paletteCandidates();
const sessions = out.filter(x => x.sub === 'Session');
c.check('one Session candidate per session', sessions.length === 3);
c.check('display_name used when present', sessions.some(x => x.label === 'My Session'));
c.check('falls back to project when no display_name', sessions.some(x => x.label === 'PHANTOM AI'));
c.check('falls back to generic label when neither is set', sessions.some(x => x.label === 'CLI Session'));

// 3. agents become "Agent · <status>" candidates, hook_-prefixed synthetic
// agents excluded (same exclusion every other agent list in the app uses)
const agents = out.filter(x => x.sub.startsWith('Agent'));
c.check('hook_-prefixed agents excluded from the palette', agents.length === 2);
c.check('agent label uses name when present', agents.some(x => x.label === 'Explore repo'));
c.check('agent label falls back to id when name is empty', agents.some(x => x.label === 'ag_real2'));
c.check('agent sub includes its status', agents.some(x => x.sub === 'Agent · running') && agents.some(x => x.sub === 'Agent · done'));

// 4. static entries: exactly 7 settings panes and 10 views, every time
// (AGENTS and CLI count as two views since they were promoted from a
// CARDS sub-tab to top-level views)
c.check('exactly 7 Settings entries', out.filter(x => x.sub === 'Settings').length === 7);
c.check('exactly 10 View entries', out.filter(x => x.sub === 'View').length === 10);

// 5. every candidate has a callable action (the palette invokes it on
// selection without further checks)
c.check('every candidate has a callable action', out.every(x => typeof x.action === 'function'));

c.finish();
