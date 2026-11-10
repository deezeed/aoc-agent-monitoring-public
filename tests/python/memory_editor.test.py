"""Memory & CLAUDE.md editor (monitor.py): _mem_discover finds the user /
project CLAUDE.md files and every auto-memory folder; _mem_lint flags size,
long index lines, dead links / paths, orphans, frontmatter, duplicates;
_memory_save / _memory_undo write with a conflict check and keep backups
outside the memory folder."""
import sys, os, re, glob, json, tempfile, time, threading
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
root = tempfile.mkdtemp(prefix="aoc_mem_")
home, data = os.path.join(root, "home"), os.path.join(root, "aocdata")
proj = os.path.join(root, "proj")
memd = os.path.join(home, ".claude", "projects", "C--proj", "memory")
for d in (memd, proj, os.path.join(proj, ".claude"), data):
    os.makedirs(d, exist_ok=True)
def w(p, t, nl="\n"):
    with open(p, "w", encoding="utf-8", newline=nl) as f:
        f.write(t)
w(os.path.join(home, ".claude", "CLAUDE.md"), "- Use utf-8 when opening files on this machine please.\n")
w(os.path.join(proj, "CLAUDE.md"), "# Project\nRun tests with pytest.\n- Use utf-8 when opening files on this machine please.\n")
w(os.path.join(proj, "CLAUDE.local.md"), "local notes\n")
w(os.path.join(memd, "MEMORY.md"), "# Memory Index\n\n- [AOC](project_aoc.md) — dashboard\n- [Gone](missing.md) — x\n"
  "- " + "y" * 400 + "\n", nl="\r\n")
w(os.path.join(memd, "project_aoc.md"), "---\nname: project-aoc\ndescription: dashboard\n---\n\n"
  f"Lives in `{proj}` and {os.path.join(proj, 'gone.txt')}; see [[project-aoc]] and [[later-thing]].\n"
  "Example: C:\\Users\\nobody-here\\.ssh\\id_rsa and https://example.com/a.md\n"
  "- Use utf-8 when opening files on this machine please.\n")
w(os.path.join(memd, "orphan.md"), "no frontmatter here\n")

ns = exec_functions(
    ["_CTX_CHARS_PER_TOKEN", "_ctx_tokens", "_MEM_MAX_FILES", "_MEM_MAX_BYTES", "_MEM_BACKUP_DIR", "_MEM_BACKUPS_KEEP",
     "_MEM_LOADED", "_MEM_BIG_TOKENS", "_MEM_BIG_TOPIC_TOKENS", "_MEM_INDEX_MAX_LINES", "_MEM_INDEX_LINE_CHARS",
     "_MEM_DUPE_MIN_CHARS", "_MEM_LINK", "_MEM_WIKI", "_MEM_ABS", "_MEM_SEV", "_mem_discover", "_mem_frontmatter",
     "_mem_find_path", "_mem_lint", "_mem_read", "_mem_files", "_mem_known", "_mem_backup_prefix", "_mem_backups",
     "_mem_write_text", "_memory_save", "_memory_undo", "_memory_lint", "_mem_backup", "_mem_archive_record",
     "_mem_archive_target", "_mem_is_archive", "_mem_intro_end", "_mem_pieces", "_mem_archive_split",
     "_MEM_ARCHIVE_KEEP_TOKENS", "_MEM_ARCHIVE_MIN_KEEP", "_MEM_ARCHIVE_MIN_MOVE", "_MEM_DATE", "_MEM_RESUME",
     "_MEM_LIST_ITEM"],
    {"os": os, "re": re, "glob": glob, "json": json, "datetime": datetime, "AOC_DATA_DIR": data,
     "_launch_known_dirs": lambda: [{"path": proj}]})
os.path.expanduser = lambda p, _o=os.path.expanduser: p.replace("~", home, 1) if p.startswith("~") else p

found = ns["_mem_discover"](home, [proj])
kinds = [(os.path.basename(f["path"]), f["kind"]) for f in found]
c.check("discovers user, project, local, index first then topics", kinds == [
    ("CLAUDE.md", "user"), ("CLAUDE.md", "project"), ("CLAUDE.local.md", "local"),
    ("MEMORY.md", "index"), ("orphan.md", "memory"), ("project_aoc.md", "memory")])
c.check("same folder twice -> listed once", len(ns["_mem_discover"](home, [proj, proj + os.sep])) == len(found))

rep = ns["_memory_lint"]()
by = {os.path.basename(r["path"]) + ":" + r["kind"]: r for r in rep["files"]}
rules = lambda k: [(x["rule"], x["line"]) for x in by[k]["issues"]]
idx = rules("MEMORY.md:index")
c.check("index: dead link to missing.md (line 4), long line 5, real link fine",
        ("dead_link", 4) in idx and ("long_line", 5) in idx and not any(r == "dead_link" and l == 3 for r, l in idx))
c.check("CRLF file read as LF (line numbers right)", by["MEMORY.md:index"]["lines"] == 5)
topic = by["project_aoc.md:memory"]["issues"]
c.check("topic: missing file in an existing folder flagged, existing folder / example path / URL not",
        [x["msg"].split(" doesn't")[0] for x in topic if x["rule"] == "dead_path"] == [os.path.join(proj, "gone.txt")])
c.check("wikilink: known name ok, unknown one is info", [x["msg"] for x in topic if x["rule"] == "wikilink"]
        == ["[[later-thing]]: no memory with that name yet."] and all(x["sev"] == "info" for x in topic if x["rule"] == "wikilink"))
