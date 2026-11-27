"""Typed caches. The key ``kind`` field is derived from the class name:
``FooCache`` caches ``foo`` entries."""

from __future__ import annotations

from typing import Any, Optional

from ferrule.cache.backend import TTLCache
from ferrule.cache.keys import make_key


class BaseCache:
    def __init__(self, settings, tenant: str, backend: Optional[TTLCache] = None) -> None:
        cfg = settings["cache"]
        self.namespace = cfg["namespace"]
        self.tenant = tenant
        self.backend = backend or TTLCache(cfg["ttl_s"])

    @property
    def kind(self) -> str:
        name = type(self).__name__
        return (name[:-5] if name.endswith("Cache") else name).lower()

    def key(self, ident: Any) -> str:
        return make_key(self.namespace, self.tenant, self.kind, ident)

    def get(self, ident: Any) -> Optional[Any]:
        return self.backend.get(self.key(ident))

    def set(self, ident: Any, value: Any) -> None:
        self.backend.set(self.key(ident), value)

    def invalidate(self, ident: Any) -> None:
        self.backend.delete(self.key(ident))


class JobRecordCache(BaseCache):
    """Serialized Job documents by job id."""


class QueueStatsCache(BaseCache):
    """Per-queue state counts."""


# Public name; the record cache was the only job cache until 2.6.
JobCache = JobRecordCache
