"""The product catalog.

Reads go through a TTL cache (products are read on every order and every
product page; writes are rare). Every write path invalidates the cached
entry for the product it touched, so a read that follows a completed write
always sees it.
"""
from __future__ import annotations

from .. import cache as cachemod
from .. import db as dbmod
from ..errors import Conflict, NotFound
from ..models import Product
from . import search


class Catalog:
    def __init__(self, db, cache, clock, logger):
        self.db = db
        self.cache = cache
        self.clock = clock
        self.logger = logger

    # -- reads -------------------------------------------------------------
    def get(self, product_id) -> Product | None:
        """The product with this id, or None. ``product_id`` may be an int or
        its decimal string form."""
        k = cachemod.key("product", product_id)
        hit = self.cache.get(k)
        if hit is not cachemod.MISSING:
            return hit
        row = self.db.products.find(int(product_id))
        if row is None:
            return None
        product = Product.from_row(row)
        self.cache.put(k, product)
        return product

    def require(self, product_id) -> Product:
        p = self.get(product_id)
        if p is None:
            raise NotFound("no product %s" % product_id, code="product_not_found")
        return p

    def listing(self, query: str = "", tag: str | None = None) -> list[dict]:
        m = search.Matcher(query or "")
        tag = tag.strip().lower() if tag else None

        def keep(row):
            if tag is not None and tag not in row.get("tags", ()):
                return False
            return m.matches(row) if m else True

        return self.db.products.scan(keep)

    def warm(self, rows: list[dict]) -> None:
        """Pre-load the cache with rows a listing just returned: shoppers
        almost always check out right after browsing a page."""
        for row in rows:
            self.cache.put(cachemod.key("product", row["id"]), Product.from_row(row))

    # -- writes ------------------------------------------------------------
    def create(self, fields: dict) -> Product:
        now = self.clock.now_ms()
        row = dict(fields)
        row.setdefault("tags", ())
        row["slug"] = search.slugify(row["name"]) or row["sku"].lower()
        row["created_ms"] = now
        row["updated_ms"] = now
        try:
            with self.db.transaction():
                stored = self.db.products.insert(row)
        except dbmod.IntegrityError:
            raise Conflict("sku %s already exists" % row["sku"], code="duplicate_sku")
        self.logger.info("catalog.create", product=stored["id"], sku=stored["sku"])
        return Product.from_row(stored)

    def update(self, product_id, changes: dict) -> Product:
        row = self.db.products.find(int(product_id))
        if row is None:
            raise NotFound("no product %s" % product_id, code="product_not_found")
        changes = dict(changes)
        if "name" in changes:
            changes["slug"] = search.slugify(changes["name"]) or row["sku"].lower()
        changes["updated_ms"] = self.clock.now_ms()
        with self.db.transaction():
            stored = self.db.products.update(int(product_id), changes)
        self.cache.invalidate(cachemod.key("product", product_id))
        self.logger.info("catalog.update", product=stored["id"], fields=",".join(sorted(changes)))
        return Product.from_row(stored)

    def delete(self, product_id) -> None:
        row = self.db.products.find(int(product_id))
        if row is None:
            raise NotFound("no product %s" % product_id, code="product_not_found")
        with self.db.transaction():
            self.db.products.delete(int(product_id))
        self.cache.invalidate(cachemod.key("product", product_id))
        self.logger.info("catalog.delete", product=row["id"], sku=row["sku"])

    def adjust_stock(self, product_id: int, delta: int) -> int:
        """Add ``delta`` to a product's stock (inside the caller's
        transaction). Returns the new stock level."""
        row = self.db.products.get(product_id)
        new = row["stock"] + delta
        self.db.products.update(product_id, {"stock": new})
        self.cache.invalidate(cachemod.key("product", product_id))
        return new
