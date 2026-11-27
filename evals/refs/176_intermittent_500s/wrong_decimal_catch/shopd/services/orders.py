"""Placing, reading and cancelling orders."""
from __future__ import annotations

import decimal

from ..errors import Conflict, NotFound, Unprocessable
from ..models import Customer, Order
from . import pricing


class OrderService:
    def __init__(self, db, catalog, inventory, promotions, clock, config, logger):
        self.db = db
        self.catalog = catalog
        self.inventory = inventory
        self.promotions = promotions
        self.clock = clock
        self.config = config
        self.logger = logger

    def _customer(self, customer_id: int) -> Customer:
        row = self.db.customers.find(customer_id)
        if row is None:
            raise Unprocessable("unknown customer %d" % customer_id, code="unknown_customer",
                                customer_id=customer_id)
        return Customer.from_row(row)

    def _items(self, lines: list[dict]):
        merged: dict[int, int] = {}
        order: list[int] = []
        for line in lines:
            pid = line["product_id"]
            if pid not in merged:
                order.append(pid)
                merged[pid] = 0
            merged[pid] += line["qty"]
        items = []
        for pid in order:
            product = self.catalog.get(pid)
            if product is None:
                raise Unprocessable("unknown product %d" % pid, code="unknown_product", product_id=pid)
            if merged[pid] > self.config.max_qty:
                raise Unprocessable("quantity for product %d exceeds %d" % (pid, self.config.max_qty),
                                    code="qty_limit", product_id=pid)
            items.append((product, merged[pid]))
        return items

    def place(self, customer_id: int, lines: list[dict], coupon: str | None,
              idempotency_key: str | None = None) -> Order:
        customer = self._customer(customer_id)
        items = self._items(lines)
        try:
            quote = pricing.price(items, customer, coupon, self.promotions, self.config)
        except decimal.InvalidOperation:
            raise Unprocessable("order amount out of range", code="amount_out_of_range")
        now = self.clock.now_ms()
        with self.db.transaction():
            self.inventory.reserve(quote.lines)
            if quote.coupon is not None:
                self.promotions.consume(quote.coupon)
            row = self.db.orders.insert({
                "customer_id": customer.id,
                "lines": tuple(li.to_row() for li in quote.lines),
                "subtotal": quote.subtotal,
                "discount": quote.discount,
                "tier_discount": quote.tier_discount,
                "tax": quote.tax,
                "total": quote.total,
                "coupon": quote.coupon.code if quote.coupon is not None else None,
                "status": "placed",
                "created_ms": now,
                "cancelled_ms": None,
                "idempotency_key": idempotency_key,
            })
        order = Order.from_row(row)
        self.logger.info("order.placed", order=order.id, customer=customer.id,
                         lines=len(order.lines), total=str(order.total))
        return order

    def get(self, order_id: int) -> Order:
        row = self.db.orders.find(order_id)
        if row is None:
            raise NotFound("no order %d" % order_id, code="order_not_found")
        return Order.from_row(row)

    def cancel(self, order_id: int) -> Order:
        with self.db.transaction():
            order = self.get(order_id)
            if order.status == "cancelled":
                raise Conflict("order %d is already cancelled" % order_id, code="already_cancelled")
            self.inventory.release(order.lines)
            if order.coupon:
                self.promotions.release(order.coupon)
            row = self.db.orders.update(order_id, {"status": "cancelled",
                                                   "cancelled_ms": self.clock.now_ms()})
        self.logger.info("order.cancelled", order=order_id)
        return Order.from_row(row)

    def listing(self, customer_id: int | None = None, status: str | None = None) -> list[dict]:
        def keep(row):
            if customer_id is not None and row["customer_id"] != customer_id:
                return False
            if status is not None and row["status"] != status:
                return False
            return True
        return self.db.orders.scan(keep)
