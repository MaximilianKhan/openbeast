"""Per-API-key token buckets.

Each key gets a bucket sized ``ratelimit.burst`` and refilled at
``ratelimit.rate_per_s``, both scaled by the key's tier multiplier
(``ratelimit.tiers``). A request costs one token unless its route is
listed in ``COSTS``.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from ferrule.errors import RateLimited
from ferrule.jobs.clock import Clock, SystemClock

COSTS: Dict[str, int] = {
    "jobs.create": 2,
    "jobs.bulk_create": 10,
    "jobs.purge": 50,
    "queues.drain": 5,
}


@dataclass
class Bucket:
    capacity: float
    rate: float
    tokens: float
    updated_ms: int

    def refill(self, now_ms: int) -> None:
        elapsed = max(now_ms - self.updated_ms, 0) / 1000.0
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.updated_ms = now_ms


class RateLimiter:
    def __init__(self, settings, clock: Optional[Clock] = None) -> None:
        cfg = settings["ratelimit"]
        self.rate = float(cfg["rate_per_s"])
        self.burst = int(cfg["burst"])
        self.initial_fill = float(cfg["initial_fill"])
        self.tiers: Dict[str, float] = dict(cfg["tiers"])
        self.clock = clock or SystemClock()
        self._buckets: Dict[Tuple[str, str], Bucket] = {}
        self._lock = threading.Lock()

    def _multiplier(self, tier: str) -> float:
        return float(self.tiers.get(tier, self.tiers.get("standard", 1.0)))

    def _bucket(self, key: str, tier: str) -> Bucket:
        b = self._buckets.get((key, tier))
        if b is None:
            mult = self._multiplier(tier)
            capacity = float(int(self.burst * mult))
            b = Bucket(capacity, self.rate * mult, capacity * self.initial_fill, self.clock.now_ms())
            self._buckets[(key, tier)] = b
        return b

    def check(self, key: str, tier: str, route_name: Optional[str] = None) -> None:
        cost = COSTS.get(route_name or "", 1)
        with self._lock:
            b = self._bucket(key, tier)
            b.refill(self.clock.now_ms())
            if b.tokens >= cost:
                b.tokens -= cost
                return
            deficit = cost - b.tokens
        raise RateLimited(deficit / b.rate if b.rate else float("inf"))

    def remaining(self, key: str, tier: str) -> float:
        with self._lock:
            b = self._bucket(key, tier)
            b.refill(self.clock.now_ms())
            return b.tokens
