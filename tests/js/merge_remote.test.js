/* Tests _mergeRemoteData, _mergeAnalytics, _mergeHistorySessions -- the
 * multi-machine merge logic. Extracted straight from monitor.py so this
 * can never silently drift from the shipped code. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

let _remoteMachines = [];
let _remoteCache = {};
let _localMachineName = '';

const src = readMonitorSource();
eval(extractFunction(src, '_mergeRemoteData'));
eval(extractFunction(src, '_mergeAnalytics'));
eval(extractFunction(src, '_mergeHistorySessions'));

const c = new Checker();

// ── _mergeRemoteData ──────────────────────────────────────────────────
const localSr = {
  sessions_list: [{ id: 'sess-local-1', project: 'Git' }],
  agents: [{ id: 'ag_aaaa111111', name: 'Local agent', session_id: 'sess-local-1' }],
  sessions: { 'sess-local-1': { last_seen_epoch: 1000, dismissed: false } },
  sessions_count: 1,
  tunnel: { status: 'off' },
};

_remoteMachines = [];
_remoteCache = {};
let out = _mergeRemoteData(localSr);
c.check('_mergeRemoteData: no remotes -> pass-through (same object)', out === localSr);

_remoteMachines = [{ name: 'Desktop', url: 'http://192.168.1.50:5151', token: 'tok' }];
_remoteCache = {
  0: {
    ok: true,
    data: {
      sessions_list: [{ id: 'sess-remote-1', project: 'PHANTOM AI' }],
      agents: [{ id: 'ag_bbbb222222', name: 'Remote agent', session_id: 'sess-remote-1' }],
      sessions: { 'sess-remote-1': { last_seen_epoch: 2000, dismissed: false } },
    },
  },
};
out = _mergeRemoteData(localSr);
c.check('_mergeRemoteData: sessions_list has both local and remote', out.sessions_list.length === 2);
c.check('_mergeRemoteData: agents has both local and remote', out.agents.length === 2);
c.check('_mergeRemoteData: sessions_count recomputed to merged length', out.sessions_count === 2);
const localSession = out.sessions_list.find(s => s.id === 'sess-local-1');
const remoteSession = out.sessions_list.find(s => s.id === 'sess-remote-1');
c.check('_mergeRemoteData: local session tagged _isLocal=true', localSession._isLocal === true);
c.check('_mergeRemoteData: remote session tagged _isLocal=false, machine=Desktop', remoteSession._isLocal === false && remoteSession.machine === 'Desktop');
c.check('_mergeRemoteData: domIds prefixed local_/m0_', localSession._domId === 'local_sess-local-1' && remoteSession._domId === 'm0_sess-remote-1');
c.check('_mergeRemoteData: raw sessions dict merged (checkStaleAgents lookup)', out.sessions['sess-remote-1'] && out.sessions['sess-remote-1'].last_seen_epoch === 2000);
c.check('_mergeRemoteData: tunnel field stays local-only', out.tunnel.status === 'off');

_remoteCache = { 0: { ok: false, data: null } };
out = _mergeRemoteData(localSr);
c.check('_mergeRemoteData: failed remote (ok:false) excluded cleanly', out.sessions_list.length === 1);

_remoteCache = {
  0: { ok: true, data: { sessions_list: [], agents: [{ id: 'ag_aaaa111111', name: 'collides', session_id: 'x' }], sessions: {} } },
};
out = _mergeRemoteData(localSr);
const collided = out.agents.filter(a => a.id === 'ag_aaaa111111');
c.check('_mergeRemoteData: id collision still gets distinct _domId', collided.length === 2 && collided[0]._domId !== collided[1]._domId);

// ── _mergeAnalytics ───────────────────────────────────────────────────
_remoteMachines = [{ name: 'Desktop', url: 'http://x', token: '' }];
const localAnalytics = {
  total: { sessions: 10, cost: 5.0 },
  hook_misses: 3,
  by_day: [{ date: '2026-07-18', sessions: 2, cost: 1.0, waiting_on_you_s: 200 }],
  by_project: [{ project: 'AOC', sessions: 6, cost: 3.0 }],
  by_day_project: [{ date: '2026-07-18', project: 'AOC', cost: 1.0 }],
  by_model: [{ model: 'claude-sonnet-4-5-20250514', agents: 4, tokens: 40000, cost: 3.0, tool_use_count: 20 }],
  by_day_model: [{ date: '2026-07-18', model: 'claude-sonnet-4-5-20250514', tokens: 30000, cost: 3.0 }],
  by_subagent_type: [{ subagent_type: 'Explore', agents: 4, tokens: 40000, cost: 3.0 }],
  by_day_subagent_type: [{ date: '2026-07-18', subagent_type: 'Explore', tokens: 30000, cost: 3.0 }],
  by_day_hook_reliability: [{ date: '2026-07-18', misses: 1, total: 10 }],
  file_hotspots: [{ path: 'monitor.py', changes: 5, sessions: 3, total_lines: 100 }],
  by_day_file_hotspots: [{ date: '2026-07-18', path: 'monitor.py', changes: 5 }],
  by_file_type: [{ ext: 'py', changes: 5, sessions: 3, total_lines: 100 }],
  tag_cloud: [{ tag: 'refactor', count: 5 }],
  retry_count: 2,
  retry_patterns: [{ name: 'Fix bug X', retries: 2, sessions: 1 }],
  slowest_agents: [{ agent_id: 'a1', name: 'Local slow', session_id: 's1', duration_s: 300, status: 'done' }],
  common_errors: [{ error_msg: 'Timeout', occurrences: 3, last_session_id: 's1', project: 'AOC' }],
};
const remoteAnalytics = {
  total: { sessions: 5, cost: 2.5 },
  hook_misses: 1,
  by_day: [{ date: '2026-07-18', sessions: 1, cost: 0.5, waiting_on_you_s: 50 }, { date: '2026-07-16', sessions: 2, cost: 1.0, waiting_on_you_s: 30 }],
  by_project: [{ project: 'AOC', sessions: 2, cost: 1.0 }, { project: 'AOC Monitor', sessions: 3, cost: 1.5 }],
  by_day_project: [{ date: '2026-07-18', project: 'AOC', cost: 0.5 }],
  by_model: [{ model: 'claude-sonnet-4-5-20250514', agents: 2, tokens: 10000, cost: 1.0, tool_use_count: 8 }, { model: 'claude-opus-4-1-20250805', agents: 1, tokens: 5000, cost: 1.5, tool_use_count: 5 }],
  by_day_model: [{ date: '2026-07-18', model: 'claude-sonnet-4-5-20250514', tokens: 10000, cost: 1.0 }, { date: '2026-07-18', model: 'claude-opus-4-1-20250805', tokens: 5000, cost: 1.5 }],
  by_subagent_type: [{ subagent_type: 'Explore', agents: 2, tokens: 10000, cost: 1.0 }, { subagent_type: 'Plan', agents: 1, tokens: 5000, cost: 1.5 }],
  by_day_subagent_type: [{ date: '2026-07-18', subagent_type: 'Explore', tokens: 10000, cost: 1.0 }, { date: '2026-07-18', subagent_type: 'Plan', tokens: 5000, cost: 1.5 }],
  by_day_hook_reliability: [{ date: '2026-07-18', misses: 2, total: 5 }, { date: '2026-07-16', misses: 1, total: 4 }],
  file_hotspots: [{ path: 'monitor.py', changes: 2, sessions: 2, total_lines: 40 }, { path: 'README.md', changes: 8, sessions: 4, total_lines: 200 }],
  by_day_file_hotspots: [{ date: '2026-07-18', path: 'monitor.py', changes: 2 }, { date: '2026-07-18', path: 'README.md', changes: 8 }],
  by_file_type: [{ ext: 'py', changes: 2, sessions: 2, total_lines: 40 }, { ext: 'md', changes: 8, sessions: 4, total_lines: 200 }],
  tag_cloud: [{ tag: 'refactor', count: 2 }, { tag: 'experiment', count: 8 }],
  retry_count: 1,
  retry_patterns: [{ name: 'Fix bug X', retries: 1, sessions: 1 }, { name: 'Remote task', retries: 2, sessions: 2 }],
  slowest_agents: [{ agent_id: 'b1', name: 'Remote slow', session_id: 'r1', duration_s: 900, status: 'error' }],
  common_errors: [{ error_msg: 'Timeout', occurrences: 2, last_session_id: 'r1', project: 'PHANTOM AI' }, { error_msg: 'FileNotFoundError', occurrences: 10, last_session_id: 'r2', project: 'AOC' }],
};
const mergedAnalytics = _mergeAnalytics(localAnalytics, [remoteAnalytics]);
c.check('_mergeAnalytics: total summed', mergedAnalytics.total.sessions === 15 && mergedAnalytics.total.cost === 7.5);
c.check('_mergeAnalytics: hook_misses summed', mergedAnalytics.hook_misses === 4);
c.check('_mergeAnalytics: by_day same-date rows summed', mergedAnalytics.by_day.find(r => r.date === '2026-07-18').cost === 1.5);
c.check('_mergeAnalytics: by_day remote-only date included', !!mergedAnalytics.by_day.find(r => r.date === '2026-07-16'));
c.check('_mergeAnalytics: by_day sums waiting_on_you_s too (200+50=250)', mergedAnalytics.by_day.find(r => r.date === '2026-07-18').waiting_on_you_s === 250);
c.check('_mergeAnalytics: by_project same-project summed', mergedAnalytics.by_project.find(r => r.project === 'AOC').cost === 4.0);
c.check('_mergeAnalytics: by_project remote-only project included', !!mergedAnalytics.by_project.find(r => r.project === 'AOC Monitor'));
c.check('_mergeAnalytics: by_model same-model summed', mergedAnalytics.by_model.find(r => r.model === 'claude-sonnet-4-5-20250514').cost === 4.0);
c.check('_mergeAnalytics: by_model remote-only model included', !!mergedAnalytics.by_model.find(r => r.model === 'claude-opus-4-1-20250805'));
c.check('_mergeAnalytics: by_model sorted by cost descending', mergedAnalytics.by_model[0].model === 'claude-sonnet-4-5-20250514');
c.check('_mergeAnalytics: by_model sums tool_use_count too (20+8=28)', mergedAnalytics.by_model.find(r => r.model === 'claude-sonnet-4-5-20250514').tool_use_count === 28);
c.check('_mergeAnalytics: by_day_model same date+model summed', mergedAnalytics.by_day_model.find(r => r.date === '2026-07-18' && r.model === 'claude-sonnet-4-5-20250514').cost === 4.0);
c.check('_mergeAnalytics: by_day_model remote-only date+model included', !!mergedAnalytics.by_day_model.find(r => r.model === 'claude-opus-4-1-20250805'));
c.check('_mergeAnalytics: by_subagent_type same-type summed', mergedAnalytics.by_subagent_type.find(r => r.subagent_type === 'Explore').cost === 4.0);
c.check('_mergeAnalytics: by_subagent_type remote-only type included', !!mergedAnalytics.by_subagent_type.find(r => r.subagent_type === 'Plan'));
c.check('_mergeAnalytics: by_subagent_type sorted by cost descending', mergedAnalytics.by_subagent_type[0].subagent_type === 'Explore');
c.check('_mergeAnalytics: by_day_subagent_type same date+type summed', mergedAnalytics.by_day_subagent_type.find(r => r.date === '2026-07-18' && r.subagent_type === 'Explore').cost === 4.0);
c.check('_mergeAnalytics: by_day_subagent_type remote-only date+type included', !!mergedAnalytics.by_day_subagent_type.find(r => r.subagent_type === 'Plan'));
c.check('_mergeAnalytics: file_hotspots same-path summed (changes/sessions/total_lines)', (() => {
  const r = mergedAnalytics.file_hotspots.find(r => r.path === 'monitor.py');
  return r.changes === 7 && r.sessions === 5 && r.total_lines === 140;
})());
c.check('_mergeAnalytics: file_hotspots remote-only path included', !!mergedAnalytics.file_hotspots.find(r => r.path === 'README.md'));
c.check('_mergeAnalytics: file_hotspots sorted by changes descending', mergedAnalytics.file_hotspots[0].path === 'README.md');
c.check('_mergeAnalytics: by_file_type same-ext summed (changes/sessions/total_lines)', (() => {
  const r = mergedAnalytics.by_file_type.find(r => r.ext === 'py');
  return r.changes === 7 && r.sessions === 5 && r.total_lines === 140;
})());
c.check('_mergeAnalytics: by_file_type remote-only ext included', !!mergedAnalytics.by_file_type.find(r => r.ext === 'md'));
c.check('_mergeAnalytics: by_file_type sorted by changes descending', mergedAnalytics.by_file_type[0].ext === 'md');
c.check('_mergeAnalytics: tag_cloud same-tag counts summed (5+2=7)', mergedAnalytics.tag_cloud.find(r => r.tag === 'refactor').count === 7);
c.check('_mergeAnalytics: tag_cloud remote-only tag included', !!mergedAnalytics.tag_cloud.find(r => r.tag === 'experiment'));
c.check('_mergeAnalytics: tag_cloud sorted by count descending', mergedAnalytics.tag_cloud[0].tag === 'experiment');
c.check('_mergeAnalytics: retry_count summed (2+1=3)', mergedAnalytics.retry_count === 3);
c.check('_mergeAnalytics: retry_patterns same-name retries/sessions summed (2+1=3, 1+1=2)', (() => {
  const r = mergedAnalytics.retry_patterns.find(r => r.name === 'Fix bug X');
  return r.retries === 3 && r.sessions === 2;
})());
c.check('_mergeAnalytics: retry_patterns remote-only name included', !!mergedAnalytics.retry_patterns.find(r => r.name === 'Remote task'));
c.check('_mergeAnalytics: retry_patterns sorted by retries descending', mergedAnalytics.retry_patterns[0].name === 'Fix bug X');
c.check('_mergeAnalytics: slowest_agents concatenated local+remote', mergedAnalytics.slowest_agents.length === 2);
c.check('_mergeAnalytics: slowest_agents sorted by duration_s descending (remote 900s beats local 300s)', mergedAnalytics.slowest_agents[0].agent_id === 'b1');
c.check('_mergeAnalytics: slowest_agents local row tagged _isLocal=true', mergedAnalytics.slowest_agents.find(r => r.agent_id === 'a1')._isLocal === true);
c.check('_mergeAnalytics: slowest_agents remote row tagged _isLocal=false + machine name', (() => {
  const r = mergedAnalytics.slowest_agents.find(r => r.agent_id === 'b1');
  return r._isLocal === false && r.machine === 'Desktop';
})());
c.check('_mergeAnalytics: by_day_file_hotspots same date+path changes summed (5+2=7)', (() => {
  const r = mergedAnalytics.by_day_file_hotspots.find(r => r.date === '2026-07-18' && r.path === 'monitor.py');
  return r.changes === 7;
})());
c.check('_mergeAnalytics: by_day_file_hotspots remote-only path included', !!mergedAnalytics.by_day_file_hotspots.find(r => r.path === 'README.md'));
c.check('_mergeAnalytics: common_errors same-text occurrences summed (3+2=5)', (() => {
  const r = mergedAnalytics.common_errors.find(r => r.error_msg === 'Timeout');
  return r.occurrences === 5;
})());
c.check('_mergeAnalytics: common_errors remote-only error text included', !!mergedAnalytics.common_errors.find(r => r.error_msg === 'FileNotFoundError'));
c.check('_mergeAnalytics: common_errors sorted by occurrences descending', mergedAnalytics.common_errors[0].error_msg === 'FileNotFoundError');
c.check('_mergeAnalytics: common_errors keeps the existing (local) last_session_id/project on a merge, not the remote one', (() => {
  const r = mergedAnalytics.common_errors.find(r => r.error_msg === 'Timeout');
  return r.last_session_id === 's1' && r.project === 'AOC';
})());
c.check('_mergeAnalytics: by_day_hook_reliability same-date misses/total summed', (() => {
  const r = mergedAnalytics.by_day_hook_reliability.find(r => r.date === '2026-07-18');
  return r.misses === 3 && r.total === 15;
})());
c.check('_mergeAnalytics: by_day_hook_reliability remote-only date included', !!mergedAnalytics.by_day_hook_reliability.find(r => r.date === '2026-07-16'));
c.check('_mergeAnalytics: null remote data excluded cleanly', _mergeAnalytics(localAnalytics, [null]).total.sessions === 10);
c.check('_mergeAnalytics: no remotes configured -> pass-through', (() => { _remoteMachines = []; return _mergeAnalytics(localAnalytics, []) === localAnalytics; })());

// ── _mergeHistorySessions ─────────────────────────────────────────────
_remoteMachines = [{ name: 'Desktop', url: 'http://x', token: '' }];
_localMachineName = 'Laptop';
const localSessions = [
  { id: 'l1', started_at: '2026-07-18 10:00:00' },
  { id: 'l2', started_at: '2026-07-16 09:00:00' },
];
const remoteSessions = [{ id: 'r1', started_at: '2026-07-17 12:00:00' }];
const mergedHistory = _mergeHistorySessions(localSessions, [remoteSessions]);
c.check('_mergeHistorySessions: total count is local+remote', mergedHistory.length === 3);
c.check('_mergeHistorySessions: local tagged with local machine name', mergedHistory.find(s => s.id === 'l1').machine === 'Laptop');
c.check('_mergeHistorySessions: remote tagged with configured machine name', mergedHistory.find(s => s.id === 'r1').machine === 'Desktop' && mergedHistory.find(s => s.id === 'r1')._isLocal === false);
c.check('_mergeHistorySessions: sorted newest-first across machines', mergedHistory[0].id === 'l1' && mergedHistory[1].id === 'r1' && mergedHistory[2].id === 'l2');
c.check('_mergeHistorySessions: null remote data excluded cleanly', _mergeHistorySessions(localSessions, [null]).length === 2);

c.finish();
