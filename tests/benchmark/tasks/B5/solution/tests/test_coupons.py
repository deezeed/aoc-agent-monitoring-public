import unittest

from shop.coupons import apply_coupon


class CouponTest(unittest.TestCase):
    def test_percent(self):
        self.assertEqual(apply_coupon(80.0, "SAVE10"), 72.0)

    def test_amount_floor(self):
        self.assertEqual(apply_coupon(3.0, "FIVE"), 0)

    def test_unknown(self):
        with self.assertRaises(KeyError):
            apply_coupon(1.0, "nope")


if __name__ == "__main__":
    unittest.main()
