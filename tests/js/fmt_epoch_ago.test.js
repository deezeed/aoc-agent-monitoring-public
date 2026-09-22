/* Tests _fmtEpochAgo, extracted straight from monitor.py. watchdog/sentinel
 * last_log_ts (Python epoch seconds) were already computed and shipped in
 * /status's infra_health, but _infraUpdateUI only ever showed the raw log
 * line text, never how stale it is -- this locks down the pure
 * epoch-to-"Xm ago" formatting logic. Uses offsets from the real current
 * time (no Date.now mock) so it stays robust to whenever the suite runs.
 * Thresholds mirror the existing _fmtRelTime helper (diff<10 -> "just now"). */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, '_fmtEpochAgo'));

const c = new Checker();

const nowS = () => Date.now() / 1000;

c.check('null -> empty string', _fmtEpochAgo(null) === '');
c.check('undefined -> empty string', _fmtEpochAgo(undefined) === '');
c.check('right now -> "just now"', _fmtEpochAgo(nowS()) === 'just now');
c.check('5s ago -> "just now" (below the 10s floor)', _fmtEpochAgo(nowS() - 5) === 'just now');
c.check('15s ago -> "15s ago"', _fmtEpochAgo(nowS() - 15) === '15s ago');
c.check('90s ago -> "1m ago"', _fmtEpochAgo(nowS() - 90) === '1m ago');
c.check('45min ago -> "45m ago"', _fmtEpochAgo(nowS() - 45 * 60) === '45m ago');
c.check('2h ago -> "2h ago"', _fmtEpochAgo(nowS() - 2 * 3600) === '2h ago');
c.check('90min ago -> "1h ago"', _fmtEpochAgo(nowS() - 90 * 60) === '1h ago');
c.check('3 days ago -> "3d ago"', _fmtEpochAgo(nowS() - 3 * 86400) === '3d ago');
c.check('slightly in the future (clock skew) -> "just now"', _fmtEpochAgo(nowS() + 5) === 'just now');

c.finish();
