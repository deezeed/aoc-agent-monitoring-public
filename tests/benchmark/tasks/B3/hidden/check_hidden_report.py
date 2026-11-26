"""Hidden: the facts and the pick are right, not only the shape."""
import re
import sys

text = open("report.md", encoding="utf-8").read()


def section(name):
    m = re.search(r"^## " + name + r"\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1) if m else ""


problems = []
comp, rec = section("Comparison"), section("Recommendation")
for price in ("18", "21", "12"):
    if not re.search(r"\b" + price + r"\b", comp):
        problems.append(f"price {price} missing from the Comparison")
for sla in ("99.5", "99.95", "99.99"):
    if sla not in comp:
        problems.append(f"SLA {sla} missing from the Comparison")
first = [(m.start(), name) for name in ("Alder", "Birchvault", "Cedar")
         for m in [re.search(name, rec, re.I)] if m]
if not first or min(first)[1] != "Birchvault":
    problems.append("the Recommendation does not start with Birchvault")
if problems:
    sys.exit("; ".join(problems))
print("report facts right")
