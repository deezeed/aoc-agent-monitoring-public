"""Tests setup.py's argument check (_check_args), extracted straight from
setup.py, plus two real runs: `setup.py --help` and `setup.py --bogus` must
exit before touching anything. Unknown flags used to be silently ignored,
so `python setup.py --help` ran the full, state-changing setup.

The real runs point USERPROFILE/HOME at a scratch dir, so if the check
ever regressed, deployed files would land there (and fail the test)
instead of in the real ~/.claude. They're skipped if the pure checks fail,
so a broken _check_args can't reach the scheduled-task step."""
import sys, os, subprocess, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

SETUP_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "setup.py"))

c = Checker()
ns = exec_functions(["_check_args", "_BOOL_FLAGS", "_VALUE_FLAGS"], {}, path=SETUP_PATH)
check_args = ns["_check_args"]
A = lambda *args: check_args(["setup.py", *args])

failed_before = c.failed
for label, cond in [
    ("no args -> run", A() == ("run", "")),
    ("--dry-run -> run", A("--dry-run")[0] == "run"),
    ("--stop -> run", A("--stop")[0] == "run"),
    ("--uninstall -> run", A("--uninstall")[0] == "run"),
    ("cloud url + key -> run", A("--cloud-url", "https://x", "--cloud-key", "aoc_live_1")[0] == "run"),
    ("dry-run with cloud flags -> run", A("--dry-run", "--cloud-url", "https://x", "--cloud-key", "k")[0] == "run"),
    ("--help -> help", A("--help")[0] == "help"),
    ("-h -> help", A("-h")[0] == "help"),
    ("help wins over other flags", A("--dry-run", "--help")[0] == "help"),
    ("unknown flag -> error naming it", A("--bogus") == ("error", "unknown argument: --bogus")),
    ("typo'd flag -> error", A("--dryrun")[0] == "error"),
    ("stray positional -> error", A("install")[0] == "error"),
    ("--cloud-url without value -> error", A("--cloud-url") == ("error", "--cloud-url needs a value")),
    ("--cloud-url followed by a flag -> error", A("--cloud-url", "--cloud-key", "k")[0] == "error"),
    ("url without key -> error", A("--cloud-url", "https://x")[0] == "error"),
    ("key without url -> error", A("--cloud-key", "k")[0] == "error"),
]:
    c.check(label, cond)
pure_ok = c.failed == failed_before

if pure_ok:
    scratch = tempfile.mkdtemp(prefix="aoc_test_setup_args_")
    try:
        env = dict(os.environ, USERPROFILE=scratch, HOME=scratch)
        run = lambda *a: subprocess.run([sys.executable, SETUP_PATH, *a], capture_output=True,
                                        text=True, env=env, timeout=60)
        r = run("--help")
        c.check("--help exits 0", r.returncode == 0)
        c.check("--help prints the usage", "Usage:" in r.stdout and "--dry-run" in r.stdout)
        r = run("--bogus")
        c.check("--bogus exits 2", r.returncode == 2)
        c.check("--bogus says nothing was changed", "unknown argument: --bogus" in r.stderr and "Nothing was changed" in r.stderr)
        c.check("neither run wrote anything", os.listdir(scratch) == [])
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
else:
    c.check("real runs skipped because _check_args is broken", False)

c.finish()
