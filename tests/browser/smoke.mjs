// Browser smoke test: every main view and every Settings tab, at 1440 and
// 390 px, dark and light, in a real headless Chrome -- fails on any page
// JS exception, console.error or horizontal overflow.
//
//   node tests/browser/smoke.mjs [--shots DIR]
//
// Starts its own monitor.py on a free AOC_PORT with a temp AOC_DATA_DIR
// (any port but 5151 is a silent preview: no toasts, push or webhooks), so
// it never touches a running AOC. Drives Chrome over raw CDP (Node's global
// WebSocket), no puppeteer. Chrome: CHROME_PATH, else the usual install
// paths. Lives outside tests/js so tests/run_all.ps1 doesn't pick it up.
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const VIEWS = ['command', 'today', 'inbox', 'orch', 'agents', 'cli', 'history', 'diag'];
const SIZES = [[1440, 900], [390, 844]];
const shotsArg = process.argv.indexOf('--shots');
const SHOTS = shotsArg > 0 ? resolve(process.argv[shotsArg + 1] || 'smoke-shots') : '';
const sleep = ms => new Promise(r => setTimeout(r, ms));

function findChrome() {
  const c = [process.env.CHROME_PATH,
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    process.env.LOCALAPPDATA && join(process.env.LOCALAPPDATA, 'Google/Chrome/Application/chrome.exe'),
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    '/usr/bin/google-chrome', '/usr/bin/chromium'].filter(Boolean);
  return c.find(p => existsSync(p));
}

const freePort = () => new Promise((res, rej) => {
  const s = createServer(); s.unref();
  s.on('error', rej);
  s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); });
});

function killTree(proc) {
  if (!proc || proc.exitCode !== null) return;
  if (process.platform === 'win32') spawnSync('taskkill', ['/T', '/F', '/PID', String(proc.pid)], { stdio: 'ignore' });
  else proc.kill('SIGKILL');
}

async function waitFor(fn, ms, what) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    try { const v = await fn(); if (v) return v; } catch { /* not up yet */ }
    await sleep(300);
  }
  throw new Error(`timed out waiting for ${what}`);
}

