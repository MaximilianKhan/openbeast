"""Domain records.

Each model converts to and from the plain-dict rows that db.Table stores.
Money fields are Decimal; timestamps are epoch milliseconds.
"""
from __future__ import annotations

import dataclasses
from decimal import Decimal

TIERS = ("standard", "gold")
ORDER_STATUSES = ("placed", "cancelled")


@dataclasses.dataclass
class Product:
    id: int
    sku: str
    name: str
    price: Decimal
    stock: int
    tags: tuple = ()
    slug: str = ""
    created_ms: int = 0
    updated_ms: int = 0

    @classmethod
    def from_row(cls, row: dict) -> "Product":
        return cls(id=row["id"], sku=row["sku"], name=row["name"], price=row["price"],
                   stock=row["stock"], tags=tuple(row.get("tags", ())), slug=row.get("slug", ""),
                   created_ms=row.get("created_ms", 0), updated_ms=row.get("updated_ms", 0))

    def to_row(self) -> dict:
        d = dataclasses.asdict(self)
        d["tags"] = tuple(self.tags)
        return d


@dataclasses.dataclass
class Customer:
    id: int
    email: str
    name: str
    tier: str = "standard"
    created_ms: int = 0

    @classmethod
    def from_row(cls, row: dict) -> "Customer":
        return cls(id=row["id"], email=row["email"], name=row["name"], tier=row["tier"],
                   created_ms=row.get("created_ms", 0))


@dataclasses.dataclass
class Coupon:
    id: int
    code: str
    percent_off: Decimal | None = None
    amount_off: Decimal | None = None
    min_subtotal: Decimal = Decimal("0.00")
    max_uses: int | None = None
    uses: int = 0

    @classmethod
    def from_row(cls, row: dict) -> "Coupon":
        return cls(id=row["id"], code=row["code"], percent_off=row.get("percent_off"),
                   amount_off=row.get("amount_off"), min_subtotal=row.get("min_subtotal", Decimal("0.00")),
                   max_uses=row.get("max_uses"), uses=row.get("uses", 0))


@dataclasses.dataclass
class LineItem:
    product_id: int
    sku: str
    name: str
    qty: int
    unit_price: Decimal
    line_total: Decimal

    def to_row(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_row(cls, row: dict) -> "LineItem":
        return cls(**row)


@dataclasses.dataclass
class Order:
    id: int
    customer_id: int
    lines: list
    subtotal: Decimal
    discount: Decimal
    tier_discount: Decimal
    tax: Decimal
    total: Decimal
    coupon: str | None = None
    status: str = "placed"
    created_ms: int = 0
    cancelled_ms: int | None = None
    idempotency_key: str | None = None

    @classmethod
    def from_row(cls, row: dict) -> "Order":
        return cls(id=row["id"], customer_id=row["customer_id"],
                   lines=[LineItem.from_row(dict(li)) for li in row["lines"]],
                   subtotal=row["subtotal"], discount=row["discount"],
                   tier_discount=row["tier_discount"], tax=row["tax"], total=row["total"],
                   coupon=row.get("coupon"), status=row.get("status", "placed"),
                   created_ms=row.get("created_ms", 0), cancelled_ms=row.get("cancelled_ms"),
                   idempotency_key=row.get("idempotency_key"))

    def to_row(self) -> dict:
        d = dataclasses.asdict(self)
        d["lines"] = tuple(dict(li) for li in d["lines"])
        return d
