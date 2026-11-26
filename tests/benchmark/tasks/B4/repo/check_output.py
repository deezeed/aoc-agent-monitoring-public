"""python check_output.py <got.csv> <expected.csv>: the same rows, totals to the cent."""
import csv
import sys


def rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return [[c.strip() for c in r] for r in csv.reader(f) if r]


try:
    got, want = rows(sys.argv[1]), rows(sys.argv[2])
except OSError as e:
    sys.exit(f"cannot read: {e}")
if got != want:
    sys.exit(f"{sys.argv[1]} differs from {sys.argv[2]}:\n got  {got}\n want {want}")
print(f"{sys.argv[1]} matches")
