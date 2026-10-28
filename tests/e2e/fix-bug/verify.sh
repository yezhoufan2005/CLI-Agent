set -e
python3 -m unittest -q
grep -q 'assertEqual(mean(\[1, 2, 3\]), 2)' test_stats.py  # tests untouched
