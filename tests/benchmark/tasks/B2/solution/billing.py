"""Prorated billing for short stays."""
from datetime import date


def days_between(start: date, end: date) -> int:
    """Billable days of a stay from `start` to `end`, both included."""
    if end < start:
        raise ValueError("the stay ends before it starts")
    return (end - start).days + 1


def prorate(monthly_price: float, start: date, end: date, days_in_month: int = 30) -> float:
    """What a stay costs when a month costs `monthly_price`."""
    return round(monthly_price * days_between(start, end) / days_in_month, 2)
