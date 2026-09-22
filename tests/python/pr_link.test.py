"""Tests _check_pr_for_branch, extracted straight from monitor.py, with
the actual `gh` subprocess call stubbed out (mirrors how
session_active.test.py stubs _is_claude_pid_alive rather than hitting
real Windows process APIs -- same idea, different external boundary)."""
import sys, os, json

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker


class _FakeResult:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


_run_calls = []


def _fake_run(cmd, cwd=None, timeout=None):
    _run_calls.append({"cmd": cmd, "cwd": cwd, "timeout": timeout})
    return _fake_run.next_result


ns = exec_functions(["_check_pr_for_branch"], extra_globals={"_run": _fake_run, "json": json})
_check_pr_for_branch = ns["_check_pr_for_branch"]

c = Checker()

# 1. an open PR: gh succeeds, JSON has a url
_fake_run.next_result = _FakeResult(0, '{"url": "https://github.com/owner/repo/pull/42"}')
_run_calls.clear()
url = _check_pr_for_branch("feature-x", "C:/some/repo")
c.check("returns the PR url when gh succeeds with a url", url == "https://github.com/owner/repo/pull/42")
c.check("passes the branch name through to gh pr view", "feature-x" in _run_calls[0]["cmd"])
c.check("passes cwd through so gh infers the right repo", _run_calls[0]["cwd"] == "C:/some/repo")

# 2. no PR for this branch: gh exits non-zero
_fake_run.next_result = _FakeResult(1, "")
c.check("no PR -> None (gh non-zero exit)", _check_pr_for_branch("no-pr-branch", "C:/some/repo") is None)

# 3. gh not installed / raises an exception -- caught, returns None
def _raising_run(cmd, cwd=None, timeout=None):
    raise FileNotFoundError("gh not found")
ns2 = exec_functions(["_check_pr_for_branch"], extra_globals={"_run": _raising_run, "json": json})
c.check("gh not installed -> None, doesn't raise", ns2["_check_pr_for_branch"]("any-branch", "C:/some/repo") is None)

# 4. malformed JSON from gh -- caught, returns None
_fake_run.next_result = _FakeResult(0, "not valid json")
c.check("malformed JSON output -> None, doesn't raise", _check_pr_for_branch("weird-branch", "C:/some/repo") is None)

# 5. valid JSON but missing "url" key -> None (via .get default)
_fake_run.next_result = _FakeResult(0, '{"number": 42}')
c.check("JSON with no url field -> None", _check_pr_for_branch("no-url-branch", "C:/some/repo") is None)

c.finish()
