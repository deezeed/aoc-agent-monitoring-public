/* Health -> DATABASE backups: _backupKind labels each backups/ file and
 * picks its restore confirm text, extracted from monitor.py. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, '_backupKind'));
const c = new Checker();

const h = _backupKind('history_2026-11-17.db');
c.check('history: label, noun, Session History note', h.label === 'History' && h.what === 'history.db'
  && h.confirm.includes('Session History/Analytics only'));
const pre = _backupKind('history_PRE_RESTORE_2026-11-17_101010.db');
c.check('history pre-restore copy is still history', pre.label === 'History');
const o = _backupKind('orchestration_2026-11-17.db');
c.check('orchestration: label + noun', o.label === 'Orchestrate' && o.what === 'orchestration.db');
c.check('orchestration confirm names the file and the busy rule', o.confirm.includes('"orchestration_2026-11-17.db"')
  && o.confirm.includes('Refused while a run, agent or scout is working'));
const s = _backupKind('state_2026-11-17.zip');
c.check('settings: label + restart hint', s.label === 'Settings' && s.what === 'settings' && s.confirm.includes('Restart AOC'));
c.check('junk input -> history default, no throw', _backupKind(undefined).label === 'History' && _backupKind(null).what === 'history.db');
c.check('row and restore use it', src.includes('_backupKind(b.filename).label') && src.includes('const kind=_backupKind(filename);'));
c.finish();
