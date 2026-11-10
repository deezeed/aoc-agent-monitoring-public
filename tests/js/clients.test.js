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

// round 9: active time per row + client, rate x hours, rate/currency in the editor
const d9 = JSON.parse(JSON.stringify(d));
d9.report.clients[0] = { ...d9.report.clients[0], active_s: 2 * 3600 + 5 * 60, rate: 45.5, currency: 'EUR', hours: 2.25, time_amount: 102.38 };
d9.report.clients[0].sessions[0].active_s = 3 * 60 + 59;
d9.report.clients[1].active_s = 600;
h = _clientsHtml(d9, false, true, '');
c.check('Time column header + h:mm per session (not a locale format)', h.includes('>Time</th>') && h.includes('>0:03</td>'));
c.check('session without time -> 0:00', h.includes('>0:00</td>'));
c.check('client card: active time -> billed quarter hours x rate = amount in its currency',
        h.includes('<b>2:05</b> h → <b>2.25 h</b> billed (rounded up to 0.25 h) × 45.50 EUR = <b>102.38 EUR</b>'));
c.check('client with time but no rate -> hours only', h.includes('<b>0:10</b> h</span>'));
c.check('client without time -> no hours line', !/Random[\s\S]*<b>0:00<\/b> h/.test(h));
d9.config.clients[0] = { ...d9.config.clients[0], rate: 45.5, currency: 'CZK' };
h = _clientsHtml(d9, true, true, '');
c.check('editor: rate + currency inputs with values', h.includes('class="settings-input cl-in-rate" type="number" min="0" step="0.5" value="45.5"')
  && h.includes('value="CZK"'));
c.check('editor: client without currency defaults to EUR', (h.match(/cl-in-cur" maxlength="3" placeholder="EUR" value="EUR"/g) || []).length === 1);
c.check('editor: currency escaped', _clientEditRow({ name: 'x', match: [], markup: 0, rate: 0, currency: '"><b>' }, 0).includes('value="&quot;&gt;&lt;b&gt;"'));
c.check('error', _clientsHtml({ error: 'x' }).includes('failed'));

// round 10: the report for the client (no costs) + a note per client
h = _clientsHtml(d, false, true, 'tok&1', 'sk');
c.check('client report link: month, client, page language and token',
        h.includes('/clients/report?month=2026-10&amp;client=Omni%20%3CSocial%3E&amp;lang=sk&amp;token=tok%261'));
c.check('client report link next to every named client', (h.match(/⎙ Client report/g) || []).length === 2);
c.check('no client report for unassigned sessions', !h.includes('/clients/report?month=2026-10&amp;client=&amp;'));
c.check('no language -> en', _clientsHtml(d, false, false, '').includes('&amp;lang=en"'));
c.check('client report available without Pro', _clientsHtml(d, false, false, '').includes('/clients/report?'));
c.check('editor: note textarea per client, escaped', _clientEditRow({ name: 'x', match: [], note: 'Zmluva <12>' }, 0)
  .includes('class="settings-input cl-in-note" rows="2" maxlength="2000"') && _clientEditRow({ name: 'x', match: [], note: 'Zmluva <12>' }, 0).includes('>Zmluva &lt;12&gt;</textarea>'));
c.check('editor: no note -> empty textarea', _clientEditRow({ name: 'x', match: [] }, 0).includes('"></textarea>'));
c.check('save sends the note', src.includes("note:(r.querySelector('.cl-in-note')||{value:''}).value.trim()"));
c.check('the tab passes the page language', src.includes("window._AOC_TOKEN||'', document.documentElement.lang)"));
c.finish();
