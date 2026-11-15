"""Stock reservation.

Placing an order reserves its quantities: every line's product must have at
least ``qty`` in stock, otherwise nothing is reserved and the order fails
with 409 insufficient_stock. Cancelling an order puts the stock back.
Reservations run inside the order's transaction and always read the
authoritative table, never the cache.
"""
from __future__ import annotations

from ..errors import Conflict


class Inventory:
    def __init__(self, db, catalog, logger):
        self.db = db
        self.catalog = catalog
        self.logger = logger

    def reserve(self, lines) -> None:
        wanted: dict[int, int] = {}
        for li in lines:
            wanted[li.product_id] = wanted.get(li.product_id, 0) + li.qty
        for pid in sorted(wanted):
            row = self.db.products.get(pid)
            if row["stock"] < wanted[pid]:
                raise Conflict("not enough stock for product %d" % pid, code="insufficient_stock",
                               product_id=pid, available=row["stock"], requested=wanted[pid])
        for pid in sorted(wanted):
            left = self.catalog.adjust_stock(pid, -wanted[pid])
            if left < 5:
                self.logger.warn("inventory.low", product=pid, stock=left)

    def release(self, lines) -> None:
        for li in lines:
            if self.db.products.find(li.product_id) is None:
                # Product was deleted after the order was placed: nothing to restock.
                continue
            self.catalog.adjust_stock(li.product_id, li.qty)
