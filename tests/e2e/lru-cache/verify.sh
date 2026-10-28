set -e
python3 - <<'PY'
from lru import LRUCache
for bad in (0, -1):
    try:
        LRUCache(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("capacity check")
c = LRUCache(2)
c.put("a", 1); c.put("b", 2)
assert c.get("a") == 1          # a is now most recent
c.put("c", 3)                   # evicts b
assert "b" not in c and c.get("b", "x") == "x"
assert len(c) == 2 and c.evictions == 1
assert "a" in c                 # must not change recency
c.put("a", 10)                  # update marks a recent
c.put("d", 4)                   # evicts c
assert "c" not in c and c.get("a") == 10 and c.evictions == 2
PY
python3 -m unittest -q test_lru
