/* Tests _staleSessions, extracted straight from monitor.py. This is the
 * stale-session sweep's filtering logic: which sessions are eligible for
 * bulk dismissal (closed, local-only -- remote sessions are excluded,
 * same gate the manual Dismiss button already uses since their lifecycle
 * belongs to their own machine). */
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
    { id: 'closed-remote', session_active: false, _isLocal: false },
    { id: 'active-remote', session_active: true, _isLocal: false },
  ],
};

const stale = _staleSessions(sr);
const staleIds = stale.map(s => s.id);

c.check('excludes active local sessions', !staleIds.includes('active-local'));
c.check('includes closed local sessions', staleIds.includes('closed-local') && staleIds.includes('closed-local-2'));
c.check('excludes closed REMOTE sessions (not this machine\'s to dismiss)', !staleIds.includes('closed-remote'));
c.check('excludes active remote sessions', !staleIds.includes('active-remote'));
c.check('exactly the 2 eligible sessions are returned', stale.length === 2);

// no sessions_list at all -> empty array, not a crash
c.check('empty sr -> empty array', _staleSessions({}).length === 0);
c.check('undefined sessions_list -> empty array', _staleSessions({ sessions_list: undefined }).length === 0);

c.finish();
