"""Memory archive (History -> MEMORY -> ARCHIVE OLD): _mem_archive_split
moves the older dated entries of a big topic file into <name>_archive.md
and keeps the frontmatter, the intro, headings, every RESUME HERE entry and
the newest history; nothing is lost or duplicated. _memory_archive_preview /
_memory_archive_apply write with backups and a conflict check, UNDO SAVE
puts both files back; the lint offers it from 8k tokens and doesn't call
a linked archive an orphan. The test files are synthetic, shaped like a
real project memory (status entries with nested progress items)."""
import sys, os, re, glob, json, tempfile, time
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

c = Checker()
root = tempfile.mkdtemp(prefix="aoc_memarch_")
home, data = os.path.join(root, "home"), os.path.join(root, "aocdata")
memd = os.path.join(home, ".claude", "projects", "C--proj", "memory")
os.makedirs(memd)
os.makedirs(data)

ns = exec_functions(
    ["_CTX_CHARS_PER_TOKEN", "_ctx_tokens", "_MEM_MAX_FILES", "_MEM_MAX_BYTES", "_MEM_BACKUP_DIR", "_MEM_BACKUPS_KEEP",
     "_MEM_LOADED", "_MEM_BIG_TOKENS", "_MEM_BIG_TOPIC_TOKENS", "_MEM_INDEX_MAX_LINES", "_MEM_INDEX_LINE_CHARS",
     "_MEM_DUPE_MIN_CHARS", "_MEM_LINK", "_MEM_WIKI", "_MEM_ABS", "_MEM_SEV", "_mem_discover", "_mem_frontmatter",
     "_mem_find_path", "_mem_lint", "_mem_read", "_mem_files", "_mem_known", "_mem_backup_prefix", "_mem_backups",
     "_mem_write_text", "_memory_save", "_memory_undo", "_memory_lint", "_mem_backup", "_mem_archive_record",
     "_mem_archive_target", "_mem_is_archive", "_mem_intro_end", "_mem_pieces", "_mem_archive_split",
     "_mem_archive_current", "_memory_archive_preview", "_memory_archive_apply",
     "_MEM_ARCHIVE_KEEP_TOKENS", "_MEM_ARCHIVE_MIN_KEEP", "_MEM_ARCHIVE_MIN_MOVE", "_MEM_DATE", "_MEM_RESUME",
     "_MEM_LIST_ITEM"],
    {"os": os, "re": re, "glob": glob, "json": json, "datetime": datetime, "AOC_DATA_DIR": data,
     "_launch_known_dirs": lambda: []})
os.path.expanduser = lambda p, _o=os.path.expanduser: p.replace("~", home, 1) if p.startswith("~") else p
split = ns["_mem_archive_split"]
tok = ns["_ctx_tokens"]

FILL = "Did the thing, verified it live, wrote the tests and pushed it to origin. " * 6   # ~120 tokens


def project_memory(rounds=40):
    """A project memory in the shape real ones grow into."""
    out = ["---", "name: project-shop", "description: \"Shop frontend -- architecture, status\"", "metadata:",
           "  node_type: memory", "  type: project", "---", "",
           "Shop frontend at `C:\\proj\\shop`, Next.js + Stripe. Deploys from master.", "",
           "Payment webhooks are idempotent by event id (undated design note).", ""]
    for r in range(1, rounds + 1):
        d = f"2026-{9 + r // 28:02d}-{1 + r % 28:02d}"
        out.append(f"- **{d} round {r}**: {FILL}")
        if r % 3 == 0:
            out += [f"  - {d[5:]} detail {r}a: {FILL}", f"  - {d[5:]} detail {r}b: {FILL}",
                    "    1. deeper step that belongs to the item above", "    2. second deeper step"]
        if r == 6:
            out.append(f"  - **RESUME HERE ({d[5:]})**: pick up the checkout redesign.")
        if r == 12:
            out += ["", "## Status line moved from MEMORY.md (20261029)", "Kept verbatim when the index entry was shortened:", ""]
    out += ["", "- **11-04 RESUME HERE**: now on round 25, next = invoices."]
    return "\n".join(out) + "\n"


text = project_memory()
P = os.path.join(memd, "project_shop.md")
sp = split(text, P, None, "2026-11-04")
c.check("a big dated file gets a split", sp is not None and sp["moved"])
kept, arch = sp["kept"], sp["archive_text"]
kl, al = kept.split("\n"), arch.split("\n")

