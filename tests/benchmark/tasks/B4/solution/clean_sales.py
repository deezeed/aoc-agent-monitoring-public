"""python clean_sales.py <input.csv> <output.csv>: total and count per region."""
import csv
import sys
from decimal import Decimal, InvalidOperation


def amount(text):
    t = text.replace("€", "").replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return Decimal(t) if t else None
    except InvalidOperation:
        return None


def main(src, dst):
    seen, totals = set(), {}
    with open(src, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            day, region = (row.get("date") or "").strip(), (row.get("region") or "").strip().title()
            value = amount((row.get("amount") or "").strip())
            if value is None or (day, region, value) in seen:
                continue
            seen.add((day, region, value))
            total, count = totals.get(region, (Decimal(0), 0))
            totals[region] = (total + value, count + 1)
    with open(dst, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["region", "total", "count"])
        for region in sorted(totals):
            total, count = totals[region]
            w.writerow([region, f"{total:.2f}", count])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
