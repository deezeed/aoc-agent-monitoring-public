import unittest

from textkit import slugify


class SlugifyTest(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_punctuation_collapses(self):
        self.assertEqual(slugify("Hello,   World!!"), "hello-world")

    def test_diacritics(self):
        self.assertEqual(slugify("Žltý kôň"), "zlty-kon")


if __name__ == "__main__":
    unittest.main()