# ── what stays ──
c.check("frontmatter kept untouched", kept.startswith("\n".join(text.split("\n")[:7])))
c.check("intro paragraph kept", "Shop frontend at `C:\\proj\\shop`" in kept)
c.check("undated entry before the first dated one kept", "Payment webhooks are idempotent" in kept and "idempotent" not in arch)
c.check("heading kept in place", "## Status line moved from MEMORY.md (20261029)" in kl)
c.check("every RESUME HERE entry kept", "RESUME HERE (09-07)" in kept and "11-04 RESUME HERE" in kept and "RESUME" not in arch)
c.check("entry whose nested RESUME item stays keeps its own line", "- **2026-09-07 round 6**" in kept)
c.check("newest history kept", "round 40**" in kept and "round 39**" in kept)
c.check("the newest RESUME HERE entry stays even when every dated entry before it could move",
        "11-04 RESUME HERE" in split("---\nname: x\n---\n\nIntro.\n\n- **11-04 RESUME HERE**: next = invoices.\n\n"
                                     + "".join(f"- **2026-10-{d:02d} r**: {FILL}\n" for d in range(1, 29)), P)["kept"])
c.check("kept part ~ keep budget (newest history + fixed parts)",
        tok(len(kept)) < tok(len(text)) / 2 and tok(len(kept)) >= ns["_MEM_ARCHIVE_KEEP_TOKENS"])
c.check("link to the archive right after the intro",
        kl[kl.index("Shop frontend at `C:\\proj\\shop`, Next.js + Stripe. Deploys from master.") + 2]
        == "Older entries: [[project-shop-archive]] (project_shop_archive.md, moved there by AOC)." and sp["link_added"])
c.check("no runs of 3+ blank lines left behind", "\n\n\n" not in kept)

# ── what moves ──
c.check("oldest entries moved, in order", arch.index("round 1**") < arch.index("round 2**") < arch.index("round 3**")
        and "round 1**" not in kept)
c.check("archive frontmatter: name -archive, type copied",
        arch.startswith("---\nname: project-shop-archive\ndescription: Older history moved out of project_shop.md")
        and "\n  type: project\n---" in arch and "[[project-shop]]" in arch)
c.check("batch header with the date", "## Moved from project_shop.md on 2026-11-04" in al)
c.check("entry moved with its nested items and deeper steps",
        al[al.index("- **2026-09-04 round 3**: " + FILL) + 1].startswith("  - 09-04 detail 3a")
        and "    1. deeper step that belongs to the item above" in al)
c.check("tight list stays tight (no blank lines between consecutive items)",
        al[al.index("- **2026-09-02 round 1**: " + FILL) + 1] == "- **2026-09-03 round 2**: " + FILL)
c.check("nested item of an entry that stays is lifted to the top level",
        "- 09-07 detail 6a: " + FILL in al and "  - 09-07 detail 6a: " + FILL not in al and "detail 6a" not in kept)
c.check("entries under a heading carry it into the archive, one level down",
        "### Status line moved from MEMORY.md (20261029)" in al
        and al.index("### Status line moved from MEMORY.md (20261029)") < al.index("- **2026-09-14 round 13**: " + FILL))
c.check("moved list: line numbers + titles + tokens",
        sp["moved"][0]["line"] == text.split("\n").index("- **2026-09-02 round 1**: " + FILL) + 1
        and sp["moved"][0]["title"].startswith("- **2026-09-02 round 1**") and sp["moved_tokens"] == sum(m["tokens"] for m in sp["moved"]))

# ── nothing lost, nothing doubled ──
added = {"Older entries: [[project-shop-archive]] (project_shop_archive.md, moved there by AOC)."}
orig = [ln for ln in text.split("\n") if ln.strip()]
# headings stay in place; the archive repeats them (one level down) only as context
after = [ln for ln in kl if ln.strip() and ln not in added] + [ln.strip() for ln in al[al.index("## Moved from project_shop.md on 2026-11-04") + 1:]
                                                               if ln.strip() and not ln.startswith("#")]
norm = lambda xs: sorted(x.strip().lstrip("#").strip() for x in xs)
c.check("every original line ends up exactly once in the two files", norm(orig) == norm(after))

# ── when it does nothing ──
c.check("no dates at all -> nothing", split("---\nname: me\n---\n\nAbout me.\n\n" + ("- likes tea and long walks. " * 40 + "\n") * 40, P) is None)
c.check("small file -> nothing worth moving", split(project_memory(3), P) is None)
c.check("an archive is never split again", split(text, os.path.join(memd, "project_shop_archive.md")) is None)

# ── second run: existing archive, link already there ──
sp2 = split(kept + "".join(f"- **2026-11-{d:02d} more**: {FILL}\n" for d in range(5, 28)), P, arch, "2026-12-01")
c.check("second run appends to the existing archive, no second frontmatter",
        sp2 and sp2["archive_text"].startswith(arch.rstrip("\n")) and sp2["archive_text"].count("\nname: ") == 1
        and "## Moved from project_shop.md on 2026-12-01" in sp2["archive_text"])
c.check("link not added twice", not sp2["link_added"] and sp2["kept"].count("[[project-shop-archive]]") == 1)
c.check("CRLF input split the same", split(text.replace("\n", "\r\n"), P, None, "2026-11-04")["kept"] == kept)
c.check("no frontmatter name -> archive named after the file",
        split(text.replace("name: project-shop\n", ""), P)["archive_name"] == "project-shop-archive")

