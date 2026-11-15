"""Service configuration.

All knobs live in one frozen dataclass. ``Config.from_env`` reads the
``SHOPD_*`` environment variables used in production; tests construct
``Config(...)`` directly or take the defaults.
"""
from __future__ import annotations

import dataclasses
from decimal import Decimal


@dataclasses.dataclass(frozen=True)
class Config:
    # Sales tax applied to the discounted subtotal of every order.
    tax_rate: Decimal = Decimal("0.0825")
    # Extra percentage off for customers of the "gold" tier.
    gold_discount_pct: Decimal = Decimal("5")
    # How long a product read stays in the read-through cache.
    cache_ttl_ms: int = 30_000
    cache_max_entries: int = 2048
    # Listing pages.
    page_default: int = 20
    page_max: int = 100
    # Order limits.
    max_lines: int = 50
    max_qty: int = 1000
    # Idempotency keys are remembered for this long.
    idempotency_ttl_ms: int = 24 * 3600 * 1000
    # Token bucket per client: capacity and refill per minute.
    rate_capacity: int = 60
    rate_per_min: int = 120
    # When true the access log also records request bodies (truncated).
    log_bodies: bool = False
    log_body_max: int = 400
    # Name of the worker in log lines when no thread name is set.
    default_worker: str = "w0"

    @classmethod
    def from_env(cls, env) -> "Config":
        def flag(name, default):
            v = env.get(name)
            if v is None:
                return default
            return v.strip().lower() in ("1", "true", "yes", "on")

        def num(name, default, kind=int):
            v = env.get(name)
            return default if v in (None, "") else kind(v)

        return cls(
            tax_rate=num("SHOPD_TAX_RATE", cls.tax_rate, Decimal),
            gold_discount_pct=num("SHOPD_GOLD_PCT", cls.gold_discount_pct, Decimal),
            cache_ttl_ms=num("SHOPD_CACHE_TTL_MS", cls.cache_ttl_ms),
            cache_max_entries=num("SHOPD_CACHE_MAX", cls.cache_max_entries),
            page_default=num("SHOPD_PAGE_DEFAULT", cls.page_default),
            page_max=num("SHOPD_PAGE_MAX", cls.page_max),
            rate_capacity=num("SHOPD_RATE_CAPACITY", cls.rate_capacity),
            rate_per_min=num("SHOPD_RATE_PER_MIN", cls.rate_per_min),
            log_bodies=flag("SHOPD_LOG_BODIES", cls.log_bodies),
        )
