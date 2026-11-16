"""/products"""
from __future__ import annotations

from .. import pagination, serialization
from ..http import empty_response
from ..validation import Field, validate
from .common import body_of, list_response, path_id

PRODUCT_SCHEMA = {
    "sku": Field("str", pattern=r"^[A-Z0-9][A-Z0-9-]{1,31}$"),
    "name": Field("str", max_len=120),
    "price": Field("money"),
    "stock": Field("int", min=0, max=1_000_000),
    "tags": Field("tags", required=False),
}
PATCH_SCHEMA = {
    "name": Field("str", max_len=120),
    "price": Field("money"),
    "stock": Field("int", min=0, max=1_000_000),
    "tags": Field("tags"),
}


def register(router, s) -> None:
    def create(request, _params):
        fields = validate(body_of(request), PRODUCT_SCHEMA)
        product = s.catalog.create(fields)
        return serialization.respond(201, "product", serialization.product_payload(product))

    def show(_request, params):
        product = s.catalog.require(path_id(params, what="product"))
        return serialization.respond(200, "product", serialization.product_payload(product))

    def patch(request, params):
        pid = path_id(params, what="product")
        fields = validate(body_of(request), PATCH_SCHEMA, partial=True)
        product = s.catalog.update(pid, fields)
        return serialization.respond(200, "product", serialization.product_payload(product))

    def delete(_request, params):
        s.catalog.delete(path_id(params, what="product"))
        return empty_response(204)

    def index(request, _params):
        q = request.query
        limit = pagination.parse_limit(q, s.config.page_default, s.config.page_max)
        rows = s.catalog.listing(q.get("q", ""), q.get("tag"))
        page, nxt = pagination.paginate(rows, limit, q.get("cursor"), newest_first=False)
        s.catalog.warm(page)
        from ..models import Product
        items = serialization.finish_list(
            "product", [serialization.product_payload(Product.from_row(r)) for r in page])
        return list_response(items, nxt)

    router.add("POST", "/products", create, name="products.create")
    router.add("GET", "/products", index, name="products.index")
    router.add("GET", "/products/{id}", show, name="products.show")
    router.add("PATCH", "/products/{id}", patch, name="products.patch")
    router.add("DELETE", "/products/{id}", delete, name="products.delete")
