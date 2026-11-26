"""Prorated billing for short stays."""
from datetime import date


def days_between(start: date, end: date) -> int:
    """Billable days of a stay from `start` to `end`."""
    return (end - start).days


def prorate(monthly_price: float, start: date, end: date, days_in_month: int = 30) -> float:
    """What a stay costs when a month costs `monthly_price`."""
    return round(monthly_price * days_between(start, end) / days_in_month, 2)
