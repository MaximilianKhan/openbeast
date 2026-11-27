"""HTTP handlers. Each module exposes ``register(router, services)``."""
from __future__ import annotations

from ..http import json_response
from . import coupons, customers, orders, products, reports


def health(_request, _params, services):
    return json_response(200, {"status": "ok", "version": services.version})


def register_all(router, services) -> None:
    router.add("GET", "/health", lambda r, p: health(r, p, services), name="health")
    for mod in (products, customers, coupons, orders, reports):
        mod.register(router, services)
