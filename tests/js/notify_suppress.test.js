/* Tests _isNotifySuppressed (quiet hours + muted projects) and _agentCost
 * (client-side cost estimate). Extracted straight from monitor.py. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

let _mutedProjects = [];
let _quietStart = '';
let _quietEnd = '';
let _fakeNow = new Date(2026, 0, 1, 12, 0);
let _costRate = 9;

const RealDate = Date;
function FakeDate(...args) { return args.length === 0 ? _fakeNow : new RealDate(...args); }
FakeDate.now = () => _fakeNow.getTime();
Date = FakeDate;

const src = readMonitorSource();
eval(extractFunction(src, '_isNotifySuppressed'));
eval(extractFunction(src, '_agentCost'));

const c = new Checker();

// ── _isNotifySuppressed ───────────────────────────────────────────────
_quietStart = ''; _quietEnd = ''; _mutedProjects = [];
_fakeNow = new RealDate(2026, 0, 1, 3, 0);
c.check('no config -> not suppressed', _isNotifySuppressed('AnyProject') === false);

_mutedProjects = ['MutedProj'];
_fakeNow = new RealDate(2026, 0, 1, 15, 0);
c.check('muted project -> suppressed regardless of time', _isNotifySuppressed('MutedProj') === true);
c.check('other project, same mute list -> not suppressed', _isNotifySuppressed('OtherProj') === false);

_mutedProjects = [];
_quietStart = '09:00'; _quietEnd = '17:00';
_fakeNow = new RealDate(2026, 0, 1, 12, 0);
c.check('normal range: inside -> suppressed', _isNotifySuppressed('P') === true);
_fakeNow = new RealDate(2026, 0, 1, 8, 59);
c.check('normal range: just before start -> not suppressed', _isNotifySuppressed('P') === false);
_fakeNow = new RealDate(2026, 0, 1, 17, 0);
c.check('normal range: at end (exclusive) -> not suppressed', _isNotifySuppressed('P') === false);

_quietStart = '22:00'; _quietEnd = '07:00';
_fakeNow = new RealDate(2026, 0, 1, 23, 30);
c.check('overnight range: 23:30 -> suppressed', _isNotifySuppressed('P') === true);
_fakeNow = new RealDate(2026, 0, 1, 3, 0);
c.check('overnight range: 03:00 -> suppressed', _isNotifySuppressed('P') === true);
_fakeNow = new RealDate(2026, 0, 1, 12, 0);
c.check('overnight range: daytime -> not suppressed', _isNotifySuppressed('P') === false);

Date = RealDate;

// ── _agentCost ────────────────────────────────────────────────────────
c.check('_agentCost: uses estimated_cost when present', _agentCost({ estimated_cost: 1.2345, tokens_used: 999999 }) === 1.2345);
_costRate = 9;
c.check('_agentCost: falls back to tokens_used/1e6 * _costRate', Math.abs(_agentCost({ tokens_used: 1000000 }) - 9) < 1e-9);
c.check('_agentCost: no tokens_used -> 0', _agentCost({}) === 0);

c.finish();
