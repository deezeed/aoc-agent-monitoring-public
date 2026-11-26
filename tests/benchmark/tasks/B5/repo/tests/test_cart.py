import unittest

from shop.cart import cart_total


class CartTest(unittest.TestCase):
    def test_total(self):
        self.assertEqual(cart_total({"tea": 2, "mug": 1}), 18.30)

    def test_empty(self):
        self.assertEqual(cart_total({}), 0)


if __name__ == "__main__":
    unittest.main()
