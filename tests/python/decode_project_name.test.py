"""Tests _decode_project_name, extracted straight from monitor.py. Had zero
coverage until now despite being load-bearing for every project-attributed
feature in the app (KPI bar, History, by_project/by_day_project analytics,
budgets, muted-project filters) -- if this misresolves an encoded project
path, every one of those silently misattributes cost.

Runs against real directories under a scratch tempdir (never a hardcoded
path), since the function's whole job is walking os.path.isdir against
the real filesystem to resolve the segmentation ambiguity in Claude Code's
encoded project names (e.g. is "ai-antivirus" one directory with a literal
hyphen, or two hyphen-joined path segments "ai" and "antivirus"?). The
encoded string is always derived from the real path actually created,
never hand-typed, so the test can't drift out of sync with whatever
drive/user/temp-dir shape this machine happens to have."""
import sys, os, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ns = exec_functions(["_decode_project_name"], {"os": os})
_decode_project_name = ns["_decode_project_name"]

c = Checker()
tmpdir = tempfile.mkdtemp(prefix="aoc_test_decode_proj_")

def encode(path):
    """Build the same 'C--Users-marek-ai-antivirus'-shaped string Claude
    Code itself would encode this real path as, so the test target is
    always derived from a real directory rather than hand-typed."""
    drive, rest = os.path.splitdrive(path)
    letter = drive.rstrip(':')
    parts = [p for p in rest.split(os.sep) if p]
    return f"{letter}--" + "-".join(parts)

try:
    # 1. A directory whose own name genuinely contains a literal hyphen --
    # the exact ambiguity this function exists to resolve via filesystem
    # lookup (greedy-longest-match-first) rather than a naive split('-').
    hyphenated = os.path.join(tmpdir, "ai-antivirus")
    os.makedirs(hyphenated)
    c.check("resolves a hyphenated directory name via filesystem walk",
            _decode_project_name(encode(hyphenated)) == "ai-antivirus")

    # 2. A plain (non-hyphenated) directory name still resolves correctly.
    plain = os.path.join(tmpdir, "PhantomAI")
    os.makedirs(plain)
    c.check("resolves a plain directory name", _decode_project_name(encode(plain)) == "PhantomAI")

    # 3. Nested hyphenated segments: two separate directories that both
    # individually contain hyphens must each resolve as their own segment,
    # not get merged into one.
    nested = os.path.join(tmpdir, "multi-word-parent", "sub-project")
    os.makedirs(nested)
    c.check("resolves the deepest of nested hyphenated directories",
            _decode_project_name(encode(nested)) == "sub-project")

    # 4. Input that doesn't match the leading "<letter>--" pattern at all
    # is returned unchanged -- no filesystem access attempted.
    c.check("non-matching input returned as-is", _decode_project_name("not-an-encoded-path") == "not-an-encoded-path")
    c.check("input missing the drive-letter prefix returned unchanged", _decode_project_name("just-some-text") == "just-some-text")

    # 5. A path that resolves to nothing on disk at all (root never
    # matches) -- basename of the bare drive root is '', which the
    # function's own home/Users/empty exclusion list maps to ''.
    c.check("completely nonexistent path resolves to empty string, not the raw encoded input",
            _decode_project_name("Z--Totally-Nonexistent-Path-xyz123") == "")

    # 6. Resolving exactly to the home directory itself is deliberately
    # excluded (too generic to be a useful "project name").
    home = os.path.expanduser("~")
    c.check("resolving to the home directory itself returns empty string",
            _decode_project_name(encode(home)) == "")

finally:
    shutil.rmtree(tmpdir, ignore_errors=True)

c.finish()
