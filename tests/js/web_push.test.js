/* Phone push UI, extracted from monitor.py: _pushSupport explains when a
 * page can't subscribe (plain http through a LAN IP, iPhone outside the
 * Home Screen app, no Web Push), _b64uToBytes decodes the VAPID key. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['_pushSupport', '_b64uToBytes']) eval(extractFunction(src, f));
global.atob = global.atob || (s => Buffer.from(s, 'base64').toString('binary'));

const c = new Checker();
const W = (secure, ua, sw = true, pm = true) => {
  const w = { isSecureContext: secure, navigator: { userAgent: ua } };
  if (sw) w.navigator.serviceWorker = {};
  if (pm) w.PushManager = function () {};
  return w;
};
c.check('https + Web Push -> ok', _pushSupport(W(true, 'Android Chrome')).ok === true);
const http = _pushSupport(W(false, 'Android Chrome'));
c.check('plain http -> use the Remote access https link', !http.ok && http.why.includes('Remote access'));
const ios = _pushSupport(W(true, 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)', true, false));
c.check('iPhone in Safari -> Add to Home Screen first', !ios.ok && ios.why.includes('Add to Home Screen'));
c.check('no Web Push elsewhere -> says so', _pushSupport(W(true, 'Old browser', false, false)).why === 'This browser has no Web Push.');
const bytes = _b64uToBytes('BOZE-_8');
c.check('b64url -> bytes', bytes.length === 5 && bytes[0] === 0x04 && bytes[3] === 0xfb && bytes[4] === 0xff);
c.finish();
