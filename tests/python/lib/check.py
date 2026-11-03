"""Tiny shared assertion harness -- no framework (pytest/unittest), matching
this project's own dependency-free choice. Each test file creates one
Checker, calls .check(label, cond) for every assertion, then .finish()."""
import sys

# A non-ASCII label (e.g. "včera") must not crash a cp1252 console like the
# GitHub Actions Windows runner's -- print it escaped instead.
try:
    sys.stdout.reconfigure(errors="backslashreplace")
except (AttributeError, ValueError):
    pass


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
