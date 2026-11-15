from __future__ import annotations

import unittest

from helpers import AppCase

from shopd.http import Request


class Pricing(AppCase):
    def test_plain_order(self):
        p = self.product("AB-1", "19.99", 10)
        c = self.customer()
        o = self.order(c["id"], [(p["id"], 3)])
        self.assertEqual(o["subtotal"], "59.97")
        self.assertEqual(o["discount"], "0.00")
        self.assertEqual(o["tier_discount"], "0.00")
        self.assertEqual(o["tax"], "4.95")      # 59.97 * 0.0825 = 4.947525
        self.assertEqual(o["total"], "64.92")
        self.assertEqual(o["lines"], [{"product_id": p["id"], "sku": "AB-1", "name": "Product AB-1",
                                       "qty": 3, "unit_price": "19.99", "line_total": "59.97"}])
        self.assertEqual(self.ok("GET", "/products/%d" % p["id"])["stock"], 7)

    def test_gold_and_percent_coupon(self):
        p = self.product("AB-1", "40.00")
        c = self.customer(tier="gold")
        self.coupon(code="SPRING15", percent_off="15")
        o = self.order(c["id"], [(p["id"], 2)], coupon="spring15")
        self.assertEqual(o["coupon"], "SPRING15")
        self.assertEqual(o["discount"], "12.00")
        self.assertEqual(o["tier_discount"], "3.40")   # 5% of 68.00
        self.assertEqual(o["tax"], "5.33")              # 64.60 * 0.0825 = 5.3295
        self.assertEqual(o["total"], "69.93")

    def test_amount_coupon_is_capped(self):
        p = self.product("AB-1", "4.00")
        c = self.customer()
        self.coupon(code="FIVER", amount_off="5")
        o = self.order(c["id"], [(p["id"], 1)], coupon="FIVER")
        self.assertEqual(o["discount"], "4.00")
        self.assertEqual(o["total"], "0.00")

    def test_merged_lines(self):
        a = self.product("AB-1", "1.10")
        b = self.product("AB-2", "2.20")
        c = self.customer()
        o = self.order(c["id"], [(b["id"], 1), (a["id"], 2), (b["id"], 3)])
        self.assertEqual([(li["product_id"], li["qty"]) for li in o["lines"]], [(b["id"], 4), (a["id"], 2)])
        self.assertEqual(o["subtotal"], "11.00")


class Failures(AppCase):
    def setUp(self):
        super().setUp()
        self.p = self.product("AB-1", "10.00", 5)
        self.c = self.customer()

    def err(self, body, status, code):
        r = self.call("POST", "/orders", body)
        self.assertEqual(r.status, status, r.body)
        self.assertEqual(r.json()["error"], code)
        return r.json()

    def test_unknown_customer(self):
        self.err({"customer_id": 99, "lines": [{"product_id": self.p["id"], "qty": 1}]}, 422, "unknown_customer")

    def test_unknown_product(self):
        e = self.err({"customer_id": self.c["id"], "lines": [{"product_id": 77, "qty": 1}]}, 422, "unknown_product")
        self.assertEqual(e["product_id"], 77)

    def test_insufficient_stock(self):
        e = self.err({"customer_id": self.c["id"], "lines": [{"product_id": self.p["id"], "qty": 6}]},
                     409, "insufficient_stock")
        self.assertEqual((e["available"], e["requested"]), (5, 6))
        self.assertEqual(self.ok("GET", "/products/%d" % self.p["id"])["stock"], 5)

    def test_coupon_rules(self):
        self.coupon(code="ONCE", percent_off="10", max_uses=1)
        self.coupon(code="BIG", amount_off="5", min_subtotal="100")
        line = [{"product_id": self.p["id"], "qty": 1}]
        self.err({"customer_id": self.c["id"], "lines": line, "coupon": "NOPE"}, 422, "unknown_coupon")
        self.err({"customer_id": self.c["id"], "lines": line, "coupon": "BIG"}, 422, "coupon_min_subtotal")
        self.order(self.c["id"], [(self.p["id"], 1)], coupon="ONCE")
        self.err({"customer_id": self.c["id"], "lines": line, "coupon": "ONCE"}, 422, "coupon_exhausted")

    def test_body_errors(self):
        r = self.call("POST", "/orders", None, headers={"Content-Type": "application/json"})
        self.assertEqual(r.status, 400)
        r = self.app.handle(Request("POST", "/orders", body=b"{not json"))
        self.assertEqual((r.status, r.json()["error"]), (400, "invalid_json"))
        self.err({"customer_id": self.c["id"], "lines": []}, 400, "validation_failed")


