/* Regression test for the recurring "dismissed" bug: diffStatusWithRemove
 * used to schedule an uncancellable 5s auto-dismiss timer the moment a
 * session's session_active flipped false -- a completely normal
 * occurrence for any >5min gap between hook heartbeats. This test proves
 * (a) a session reactivating before the grace window elapses cancels the
 * pending dismiss, (b) a session that stays inactive past the window
 * still gets dismissed, and (c) remote (merged-in) sessions are never
 * auto-dismissed locally at all. Extracted straight from monitor.py. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

// ── fake timer harness (real timers would make a 90s window impractical) ──
let _timers = {};
let _nextTimerId = 1;
function fakeSetTimeout(fn, delay) {
  const id = _nextTimerId++;
  _timers[id] = { fn, delay, cancelled: false };
  return id;
}
function fakeClearTimeout(id) {
  if (_timers[id]) _timers[id].cancelled = true;
}
function fireTimer(id) {
  const t = _timers[id];
  if (t && !t.cancelled) t.fn();
}
global.setTimeout = fakeSetTimeout;
global.clearTimeout = fakeClearTimeout;

// ── stubs for diffStatusWithRemove's free-variable dependencies ──
function diffStatus() {}  // aliased internally as _origDiff; not under test here
const _origDiff = diffStatus;
let _prevSessionActive = {};
let _sessionAutoDismissTimers = {};
let sessionFilter = null;
function setSessionFilter(v) { sessionFilter = v; }
let _dismissCalls = [];
function _dismissSession(id) { _dismissCalls.push(id); }
// diffStatusWithRemove's full body also handles agent-status diffing/
// notifications after the session loop under test -- stub everything it
// might touch so it runs cleanly with an empty `agents` array (which is
// all every test case below passes).
function renderConflicts() {}
function _playSound() {}
function showToast() {}
function _browserNotify() {}
function _fireWebhook() {}
function _isNotifySuppressed() { return false; }
function parseTimeStr() { return Date.now(); }
function scheduleRemove() {}
let _lastProgressAt = {};
let _stuckNotified = new Set();
let _prevStatuses = {};
// token-burn anomaly detection (added alongside the session loop under test)
let _tokenHistory = {};
let _burnNotified = new Set();
function _computeBurnRates() { return null; }  // no test case here exercises a real spike
function _isBurnSpike() { return false; }
function _fireBurnWebhook() {}
// activity sparkline sampling (also added alongside the session loop under test)
let _activityHistory = {};
// waiting-too-long nudge (also added alongside the session loop under test)
let _waitingNotified = new Set();
function _fireWaitingWebhook() {}
// cost-spike detection (also added alongside the session loop under test)
let _kpiAnalytics = null;
let _costSpikeNotified = new Set();
function _projectAvgCosts() { return {}; }
function _isCostSpike() { return false; }  // no test case here exercises a real spike
function _fireCostSpikeWebhook() {}

const src = readMonitorSource();
eval(extractFunction(src, 'diffStatusWithRemove'));

const c = new Checker();

function reset() {
  _timers = {}; _nextTimerId = 1;
  _prevSessionActive = {}; _sessionAutoDismissTimers = {};
  _dismissCalls = []; sessionFilter = null;
}

// 1. active -> inactive -> active again BEFORE the window elapses: no dismiss
reset();
diffStatusWithRemove(null, { sessions_list: [{ id: 's1', session_active: true }] });
diffStatusWithRemove(null, { sessions_list: [{ id: 's1', session_active: false }] });
const timerIdAfterInactive = _sessionAutoDismissTimers['s1'];
c.check('timer scheduled after active->inactive transition', timerIdAfterInactive !== undefined);
diffStatusWithRemove(null, { sessions_list: [{ id: 's1', session_active: true }] });
c.check('timer cancelled (removed from tracking) after reactivation', _sessionAutoDismissTimers['s1'] === undefined);
// firing whatever timer id was originally scheduled must now be a no-op
if (timerIdAfterInactive !== undefined) fireTimer(timerIdAfterInactive);
c.check('_dismissSession NOT called -- reactivation prevented the wrongful dismiss', _dismissCalls.length === 0);

// 2. active -> inactive -> stays inactive past the window: dismiss fires
reset();
diffStatusWithRemove(null, { sessions_list: [{ id: 's2', session_active: true }] });
diffStatusWithRemove(null, { sessions_list: [{ id: 's2', session_active: false }] });
const pendingId = _sessionAutoDismissTimers['s2'];
c.check('timer scheduled for s2', pendingId !== undefined);
fireTimer(pendingId);
c.check('_dismissSession IS called once the window genuinely elapses with no reactivation', _dismissCalls.includes('s2'));

// 3. remote (merged-in) session transitioning inactive is never auto-dismissed
reset();
diffStatusWithRemove(null, { sessions_list: [{ id: 'r1', session_active: true, _isLocal: false }] });
diffStatusWithRemove(null, { sessions_list: [{ id: 'r1', session_active: false, _isLocal: false }] });
c.check('remote session never gets an auto-dismiss timer scheduled', _sessionAutoDismissTimers['r1'] === undefined);
c.check('_dismissSession never called for a remote session', !_dismissCalls.includes('r1'));

// 4. sessionFilter gets cleared if the dismissed session was the active filter
reset();
sessionFilter = 's3';
diffStatusWithRemove(null, { sessions_list: [{ id: 's3', session_active: true }] });
diffStatusWithRemove(null, { sessions_list: [{ id: 's3', session_active: false }] });
for (const id of Object.keys(_timers)) fireTimer(Number(id));
c.check('sessionFilter cleared when the filtered session gets auto-dismissed', sessionFilter === null);

c.finish();
