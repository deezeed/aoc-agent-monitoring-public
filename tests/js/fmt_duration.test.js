/* Tests _fmtDurationDHM, extracted straight from monitor.py. This exact
 * days/hours/minutes formatting expression was independently duplicated
 * 3 times (the WAITING pill's duration, the infra panel's monitor
 * uptime, and renderDiag's own fmtUptime) before being factored out here
 * -- this test locks down the one shared behavior all 3 call sites now
 * depend on. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, '_fmtDurationDHM'));

const c = new Checker();

c.check('null -> em dash', _fmtDurationDHM(null) === '—');
c.check('0 seconds -> "0m"', _fmtDurationDHM(0) === '0m');
c.check('under a minute -> "0m" (floors to whole minutes)', _fmtDurationDHM(45) === '0m');
c.check('90 seconds -> "1m"', _fmtDurationDHM(90) === '1m');
c.check('under an hour -> just minutes', _fmtDurationDHM(60 * 42) === '42m');
c.check('exactly 1 hour -> "1h 0m"', _fmtDurationDHM(3600) === '1h 0m');
c.check('hours + minutes, no days', _fmtDurationDHM(3600 * 2 + 60 * 15) === '2h 15m');
c.check('exactly 1 day -> "1d 0h 0m"', _fmtDurationDHM(86400) === '1d 0h 0m');
c.check('days + hours + minutes', _fmtDurationDHM(86400 * 2 + 3600 * 5 + 60 * 30) === '2d 5h 30m');
c.check('large multi-day uptime', _fmtDurationDHM(86400 * 10 + 3600 * 3) === '10d 3h 0m');

c.finish();
