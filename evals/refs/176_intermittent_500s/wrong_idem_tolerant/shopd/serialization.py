"""Turning domain records into wire payloads.

Two stages. ``*_payload`` builds a plain dict straight from a model (money
still Decimal, plus an ``_location`` entry for resources that have one).
``finish`` then converts that dict to its wire form: money to two-decimal
strings, the ``_location`` entry moved into the Location header. ``finish``
works in place, because order listings can be large and copying every
payload twice showed up in profiles.
"""
from __future__ import annotations

from . import money
from .clock import iso
from .http import Headers, json_response

ORDER_MONEY = ("subtotal", "discount", "tier_discount", "tax", "total")
LINE_MONEY = ("unit_price", "line_total")


def product_payload(p) -> dict:
    return {
        "id": p.id,
        "sku": p.sku,
        "name": p.name,
        "slug": p.slug,
        "price": p.price,
        "stock": p.stock,
        "tags": list(p.tags),
        "created_at": iso(p.created_ms),
        "updated_at": iso(p.updated_ms),
        "_location": "/products/%d" % p.id,
    }


def customer_payload(c) -> dict:
    return {
        "id": c.id,
        "email": c.email,
        "name": c.name,
        "tier": c.tier,
        "created_at": iso(c.created_ms),
        "_location": "/customers/%d" % c.id,
    }


def coupon_payload(c) -> dict:
    return {
        "code": c.code,
        "percent_off": None if c.percent_off is None else str(c.percent_off),
        "amount_off": None if c.amount_off is None else money.fmt(c.amount_off),
        "min_subtotal": money.fmt(c.min_subtotal),
        "max_uses": c.max_uses,
        "uses": c.uses,
        "_location": "/coupons/%s" % c.code,
    }


def order_payload(o) -> dict:
    return {
        "id": o.id,
        "customer_id": o.customer_id,
        "status": o.status,
        "coupon": o.coupon,
        "lines": [
            {"product_id": li.product_id, "sku": li.sku, "name": li.name, "qty": li.qty,
             "unit_price": li.unit_price, "line_total": li.line_total}
            for li in o.lines
        ],
        "subtotal": o.subtotal,
        "discount": o.discount,
        "tier_discount": o.tier_discount,
        "tax": o.tax,
        "total": o.total,
        "created_at": iso(o.created_ms),
        "cancelled_at": None if o.cancelled_ms is None else iso(o.cancelled_ms),
        "_location": "/orders/%d" % o.id,
    }


def _finish_product(p: dict) -> None:
    p["price"] = money.fmt(p["price"])


def _finish_order(o: dict) -> None:
    for k in ORDER_MONEY:
        o[k] = money.fmt(o[k])
    for li in o["lines"]:
        for k in LINE_MONEY:
            li[k] = money.fmt(li[k])


def finish(kind: str, payload: dict, headers: Headers) -> dict:
    """Convert one payload to wire form (in place) and return it."""
    if kind == "product":
        _finish_product(payload)
    elif kind == "order":
        _finish_order(payload)
    loc = payload.pop("_location", None)
    if loc is not None:
        headers["Location"] = loc
    return payload


def finish_list(kind: str, payloads: list) -> list:
    for p in payloads:
        if kind == "product":
            _finish_product(p)
        elif kind == "order":
            _finish_order(p)
        p.pop("_location", None)
    return payloads


def respond(status: int, kind: str, payload: dict, headers=None):
    """A JSON response for a single resource."""
    hdrs = Headers(headers)
    body = finish(kind, payload, hdrs)
    return json_response(status, body, hdrs)