class Lifecycle(AppCase):
    def test_cancel_restores_stock_and_coupon(self):
        p = self.product("AB-1", "10.00", 5)
        c = self.customer()
        self.coupon(code="ONCE", percent_off="10", max_uses=1)
        o = self.order(c["id"], [(p["id"], 2)], coupon="ONCE")
        self.clock.advance(60_000)
        x = self.ok("POST", "/orders/%d/cancel" % o["id"])
        self.assertEqual(x["status"], "cancelled")
        self.assertEqual(x["cancelled_at"], "2026-01-01T00:01:00.000Z")
        self.assertEqual(self.ok("GET", "/coupons/ONCE")["uses"], 0)
        r = self.call("POST", "/orders/%d/cancel" % o["id"])
        self.assertEqual((r.status, r.json()["error"]), (409, "already_cancelled"))

    def test_get_and_list(self):
        p = self.product()
        a = self.customer("a@example.com")
        b = self.customer("b@example.com")
        o1 = self.order(a["id"], [(p["id"], 1)])
        self.clock.advance(1000)
        o2 = self.order(b["id"], [(p["id"], 1)])
        self.clock.advance(1000)
        o3 = self.order(a["id"], [(p["id"], 1)])
        self.assertEqual(self.ok("GET", "/orders/%d" % o2["id"]), o2)
        ids = [o["id"] for o in self.ok("GET", "/orders")["items"]]
        self.assertEqual(ids, [o3["id"], o2["id"], o1["id"]])
        ids = [o["id"] for o in self.ok("GET", "/orders?customer_id=%d" % a["id"])["items"]]
        self.assertEqual(ids, [o3["id"], o1["id"]])
        self.assertEqual(self.call("GET", "/orders?status=open").status, 400)


class Idempotency(AppCase):
    def test_key_format(self):
        p = self.product()
        c = self.customer()
        body = {"customer_id": c["id"], "lines": [{"product_id": p["id"], "qty": 1}]}
        r = self.call("POST", "/orders", body, headers={"Idempotency-Key": "short"})
        self.assertEqual((r.status, r.json()["error"]), (400, "invalid_idempotency_key"))

    def test_first_use_and_conflict(self):
        p = self.product()
        c = self.customer()
        body = {"customer_id": c["id"], "lines": [{"product_id": p["id"], "qty": 1}]}
        r = self.call("POST", "/orders", body, headers={"Idempotency-Key": "key-0001"})
        self.assertEqual(r.status, 201)
        self.assertIsNone(r.headers.get("Idempotent-Replay"))
        body["lines"][0]["qty"] = 2
        r = self.call("POST", "/orders", body, headers={"Idempotency-Key": "key-0001"})
        self.assertEqual((r.status, r.json()["error"]), (422, "idempotency_conflict"))

    def test_failed_attempt_is_not_remembered(self):
        p = self.product(stock=1)
        c = self.customer()
        body = {"customer_id": c["id"], "lines": [{"product_id": p["id"], "qty": 2}]}
        r = self.call("POST", "/orders", body, headers={"Idempotency-Key": "key-0002"})
        self.assertEqual(r.status, 409)
        self.ok("PATCH", "/products/%d" % p["id"], {"stock": 5})
        r = self.call("POST", "/orders", body, headers={"Idempotency-Key": "key-0002"})
        self.assertEqual(r.status, 201)


if __name__ == "__main__":
    unittest.main()
