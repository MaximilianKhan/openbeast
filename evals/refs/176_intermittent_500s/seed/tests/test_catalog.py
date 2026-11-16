from __future__ import annotations

import unittest

from helpers import AppCase


class ProductCrud(AppCase):
    def test_create_and_get(self):
        p = self.product("AB-1", "12.5", 7, name="Café Crème", tags=["Coffee", "hot", "coffee"])
        self.assertEqual(p["price"], "12.50")
        self.assertEqual(p["slug"], "cafe-creme")
        self.assertEqual(p["tags"], ["coffee", "hot"])
        r = self.call("GET", "/products/%d" % p["id"])
        self.assertEqual(r.status, 200)
        self.assertEqual(r.json(), p)
        self.assertEqual(r.headers.get("Location"), "/products/%d" % p["id"])

    def test_leading_zero_ids(self):
        p = self.product()
        self.assertEqual(self.ok("GET", "/products/00%d" % p["id"])["id"], p["id"])
        self.assertEqual(self.call("GET", "/products/0").status, 404)
        self.assertEqual(self.call("GET", "/products/-1").status, 404)
        self.assertEqual(self.call("GET", "/products/abc").status, 404)

    def test_duplicate_sku(self):
        self.product("AB-1")
        r = self.call("POST", "/products", {"sku": "AB-1", "name": "x", "price": "1", "stock": 1})
        self.assertEqual(r.status, 409)
        self.assertEqual(r.json()["error"], "duplicate_sku")

    def test_validation(self):
        r = self.call("POST", "/products", {"sku": "bad sku", "name": "", "price": "1.234", "stock": -1,
                                            "colour": "red"})
        self.assertEqual(r.status, 400)
        body = r.json()
        self.assertEqual(body["error"], "validation_failed")
        self.assertEqual(sorted(body["problems"]), ["colour", "name", "price", "sku", "stock"])

    def test_patch(self):
        p = self.product("AB-1", "10.00")
        q = self.ok("PATCH", "/products/%d" % p["id"], {"price": "11.00", "name": "Renamed thing"})
        self.assertEqual(q["price"], "11.00")
        self.assertEqual(q["slug"], "renamed-thing")
        self.assertEqual(self.ok("GET", "/products/%d" % p["id"])["price"], "11.00")

    def test_patch_needs_a_field(self):
        p = self.product()
        r = self.call("PATCH", "/products/%d" % p["id"], {})
        self.assertEqual(r.status, 400)

    def test_delete(self):
        p = self.product()
        self.assertEqual(self.call("DELETE", "/products/%d" % p["id"]).status, 204)
        self.assertEqual(self.call("GET", "/products/%d" % p["id"]).status, 404)
        self.assertEqual(self.call("DELETE", "/products/%d" % p["id"]).status, 404)

    def test_slug_falls_back_to_sku(self):
        p = self.product("ZZ-9", name="日本茶")
        self.assertEqual(p["slug"], "zz-9")


class Search(AppCase):
    def setUp(self):
        super().setUp()
        self.product("TEA-1", name="Earl Grey Tea", tags=["tea"])
        self.product("TEA-2", name="Thé à la menthe", tags=["tea", "mint"])
        self.product("COF-1", name="Crème brûlée coffee", tags=["coffee"])

    def names(self, target):
        return [p["sku"] for p in self.ok("GET", target)["items"]]

    def test_prefix_tokens(self):
        self.assertEqual(self.names("/products?q=earl+gr"), ["TEA-1"])
        self.assertEqual(self.names("/products?q=the"), ["TEA-2"])
        self.assertEqual(self.names("/products?q=CREME"), ["COF-1"])

    def test_tag_filter(self):
        self.assertEqual(self.names("/products?tag=tea"), ["TEA-1", "TEA-2"])
        self.assertEqual(self.names("/products?tag=mint&q=menthe"), ["TEA-2"])
        self.assertEqual(self.names("/products?tag=nope"), [])


if __name__ == "__main__":
    unittest.main()
