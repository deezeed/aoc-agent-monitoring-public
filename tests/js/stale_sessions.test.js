/* Tests _staleSessions, extracted straight from monitor.py. This is the
 * stale-session sweep's filtering logic: which sessions are eligible for
 * bulk dismissal -- every closed one, local AND merged-in remote. Remote
 * sessions used to be excluded; since a2227d8 sweepStaleSessions routes
 * each dismissal to the session's own machine (_dismissSession(id,
 * s.machine) -> _apiUrl/_machineFor), so they're included, and each
 * returned session must keep its `machine` for that routing to work. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, '_staleSessions'));

const c = new Checker();

const sr = {
  sessions_list: [
    { id: 'active-local', session_active: true },
    { id: 'closed-local', session_active: false },
    { id: 'closed-local-2', session_active: false },
    { id: 'closed-remote', session_active: false, _isLocal: false, machine: 'laptop' },
    { id: 'active-remote', session_active: true, _isLocal: false, machine: 'laptop' },
  ],
};

const stale = _staleSessions(sr);
const staleIds = stale.map(s => s.id);

c.check('excludes active local sessions', !staleIds.includes('active-local'));
c.check('includes closed local sessions', staleIds.includes('closed-local') && staleIds.includes('closed-local-2'));
c.check('includes closed REMOTE sessions (dismissal is routed to their machine)', staleIds.includes('closed-remote'));
c.check('remote session keeps its machine for routing', stale.find(s => s.id === 'closed-remote').machine === 'laptop');
c.check('excludes active remote sessions', !staleIds.includes('active-remote'));
c.check('exactly the 3 eligible sessions are returned', stale.length === 3);

// no sessions_list at all -> empty array, not a crash
c.check('empty sr -> empty array', _staleSessions({}).length === 0);
c.check('undefined sessions_list -> empty array', _staleSessions({ sessions_list: undefined }).length === 0);

c.finish();
