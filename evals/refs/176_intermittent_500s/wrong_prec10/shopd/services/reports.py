"""Sales reports.

Both reports count only orders whose status is "placed". Revenue is the sum
of order totals. A tier's ``share`` is its revenue as a percentage of the
report's revenue, rounded half-up to two decimals ("0.00" when the report
has no revenue).

The summary scans every order on every dashboard poll, so the aggregation
runs on integer cents and only the shares touch Decimal.
"""
from __future__ import annotations

import decimal
from decimal import ROUND_HALF_UP, Decimal

from .. import clock as clockmod
from .. import money
from ..models import TIERS

TWO_PLACES = Decimal("0.01")
SHARE_PRECISION = 10


def _cents(amount: Decimal) -> int:
    """Exact integer cents of a stored amount (stored amounts have two
    decimals)."""
    sign, digits, exponent = amount.as_tuple()
    value = int("".join(map(str, digits)) or "0") * 10 ** (exponent + 2)
    return -value if sign else value


def _fmt_cents(cents: int) -> str:
    return "%d.%02d" % divmod(cents, 100)


def _average(total_cents: int, n: int) -> str:
    if n == 0:
        return "0.00"
    return _fmt_cents((2 * total_cents + n) // (2 * n))   # half up


def _share(part_cents: int, whole_cents: int) -> str:
    # Shares are reported to two decimals; six significant digits is plenty
    # and keeps the division cheap on the big summary.
    ctx = decimal.getcontext()
    saved = ctx.prec
    ctx.prec = SHARE_PRECISION
    if not part_cents or not whole_cents:
        return "0.00"
    pct = (Decimal(part_cents) * money.HUNDRED / Decimal(whole_cents)).quantize(
        TWO_PLACES, rounding=ROUND_HALF_UP)
    ctx.prec = saved
    return str(pct)


class Reports:
    def __init__(self, db, customers_by_id):
        self.db = db
        self._customers_by_id = customers_by_id

    def _placed(self, start_ms=None, end_ms=None) -> list[dict]:
        def keep(row):
            if row["status"] != "placed":
                return False
            if start_ms is not None and not (start_ms <= row["created_ms"] < end_ms):
                return False
            return True
        return self.db.orders.scan(keep)

    def _aggregate(self, orders: list[dict]) -> dict:
        tiers = self._customers_by_id()
        revenue = 0
        by_tier = {t: [0, 0] for t in TIERS}
        units: dict[int, list] = {}
        for o in orders:
            total = _cents(o["total"])
            revenue += total
            t = tiers.get(o["customer_id"], "standard")
            by_tier[t][0] += 1
            by_tier[t][1] += total
            for li in o["lines"]:
                u = units.setdefault(li["product_id"], [li["sku"], 0, 0])
                u[1] += li["qty"]
                u[2] += _cents(li["line_total"])
        top = sorted(units.items(), key=lambda kv: (-kv[1][1], kv[0]))[:5]
        return {
            "orders": len(orders),
            "revenue": _fmt_cents(revenue),
            "average_order": _average(revenue, len(orders)),
            "by_tier": {t: {"orders": by_tier[t][0], "revenue": _fmt_cents(by_tier[t][1]),
                            "share": _share(by_tier[t][1], revenue)} for t in TIERS},
            "top_products": [{"product_id": pid, "sku": u[0], "units": u[1], "revenue": _fmt_cents(u[2])}
                             for pid, u in top],
        }

    def summary(self) -> dict:
        return self._aggregate(self._placed())

    def daily(self, day: str) -> dict:
        start, end = clockmod.day_bounds(day)
        out = self._aggregate(self._placed(start, end))
        out["date"] = day
        return out
