/* History -> MEMORY -> ARCHIVE OLD, extracted from monitor.py: the button
 * (only on a big memory topic file, disabled while there is an unsaved edit),
 * the preview panel (counts, tokens, what moves, the diff, apply / cancel)
 * and that the panel shows only for the file it belongs to. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
for (const f of ['escHtml', 'jsq', '_memDiffHtml', '_memArchiveHtml', '_memEditorHtml'])
  eval(extractFunction(src, f));
const grab = name => { const a = src.indexOf('const ' + name + '='); const m = /;\r?\n/g; m.lastIndex = a; return src.slice(a, m.exec(src).index + 1).replace(/^const /, 'var '); };
['_memKindLabel', '_memTok'].forEach(n => (0, eval)(grab(n)));

const c = new Checker();
const P = 'C:\\Users\\me\\.claude\\projects\\C--proj\\memory\\project_shop.md';
const A = 'C:\\Users\\me\\.claude\\projects\\C--proj\\memory\\project_shop_archive.md';
const offer = { entries: 31, tokens: 5200, file: 'project_shop_archive.md' };
const file = { path: P, kind: 'memory', tokens: 9100, issues: [], backups: 0, archive: offer };
const doc = { path: P, kind: 'memory', text: 'x\n', mtime: 1, draft: null };

// ── the button ──
let ed = _memEditorHtml(doc, file, true, '');
c.check('ARCHIVE OLD on a big memory file, enabled, tooltip names count + target',
  ed.includes('>ARCHIVE OLD</button>') && !/_memArchivePreview\(\)" disabled/.test(ed)
  && ed.includes('Move 31 older dated entries (~5,200 tokens) into project_shop_archive.md'));
c.check('disabled while there is an unsaved edit',
  /_memArchivePreview\(\)" disabled/.test(_memEditorHtml({ ...doc, draft: 'changed' }, file, true, '')));
c.check('no button without an offer, on a non-memory file, or away from this PC',
  !_memEditorHtml(doc, { ...file, archive: null }, true, '').includes('ARCHIVE OLD')
  && !_memEditorHtml({ ...doc, kind: 'index' }, file, true, '').includes('ARCHIVE OLD')
  && !_memEditorHtml(doc, file, false, '').includes('ARCHIVE OLD'));

// ── the preview ──
const moved = Array.from({ length: 45 }, (_, i) => ({ line: 12 + i * 2, title: `- **2026-09-${String(i % 28 + 1).padStart(2, '0')} round ${i + 1}** <b>`, tokens: 120 }));
const pv = { ok: true, path: P, archive_path: A, archive_exists: false, before_tokens: 9100, after_tokens: 3700,
  moved, moved_tokens: 5400, link_added: true, diff: '--- a\n+++ b\n@@ -1 +1 @@\n-- **2026-09-01 round 1**\n+Older entries: [[x]]', diff_cut: false };
const h = _memArchiveHtml(pv);
c.check('preview: tokens before -> after, count, target file (new)',
  h.includes('~9,100 → ~3,700 tokens in project_shop.md') && h.includes('45 entries (~5,400 tokens)')
  && h.includes('<code>project_shop_archive.md</code>') && h.includes('(new file)'));
c.check('preview: first 40 listed with line numbers, the rest counted, titles escaped',
  (h.match(/<li>/g) || []).length === 40 && h.includes('>L12<') && h.includes('… and 5 more')
  && h.includes('round 1** &lt;b&gt;') && !h.includes('round 1** <b>'));
c.check('preview: diff coloured, link noted, MOVE + CANCEL',
  h.includes('class="md-del">-- **2026-09-01 round 1**') && h.includes('class="md-add">+Older entries')
  && h.includes('(plus a link to the archive)') && h.includes('onclick="_memArchiveApply()">MOVE 45 ENTRIES')
  && h.includes('onclick="_memArchiveCancel()"'));
c.check('preview: RESUME HERE promise + undo note', h.includes('every RESUME HERE entry') && h.includes('UNDO SAVE puts both files back'));
const h1 = _memArchiveHtml({ ...pv, moved: moved.slice(0, 1), archive_exists: true, link_added: false, diff_cut: true });
c.check('preview: one entry, existing archive, cut diff',
  h1.includes('1 entry (') && h1.includes('MOVE 1 ENTRY<') && h1.includes('(added at its end)')
  && !h1.includes('plus a link') && h1.includes('… diff cut') && !h1.includes('… and'));
c.check('preview: nothing open -> empty', _memArchiveHtml(null) === '');

// ── shown only for its own file, only locally ──
c.check('editor shows the preview of the open file',
  _memEditorHtml(doc, file, true, '', pv).includes('class="mem-arch"'));
c.check('not for another file, not away from this PC',
  !_memEditorHtml({ ...doc, path: A }, file, true, '', pv).includes('class="mem-arch"')
  && !_memEditorHtml(doc, file, false, '', pv).includes('class="mem-arch"'));

c.finish();
