import unittest

from temperature import f_to_c


class LegacyTest(unittest.TestCase):
    def test_body_temperature(self):
        self.assertAlmostEqual(f_to_c(98.6), 37.0, places=5)


if __name__ == "__main__":
    unittest.main()
