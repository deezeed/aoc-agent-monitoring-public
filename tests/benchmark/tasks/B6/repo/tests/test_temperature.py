import unittest

from temperature import c_to_f, f_to_c


class TemperatureTest(unittest.TestCase):
    def test_freezing(self):
        self.assertEqual(c_to_f(0), 32)

    def test_boiling(self):
        self.assertEqual(c_to_f(100), 212)

    def test_back(self):
        self.assertEqual(f_to_c(212), 100)


if __name__ == "__main__":
    unittest.main()
