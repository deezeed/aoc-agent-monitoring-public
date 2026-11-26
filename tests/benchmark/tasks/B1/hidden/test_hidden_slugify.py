import unittest

from textkit import slugify


class HiddenSlugifyTest(unittest.TestCase):
    def test_no_edge_hyphens(self):
        self.assertEqual(slugify("  --Hello--  "), "hello")

    def test_digits_kept(self):
        self.assertEqual(slugify("Release 2.0 notes"), "release-2-0-notes")

    def test_empty_and_symbols(self):
        self.assertEqual(slugify(""), "")
        self.assertEqual(slugify("!!! ??? ***"), "")

    def test_max_len_cuts(self):
        self.assertEqual(slugify("abcdef", max_len=4), "abcd")
        self.assertLessEqual(len(slugify("Hello World Again", max_len=10)), 10)

    def test_max_len_never_ends_with_hyphen(self):
        self.assertEqual(slugify("Hello World", max_len=6), "hello")

    def test_more_diacritics(self):
        self.assertEqual(slugify("Ďakujem, Štefan & Äňa"), "dakujem-stefan-ana")

    def test_underscore_is_a_separator(self):
        self.assertEqual(slugify("snake_case_name"), "snake-case-name")


if __name__ == "__main__":
    unittest.main()
