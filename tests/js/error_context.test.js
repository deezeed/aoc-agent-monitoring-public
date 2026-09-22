/* Tests _buildErrorContext, extracted straight from monitor.py. The
 * existing "copy" button on an error card only ever copied the bare
 * error_message; this bundles project + task + error + log tail into one
 * paste-ready block, mirroring the existing resume-copy button's UX. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, '_buildErrorContext'));

const c = new Checker();

c.check('null agent -> empty string', _buildErrorContext(null) === '');
c.check('undefined agent -> empty string', _buildErrorContext(undefined) === '');

const full = {
  name: 'Fix auth middleware',
  session_project: 'PHANTOM AI',
  status: 'error',
  description: 'Investigate token refresh bug',
  tasks: [{ done: true }, { done: true }, { done: false }],
  error_message: 'TypeError: cannot read property of undefined',
  log: Array.from({ length: 15 }, (_, i) => `line ${i}`),
};
const out = _buildErrorContext(full);

c.check('includes agent name', out.includes('Agent: Fix auth middleware'));
c.check('includes project', out.includes('Project: PHANTOM AI'));
c.check('includes status', out.includes('Status: error'));
c.check('includes task description', out.includes('Task: Investigate token refresh bug'));
c.check('includes subtask progress (2/3 done)', out.includes('Subtasks: 2/3 done'));
c.check('includes the error message', out.includes('Error: TypeError: cannot read property of undefined'));
c.check('log tail is capped at the last 10 entries', out.includes('Log tail (last 10)'));
c.check('log tail includes the most recent line', out.includes('line 14'));
c.check('log tail excludes lines beyond the last 10', !out.includes('line 4\n') && !out.includes('line 0'));

const minimal = { name: 'Bare agent', status: 'error' };
const outMinimal = _buildErrorContext(minimal);
c.check('minimal agent still includes name and status', outMinimal.includes('Agent: Bare agent') && outMinimal.includes('Status: error'));
c.check('minimal agent has no Project line', !outMinimal.includes('Project:'));
c.check('minimal agent has no Task line', !outMinimal.includes('Task:'));
c.check('minimal agent has no Subtasks line', !outMinimal.includes('Subtasks:'));
c.check('minimal agent has no Error line', !outMinimal.includes('Error:'));
c.check('minimal agent has no log tail section', !outMinimal.includes('Log tail'));

const unnamed = { status: 'error' };
c.check('missing name falls back to "(unnamed)"', _buildErrorContext(unnamed).includes('Agent: (unnamed)'));

const noTasks = { name: 'x', status: 'error', tasks: [] };
c.check('empty tasks array produces no Subtasks line', !_buildErrorContext(noTasks).includes('Subtasks:'));

c.finish();
