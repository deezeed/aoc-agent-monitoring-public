"""The coupon code exists, has tests, and README documents every code."""
import json
import re
import sys

problems = []
try:
    import shop.coupons  # noqa: F401
except Exception as e:  # any import failure is a missing deliverable
    problems.append(f"shop/coupons.py does not import: {e}")
try:
    tests = open("tests/test_coupons.py", encoding="utf-8").read()
    if len(re.findall(r"^\s*def test_", tests, re.M)) < 3:
        problems.append("tests/test_coupons.py has fewer than 3 test methods")
except OSError:
    problems.append("tests/test_coupons.py is missing")
readme = open("README.md", encoding="utf-8").read()
m = re.search(r"^## Coupons\s*$(.*?)(?=^## |\Z)", readme, re.M | re.S)
if not m:
    problems.append("README.md has no '## Coupons' section")
else:
    codes = json.load(open("shop/coupons.json", encoding="utf-8"))
    missing = [c for c in codes if c not in m.group(1)]
    if missing:
        problems.append("the Coupons section does not list " + ", ".join(missing))
if problems:
    sys.exit("; ".join(problems))
print("deliverables present")
