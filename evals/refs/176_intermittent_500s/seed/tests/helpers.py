"""Shared fixtures for the shopd test-suite."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shopd.app import create_app  # noqa: E402
from shopd.clock import FakeClock  # noqa: E402
from shopd.config import Config  # noqa: E402

START = FakeClock.DEFAULT_START


class AppCase(unittest.TestCase):
    """A fresh service per test, with a fake clock and an in-memory log."""

    config = Config(rate_capacity=0)

    def setUp(self):
        self.clock = FakeClock(START)
        self.log = []
        self.app = create_app(config=self.config, clock=self.clock, log_sink=self.log)

    # -- request helpers ---------------------------------------------------
    def call(self, method, target, body=None, headers=None, client="test"):
        return self.app.call(method, target, json_body=body, headers=headers, client=client)

    def ok(self, method, target, body=None, status=200, headers=None):
        r = self.call(method, target, body, headers)
        self.assertEqual(r.status, status, r.body)
        return r.json()

    # -- data helpers -------------------------------------------------------
    def product(self, sku="SKU-1", price="10.00", stock=100, name=None, tags=None):
        body = {"sku": sku, "name": name or ("Product " + sku), "price": price, "stock": stock}
        if tags is not None:
            body["tags"] = tags
        return self.ok("POST", "/products", body, status=201)

    def customer(self, email="ann@example.com", tier="standard", name="Ann"):
        return self.ok("POST", "/customers", {"email": email, "name": name, "tier": tier}, status=201)

    def coupon(self, **fields):
        return self.ok("POST", "/coupons", fields, status=201)

    def order(self, customer_id, lines, coupon=None, status=201, headers=None):
        body = {"customer_id": customer_id, "lines": [{"product_id": p, "qty": q} for p, q in lines]}
        if coupon is not None:
            body["coupon"] = coupon
        return self.ok("POST", "/orders", body, status=status, headers=headers)
