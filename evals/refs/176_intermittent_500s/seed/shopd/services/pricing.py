"""Order pricing.

Implements docs/API.md "Pricing" step by step. Every intermediate amount is
rounded to cents where the spec says so and nowhere else; the explicit
``quantize`` calls on sums are no-ops for well-formed inputs and are kept
so that a stray sub-cent value can never reach the wire.
"""
from __future__ import annotations

import dataclasses
from decimal import Decimal

from .. import money
from ..models import LineItem


@dataclasses.dataclass
class Quote:
    lines: list
    subtotal: Decimal
    discount: Decimal
    tier_discount: Decimal
    tax: Decimal
    total: Decimal
    coupon: object = None


def price(items, customer, coupon, promotions, config) -> Quote:
    """Price ``items`` (a list of ``(product, qty)``) for ``customer``.

    ``coupon`` is a code or None; when given it is checked against the
    subtotal (and may raise the coupon_* errors).
    """
    lines = []
    for product, qty in items:
        line_total = money.quantize(product.price * qty)
        lines.append(LineItem(product_id=product.id, sku=product.sku, name=product.name,
                              qty=qty, unit_price=product.price, line_total=line_total))
    subtotal = money.quantize(sum((li.line_total for li in lines), money.ZERO))

    coupon_obj = None
    discount = money.ZERO
    if coupon:
        coupon_obj = promotions.require_usable(coupon, subtotal)
        discount = promotions.discount(coupon_obj, subtotal)
    after_coupon = money.quantize(subtotal - discount)

    tier_discount = money.ZERO
    if customer.tier == "gold":
        tier_discount = money.percent_of(after_coupon, config.gold_discount_pct)
    taxable = money.quantize(after_coupon - tier_discount)

    tax = money.quantize(taxable * config.tax_rate)
    total = money.quantize(taxable + tax)
    return Quote(lines=lines, subtotal=subtotal, discount=discount, tier_discount=tier_discount,
                 tax=tax, total=total, coupon=coupon_obj)
