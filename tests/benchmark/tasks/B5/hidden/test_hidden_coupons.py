import unittest

from shop.coupons import apply_coupon


class HiddenCouponTest(unittest.TestCase):
    def test_percent(self):
        self.assertEqual(apply_coupon(80.0, "SAVE10"), 72.0)
        self.assertEqual(apply_coupon(18.40, "WINTER25"), 13.80)

    def test_amount(self):
        self.assertEqual(apply_coupon(18.30, "FIVE"), 13.30)

    def test_never_below_zero(self):
        self.assertEqual(apply_coupon(3.0, "FIVE"), 0)

    def test_case_and_spaces(self):
        self.assertEqual(apply_coupon(50.0, "  save10 "), 45.0)

    def test_unknown(self):
        with self.assertRaises(KeyError):
            apply_coupon(10.0, "NOPE")

    def test_returns_float(self):
        self.assertIsInstance(apply_coupon(10.0, "FIVE"), float)


if __name__ == "__main__":
    unittest.main()
