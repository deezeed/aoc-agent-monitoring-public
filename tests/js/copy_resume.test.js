/* Regression test for the "resume" clipboard copy button on a closed CLI
 * session card. Only _copyResumeCmd itself is tested here (extracted
 * straight from monitor.py) -- the button's render gating (!isActive &&
 * !sIsRemote) lives inside the much larger renderAgents() template, which
 * isn't practical to extract in isolation, and reuses the exact same
 * gating condition the existing Dismiss button already relies on. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

let _clipboardText = null;
let _toastCalls = [];
// Node 21+ ships a built-in read-only `navigator` global -- plain assignment
// silently no-ops, so this needs defineProperty to actually override it.
Object.defineProperty(global, 'navigator', {
  value: { clipboard: { writeText(text) { _clipboardText = text; return Promise.resolve(); } } },
  configurable: true,
  writable: true,
});
function showToast(type, title, msg) { _toastCalls.push({ type, title, msg }); }

const src = readMonitorSource();
eval(extractFunction(src, '_copyResumeCmd'));

const c = new Checker();

async function run() {
  _clipboardText = null;
  _toastCalls = [];
  _copyResumeCmd('a4492e3e-b4d1-4658-b10a-77c2c7c64826');
  // writeText resolves on a microtask -- flush it before asserting
  await Promise.resolve();
  await Promise.resolve();

  c.check(
    'copies the correct resume command to the clipboard',
    _clipboardText === 'claude --resume a4492e3e-b4d1-4658-b10a-77c2c7c64826',
  );
  c.check(
    'shows a confirmation toast after copying',
    _toastCalls.length === 1 && _toastCalls[0].type === 'info',
  );
  c.check(
    'toast message includes the copied command',
    _toastCalls[0].msg === 'claude --resume a4492e3e-b4d1-4658-b10a-77c2c7c64826',
  );

  c.finish();
}

run();
