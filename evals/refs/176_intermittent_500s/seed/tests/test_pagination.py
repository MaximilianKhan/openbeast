from __future__ import annotations

import unittest

from helpers import AppCase

from shopd import pagination


class Limits(AppCase):
    def test_limit_validation(self):
        for bad in ("0", "-1", "x", "101"):
            r = self.call("GET", "/orders?limit=" + bad)
            self.assertEqual((r.status, r.json()["error"]), (400, "invalid_limit"), bad)

    def test_single_page(self):
        self.product("AB-1")
        self.product("AB-2")
        page = self.ok("GET", "/products?limit=5")
        self.assertEqual(len(page["items"]), 2)
        self.assertIsNone(page["next_cursor"])

    def test_exact_fit_has_no_next_page(self):
        for i in range(3):
            self.product("AB-%d" % i)
        page = self.ok("GET", "/products?limit=3")
        self.assertIsNone(page["next_cursor"])


class Traversal(AppCase):
    def test_products_two_pages(self):
        for i in range(5):
            self.product("AB-%d" % i)
            self.clock.advance(1000)
        first = self.ok("GET", "/products?limit=3")
        self.assertEqual([p["sku"] for p in first["items"]], ["AB-0", "AB-1", "AB-2"])
        second = self.ok("GET", "/products?limit=3&cursor=" + first["next_cursor"])
        self.assertEqual([p["sku"] for p in second["items"]], ["AB-3", "AB-4"])
        self.assertIsNone(second["next_cursor"])

    def test_orders_newest_first(self):
        p = self.product(stock=1000)
        c = self.customer()
        ids = []
        for _ in range(4):
            ids.append(self.order(c["id"], [(p["id"], 1)])["id"])
            self.clock.advance(250)
        first = self.ok("GET", "/orders?limit=2")
        self.assertEqual([o["id"] for o in first["items"]], ids[::-1][:2])
        second = self.ok("GET", "/orders?limit=2&cursor=" + first["next_cursor"])
        self.assertEqual([o["id"] for o in second["items"]], ids[::-1][2:])

    def test_rows_inserted_between_pages(self):
        for i in range(4):
            self.product("AB-%d" % i)
            self.clock.advance(2000)
        first = self.ok("GET", "/products?limit=2")
        self.product("AB-9")
        second = self.ok("GET", "/products?limit=10&cursor=" + first["next_cursor"])
        self.assertEqual([p["sku"] for p in second["items"]], ["AB-2", "AB-3", "AB-9"])


class CursorCodec(unittest.TestCase):
    def test_roundtrip(self):
        for key in [(0, 1), (1767225600000, 42), (1767225600000 + 86400000, 2 ** 31 - 1)]:
            self.assertEqual(pagination.decode_cursor(pagination.encode_cursor(key)), key)


if __name__ == "__main__":
    unittest.main()
