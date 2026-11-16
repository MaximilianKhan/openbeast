"""/orders"""
from __future__ import annotations

from .. import idempotency, pagination, serialization
from ..errors import BadRequest
from ..models import ORDER_STATUSES, Order
from ..validation import Field, validate
from .common import body_of, list_response, path_id, query_int

LINE_SCHEMA = {
    "product_id": Field("int", min=1),
    "qty": Field("int", min=1, max=1000),
}
ORDER_SCHEMA = {
    "customer_id": Field("int", min=1),
    "lines": Field("list", min=1, max=50, items=LINE_SCHEMA),
    "coupon": Field("str", required=False, max_len=20, nullable=True),
}


def register(router, s) -> None:
    def create(request, _params):
        body = body_of(request)
        key = request.headers.get("Idempotency-Key")
        fp = None
        if key is not None:
            idempotency.check_key(key)
            fp = idempotency.fingerprint("POST", "/orders", body)
            stored = s.idempotency.lookup(key, fp)
            if stored is not None:
                s.logger.info("idempotency.replay", key=key)
        fields = validate(body, ORDER_SCHEMA)
        order = s.orders.place(fields["customer_id"], fields["lines"], fields.get("coupon"),
                               idempotency_key=key)
        payload = serialization.order_payload(order)
        if key is not None:
            s.idempotency.save(key, fp, 201, None)
        return serialization.respond(201, "order", payload)

    def show(_request, params):
        order = s.orders.get(int(path_id(params, what="order")))
        return serialization.respond(200, "order", serialization.order_payload(order))

    def cancel(_request, params):
        order = s.orders.cancel(int(path_id(params, what="order")))
        return serialization.respond(200, "order", serialization.order_payload(order))

    def index(request, _params):
        q = request.query
        limit = pagination.parse_limit(q, s.config.page_default, s.config.page_max)
        status = q.get("status") or None
        if status is not None and status not in ORDER_STATUSES:
            raise BadRequest("status must be one of " + ", ".join(ORDER_STATUSES), code="invalid_query")
        rows = s.orders.listing(query_int(q, "customer_id"), status)
        page, nxt = pagination.paginate(rows, limit, q.get("cursor"), newest_first=True)
        items = serialization.finish_list(
            "order", [serialization.order_payload(Order.from_row(r)) for r in page])
        return list_response(items, nxt)

    router.add("POST", "/orders", create, name="orders.create")
    router.add("GET", "/orders", index, name="orders.index")
    router.add("GET", "/orders/{id}", show, name="orders.show")
    router.add("POST", "/orders/{id}/cancel", cancel, name="orders.cancel")
