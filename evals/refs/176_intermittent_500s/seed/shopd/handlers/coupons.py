"""/coupons"""
from __future__ import annotations

from .. import money, serialization
from ..errors import NotFound
from ..validation import Field, validate
from .common import body_of

COUPON_SCHEMA = {
    "code": Field("str", max_len=20),
    "percent_off": Field("pct", required=False),
    "amount_off": Field("money", required=False),
    "min_subtotal": Field("money", required=False, min=money.ZERO),
    "max_uses": Field("int", required=False, min=1, max=1_000_000),
}


def register(router, s) -> None:
    def create(request, _params):
        fields = validate(body_of(request), COUPON_SCHEMA)
        coupon = s.promotions.create(fields)
        return serialization.respond(201, "coupon", serialization.coupon_payload(coupon))

    def show(_request, params):
        coupon = s.promotions.find(params["code"])
        if coupon is None:
            raise NotFound("no coupon %s" % params["code"], code="coupon_not_found")
        return serialization.respond(200, "coupon", serialization.coupon_payload(coupon))

    router.add("POST", "/coupons", create, name="coupons.create")
    router.add("GET", "/coupons/{code}", show, name="coupons.show")
