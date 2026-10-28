import unittest

from pager import get_page, page_count


class T(unittest.TestCase):
    def test_exact(self):
        self.assertEqual(page_count(10, 5), 2)


if __name__ == "__main__":
    unittest.main()
