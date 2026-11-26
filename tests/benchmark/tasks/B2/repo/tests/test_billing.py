import unittest
from datetime import date

from billing import days_between, prorate


class BillingTest(unittest.TestCase):
    def test_one_day_stay_is_one_day(self):
        self.assertEqual(days_between(date(2026, 3, 4), date(2026, 3, 4)), 1)

    def test_week(self):
        self.assertEqual(days_between(date(2026, 3, 1), date(2026, 3, 7)), 7)

    def test_prorate_ten_days(self):
        self.assertEqual(prorate(300.0, date(2026, 3, 1), date(2026, 3, 10)), 100.0)


if __name__ == "__main__":
    unittest.main()