// a session with a few agents + a waiting one, so the views aren't all empty states
async function seed(base) {
  const post = body => fetch(base + '/update', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  await post({ session_id: 'smoke-s1', cwd: 'C:\\proj\\shop-frontend', session_active: true, display_name: 'Shop frontend <redesign>',
    project: 'shop-frontend', orchestrator: 'Claude', agents: [
      { id: 'smoke-a1', unit: 'UI', name: 'Checkout page with a rather long agent name to test truncation', status: 'running',
        tasks: [{ name: 'layout', status: 'done' }, { name: 'tests', status: 'running' }] },
      { id: 'smoke-a2', unit: 'DB', name: 'Migrations', status: 'done', tasks: [{ name: 'schema', status: 'done' }] },
      { id: 'smoke-a3', unit: 'ER', name: 'Payment webhook', status: 'error', error: 'TypeError: cannot read "id"', tasks: [] }] });
  await post({ session_id: 'smoke-s2', cwd: 'C:\\proj\\memebot', session_active: true, display_name: 'Memebot', waiting_on_you: true, agents: [] });
  // a repeating run (paused: it never fires here), so Orchestrate shows a schedule card and its edit form
  const orch = (path, body) => fetch(base + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(r => r.json());
  const s = await orch('/orch/schedule_save', { goal: 'Weekly report on the <shop> orders, with a long goal to wrap on a phone',
    workspace: 'smoke-ws', schedule: { type: 'weekdays', at: '03:00' }, budget_usd: 2, budget_day: 5, budget_month: 50 });
  if (!s.ok) throw new Error('seed schedule: ' + s.error);
  await orch('/orch/schedule_pause', { id: s.id, paused: true });
  return s.id;
}

let monitor, chrome, data, profile;
const problems = [], rows = [];
try {
  const chromePath = findChrome();
  if (!chromePath) throw new Error('no Chrome found (set CHROME_PATH)');
  const port = await freePort();
  const base = `http://127.0.0.1:${port}`;
  data = mkdtempSync(join(tmpdir(), 'aoc-smoke-data-'));
  monitor = spawn(process.env.PYTHON || 'python', [join(ROOT, 'monitor.py'), '--headless'], {
    cwd: ROOT, stdio: 'ignore', env: { ...process.env, AOC_PORT: String(port), AOC_DATA_DIR: data } });
  await waitFor(async () => (await fetch(base + '/status')).ok, 120000, 'monitor /status');
  const schedId = await seed(base);

  const cdpPort = await freePort();
  profile = mkdtempSync(join(tmpdir(), 'aoc-smoke-chrome-'));
  chrome = spawn(chromePath, ['--headless=new', `--remote-debugging-port=${cdpPort}`, '--no-first-run',
    '--no-default-browser-check', `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' });
  const tabs = await waitFor(async () => (await fetch(`http://127.0.0.1:${cdpPort}/json`)).json(), 30000, 'Chrome DevTools');
  const ws = new WebSocket(tabs.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  let id = 0, errors = [], onLoad = null;
  const pend = {};
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id && pend[m.id]) { pend[m.id](m); delete pend[m.id]; }
    if (m.method === 'Page.loadEventFired' && onLoad) { onLoad(); onLoad = null; }
    if (m.method === 'Runtime.exceptionThrown') {
      const d = m.params.exceptionDetails;
      errors.push(((d.exception && d.exception.description) || d.text || 'exception').split('\n')[0]);
    }
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
      errors.push('console.error: ' + m.params.args.map(a => a.value ?? a.description ?? '').join(' ').split('\n')[0]);
    }
  };
  const send = (method, params = {}) => new Promise(r => { pend[++id] = r; ws.send(JSON.stringify({ id, method, params })); });
  const ev = async expr => {
    const m = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (m.result && m.result.exceptionDetails) errors.push('evaluate: ' + (m.result.exceptionDetails.exception?.description || '').split('\n')[0]);
    return m.result && m.result.result ? m.result.result.value : undefined;
  };
  await send('Runtime.enable'); await send('Page.enable');
  if (SHOTS) mkdirSync(SHOTS, { recursive: true });

  const check = async (label, extra = '') => {
    // the page itself, and each visible view container (*-area scrolls on
    // its own, so content too wide for it never widens the page)
    const over = await ev(`(()=>{const d=document.documentElement; const o=[]; if(d.scrollWidth>innerWidth+1) o.push('page '+d.scrollWidth+'>'+innerWidth);
      document.querySelectorAll('[id$="-area"]').forEach(el=>{ if(el.offsetParent&&el.id!=='xterm-area'&&el.scrollWidth>el.clientWidth+1) o.push('#'+el.id+' '+el.scrollWidth+'>'+el.clientWidth); });
      ${extra} return o;})()`) || [];
    const bad = [...over.map(x => 'overflow: ' + x), ...errors.map(x => 'JS: ' + x)];
    rows.push([label, bad.length ? 'FAIL' : 'ok']);
    bad.forEach(b => problems.push(`${label}: ${b}`));
    errors = [];
    if (SHOTS) {
      const s = await send('Page.captureScreenshot', { format: 'png' });
      writeFileSync(join(SHOTS, label.replace(/[^\w.-]+/g, '_') + '.png'), Buffer.from(s.result.data, 'base64'));
    }
  };

  for (const [w, h] of SIZES) for (const theme of ['dark', 'light']) {
    await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: w < 500 });
    await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: theme }] });
    // load the dashboard; one retry -- a headless navigation now and then
    // never fires its load event (seen once in ~5 local runs)
    for (let attempt = 1; ; attempt++) {
      errors = [];
      const loaded = new Promise(r => { onLoad = r; });
      await send('Page.navigate', { url: base + '/' });
      const ok = await Promise.race([loaded.then(() => true), sleep(45000).then(() => false)]) &&
        await waitFor(() => ev(`typeof setView==='function'`), 20000, 'dashboard script').catch(() => false);
      if (ok) break;
      const state = await ev(`location.href+' '+document.readyState`).catch(() => '?');
      if (attempt >= 2) throw new Error(`dashboard did not load at ${w} ${theme} (page: ${state})`);
    }
    await sleep(2500);
    if (theme === 'light') await ev(`document.body.classList.add('light')`);
    await check(`load ${w} ${theme}`);
    for (const v of VIEWS) {
      await ev(`setView(${JSON.stringify(v)})`); await sleep(1500);
      await check(`${v} ${w} ${theme}`);
      if (v === 'orch') {   // the schedule's Edit opens the form filled in; Cancel puts it back
        const card = await ev(`!!document.querySelector('.or-sch') && document.body.innerHTML.includes('_orScheduleRun(')`);
        if (!card) problems.push(`orch ${w} ${theme}: no schedule card with Run now`);
        await ev(`_orScheduleEdit(${JSON.stringify(schedId)})`); await sleep(500);
        if (!await ev(`!!document.querySelector('.or-new-ed[open]')`)) problems.push(`orch ${w} ${theme}: Edit did not open the form`);
        await check(`orch:edit ${w} ${theme}`);
        await ev(`_orScheduleEditCancel()`);
      }
    }
    const panes = await ev(`[...document.querySelectorAll('.settings-tab')].map(t=>t.dataset.pane)`) || [];
    if (!panes.length) problems.push(`settings ${w} ${theme}: no tabs found`);
    await ev(`openSettings()`); await sleep(500);
    for (const p of panes) {
      await ev(`setSettingsTab(${JSON.stringify(p)})`); await sleep(700);
      await check(`settings:${p} ${w} ${theme}`,
        `const sp=document.querySelector('.settings-panel'); if(sp&&sp.scrollWidth>sp.clientWidth+1) o.push('settings panel '+sp.scrollWidth+'>'+sp.clientWidth);`);
    }
    await ev(`closeSettings()`);
  }
  ws.close();
} catch (e) {
  problems.push('smoke run: ' + (e && e.message || e));
} finally {
  killTree(chrome);
  killTree(monitor);
  await sleep(500);
  for (const d of [profile, data]) if (d) try { rmSync(d, { recursive: true, force: true, maxRetries: 5 }); } catch { /* locked; temp dir anyway */ }
  // monitor.py was killed, so it never released its per-port lock file
  try { spawnSync(process.execPath, ['-e', `try{require('fs').readdirSync(${JSON.stringify(ROOT)}).filter(f=>/^monitor-\\d+\\.pid$/.test(f)&&f!=='monitor-5151.pid').forEach(f=>{const p=require('path').join(${JSON.stringify(ROOT)},f);const pid=+require('fs').readFileSync(p,'utf8');try{process.kill(pid,0)}catch{require('fs').unlinkSync(p)}})}catch{}`]); } catch { /* ignore */ }
}

const wide = Math.max(10, ...rows.map(r => r[0].length));
for (const [label, res] of rows) console.log(label.padEnd(wide) + '  ' + res);
console.log(`\n${rows.length} checks, ${problems.length} problem(s)`);
if (problems.length) { console.log(problems.map(p => '  - ' + p).join('\n')); process.exit(1); }
console.log('Browser smoke passed.');
