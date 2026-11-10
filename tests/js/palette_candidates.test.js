/* Tests _paletteCandidates (-> _paletteItems), extracted straight from
 * monitor.py: the full unfiltered list the command palette narrows by query.
 * A session/agent/settings pane/view dropped from it would silently vanish
 * from the palette. Actions are covered in palette_actions.test.js. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

let lastStatus = null;
const _PALETTE_SOURCES = [];
const _paletteCtx = { resume: null, fixes: null };
const _fixesApplied = {};
const _fixLocal = () => true;

const src = readMonitorSource();
eval(extractFunction(src, '_paletteItems'));
eval(extractFunction(src, '_paletteCandidates'));

const c = new Checker();

lastStatus = null;
const bare = _paletteCandidates();
c.check('null lastStatus -> no session/agent entries, doesn\'t crash', !bare.some(x => x.kind === 'session' || x.kind === 'agent'));
c.check('null lastStatus -> settings/view entries still present', bare.some(x => x.sub === 'Settings') && bare.some(x => x.sub === 'View'));

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
const show = out.filter(x => x.run[0] === 'sessionAgents');
c.check('one "Show agents" entry per session', show.length === 3);
c.check('display_name used when present', show.some(x => x.label === 'Show agents · My Session'));
c.check('falls back to project when no display_name', show.some(x => x.label === 'Show agents · PHANTOM AI'));
c.check('falls back to generic label when neither is set', show.some(x => x.label === 'Show agents · CLI session'));

const agents = out.filter(x => x.kind === 'agent');
c.check('hook_-prefixed agents excluded from the palette', agents.length === 2);
c.check('agent label uses name when present', agents.some(x => x.label === 'Explore repo'));
c.check('agent label falls back to id when name is empty', agents.some(x => x.label === 'ag_real2'));
c.check('agent sub includes its status', agents.some(x => x.sub === 'Agent · running') && agents.some(x => x.sub === 'Agent · done'));

c.check('exactly 7 Settings entries', out.filter(x => x.sub === 'Settings').length === 7);
c.check('exactly 14 View entries (Command Center + Inbox + Orchestrate)', out.filter(x => x.sub === 'View').length === 14);
c.check('Today is a palette view', out.some(x => x.sub === 'View' && x.label === 'View → Today'));
c.check('every candidate has a run descriptor', out.every(x => Array.isArray(x.run) && typeof x.run[0] === 'string'));

c.finish();
