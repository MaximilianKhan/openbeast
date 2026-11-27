"""Coupons.

A coupon has either ``percent_off`` (0 < p <= 100) or ``amount_off``, an
optional ``min_subtotal`` and an optional ``max_uses``. See docs/API.md
"Pricing" for exactly how a coupon turns into a discount.
"""
from __future__ import annotations

import re

from .. import db as dbmod
from .. import money
from ..errors import Conflict, Unprocessable, ValidationFailed
from ..models import Coupon

CODE_RE = re.compile(r"^[A-Z0-9]{3,20}$")


class Promotions:
    def __init__(self, db, logger):
        self.db = db
        self.logger = logger

    def create(self, fields: dict) -> Coupon:
        pct, amt = fields.get("percent_off"), fields.get("amount_off")
        if (pct is None) == (amt is None):
            raise ValidationFailed({"percent_off": "exactly one of percent_off, amount_off is required"})
        if amt is not None and amt <= money.ZERO:
            raise ValidationFailed({"amount_off": "must be > 0"})
        row = {
            "code": fields["code"].upper(),
            "percent_off": pct,
            "amount_off": amt,
            "min_subtotal": fields.get("min_subtotal", money.ZERO),
            "max_uses": fields.get("max_uses"),
            "uses": 0,
        }
        if not CODE_RE.match(row["code"]):
            raise ValidationFailed({"code": "must be 3-20 characters of A-Z0-9"})
        try:
            with self.db.transaction():
                stored = self.db.coupons.insert(row)
        except dbmod.IntegrityError:
            raise Conflict("coupon %s already exists" % row["code"], code="duplicate_coupon")
        return Coupon.from_row(stored)

    def find(self, code: str) -> Coupon | None:
        row = self.db.coupons.find_by("code", code.upper())
        return None if row is None else Coupon.from_row(row)

    def require_usable(self, code: str, subtotal) -> Coupon:
        c = self.find(code)
        if c is None:
            raise Unprocessable("unknown coupon %s" % code, code="unknown_coupon")
        if c.max_uses is not None and c.uses >= c.max_uses:
            raise Unprocessable("coupon %s is used up" % c.code, code="coupon_exhausted")
        if subtotal < c.min_subtotal:
            raise Unprocessable("coupon %s needs a subtotal of at least %s" % (c.code, money.fmt(c.min_subtotal)),
                                code="coupon_min_subtotal")
        return c

    @staticmethod
    def discount(c: Coupon, subtotal):
        """The coupon discount on ``subtotal`` (already rounded to cents)."""
        if c.percent_off is not None:
            return money.percent_of(subtotal, c.percent_off)
        return money.clamp(c.amount_off, money.ZERO, subtotal)

    def consume(self, c: Coupon) -> None:
        row = self.db.coupons.get(c.id)
        self.db.coupons.update(c.id, {"uses": row["uses"] + 1})

    def release(self, code: str) -> None:
        row = self.db.coupons.find_by("code", code)
        if row is not None and row["uses"] > 0:
            self.db.coupons.update(row["id"], {"uses": row["uses"] - 1})