c.check("duplicate across files loaded together, pointing at the first",
        [x["msg"] for x in by["CLAUDE.md:project"]["issues"] if x["rule"] == "duplicate"] == ["Same as CLAUDE.md:1 -- Claude reads it twice."])
c.check("topic file repeating a loaded line is not a duplicate (opened on its own)", not any(x["rule"] == "duplicate" for x in topic))
two = ns["_mem_lint"]([{"path": "x/m/a.md", "kind": "memory", "text": "## The same heading line written in two topic files\n"},
                       {"path": "x/m/b.md", "kind": "memory", "text": "## The same heading line written in two topic files\n"
                                                                      "## The same heading line written in two topic files\n"}], exists=lambda p: True)
c.check("same line in two topic files: fine; twice in one file: flagged",
        not any(x["rule"] == "duplicate" for x in two[0]["issues"]) and [(x["rule"], x["line"]) for x in two[1]["issues"] if x["rule"] == "duplicate"] == [("duplicate", 2)])
orphan = rules("orphan.md:memory")
c.check("orphan + missing frontmatter", ("orphan", 0) in orphan and ("frontmatter", 1) in orphan)
c.check("linked topic is not an orphan", not any(r == "orphan" for r, _ in rules("project_aoc.md:memory")))
c.check("issues sorted errors first", by["MEMORY.md:index"]["issues"][0]["sev"] == "error")
c.check("counts + start tokens", rep["counts"]["error"] == 1 and rep["start_tokens"] > 0)

big = ns["_mem_lint"]([{"path": "x/CLAUDE.md", "kind": "user", "text": "word " * 2000},
                       {"path": "x/m/MEMORY.md", "kind": "index", "text": "- a\n" * 230}], exists=lambda p: True)
c.check("big loaded file warned", any(x["rule"] == "size" for x in big[0]["issues"]))
c.check("index over 200 lines warned at line 201", ("index_length", 201) in [(x["rule"], x["line"]) for x in big[1]["issues"]])
spaced = ns["_mem_lint"]([{"path": "x/m/a.md", "kind": "memory", "text": "---\nname: a\ndescription: b\n---\nIn C:\\Proj\\My App\\src now"}],
                         exists=lambda p: p in ("C:\\Proj\\My App\\src", "C:\\Proj"))
c.check("path with a space resolved by the following words", not any(x["rule"] == "dead_path" for x in spaced[0]["issues"]))
q = ns["_mem_lint"]([{"path": "x/m/a.md", "kind": "memory", "text": "---\nname: a\ndescription: b\n---\nsee `C:\\A\\Proj\\New folder\\app` now"}],
                    exists=lambda p: p in ("C:\\A\\Proj",))
c.check("backticked path reported whole when only its grandparent exists",
        [x["msg"] for x in q[0]["issues"] if x["rule"] == "dead_path"] == ["C:\\A\\Proj\\New folder\\app doesn't exist (moved or renamed?)."])
shallow = ns["_mem_lint"]([{"path": "x/m/a.md", "kind": "memory", "text": "---\nname: a\ndescription: b\n---\nsee `C:\\Users\\x\\.ssh\\id_rsa`"}],
                          exists=lambda p: p in ("C:\\", "C:\\Users"))
c.check("example path whose only existing ancestor is C:\\Users -> not flagged", not any(x["rule"] == "dead_path" for x in shallow[0]["issues"]))

# ── save / undo ──
mp = os.path.join(memd, "MEMORY.md")
f = ns["_mem_known"](mp.upper() if os.name == "nt" else mp)
c.check("known: case-insensitive path, unknown path refused", f is not None and ns["_mem_known"](os.path.join(root, "x.md")) is None)
r = ns["_memory_save"](mp, "new\ntext\n", f["mtime"] - 5)
c.check("stale mtime -> changed, nothing written", r["error"] == "changed" and ns["_mem_read"](mp).startswith("# Memory Index"))
r = ns["_memory_save"](os.path.join(root, "evil.md"), "x", 0)
c.check("unknown file refused", r["ok"] is False and "knows about" in r["error"])
r = ns["_memory_save"](mp, "new\ntext\n", f["mtime"])
raw = open(mp, "rb").read()
c.check("saved, CRLF kept, backup outside the memory folder",
        r["ok"] and raw == b"new\r\ntext\r\n" and len(ns["_mem_backups"](mp)) == 1
        and os.path.dirname(ns["_mem_backups"](mp)[0]) == os.path.join(data, "memory_backups")
        and sorted(os.listdir(memd)) == ["MEMORY.md", "orphan.md", "project_aoc.md"])
c.check("same text again -> unchanged, no new backup",
        ns["_memory_save"](mp, "new\ntext\n", r["mtime"]).get("unchanged") and len(ns["_mem_backups"](mp)) == 1)
r = ns["_memory_undo"](mp)
c.check("undo restores the original and drops the backup",
        r["ok"] and ns["_mem_read"](mp).startswith("# Memory Index") and ns["_mem_backups"](mp) == [])
c.check("undo with no backup -> error", ns["_memory_undo"](mp)["ok"] is False)
cur = ns["_mem_known"](mp)["mtime"]
for i in range(12):
    time.sleep(0.002)
    cur = ns["_memory_save"](mp, f"v{i}\n", cur)["mtime"]
c.check("keeps the last 10 backups", len(ns["_mem_backups"](mp)) == 10)
c.check("too large refused", ns["_memory_save"](mp, "x" * (600 * 1024), cur)["ok"] is False)

c.finish()
