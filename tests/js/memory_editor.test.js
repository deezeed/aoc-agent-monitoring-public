/* History -> MEMORY, extracted from monitor.py: file groups, which file opens
 * first, the grouped list, the editor pane (issues, dirty state, read-only
 * away from this PC) and the whole tab. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_memGroupLabel', '_memPickFile', '_memListHtml', '_memEditorHtml', '_memHtml'])
  eval(extractFunction(src, f));
// const arrows / objects: cut at ';' + \r?\n (CI checks the file out with CRLF)
const grab = name => { const a = src.indexOf('const ' + name + '='); const m = /;\r?\n/g; m.lastIndex = a; return src.slice(a, m.exec(src).index + 1).replace(/^const /, 'var '); };
['_memKindLabel', '_memTok'].forEach(n => (0, eval)(grab(n)));

const c = new Checker();
const M = 'C:\\Users\\me\\.claude\\projects\\';
c.check('groups: user / project / .claude project / memory folders',
  _memGroupLabel({ kind: 'user', path: 'C:\\Users\\me\\.claude\\CLAUDE.md' }) === 'Every session'
  && _memGroupLabel({ kind: 'project', path: 'C:\\P\\aoc\\CLAUDE.md' }) === 'Project · aoc'
  && _memGroupLabel({ kind: 'project', path: 'C:\\P\\aoc\\.claude\\CLAUDE.md' }) === 'Project · aoc'
  && _memGroupLabel({ kind: 'memory', path: M + 'C--Users-me-OneDrive-Projekty-AOC-Agent-Monitoring\\memory\\a.md' }) === 'Memory · AOC-Agent-Monitoring'
  && _memGroupLabel({ kind: 'index', path: M + 'C--Users-me\\memory\\MEMORY.md' }) === 'Memory · home folder'
  && _memGroupLabel({ kind: 'index', path: M + 'C--Program-Files-Git\\memory\\MEMORY.md' }) === 'Memory · Program-Files-Git');

const files = [
  { path: 'C:\\u\\CLAUDE.md', kind: 'user', tokens: 120, issues: [], backups: 0 },
  { path: M + 'C--Users-me\\memory\\MEMORY.md', kind: 'index', tokens: 2276, backups: 2,
    issues: [{ sev: 'warn', line: 0, rule: 'size', msg: '~2,276 tokens' }, { sev: 'warn', line: 7, rule: 'long_line', msg: 'long <line>' }] },
  { path: M + 'C--Users-me\\memory\\a.md', kind: 'memory', tokens: 50, issues: [{ sev: 'error', line: 3, rule: 'dead_link', msg: 'Link to x.md' }] },
];
c.check('opens the file with errors first', _memPickFile(files).path.endsWith('a.md') && _memPickFile([]) === undefined);
c.check('loaded file wins a tie', _memPickFile([{ path: 'm', kind: 'memory', issues: [] }, { path: 'u', kind: 'user', issues: [] }]).path === 'u');

const list = _memListHtml(files, files[1].path);
c.check('list: grouped, active row, badges, path escaped into onclick', list.indexOf('Every session') < list.indexOf('Memory · home folder')
  && (list.match(/class="mem-file active"/g) || []).length === 1 && list.includes('mem-badge err">1') && list.includes('mem-badge warn">2')
  && list.includes('_memOpen(&quot;C:\\\\u\\\\CLAUDE.md&quot;)') && list.includes('~2,276'));

const doc = { path: files[1].path, kind: 'index', text: 'a\n<b>\n', mtime: 1, draft: null };
let ed = _memEditorHtml(doc, files[1], true, '');
c.check('editor: text escaped, issues listed, line issues clickable, whole-file ones not', ed.includes('a\n&lt;b&gt;\n</textarea>')
  && ed.includes('_memJump(7)') && ed.includes('long &lt;line&gt;') && (ed.match(/onclick="_memJump/g) || []).length === 1);
c.check('editor: clean -> SAVE disabled, undo enabled with backups', /onclick="_memSave\(\)" disabled/.test(ed) && !/onclick="_memUndo\(\)" disabled/.test(ed)
  && ed.includes('loaded at session start') && !ed.includes(' · unsaved'));
ed = _memEditorHtml({ ...doc, draft: 'changed' }, files[1], true, 'Saved.');
c.check('editor: draft shown, unsaved, SAVE enabled, message', ed.includes('>changed</textarea>') && ed.includes(' · unsaved')
  && !/onclick="_memSave\(\)" disabled/.test(ed) && ed.includes('class="mem-msg">Saved.'));
ed = _memEditorHtml({ ...doc, kind: 'memory' }, { issues: [], backups: 0 }, false, '');
c.check('editor: away from this PC read-only, no buttons; no issues -> all clear', ed.includes('readonly') && !ed.includes('_memSave')
  && ed.includes('Read-only here') && ed.includes('Nothing to fix') && ed.includes('read when Claude needs it'));
c.check('editor: nothing open', _memEditorHtml(null, null, true, '').includes('Pick a file'));

const tab = _memHtml({ files, counts: { error: 1, warn: 2, info: 0 }, start_tokens: 2396 }, files[2].path, { ...doc, path: files[2].path, kind: 'memory' }, true, '');
c.check('tab: KPIs with error styling, list + editor', tab.includes('hist-kpi bad') && tab.includes('hist-kpi warn') && tab.includes('~2,396')
  && tab.includes('mem-list') && tab.includes('Link to x.md'));
c.check('tab: error / empty', _memHtml({ error: 'x' }).includes('failed') && _memHtml({ files: [] }).includes('No CLAUDE.md'));

c.finish();
