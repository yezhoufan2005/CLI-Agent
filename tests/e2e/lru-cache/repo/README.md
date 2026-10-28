# lru

Implement `LRUCache` in `lru.py`:

- `LRUCache(capacity)` — capacity is a positive int; raise `ValueError` otherwise.
- `get(key, default=None)` — return the value and mark the key most recently used; return `default` if missing.
- `put(key, value)` — insert or update (updating also marks it most recently used). When over capacity, evict the least recently used key.
- `len(cache)` and `key in cache` work; `in` does NOT change recency.
- `evictions` attribute counts evicted keys.

Only the standard library may be used.
