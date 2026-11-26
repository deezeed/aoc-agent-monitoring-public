"""Checks report.md has the sections and the table the task asks for."""
import re
import sys

try:
    text = open("report.md", encoding="utf-8").read()
except OSError:
    sys.exit("report.md is missing")
problems = []
for section in ("## Summary", "## Comparison", "## Recommendation"):
    if section not in text:
        problems.append(f"no '{section}' section")
for name in ("Alder", "Birchvault", "Cedar"):
    if name.lower() not in text.lower():
        problems.append(f"{name} is not mentioned")
if not re.search(r"^\s*\|.*\|\s*$", text, re.M):
    problems.append("the Comparison has no Markdown table")
if problems:
    sys.exit("; ".join(problems))
print("report.md looks complete")
