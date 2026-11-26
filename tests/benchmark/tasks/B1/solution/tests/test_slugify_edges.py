import unittest

from textkit import slugify


class SlugifyEdgesTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(slugify(""), "")

    def test_max_len(self):
        self.assertEqual(slugify("Hello World", max_len=6), "hello")


if __name__ == "__main__":
    unittest.main()
