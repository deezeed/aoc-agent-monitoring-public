/* Tests _fmtDurShort, extracted straight from monitor.py. Consolidates
 * what used to be 6 independently-written "Xs"/"Xm Ys" duration
 * formatters (compare modals, history view, KPI bar) that had drifted
 * (===null vs falsy checks, rounded vs not) -- this locks down the one
 * shared behavior all 6 call sites now depend on, including the
 * intentional fix where a real 0-second duration now shows "0s" instead
 * of "—" (previously inconsistent: 2 of 6 sites already did this, 4 didn't). */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, '_fmtDurShort'));

const c = new Checker();

c.check('null -> em dash', _fmtDurShort(null) === '—');
c.check('undefined -> em dash', _fmtDurShort(undefined) === '—');
c.check('real zero -> "0s" (not em dash)', _fmtDurShort(0) === '0s');
c.check('under a minute, no rounding needed', _fmtDurShort(45) === '45s');
c.check('exactly 59s stays in seconds', _fmtDurShort(59) === '59s');
c.check('exactly 60s crosses into minutes', _fmtDurShort(60) === '1m 0s');
c.check('minutes + seconds', _fmtDurShort(125) === '2m 5s');

// round=false (default): fractional seconds pass through untouched in the math
c.check('fractional seconds, under 60, round=false -> raw value shown', _fmtDurShort(45.7) === '45.7s');
c.check('fractional seconds, over 60, round=false -> floor for minutes, raw for seconds remainder',
        _fmtDurShort(125.7) === '2m 5.7s' || _fmtDurShort(125.7) === `2m ${125.7 % 60}s`);

// round=true: branch decision still uses the RAW value (matches the
// original renderKpi behavior exactly -- only the displayed numbers round)
c.check('round=true, under 60 -> rounds the displayed seconds', _fmtDurShort(59.6, true) === '60s');
c.check('round=true, boundary case: 59.6 still takes the <60 branch (raw check), not "0m 0s"',
        _fmtDurShort(59.6, true) === '60s');
c.check('round=true, over 60 -> minutes from floor(raw/60), seconds from round(raw)%60',
        _fmtDurShort(125.6, true) === '2m 6s');

c.finish();
