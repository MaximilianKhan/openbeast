"""A small LRU cache with per-entry TTL.

Used as a read-through cache in front of the product table (see
services/catalog.py). Keys are strings built with ``key(kind, ident)``.
Values are stored as given; callers must treat them as immutable.
"""
from __future__ import annotations

import collections
import threading

MISSING = object()


def key(kind: str, ident) -> str:
    """Cache key for one record, e.g. ``key("product", 7)``."""
    return "%s:%r" % (kind, ident)


class TTLCache:
    def __init__(self, clock, ttl_ms: int, max_entries: int = 1024):
        if ttl_ms < 0 or max_entries < 1:
            raise ValueError("bad cache parameters")
        self.clock = clock
        self.ttl_ms = ttl_ms
        self.max_entries = max_entries
        self._data: "collections.OrderedDict[str, tuple[int, object]]" = collections.OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def get(self, k: str):
        """The cached value, or MISSING when absent or expired."""
        now = self.clock.now_ms()
        with self._lock:
            hit = self._data.get(k)
            if hit is None:
                self.misses += 1
                return MISSING
            expires, value = hit
            if now >= expires:
                del self._data[k]
                self.misses += 1
                return MISSING
            self._data.move_to_end(k)
            self.hits += 1
            return value

    def put(self, k: str, value) -> None:
        if self.ttl_ms == 0:
            return
        expires = self.clock.now_ms() + self.ttl_ms
        with self._lock:
            self._data[k] = (expires, value)
            self._data.move_to_end(k)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)
                self.evictions += 1

    def invalidate(self, k: str) -> bool:
        with self._lock:
            return self._data.pop(k, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        return len(self._data)

    def stats(self) -> dict:
        return {"entries": len(self._data), "hits": self.hits, "misses": self.misses,
                "evictions": self.evictions}
