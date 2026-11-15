"""/customers"""
from __future__ import annotations

from .. import db as dbmod
from .. import serialization
from ..errors import Conflict, NotFound
from ..models import TIERS, Customer
from ..validation import Field, validate
from .common import body_of, path_id

CUSTOMER_SCHEMA = {
    "email": Field("str", pattern=r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$", max_len=254),
    "name": Field("str", max_len=120),
    "tier": Field("str", required=False, choices=TIERS),
}


def register(router, s) -> None:
    def create(request, _params):
        fields = validate(body_of(request), CUSTOMER_SCHEMA)
        row = {"email": fields["email"].lower(), "name": fields["name"],
               "tier": fields.get("tier", "standard"), "created_ms": s.clock.now_ms()}
        try:
            with s.db.transaction():
                stored = s.db.customers.insert(row)
        except dbmod.IntegrityError:
            raise Conflict("a customer with that email exists", code="duplicate_email")
        return serialization.respond(201, "customer",
                                     serialization.customer_payload(Customer.from_row(stored)))

    def show(_request, params):
        cid = int(path_id(params, what="customer"))
        row = s.db.customers.find(cid)
        if row is None:
            raise NotFound("no customer %d" % cid, code="customer_not_found")
        return serialization.respond(200, "customer",
                                     serialization.customer_payload(Customer.from_row(row)))

    router.add("POST", "/customers", create, name="customers.create")
    router.add("GET", "/customers/{id}", show, name="customers.show")