# ── lint ──
w = lambda p, t: open(p, "w", encoding="utf-8", newline="\n").write(t)
big = project_memory(150)
w(P, big)
w(os.path.join(memd, "MEMORY.md"), "# Memory Index\n\n- [Shop](project_shop.md) — shop\n")
rep = ns["_memory_lint"]()
row = next(r for r in rep["files"] if r["path"].endswith("project_shop.md"))
c.check("over 8k tokens: warned, ARCHIVE OLD named", row["tokens"] >= 8000 and any(
    x["rule"] == "size" and x["sev"] == "warn" and "ARCHIVE OLD" in x["msg"] for x in row["issues"]))
c.check("lint row carries the offer", row["archive"] and row["archive"]["entries"] > 0
        and row["archive"]["file"] == "project_shop_archive.md")
c.check("small topic: no size warning", not any(x["rule"] == "size" for x in ns["_mem_lint"](
    [{"path": "x/m/a.md", "kind": "memory", "text": "---\nname: a\ndescription: b\n---\nhi\n"}], exists=lambda p: True)[0]["issues"]))
srow = next(r for r in ns["_memory_lint"]()["files"] if r["path"].endswith("MEMORY.md"))
c.check("small / non-topic file: no offer", srow["archive"] is None)

# ── preview / apply / undo ──
pv = ns["_memory_archive_preview"](P)
c.check("preview: counts, tokens before > after, a diff with removed lines, nothing written",
        pv["ok"] and pv["before_tokens"] > pv["after_tokens"] and "\n-- **2026-09-02 round 1**" in pv["diff"]
        and not pv["archive_exists"] and pv["archive_mtime"] is None and ns["_mem_read"](P) == big
        and not os.path.exists(os.path.join(memd, "project_shop_archive.md")))
c.check("preview of a non-memory file refused", ns["_memory_archive_preview"](os.path.join(memd, "MEMORY.md"))["ok"] is False)
c.check("stale mtime -> changed, nothing written",
        ns["_memory_archive_apply"](P, pv["mtime"] - 5, None)["error"] == "changed" and ns["_mem_read"](P) == big)
c.check("archive appeared since the preview -> changed",
        ns["_memory_archive_apply"](P, pv["mtime"], 123.0)["error"] == "changed")
r = ns["_memory_archive_apply"](P, pv["mtime"], None)
A = os.path.join(memd, "project_shop_archive.md")
exp = split(big, P, None, datetime.now().strftime("%Y-%m-%d"))
c.check("apply writes both files as previewed", r["ok"] and r["moved"] == len(pv["moved"])
        and ns["_mem_read"](P) == exp["kept"] and ns["_mem_read"](A) == exp["archive_text"])
c.check("topic backed up, record written", len(ns["_mem_backups"](P)) == 1 and os.path.isfile(ns["_mem_archive_record"](P)))
rep = ns["_memory_lint"]()
arow = next(r for r in rep["files"] if r["path"].endswith("project_shop_archive.md"))
c.check("linked archive: not an orphan, no size nag, no wikilink info",
        not any(x["rule"] in ("orphan", "size") for x in arow["issues"])
        and not any(x["rule"] == "wikilink" for r in rep["files"] for x in r["issues"]))
c.check("the archive itself gets no offer", arow["archive"] is None)
r = ns["_memory_undo"](P)
c.check("UNDO SAVE: topic back, the archive it created removed, record gone",
        r["ok"] and ns["_mem_read"](P) == big and not os.path.exists(A) and not os.path.exists(ns["_mem_archive_record"](P)))

# an archive that was already there gets its old text back on undo
w(A, exp["archive_text"])
w(P, exp["kept"] + "".join(f"- **2026-12-{d:02d} more**: {FILL}\n" for d in range(1, 28)))
time.sleep(0.01)
pv2 = ns["_memory_archive_preview"](P)
c.check("preview with an existing archive", pv2["ok"] and pv2["archive_exists"] and pv2["archive_mtime"])
before_topic = ns["_mem_read"](P)
r = ns["_memory_archive_apply"](P, pv2["mtime"], pv2["archive_mtime"])
c.check("second apply appends to the archive", r["ok"] and ns["_mem_read"](A).startswith(exp["archive_text"].rstrip("\n"))
        and len(ns["_mem_read"](A)) > len(exp["archive_text"]))
r = ns["_memory_undo"](P)
c.check("undo restores the archive's earlier text (not deleted)",
        r["ok"] and ns["_mem_read"](A) == exp["archive_text"] and ns["_mem_read"](P) == before_topic)
cur = ns["_mem_known"](P)["mtime"]
ns["_memory_save"](P, before_topic + "x\n", cur)
ns["_memory_undo"](P)
c.check("a later plain save's undo leaves the archive alone",
        os.path.isfile(A) and ns["_mem_read"](A) == exp["archive_text"] and ns["_mem_read"](P) == before_topic)

c.finish()
