import unittest

from textkit import truncate, word_count


class TextkitTest(unittest.TestCase):
    def test_word_count(self):
        self.assertEqual(word_count("one two  three"), 3)
        self.assertEqual(word_count(""), 0)

    def test_truncate(self):
        self.assertEqual(truncate("hello", 10), "hello")
        self.assertEqual(truncate("hello world", 8), "hello...")


if __name__ == "__main__":
    unittest.main()
