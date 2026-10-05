/* Tests _groupIntoBays, extracted straight from monitor.py. The agents view
 * groups strips into one bay per CLI session: bays must keep the incoming
 * (priority-sorted) order, match each bay to its session record, never fold
 * a remote machine's agents into a local bay, and give session-less agents
 * a bay of their own instead of dropping them. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, '_groupIntoBays'));

const c = new Checker();

const sessions = [
  { id: 's1', project: 'alpha' },
  { id: 's2', project: 'beta' },
  { id: 's1', project: 'alpha-on-laptop', machine: 'laptop' },
];
// Already sorted by renderAgents: running first.
const agents = [
  { id: 'a1', session_id: 's2', status: 'running' },
  { id: 'a2', session_id: 's1', status: 'running' },
  { id: 'a3', session_id: 's2', status: 'done' },
  { id: 'a4', session_id: 's1', status: 'running', machine: 'laptop', _isLocal: false },
  { id: 'a5', status: 'done' },
  { id: 'a6', session_id: 'gone', session_project: 'orphan-proj', status: 'done' },
];

const bays = _groupIntoBays(agents, sessions);

c.check('one bay per (machine, session), plus unattached', bays.length === 5);
c.check('bays keep first-appearance order',
  bays.map(b => b.agents[0].id).join() === 'a1,a2,a4,a5,a6');
c.check('agents keep their order inside a bay', bays[0].agents.map(a => a.id).join() === 'a1,a3');
c.check('bay matched to its session record', bays[0].session && bays[0].session.project === 'beta');
c.check('local bay gets the local session, not the remote one with the same id',
  bays[1].session.project === 'alpha');
c.check('remote agent gets its own bay with its machine session',
  bays[2].session.project === 'alpha-on-laptop' && bays[2].isRemote && bays[2].machine === 'laptop');
c.check('agent without session_id lands in an unattached bay',
  bays[3].sessionId === null && bays[3].session === null && bays[3].agents[0].id === 'a5');
c.check('unknown session keeps its project name for the title',
  bays[4].session === null && bays[4].project === 'orphan-proj');
c.check('every agent is in exactly one bay',
  bays.reduce((n, b) => n + b.agents.length, 0) === agents.length);

c.check('no agents -> no bays', _groupIntoBays([], sessions).length === 0);
c.check('missing sessions list -> still grouped', _groupIntoBays(agents, undefined).length === 5);

// List mode used to render one flat list of rows across all sessions; it
// must group into the same bays as card mode.
const render = extractFunction(src, 'renderAgents');
const listBranch = render.slice(render.indexOf('if(listMode){'), render.indexOf('} else {', render.indexOf('if(listMode){')));
c.check('list mode groups its rows into session bays',
  listBranch.includes('_groupIntoBays(') && listBranch.includes('_sessionBayHtml('));

c.finish();
