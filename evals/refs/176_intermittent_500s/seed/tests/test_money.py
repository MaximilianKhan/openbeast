from __future__ import annotations

import unittest
from decimal import Decimal

from helpers import START

from shopd import cache, money
from shopd.clock import FakeClock, day_bounds, iso


class Money(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(money.parse("12"), Decimal("12.00"))
        self.assertEqual(money.parse("0.5"), Decimal("0.50"))
        self.assertEqual(money.parse("1000000.00"), Decimal("1000000.00"))
        for bad in ("-1", "1.234", "1e3", "NaN", "", "01", "1000000.01", None, True, " "):
            with self.assertRaises(money.MoneyFormatError, msg=repr(bad)):
                money.parse(bad)

    def test_rounding_is_half_up(self):
        self.assertEqual(money.fmt(Decimal("2.475")), "2.48")
        self.assertEqual(money.fmt(Decimal("2.465")), "2.47")
        self.assertEqual(money.fmt(Decimal("2.4649")), "2.46")
        self.assertEqual(money.percent_of(Decimal("19.99"), Decimal("15")), Decimal("3.00"))

    def test_clamp(self):
        self.assertEqual(money.clamp(Decimal("5"), money.ZERO, Decimal("4")), Decimal("4"))


class Cache(unittest.TestCase):
    def test_ttl_and_lru(self):
        clock = FakeClock(START)
        c = cache.TTLCache(clock, ttl_ms=1000, max_entries=2)
        c.put("a", 1)
        c.put("b", 2)
        self.assertEqual(c.get("a"), 1)
        c.put("c", 3)                     # evicts b (least recently used)
        self.assertIs(c.get("b"), cache.MISSING)
        clock.advance(1000)
        self.assertIs(c.get("a"), cache.MISSING)
        self.assertEqual(c.stats()["evictions"], 1)

    def test_invalidate(self):
        c = cache.TTLCache(FakeClock(START), ttl_ms=1000)
        c.put(cache.key("product", 1), "x")
        self.assertTrue(c.invalidate(cache.key("product", 1)))
        self.assertFalse(c.invalidate(cache.key("product", 1)))


class Clock(unittest.TestCase):
    def test_iso_and_days(self):
        self.assertEqual(iso(START + 1234), "2026-01-01T00:00:01.234Z")
        self.assertEqual(day_bounds("2026-01-02"), (START + 86400000, START + 2 * 86400000))
        with self.assertRaises(ValueError):
            day_bounds("2026-13-01")


if __name__ == "__main__":
    unittest.main()
