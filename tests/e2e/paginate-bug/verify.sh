set -e
python3 - <<'PY'
from pager import get_page, page_count
items = list(range(1, 8))
assert page_count(7, 3) == 3, page_count(7, 3)
assert page_count(6, 3) == 2
assert page_count(0, 3) == 0
assert get_page(items, 1, 3) == [1, 2, 3]
assert get_page(items, 3, 3) == [7]
assert get_page(items, 4, 3) == []
PY
python3 -m unittest -q
# new tests were added
[ "$(grep -c 'def test_' test_pager.py)" -ge 2 ]
