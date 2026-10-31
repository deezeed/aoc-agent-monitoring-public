/* COMMAND view (Command Center) extracted from monitor.py: model names,
 * deterministic waveforms, the status pill's state, and _commandParts --
 * overview rows, waiting sessions first in the feed (AOC's start-up lines
 * skipped, commits after events), session cards, today's timeline (live /
 * waiting / done), rings, cache + models panels, footer, escaping and the
 * empty states. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
const a = src.indexOf('const _ccData=');
const b = src.indexOf("/* Pure: 'claude-opus-5-5'");
eval(src.slice(a, b).replace(/^(const|let) /gm, 'var '));
for (const f of ['escHtml', 'jsq', '_waitingSessions', '_waitLabel', '_lastMessageInfo', '_fmtDurShort', '_ccModelName', '_ccWave',
  '_ccWaveHtml', '_ccRing', '_commandParts', '_commandShell', '_ccPillState', '_ccSatAlpha', '_ccGlobeState'])
  eval(extractFunction(src, f));

const c = new Checker();
c.check('model names', _ccModelName('claude-opus-5-5') === 'Opus 5.5' && _ccModelName('claude-haiku-4-5-20251001') === 'Haiku 4.5'
  && _ccModelName('claude-sonnet-5') === 'Sonnet 5' && _ccModelName('') === '?');
c.check('waveform is deterministic and in range', JSON.stringify(_ccWave('abc', 8)) === JSON.stringify(_ccWave('abc', 8))
  && _ccWave('abc', 8).every(v => v >= 0.25 && v <= 1) && JSON.stringify(_ccWave('abc', 8)) !== JSON.stringify(_ccWave('abd', 8)));

const sess = (o) => ({ id: 's1', display_name: 'Proj <A>', session_active: true, model: 'claude-opus-5-5', estimated_cost: 1.5, ...o });
const sr = {
  sessions_list: [sess(), sess({ id: 's2', display_name: 'Waits', waiting_on_you: true, waiting_secs: 300, last_message: 'Shall I go on?' }),
    sess({ id: 's3', session_active: false, display_name: 'Closed' })],
  agents: [{ id: 'a1', session_id: 's1', status: 'running' }],
  rate_limits: { windows: [{ kind: 'five_hour', pct: 84, status: 'warn' }, { kind: 'seven_day', pct: 11, status: 'ok' }] },
  infra_health: { watchdog: { pid_alive: true } }, tunnel: { status: 'ready' },
};
const d = {
  an: { today: { cost: 12.345 } },
  hist: [{ id: 's1', date: '2026-10-27', started_at: '19:28:00', duration_s: 3600, title: 'Proj' },
    { id: 'old', date: '2026-10-27', started_at: '08:00:00', duration_s: 600, title: 'Morning <job>' },
    { id: 's2', date: '2026-10-27', started_at: '19:40:00', duration_s: 60, title: 'Waits' },
    { id: 'y', date: '2026-10-26', started_at: '10:00:00', duration_s: 60, title: 'Yesterday' }],
  sys: { cpu: 42.4, ram: 91, ram_total: 17112387584, disk: null, disk_total: 248923242496, disk_path: 'C:\\', host: 'DESKTOP-X' },
  cache: { stats: { total: { hit_rate: 0.9872, saved: 243.5, cost: 28 }, by_day: [{ day: '2026-10-26', cost: 3 }, { day: '2026-10-27', cost: 9 }],
    by_model: [{ model: 'claude-opus-5-5', cost: 26.19, n: 298 }, { model: 'claude-haiku-4-5-20251001', cost: 0.2, n: 12 }] } },
  cm: { stats: { commits: [{ subject: 'Fix <bug>', repo: 'aoc', epoch: 1793120000, cost: 0.37 }] } },
};
const notifs = [{ t: '20:00:01', type: 'success', msg: 'REAL-TIME AGENT TRACKING ENABLED' }, { t: '20:01:00', type: 'error', msg: 'SYSTEM FAILURE', tag: 'Explore' },
  { t: '20:02:00', type: 'info', msg: 'Shortcuts: Y C B' }];
const p = _commandParts(sr, d, 1793130000, '2026-10-27', notifs);
c.check('overview: sessions, agents, waiting, plan limit, spend, watchdog', p.ov.includes('2 sessions open') && p.ov.includes('1 running')
  && p.ov.includes('1 waiting') && p.ov.includes('84% of the 5-hour window') && p.ov.includes('class="warn">84%') && p.ov.includes('$12.35') && p.ov.includes('Running'));
c.check('hero: title only, numbers in the legend row', p.hero.includes('AGENT OPERATIONS CENTER') && !p.hero.includes('$')
  && p.heroStats.includes('<b>2</b>sessions') && p.heroStats.includes('cc-hs warn"><i></i><b>1</b>waiting')
  && p.heroStats.includes('cc-hs ok"><i></i><b>1</b>agent live') && p.heroStats.includes('<b>$12.35</b>today'));
c.check('feed: waiting session first with Open', p.feed.indexOf('Waits needs you') < p.feed.indexOf('SYSTEM FAILURE') && p.feed.includes('Shall I go on?') && p.feed.includes(">Open</button>"));
c.check('feed: start-up lines and the shortcut hint skipped', !p.feed.includes('REAL-TIME') && !p.feed.includes('Shortcuts'));
c.check('feed: event with its agent tag + ERROR tag, commit after', p.feed.includes('Explore · SYSTEM FAILURE') && p.feed.includes('>ERROR<')
  && p.feed.indexOf('SYSTEM FAILURE') < p.feed.indexOf('Fix &lt;bug&gt;') && p.feed.includes('>COMMIT<'));
c.check('session cards: waiting first, escaped, agents counted, closed left out', p.ags.indexOf('Waits') < p.ags.indexOf('Proj &lt;A&gt;')
  && p.ags.includes('Working · 1 agent') && p.ags.includes('Waiting on you') && !p.ags.includes('Closed') && p.ags.includes('cc-ag run'));
c.check('timeline: today only, in order, live / waiting / done', !p.tl.includes('Yesterday') && p.tl.indexOf('08:00') < p.tl.indexOf('19:28')
  && p.tl.includes('cc-tl-row done') && p.tl.includes('cc-tl-row live') && p.tl.includes('cc-tl-row wait') && p.tl.includes('Morning &lt;job&gt;'));
c.check('rings: values, red over 90, unknown as —', p.sys.includes('<b>42%</b>') && p.sys.includes('<b>91%</b>') && p.sys.includes('var(--r)')
  && p.sys.includes('<b>—</b>') && p.sys.includes('RAM 16 GB') && p.sys.includes('Disk C:\\ 232 GB'));
c.check('cache panel', p.cache.includes('99%') && p.cache.includes('$243.50') && p.cache.includes('$28.00') && p.cache.includes('<polyline'));
c.check('models panel', p.models.includes('Opus 5.5') && p.models.includes('$26.19 · 298 calls') && p.models.includes('Haiku 4.5') && p.modelsN === '2 in use');
c.check('footer: host, tunnel, weekly, launch', p.foot.includes('DESKTOP-X') && p.foot.includes('Tunnel live') && p.foot.includes('11% used')
  && p.foot.includes('openLaunch()'));
const e = _commandParts({}, {}, 1793130000, '2026-10-27', []);
c.check('hero legend when idle: no waiting chip, dim agents', e.heroStats.includes('<b>0</b>sessions') && !e.heroStats.includes('waiting')
  && e.heroStats.includes('cc-hs dim"><i></i><b>0</b>agents live'));
c.check('empty states', e.ags.includes('No Claude Code session is open') && e.tl.includes('No sessions yet today') && e.feed.includes('Nothing has happened')
  && e.models.includes('No API calls') && e.cache.includes('Reading') && e.ov.includes('No session open') && e.ov.includes('No data yet'));
c.check('one cache day -> no chart', _commandParts(sr, { cache: { stats: { total: {}, by_day: [{ cost: 1 }] } } }, 0, '', []).cache.includes('Not enough days'));
c.check('shell has every panel id', ['cc-ov', 'cc-hero-txt', 'cc-hero-stats', 'cc-feed', 'cc-ags', 'cc-tl', 'cc-sys', 'cc-cache', 'cc-models', 'cc-models-n', 'cc-foot', 'cc-globe']
  .every(id => _commandShell().includes(`id="${id}"`)));
c.check('pill', _ccPillState(null, 0)[1] === 'OFFLINE' && _ccPillState(sr, 30000)[1] === 'OFFLINE' && _ccPillState(sr, 1000)[1] === 'ATTENTION'
  && _ccPillState({ sessions_list: [] }, 0)[1] === 'OPTIMAL' && _ccPillState({ infra_health: { watchdog: { pid_alive: false } } }, 0)[1] === 'DEGRADED'
  && _ccPillState({ agents: [{ status: 'error' }] }, 0)[1] === 'DEGRADED');
const tb = { l: 100, r: 300, t: 50, b: 150 };
c.check('satellite hidden behind the title, fading in around it', _ccSatAlpha(200, 100, tb) === 0 && _ccSatAlpha(100, 50, tb) === 0
  && _ccSatAlpha(92, 100, tb) === 0.5 && _ccSatAlpha(200, 158, tb) === 0.5 && _ccSatAlpha(300 + 12, 150 + 16, tb) === 1
  && _ccSatAlpha(0, 0, tb) === 1);
const gs = _ccGlobeState(sr);
c.check('globe: one satellite per open session with name, waiting and its agents', gs.sats.length === 2
  && gs.sats[0].name === 'Proj <A>' && gs.sats[0].agents === 1 && !gs.sats[0].wait && gs.sats[1].wait && gs.waiting === 1 && gs.running === 1);
c.check('globe: signature changes with waiting / agents, stable otherwise', gs.sig === _ccGlobeState(sr).sig
  && gs.sig !== _ccGlobeState({ ...sr, agents: [] }).sig && gs.sig !== _ccGlobeState({ ...sr, sessions_list: sr.sessions_list.slice(0, 1) }).sig);
c.check('globe: empty status', _ccGlobeState(null).sats.length === 0 && _ccGlobeState(null).sig === '');
c.check('phones: dimmed to half behind the title instead of hidden', _ccSatAlpha(200, 100, tb, 0.5) === 0.5
  && _ccSatAlpha(92, 100, tb, 0.5) === 0.75 && _ccSatAlpha(0, 0, tb, 0.5) === 1);
c.finish();
