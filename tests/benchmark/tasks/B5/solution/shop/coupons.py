"""Discount coupons, defined in coupons.json next to this file."""
import json
import os

_FILE = os.path.join(os.path.dirname(__file__), "coupons.json")


def apply_coupon(total, code):
    """total after the coupon `code` (KeyError if there is no such code)."""
    with open(_FILE, encoding="utf-8") as f:
        coupons = {k.upper(): v for k, v in json.load(f).items()}
    coupon = coupons[code.strip().upper()]
    if coupon["type"] == "percent":
        new = total * (100 - coupon["value"]) / 100
    else:
        new = total - coupon["value"]
    return float(round(max(new, 0.0), 2))
