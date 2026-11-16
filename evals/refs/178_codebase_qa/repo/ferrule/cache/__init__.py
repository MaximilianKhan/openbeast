"""Result caches in front of the store, keyed per tenant."""

from ferrule.cache.job_cache import JobCache, QueueStatsCache
from ferrule.cache.keys import make_key

__all__ = ["JobCache", "QueueStatsCache", "make_key"]
