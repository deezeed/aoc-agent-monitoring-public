import unittest
from datetime import date

from billing import days_between, prorate


class HiddenBillingTest(unittest.TestCase):
    def test_across_months(self):
        self.assertEqual(days_between(date(2026, 1, 30), date(2026, 2, 2)), 4)

    def test_full_month(self):
        self.assertEqual(prorate(90.0, date(2026, 4, 1), date(2026, 4, 30)), 90.0)

    def test_end_before_start(self):
        with self.assertRaises(ValueError):
            days_between(date(2026, 5, 2), date(2026, 5, 1))
        with self.assertRaises(ValueError):
            prorate(90.0, date(2026, 5, 2), date(2026, 5, 1))


if __name__ == "__main__":
    unittest.main()
