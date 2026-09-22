/* Tests _buildSettingsBundle, extracted straight from monitor.py. This
 * is the settings export/import feature's bundle-shape logic -- takes
 * its pieces as arguments (not read from globals directly) so it stays
 * a plain, testable function independent of the DOM/localStorage. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

// _buildSettingsBundle uses `new Date().toISOString()` internally for
// exported_at -- freeze Date so the test doesn't need to special-case it.
const REAL_DATE = Date;
class FrozenDate extends REAL_DATE {
  constructor(...args) {
    if (args.length) super(...args);
    else super('2026-07-20T09:00:00.000Z');
  }
  static now() { return new FrozenDate().getTime(); }
}
global.Date = FrozenDate;

const src = readMonitorSource();
eval(extractFunction(src, '_buildSettingsBundle'));

const c = new Checker();

const state = {
  costRate: 9.5,
  budget: 2.5,
  projectBudgets: { AOC: 5, 'PHANTOM AI': 10 },
  webhookUrl: 'https://example.com/hook',
  webhookEvents: { done: true, error: true, stuck: false, burn_spike: true, weekly_digest: false, waiting_nudge: true },
  quietStart: '22:00',
  quietEnd: '07:00',
  mutedProjects: ['AOC'],
  snitchUrl: 'https://hc-ping.com/abc',
  digestCadence: 'daily',
  accent: 'violet',
  density: 'compact',
  sound: { volume: 0.5, profile: 'retro', events: { done: true } },
  localMachineName: 'Desktop',
  remoteMachines: [{ name: 'Laptop', url: 'http://192.168.1.5:5151', token: 'abc' }],
};

const bundle = _buildSettingsBundle(state);

c.check('bundle has a version number', bundle.version === 1);
c.check('bundle has an exported_at timestamp', typeof bundle.exported_at === 'string' && bundle.exported_at.length > 0);
c.check('cost_rate carried through', bundle.cost_rate === 9.5);
c.check('budget carried through', bundle.budget === 2.5);
c.check('project_budgets carried through as-is', JSON.stringify(bundle.project_budgets) === JSON.stringify({ AOC: 5, 'PHANTOM AI': 10 }));
c.check('webhook_url carried through', bundle.webhook_url === 'https://example.com/hook');
c.check('webhook_events carried through with all keys', bundle.webhook_events.burn_spike === true && bundle.webhook_events.waiting_nudge === true);
c.check('quiet_start/quiet_end carried through', bundle.quiet_start === '22:00' && bundle.quiet_end === '07:00');
c.check('muted_projects carried through', JSON.stringify(bundle.muted_projects) === JSON.stringify(['AOC']));
c.check('snitch_url carried through', bundle.snitch_url === 'https://hc-ping.com/abc');
c.check('digest_cadence carried through', bundle.digest_cadence === 'daily');
c.check('accent carried through', bundle.accent === 'violet');
c.check('density carried through', bundle.density === 'compact');
c.check('sound object carried through', bundle.sound.profile === 'retro');
c.check('local_machine_name carried through', bundle.local_machine_name === 'Desktop');
c.check('remote_machines carried through', bundle.remote_machines.length === 1 && bundle.remote_machines[0].name === 'Laptop');

// deliberately excluded: transient UI state (theme, current view, collapsed
// cards) is NOT part of this bundle -- it lives in aoc_prefs, not here.
c.check('no "theme" field leaks into the settings bundle (that\'s UI state, not config)', !('theme' in bundle));
c.check('no "view" field leaks into the settings bundle', !('view' in bundle));

global.Date = REAL_DATE;
c.finish();
