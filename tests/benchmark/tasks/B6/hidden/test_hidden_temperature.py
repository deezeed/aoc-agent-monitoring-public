import unittest

from temperature import c_to_f, f_to_c


class HiddenTemperatureTest(unittest.TestCase):
    def test_minus_forty(self):
        self.assertEqual(c_to_f(-40), -40)
        self.assertEqual(f_to_c(-40), -40)

    def test_round_trip(self):
        for c in (-10, 0, 21.5, 37):
            self.assertAlmostEqual(f_to_c(c_to_f(c)), c, places=9)


if __name__ == "__main__":
    unittest.main()
