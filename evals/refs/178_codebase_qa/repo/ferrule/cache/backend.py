"""A TTL dictionary standing in for memcached in tests and single-node use."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple


class TTLCache:
    def __init__(self, ttl_s: float) -> None:
        self.ttl_s = ttl_s
        self._data: Dict[str, Tuple[float, Any]] = {}
        self.reads = []

    def get(self, key: str) -> Optional[Any]:
        self.reads.append(key)
        hit = self._data.get(key)
        if hit is None:
            return None
        expires, value = hit
        if time.monotonic() > expires:
            del self._data[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._data[key] = (time.monotonic() + self.ttl_s, value)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)
