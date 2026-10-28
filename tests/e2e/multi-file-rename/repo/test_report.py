import unittest

from report import report


class T(unittest.TestCase):
    def test_report(self):
        self.assertEqual(report([(2, 3), (1, 1)]), [6, 1])


if __name__ == "__main__":
    unittest.main()
