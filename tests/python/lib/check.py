"""Tiny shared assertion harness -- no framework (pytest/unittest), matching
this project's own dependency-free choice. Each test file creates one
Checker, calls .check(label, cond) for every assertion, then .finish()."""
import sys
import time

# A non-ASCII label (e.g. "včera") must not crash a cp1252 console like the
# GitHub Actions Windows runner's -- print it escaped instead.
try:
    sys.stdout.reconfigure(errors="backslashreplace")
except (AttributeError, ValueError):
    pass


def wait_until(cond, timeout=10.0, step=0.01):
    """Poll cond() until it is true or `timeout` s pass -> its last value.
    For waiting on a background thread: a fixed sleep(0.1) is a guess that
    fails whenever run_all's parallel load delays the thread's start. Only
    for things that must happen; to show something does NOT happen, wait
    for the event that would have caused it, then check once."""
    end = time.monotonic() + timeout
    while True:
        v = cond()
        if v or time.monotonic() >= end:
            return v
        time.sleep(step)


class Checker:
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def check(self, label, cond):
        if cond:
            self.passed += 1
            print(f"PASS: {label}")
        else:
            self.failed += 1
            print(f"FAIL: {label}")

    def finish(self):
        print(f"\n{self.passed} passed, {self.failed} failed")
        sys.exit(1 if self.failed else 0)
