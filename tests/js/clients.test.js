/* History -> CLIENTS (_clientsHtml, _clientEditRow extracted from
 * monitor.py): month picker, totals, a card per client (markup, billed,
 * export links carrying the token, CSV locked without Pro), unassigned
 * last, per-session client picker showing overrides, editor, escaping. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_clientsHtml', '_clientEditRow'])
  eval(extractFunction(src, f));

const c = new Checker();
const S = (id, title, cost, extra = {}) => ({ id, title, cost, tokens: 1000, first_day: '2026-10-02', last_day: '2026-10-02',
  commits: 0, repos: [], how: '', ...extra });
const d = {
  month: '2026-10',
  config: { clients: [{ name: 'Omni <Social>', match: ['omnisocial'], markup: 20 }, { name: 'Phantom', match: ['phantom'], markup: 0 }],
            overrides: { m1: 'Phantom' } },
  report: { month: '2026-10', months: ['2026-10', '2026-09'], total: 24,
    clients: [
      { name: 'Omni <Social>', markup: 20, cost: 16.5, billed: 19.8, commits: 2, sessions: [
        S('o1', 'Omnisocial pokračování', 13.5, { last_day: '2026-10-03', commits: 2, repos: ['omnisocial-web'], how: 'keyword: omnisocial' }),
        S('o2', 'Pokračuj', 3) ] },
      { name: 'Phantom', markup: 0, cost: 6, billed: 6, commits: 1, sessions: [S('m1', 'Manual one', 2, { how: 'manual' }), S('p1', 'Phantom AI', 4)] },
      { name: '', markup: 0, cost: 0.5, billed: 0.5, commits: 0, sessions: [S('u1', 'Random <q>', 0.5)] },
    ] },
};

let h = _clientsHtml(d, false, true, 'tok&1');
c.check('month picker with both months, current selected', h.includes('<option value="2026-10" selected>') && h.includes('<option value="2026-09">'));
c.check('summary: total, assigned, unassigned', h.includes('<b>$24.00</b> spent') && h.includes('<b>$22.50</b> assigned to 2 clients') && h.includes('<b>$0.50</b> unassigned'));
c.check('client name escaped', h.includes('Omni &lt;Social&gt;') && !h.includes('Omni <Social>'));
c.check('markup -> billed', h.includes('+20% → <b>$19.80</b> to bill'));
c.check('no markup -> no billed line for Phantom', !h.includes('+0%'));
c.check('date range + repos + commits on a row', h.includes('10-02 – 10-03') && h.includes('omnisocial-web'));
c.check('report link carries month, client and token', h.includes('/clients/print?month=2026-10&amp;client=Omni%20%3CSocial%3E&amp;token=tok%261'));
c.check('CSV link when Pro', h.includes('/clients/export.csv?month=2026-10'));
c.check('unassigned card last', h.indexOf('>Unassigned<') > h.indexOf('>Phantom<') && h.includes('Random &lt;q&gt;'));
c.check('override shown selected on its row', /onchange="_clientAssign\(&quot;m1&quot;,this.value\)"[^>]*><option value="">Auto \(keywords\)<\/option><option value="Omni &lt;Social&gt;">Omni &lt;Social&gt;<\/option><option value="Phantom" selected>/.test(h));
c.check('how it matched in the tooltip', h.includes('title="Matched by keyword: omnisocial"'));
c.check('editor closed -> Edit button', h.includes('>Edit clients<') && !h.includes('id="cl-rows"'));

h = _clientsHtml(d, false, false, '');
c.check('no Pro -> CSV locked, report still there', h.includes('🔒 CSV (PRO)') && !h.includes('/clients/export.csv') && h.includes('/clients/print?'));
c.check('no token -> plain links', h.includes('href="/clients/print?month=2026-10&amp;client=Phantom"'));

h = _clientsHtml(d, true, true, '');
c.check('editor open -> one row per client, values escaped', (h.match(/class="cl-row"/g) || []).length === 2
  && h.includes('value="Omni &lt;Social&gt;"') && h.includes('value="omnisocial"') && h.includes('value="20"'));

h = _clientsHtml({ month: '2026-10', config: { clients: [], overrides: {} }, report: { month: '2026-10', months: [], total: 1,
  clients: [{ name: '', markup: 0, cost: 1, billed: 1, commits: 0, sessions: [S('u', 'x', 1)] }] } }, false, true, '');
c.check('no clients yet -> hint + add button', h.includes('Add your clients') && h.includes('+ Add clients'));
c.check('editor with no clients -> one empty row', (_clientsHtml({ ...d, config: { clients: [], overrides: {} } }, true, true, '').match(/class="cl-row"/g) || []).length === 1);
c.check('index not ready', _clientsHtml({ month: '2026-10', config: {} }, false, true, '').includes('Still reading'));
c.check('error', _clientsHtml({ error: 'x' }).includes('failed'));
c.finish();
