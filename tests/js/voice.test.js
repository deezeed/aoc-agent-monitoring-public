/* Voice assistant panel (web/js/20_voice.js): the yes/no reading and the
 * panel html, extracted from the shipped dashboard. */
const fs = require('fs');
const path = require('path');
const { readMonitorSource, extractFunction, WEB_DIR } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
const part = fs.readFileSync(path.join(WEB_DIR, 'js', '20_voice.js'), 'utf8');
const m = /^var _VO_LANGS=(.*);$/m.exec(part);
global._VO_LANGS = eval(m[1]);
for (const f of ['escHtml', '_voYesNo', '_voPanelHtml']) eval(extractFunction(src, f));
const c = new Checker();

c.check('yes in Slovak / Czech / English', ['áno', 'Áno, sprav to.', 'jasne', 'potvrď', 'yes', 'OK', 'do it'].every(t => _voYesNo(t) === true));
c.check('no', ['nie', 'Nie!', 'zruš', 'no', 'cancel'].every(t => _voYesNo(t) === false));
c.check('anything else is a new question', [_voYesNo('nastav minútku'), _voYesNo('ako sa máš'), _voYesNo('')].every(x => x === null)
  && _voYesNo('nokia') === null && _voYesNo('neviem') === null);

const off = _voPanelHtml({ settings: { enabled: false }, log: [] }, true);
c.check('off: explains and offers to turn on', off.includes('Turn the voice assistant on') && off.includes('only work at the PC itself'));
c.check('off, no speech recognition: says you can type', _voPanelHtml({ settings: { enabled: false }, log: [] }, false).includes('you can type instead'));
const st = { settings: { enabled: true, lang: 'sk-SK', speak: true, model: 'haiku' }, listening: false, busy: false, interim: '', err: '',
  log: [{ who: 'you', text: 'Čo robia <agenti>?' }, { who: 'aoc', text: 'Dvaja pracujú.', done: ['approve_plan: done'] }],
  pending: { token: 't', what: ['merge_run {"run_id": "R2"}'] } };
const on = _voPanelHtml(st, true);
c.check('on: the conversation (escaped), what was done, the question waiting for a yes', on.includes('Čo robia &lt;agenti&gt;?')
  && on.includes('Dvaja pracujú.') && on.includes('approve_plan: done') && on.includes('_voConfirm(true)') && on.includes('merge_run'));
c.check('on: mic + text input, language / speak / model, turn off', on.includes('class="vo-mic"') && on.includes('name="q"')
  && on.includes('<option value="sk-SK" selected>') && on.includes('speak answers') && on.includes('Turn off'));
c.check('listening: the mic stops', _voPanelHtml(Object.assign({}, st, { listening: true }), true).includes('_voStop()'));
c.check('no speech recognition: no mic, typing only', !_voPanelHtml(st, false).includes('vo-mic') && _voPanelHtml(st, false).includes('Type your question'));
c.check('nothing said yet: examples', _voPanelHtml(Object.assign({}, st, { log: [], pending: null }), true).includes('Schváľ plán'));
c.finish();
