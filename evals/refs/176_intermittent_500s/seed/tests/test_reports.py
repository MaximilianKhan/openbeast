from __future__ import annotations

import unittest

from helpers import AppCase


class Reports(AppCase):
    def setUp(self):
        super().setUp()
        self.a = self.product("AB-1", "10.00", 100)
        self.b = self.product("AB-2", "2.50", 100)
        self.std = self.customer("s@example.com")
        self.gold = self.customer("g@example.com", tier="gold")

    def test_empty_summary(self):
        s = self.ok("GET", "/reports/summary")
        self.assertEqual(s["orders"], 0)
        self.assertEqual(s["revenue"], "0.00")
        self.assertEqual(s["average_order"], "0.00")
        self.assertEqual(s["top_products"], [])

    def test_summary(self):
        self.order(self.std["id"], [(self.a["id"], 3)])               # 30.00 -> 32.48
        self.order(self.gold["id"], [(self.b["id"], 4)])              # 10.00 -> 9.50 -> 10.28
        o = self.order(self.std["id"], [(self.b["id"], 2), (self.a["id"], 1)])  # 15.00 -> 16.24
        self.ok("POST", "/orders/%d/cancel" % o["id"])
        s = self.ok("GET", "/reports/summary")
        self.assertEqual(s["orders"], 2)
        self.assertEqual(s["revenue"], "42.76")
        self.assertEqual(s["average_order"], "21.38")
        self.assertEqual(s["by_tier"]["standard"], {"orders": 1, "revenue": "32.48", "share": "75.96"})
        self.assertEqual(s["by_tier"]["gold"], {"orders": 1, "revenue": "10.28", "share": "24.04"})
        self.assertEqual(s["top_products"], [
            {"product_id": self.b["id"], "sku": "AB-2", "units": 4, "revenue": "10.00"},
            {"product_id": self.a["id"], "sku": "AB-1", "units": 3, "revenue": "30.00"},
        ])

    def test_daily(self):
        self.order(self.std["id"], [(self.a["id"], 1)])
        self.order(self.gold["id"], [(self.a["id"], 1)])
        self.clock.advance(86400000)
        self.order(self.std["id"], [(self.b["id"], 1)])
        d = self.ok("GET", "/reports/daily?date=2026-01-01")
        self.assertEqual(d["date"], "2026-01-01")
        self.assertEqual(d["orders"], 2)
        self.assertEqual(d["revenue"], "21.11")
        d2 = self.ok("GET", "/reports/daily?date=2026-01-02")
        self.assertEqual(d2["orders"], 1)
        self.assertEqual(d2["by_tier"]["standard"]["share"], "100.00")

    def test_bad_date(self):
        for bad in ("", "2026-1-1", "2026-02-30", "yesterday"):
            r = self.call("GET", "/reports/daily?date=" + bad)
            self.assertEqual((r.status, r.json()["error"]), (400, "invalid_date"), bad)


if __name__ == "__main__":
    unittest.main()
