# Hidden tests for 176_intermittent_500s.
#
# Embedded into the validator by build_task.py and fed to a fresh, isolated
# interpreter over stdin, one test function per process (so per-thread and
# per-process state cannot leak between tests). Each function raises on any
# deviation from docs/API.md. Expected values come from the independent
# oracle below, never from the service.
import decimal
import json
import random
import threading
from decimal import ROUND_HALF_UP, Decimal

from shopd.app import create_app
from shopd.clock import FakeClock
from shopd.config import Config
from shopd.http import Request

START = 1767225600000          # 2026-01-01T00:00:00Z
DAY = 86400000
TAX = Decimal("0.0825")
GOLD = Decimal("5")


class Fail(Exception):
    pass


def check(cond, what="check"):
    if not cond:
        raise Fail(what)


# The service and these tests share the thread's decimal context, which the
# code under test may change: every computation here runs in a private one.
def hp():
    return decimal.localcontext(decimal.Context(prec=60))


def dsum(xs):
    with hp():
        return sum(xs, Decimal("0"))


def money_str(n_cents):
    return "%d.%02d" % divmod(n_cents, 100)


def r2(x):
    with hp():
        return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def m(x):
    return str(r2(x))


def price_order(lines, gold, coupon):
    """Oracle for docs/API.md 5.2. lines: [(unit_price Decimal, qty)];
    coupon: None | ("pct", Decimal) | ("amt", Decimal)."""
    with hp():
        sub = sum((p * q for p, q in lines), Decimal("0"))
        if coupon is None:
            disc = Decimal("0")
        elif coupon[0] == "pct":
            disc = r2(sub * coupon[1] / 100)
        else:
            disc = min(coupon[1], sub)
        after = sub - disc
        tier = r2(after * GOLD / 100) if gold else Decimal("0")
        taxable = after - tier
        tax = r2(taxable * TAX)
        return {"subtotal": m(sub), "discount": m(disc), "tier_discount": m(tier),
                "tax": m(tax), "total": m(taxable + tax)}


def share(part, whole):
    with hp():
        if not part or not whole:
            return Decimal("0.00")
        return r2(Decimal(part) * 100 / Decimal(whole))


class Svc:
    def __init__(self, **cfg):
        cfg.setdefault("rate_capacity", 0)
        self.clock = FakeClock(START)
        self.log = []
        self.app = create_app(config=Config(**cfg), clock=self.clock, log_sink=self.log)

    def raw(self, method, target, body=None, headers=None, client="h"):
        return self.app.handle(Request.build(method, target, headers=headers, json_body=body,
                                             client=client))

    def call(self, method, target, body=None, headers=None, status=None, error=None):
        r = self.raw(method, target, body, headers)
        check(r.status != 500, "500")
        if status is not None:
            check(r.status == status, "status")
        if error is not None:
            check(r.json().get("error") == error, "error code")
        return r

    def j(self, method, target, body=None, status=200, headers=None):
        return self.call(method, target, body, headers, status=status).json()

    def product(self, sku, price, stock=1000, name=None, tags=None):
        b = {"sku": sku, "name": name or ("Item " + sku), "price": price, "stock": stock}
        if tags is not None:
            b["tags"] = tags
        return self.j("POST", "/products", b, 201)

    def customer(self, email, tier="standard"):
        return self.j("POST", "/customers", {"email": email, "name": email.split("@")[0], "tier": tier}, 201)

    def order_body(self, cid, lines, coupon=None):
        b = {"customer_id": cid, "lines": [{"product_id": p, "qty": q} for p, q in lines]}
        if coupon is not None:
            b["coupon"] = coupon
        return b

    def order(self, cid, lines, coupon=None, status=201, headers=None):
        return self.call("POST", "/orders", self.order_body(cid, lines, coupon), headers, status=status)

    def poison(self):
        """A daily report for a day on which one tier has no revenue."""
        d = self.j("GET", "/reports/daily?date=2025-12-31")
        check(d["orders"] == 0 and d["revenue"] == "0.00", "empty daily report")


def same_money(order, exp):
    for k in ("subtotal", "discount", "tier_discount", "tax", "total"):
        check(order[k] == exp[k], "money " + k)


def walk(svc, base, limit):
    """Follow next_cursor verbatim, the way the storefront does."""
    seen, target, pages = [], base + ("&" if "?" in base else "?") + "limit=%d" % limit, 0
    while True:
        page = svc.j("GET", target)
        seen.extend(page["items"])
        pages += 1
        check(pages < 500, "pagination does not terminate")
        cur = page["next_cursor"]
        if cur is None:
            return seen
        check(isinstance(cur, str) and cur, "cursor type")
        target = base + ("&" if "?" in base else "?") + "limit=%d&cursor=%s" % (limit, cur)


# --------------------------------------------------------------------------
# H01: regression -- catalog, pricing, cancel, listings (green on the seed).
def H01():
    s = Svc()
    a = s.product("AB-1", "19.99", 50, name="Crème brûlée", tags=["Dessert"])
    b = s.product("AB-2", "4.05", 50)
    g = s.customer("g@example.com", "gold")
    n = s.customer("n@example.com")
    s.j("POST", "/coupons", {"code": "take7", "percent_off": "7"}, 201)
    s.j("POST", "/coupons", {"code": "OFF3", "amount_off": "3", "max_uses": 2}, 201)
    o1 = s.order(g["id"], [(a["id"], 3), (b["id"], 1)], "TAKE7").json()
    same_money(o1, price_order([(Decimal("19.99"), 3), (Decimal("4.05"), 1)], True, ("pct", Decimal(7))))
    check(o1["coupon"] == "TAKE7")
    o2 = s.order(n["id"], [(b["id"], 7)], "off3").json()
    same_money(o2, price_order([(Decimal("4.05"), 7)], False, ("amt", Decimal(3))))
    check(s.j("GET", "/products?q=creme")["items"][0]["id"] == a["id"])
    check(s.j("GET", "/orders/%d" % o2["id"]) == o2, "GET order")
    s.clock.advance(5000)
    c = s.j("POST", "/orders/%d/cancel" % o1["id"])
    check(c["status"] == "cancelled" and c["cancelled_at"] == "2026-01-01T00:00:05.000Z")
    check(s.j("GET", "/coupons/TAKE7")["uses"] == 0)
    s.call("POST", "/orders/%d/cancel" % o1["id"], status=409, error="already_cancelled")
    lst = s.j("GET", "/orders?status=placed")
    check([o["id"] for o in lst["items"]] == [o2["id"]] and lst["next_cursor"] is None)
    s.order(n["id"], [(b["id"], 1)], "OFF3")
    s.order(n["id"], [(b["id"], 1)], "OFF3", status=422)
    s.order(n["id"], [(b["id"], 1000)], status=409)
    s.order(n["id"], [(99, 1)], status=422)
    s.order(99, [(b["id"], 1)], status=422)
    s.call("GET", "/products/abc", status=404)
    s.call("DELETE", "/orders/1", status=405)


# H02: a large order on a worker that has just served a daily report.
def H02():
    s = Svc()
    p = s.product("BULK-1", "2500.00", 500)
    q = s.product("BULK-2", "999.99", 500)
    c = s.customer("buyer@b2b.example", "gold")
    s.poison()
    o = s.order(c["id"], [(p["id"], 6), (q["id"], 3)]).json()
    same_money(o, price_order([(Decimal("2500.00"), 6), (Decimal("999.99"), 3)], True, None))
    s.poison()
    o = s.order(c["id"], [(p["id"], 400)]).json()
    same_money(o, price_order([(Decimal("2500.00"), 400)], True, None))
    check(s.j("GET", "/products/%d" % p["id"])["stock"] == 94, "stock after large orders")


# H03: after a report, ordinary orders must still be priced exactly.
def H03():
    s = Svc()
    cases = [("2398.04", 3, "standard", None), ("1437.29", 1, "gold", "12"),
             ("2315.62", 1, "gold", "33"), ("719.01", 3, "standard", None),
             ("209.31", 2, "gold", "33"), ("1516.93", 1, "standard", "15")]
    s.j("POST", "/coupons", {"code": "P12", "percent_off": "12"}, 201)
    s.j("POST", "/coupons", {"code": "P33", "percent_off": "33"}, 201)
    s.j("POST", "/coupons", {"code": "P15", "percent_off": "15"}, 201)
    gold = s.customer("gold@example.com", "gold")
    std = s.customer("std@example.com")
    for i, (price, qty, tier, pct) in enumerate(cases):
        p = s.product("DR-%d" % i, price, 50)
        s.poison()
        o = s.order((gold if tier == "gold" else std)["id"], [(p["id"], qty)],
                    None if pct is None else "P" + pct).json()
        same_money(o, price_order([(Decimal(price), qty)], tier == "gold",
                                  None if pct is None else ("pct", Decimal(pct))))


# H04: reports stay correct however often they run, on revenue above 10000.
def H04():
    s = Svc()
    p = s.product("BIG-1", "1234.56", 1000)
    q = s.product("SMALL-1", "3.33", 1000)
    g = s.customer("g@example.com", "gold")
    n = s.customer("n@example.com")
    s.poison()
    totals = {"gold": [], "standard": []}
    for i in range(12):
        cust, tier = (g, "gold") if i % 3 == 0 else (n, "standard")
        o = s.order(cust["id"], [(p["id"], 1 + i % 4), (q["id"], 2 + i)]).json()
        totals[tier].append(Decimal(o["total"]))
        if i % 4 == 1:
            s.poison()
    s.clock.advance(DAY)
    o = s.order(n["id"], [(q["id"], 1)]).json()
    for rep in range(3):
        summ = s.j("GET", "/reports/summary")
        day1 = s.j("GET", "/reports/daily?date=2026-01-01")
        day2 = s.j("GET", "/reports/daily?date=2026-01-02")
        rev1 = dsum(totals["gold"] + totals["standard"])
        check(day1["revenue"] == m(rev1) and day1["orders"] == 12, "daily revenue")
        check(summ["revenue"] == m(dsum([rev1, Decimal(o["total"])])) and summ["orders"] == 13, "summary revenue")
        with hp():
            avg = rev1 / 12
        check(day1["average_order"] == m(avg), "average")
        for t in ("gold", "standard"):
            tr = dsum(totals[t])
            check(day1["by_tier"][t]["revenue"] == m(tr), "tier revenue")
            with hp():
                close = abs(Decimal(day1["by_tier"][t]["share"]) - share(tr, rev1)) <= Decimal("0.01")
            check(close, "tier share")
        check(day2["by_tier"]["gold"]["share"] == "0.00" and day2["by_tier"]["standard"]["share"] == "100.00")
        check(day1["top_products"][0] == {"product_id": q["id"], "sku": "SMALL-1", "units": 90,
                                          "revenue": money_str(333 * 90)}, "top")


# H05: an idempotent retry gets the original response, byte for byte.
def H05():
    s = Svc()
    p = s.product("AB-1", "12.34", 10)
    c = s.customer("m@example.com", "gold")
    s.j("POST", "/coupons", {"code": "ONCE", "percent_off": "10", "max_uses": 1}, 201)
    body = s.order_body(c["id"], [(p["id"], 2)], "ONCE")
    h = {"Idempotency-Key": "mobile-7f3a9c"}
    first = s.call("POST", "/orders", body, h, status=201)
    check(first.headers.get("Idempotent-Replay") is None, "first is not a replay")
    for attempt in range(3):
        again = s.call("POST", "/orders", json.loads(json.dumps(body)), h, status=201)
        check(again.body == first.body, "replay body")
        check(again.headers.get("Location") == first.headers.get("Location"), "replay Location")
        check(again.headers.get("Idempotent-Replay") == "true", "replay header")
    check(s.j("GET", "/products/%d" % p["id"])["stock"] == 8, "stock reserved once")
    check(s.j("GET", "/coupons/ONCE")["uses"] == 1, "coupon used once")
    check(len(s.j("GET", "/orders")["items"]) == 1, "one order")


# H06: replays interleaved with other traffic; key order; conflicts.
def H06():
    s = Svc()
    p = s.product("AB-1", "7.77", 100)
    q = s.product("AB-2", "1.01", 100)
    c = s.customer("m@example.com")
    k1, k2 = {"Idempotency-Key": "K1-aaaaaaaa"}, {"Idempotency-Key": "K2-bbbbbbbb"}
    b1 = s.order_body(c["id"], [(p["id"], 1), (q["id"], 2)])
    first1 = s.call("POST", "/orders", b1, k1, status=201)
    first2 = s.call("POST", "/orders", s.order_body(c["id"], [(q["id"], 5)]), k2, status=201)
    s.order(c["id"], [(p["id"], 1)])
    s.clock.advance(3600 * 1000)
    reordered = {"lines": b1["lines"], "customer_id": c["id"]}
    r = s.call("POST", "/orders", reordered, k1, status=201)
    check(r.body == first1.body and r.headers.get("Location") == first1.headers.get("Location"), "replay 1")
    r = s.call("POST", "/orders", s.order_body(c["id"], [(q["id"], 5)]), k2, status=201)
    check(r.body == first2.body, "replay 2")
    s.call("POST", "/orders", s.order_body(c["id"], [(q["id"], 6)]), k2, status=422, error="idempotency_conflict")
    check(s.j("GET", "/orders/%d" % first1.json()["id"]) == first1.json(), "stored order unchanged")
    check(len(s.j("GET", "/orders")["items"]) == 3, "three orders")
    check(s.j("GET", "/products/%d" % q["id"])["stock"] == 93, "stock")


# H07: a price change is charged on the very next order.
def H07():
    s = Svc()
    p = s.product("AB-1", "10.00", 100)
    c = s.customer("a@example.com")
    o = s.order(c["id"], [(p["id"], 1)]).json()
    check(o["lines"][0]["unit_price"] == "10.00")
    s.clock.advance(1000)
    check(s.j("GET", "/products?limit=5")["items"][0]["price"] == "10.00")
    s.j("PATCH", "/products/%d" % p["id"], {"price": "12.50", "name": "Renamed"})
    o = s.order(c["id"], [(p["id"], 2)]).json()
    check(o["lines"][0]["unit_price"] == "12.50" and o["lines"][0]["name"] == "Renamed", "new price charged")
    same_money(o, price_order([(Decimal("12.50"), 2)], False, None))
    s.j("GET", "/products?q=renamed")
    s.j("PATCH", "/products/0%d" % p["id"], {"price": "9.99"})
    o = s.order(c["id"], [(p["id"], 1)]).json()
    check(o["lines"][0]["unit_price"] == "9.99", "leading-zero id patch")


# H08: a deleted product cannot be ordered, even right after a cached read.
def H08():
    s = Svc()
    p = s.product("AB-1", "10.00", 100)
    keep = s.product("AB-2", "3.00", 100)
    c = s.customer("a@example.com")
    s.order(c["id"], [(p["id"], 1)])
    s.j("GET", "/products/%d" % p["id"])
    s.j("GET", "/products")
    s.clock.advance(2000)
    s.call("DELETE", "/products/%d" % p["id"], status=204)
    r = s.order(c["id"], [(keep["id"], 1), (p["id"], 1)], status=422)
    check(r.json()["error"] == "unknown_product" and r.json()["product_id"] == p["id"], "unknown_product")
    s.call("GET", "/products/%d" % p["id"], status=404)
    check(s.j("GET", "/products/%d" % keep["id"])["stock"] == 100, "nothing reserved")


# H09: product reads reflect stock changes made by orders and cancels.
def H09():
    s = Svc()
    p = s.product("AB-1", "10.00", 100)
    c = s.customer("a@example.com")
    check(s.j("GET", "/products/%d" % p["id"])["stock"] == 100)
    o = s.order(c["id"], [(p["id"], 30)]).json()
    check(s.j("GET", "/products/%d" % p["id"])["stock"] == 70, "stock after order")
    s.j("POST", "/orders/%d/cancel" % o["id"])
    check(s.j("GET", "/products/00%d" % p["id"])["stock"] == 100, "stock after cancel")
    s.j("PATCH", "/products/%d" % p["id"], {"stock": 5})
    s.order(c["id"], [(p["id"], 6)], status=409)
    check(s.j("GET", "/products/%d" % p["id"])["stock"] == 5, "stock after patch")


# H10: following order cursors visits every order exactly once.
def H10():
    rng = random.Random(0x176A)
    s = Svc()
    p = s.product("AB-1", "1.00", 100000)
    cs = [s.customer("c%d@example.com" % i)["id"] for i in range(3)]
    ids = []
    for i in range(70):
        s.clock.advance(rng.choice([0, 1, 7, 250, 1000, 1999, 4321]))
        ids.append((s.clock.now_ms(), s.order(cs[i % 3], [(p["id"], 1)]).json()["id"], cs[i % 3]))
    newest = [i for _, i, _ in sorted(ids, reverse=True)]
    for limit in (1, 2, 3, 5, 7, 11, 100):
        got = [o["id"] for o in walk(s, "/orders", limit)]
        check(got == newest, "order traversal")
    got = [o["id"] for o in walk(s, "/orders?customer_id=%d" % cs[1], 4)]
    check(got == [i for i in newest if (i - 1) % 3 == 1], "filtered traversal")


# H11: following product cursors (with search) visits every match once.
def H11():
    rng = random.Random(0x176B)
    s = Svc()
    made = []
    for i in range(45):
        s.clock.advance(rng.choice([1, 3, 500, 999, 1500, 2047]))
        tag = "red" if i % 2 else "blue"
        made.append((s.product("P-%03d" % i, "1.00", 1, name="Widget %d" % i, tags=[tag])["id"], tag))
    for limit in (1, 4, 6, 9):
        check([x["id"] for x in walk(s, "/products", limit)] == [i for i, _ in made], "product traversal")
    check([x["id"] for x in walk(s, "/products?tag=red&q=widg", 4)] == [i for i, t in made if t == "red"],
          "filtered product traversal")


# H12: cursors the service did not issue are a 400.
def H12():
    s = Svc()
    p = s.product("AB-1", "1.00", 100)
    c = s.customer("a@example.com")
    for _ in range(4):
        s.order(c["id"], [(p["id"], 1)])
    for bad in ("undefined", "null", "AAAA", "%%%", "@@@@@@@@@@@@@@@@", "abc def", "x" * 40,
                "AAABm3baqAAAAAAB" + "Q", "=", "AAAAAAAAAAAAAAA"):
        for base in ("/orders?limit=2&cursor=", "/products?cursor="):
            from urllib.parse import quote
            r = s.raw("GET", base + quote(bad, safe=""))
            check(r.status == 400 and r.json()["error"] == "invalid_cursor", "invalid cursor")


# H13: seeded random traffic against a model of docs/API.md; never a 500.
def H13():
    rng = random.Random(0x5EED176)
    s = Svc()
    model_p = {}      # id -> {"price": Decimal, "stock": int, "name": str}
    deleted = set()
    custs = []        # (id, gold)
    coupons = {}      # code -> [kind, value, min_subtotal, max_uses, uses]
    orders = {}       # id -> dict(status, total, created, gold, lines, coupon)
    replays = []      # (key, body, response)
    tiers = {}

    for i in range(14):
        price = money_str(rng.choice([rng.randrange(100, 5000), rng.randrange(50000, 400000)]))
        pr = s.product("FZ-%02d" % i, price, rng.randrange(5, 400))
        model_p[pr["id"]] = {"price": Decimal(pr["price"]), "stock": pr["stock"], "name": pr["name"]}
    for i in range(8):
        gold = i % 3 == 0
        cid = s.customer("f%d@example.com" % i, "gold" if gold else "standard")["id"]
        custs.append((cid, gold))
        tiers[cid] = gold
    for code, kind, val, mins, mx in (("PCT10", "pct", "10", None, None), ("PCT33", "pct", "33", "50", 5),
                                      ("AMT25", "amt", "25", None, 3), ("BIGSPEND", "pct", "15", "8000", None)):
        b = {"code": code, ("percent_off" if kind == "pct" else "amount_off"): val}
        if mins:
            b["min_subtotal"] = mins
        if mx:
            b["max_uses"] = mx
        s.j("POST", "/coupons", b, 201)
        coupons[code] = [kind, Decimal(val), Decimal(mins or "0"), mx, 0]

    def expect_order(cid, gold, lines, coupon):
        merged, seq = {}, []
        for pid, q in lines:
            if pid not in merged:
                seq.append(pid)
                merged[pid] = 0
            merged[pid] += q
        for pid in seq:
            if pid in deleted or pid not in model_p:
                return 422, "unknown_product", None
        priced = [(model_p[pid]["price"], merged[pid]) for pid in seq]
        sub = dsum(pp * q for pp, q in priced)
        cp = None
        if coupon:
            c = coupons.get(coupon)
            if c is None:
                return 422, "unknown_coupon", None
            if c[3] is not None and c[4] >= c[3]:
                return 422, "coupon_exhausted", None
            if sub < c[2]:
                return 422, "coupon_min_subtotal", None
            cp = (c[0], c[1])
        for pid in seq:
            if merged[pid] > model_p[pid]["stock"]:
                return 409, "insufficient_stock", None
        return 201, None, (seq, merged, price_order(priced, gold, cp))

    # A "browse, edit, buy" episode: list, read, patch or delete, order, read
    # -- all inside one cache TTL.
    forced = []
    for step in range(480):
        live = sorted(set(model_p) - deleted)
        if not forced and len(live) > 7 and rng.random() < 0.07:
            f = rng.choice(live)
            forced = [(0.68, f), (0.62, f), (0.50 if rng.random() < 0.75 else 0.58, f), (0.20, f), (0.62, f)]
        if forced:
            s.clock.advance(rng.choice([0, 5, 300, 1500]))
            op, focus = forced.pop(0)
        else:
            s.clock.advance(rng.choice([0, 3, 40, 900, 2500, 7000, 7000, 31000, 3600 * 1000]))
            op, focus = rng.random(), None
        if op < 0.40 and live:
            cid, gold = rng.choice(custs)
            pool = live + sorted(deleted)[:2]
            lines = [(rng.choice(pool), rng.choice([1, 1, 2, 3, 5, 12])) for _ in range(rng.randrange(1, 4))]
            if focus is not None:
                lines = [(focus, rng.choice([1, 2, 3]))] + lines[:1]
            coupon = rng.choice([None, None, None, "PCT10", "PCT33", "AMT25", "BIGSPEND", "NOPE"])
            body = s.order_body(cid, lines, coupon)
            hdr = {"Idempotency-Key": "fz-%06d" % step} if rng.random() < 0.3 else None
            st, err, exp = expect_order(cid, gold, lines, coupon)
            r = s.call("POST", "/orders", body, hdr, status=st, error=err)
            if st == 201:
                o = r.json()
                seq, merged, money_ = exp
                same_money(o, money_)
                check([(li["product_id"], li["qty"]) for li in o["lines"]] == [(pid, merged[pid]) for pid in seq],
                      "lines")
                for li in o["lines"]:
                    check(li["unit_price"] == m(model_p[li["product_id"]]["price"]), "unit price")
                for pid in seq:
                    model_p[pid]["stock"] -= merged[pid]
                if coupon:
                    coupons[coupon][4] += 1
                orders[o["id"]] = {"status": "placed", "total": Decimal(o["total"]), "created": s.clock.now_ms(),
                                   "gold": gold, "lines": [(pid, merged[pid]) for pid in seq], "coupon": coupon}
                if hdr:
                    replays.append((hdr, body, r, s.clock.now_ms()))
        elif op < 0.48 and [x for x in replays if s.clock.now_ms() - x[3] < DAY - 3600 * 1000]:
            hdr, body, first, _t = rng.choice([x for x in replays if s.clock.now_ms() - x[3] < DAY - 3600 * 1000])
            r = s.call("POST", "/orders", body, hdr, status=201)
            check(r.body == first.body and r.headers.get("Location") == first.headers.get("Location"), "fuzz replay")
            check(r.headers.get("Idempotent-Replay") == "true", "fuzz replay header")
        elif op < 0.56 and live and (focus is None or focus in live):
            pid = rng.choice(live) if focus is None else focus
            ch = {}
            if rng.random() < 0.7:
                ch["price"] = money_str(rng.randrange(100, 300000))
            if rng.random() < 0.5 or not ch:
                ch["stock"] = rng.randrange(0, 500)
            got = s.j("PATCH", "/products/%s" % rng.choice(["%d", "0%d"]) % pid, ch)
            if "price" in ch:
                model_p[pid]["price"] = Decimal(ch["price"])
            if "stock" in ch:
                model_p[pid]["stock"] = ch["stock"]
            check(got["price"] == m(model_p[pid]["price"]) and got["stock"] == model_p[pid]["stock"], "patch body")
        elif op < 0.60 and len(live) > 6 and (focus is None or focus in live):
            pid = rng.choice(live) if focus is None else focus
            s.call("DELETE", "/products/%d" % pid, status=204)
            deleted.add(pid)
        elif op < 0.66 and focus in deleted:
            s.call("GET", "/products/%d" % focus, status=404)
        elif op < 0.66 and live:
            pid = rng.choice(live) if focus is None else focus
            got = s.j("GET", "/products/%d" % pid)
            check(got["price"] == m(model_p[pid]["price"]) and got["stock"] == model_p[pid]["stock"], "product read")
        elif op < 0.70:
            for got in walk(s, "/products", rng.choice([5, 50])):
                pid = got["id"]
                check(pid in live and got["price"] == m(model_p[pid]["price"])
                      and got["stock"] == model_p[pid]["stock"], "product listing")
        elif op < 0.76 and orders:
            oid = rng.choice(sorted(orders))
            o = orders[oid]
            if o["status"] == "cancelled":
                s.call("POST", "/orders/%d/cancel" % oid, status=409, error="already_cancelled")
            else:
                s.j("POST", "/orders/%d/cancel" % oid)
                o["status"] = "cancelled"
                for pid, q in o["lines"]:
                    if pid not in deleted:
                        model_p[pid]["stock"] += q
                if o["coupon"]:
                    coupons[o["coupon"]][4] -= 1
        elif op < 0.88:
            if rng.random() < 0.5:
                day_start = START + DAY * rng.randrange(0, 1 + (s.clock.now_ms() - START) // DAY + 1)
                day = _day(day_start)
                rep = s.j("GET", "/reports/daily?date=" + day)
                sel = [o for o in orders.values() if o["status"] == "placed"
                       and day_start <= o["created"] < day_start + DAY]
            else:
                rep = s.j("GET", "/reports/summary")
                sel = [o for o in orders.values() if o["status"] == "placed"]
            rev = dsum(o["total"] for o in sel)
            check(rep["orders"] == len(sel) and rep["revenue"] == m(rev), "fuzz report revenue")
            for t, flag in (("gold", True), ("standard", False)):
                tr = dsum(o["total"] for o in sel if o["gold"] == flag)
                check(rep["by_tier"][t]["revenue"] == m(tr), "fuzz tier revenue")
                with hp():
                    close = abs(Decimal(rep["by_tier"][t]["share"]) - share(tr, rev)) <= Decimal("0.01")
                check(close, "fuzz share")
        else:
            limit = rng.choice([1, 3, 8, 25])
            cid = rng.choice(custs)[0]
            got = [o["id"] for o in walk(s, "/orders?customer_id=%d" % cid, limit)]
            exp = [oid for oid in sorted(orders, key=lambda k: (orders[k]["created"], k), reverse=True)
                   if _owner(s, oid) == cid]
            check(got == exp, "fuzz traversal")
    check(not any("http.unhandled" in ln for ln in s.log), "unhandled exception logged")


_OWNERS = {}


def _owner(s, oid):
    if oid not in _OWNERS:
        _OWNERS[oid] = s.j("GET", "/orders/%d" % oid)["customer_id"]
    return _OWNERS[oid]


def _day(ms):
    from shopd.clock import day_of
    return day_of(ms)


# H14: plumbing regression (green on the seed).
def H14():
    s = Svc(rate_capacity=2, rate_per_min=60)
    check(s.raw("GET", "/products").status == 200)
    check(s.raw("GET", "/products").status == 200)
    r = s.raw("GET", "/products")
    check(r.status == 429 and r.headers.get("Retry-After") == "1", "rate limit")
    check(s.raw("GET", "/health").status == 200)
    check(s.raw("GET", "/products", client="other").status == 200)
    check(s.raw("GET", "/nope", client="q1").status == 404 and s.raw("PUT", "/orders", client="q2").status == 405)
    r = s.raw("POST", "/customers", {"email": "bad", "name": "", "x": 1}, client="third")
    check(r.status == 400 and sorted(r.json()["problems"]) == ["email", "name", "x"], "validation")
    r = s.raw("GET", "/health", headers={"X-Request-Id": "trace-1"})
    check(r.headers.get("X-Request-Id") == "trace-1", "request id")


# H15: worker threads that serve reports keep pricing large orders exactly.
def H15():
    t = Svc()
    p = t.product("AB-1", "2000.00", 10000)
    c = t.customer("w@example.com")
    errors = []

    def worker(n):
        try:
            for i in range(15):
                if i % 5 == 0:
                    t.j("GET", "/reports/daily?date=2025-12-30")
                o = t.order(c["id"], [(p["id"], 1 + (n + i) % 7)]).json()
                same_money(o, price_order([(Decimal("2000.00"), 1 + (n + i) % 7)], False, None))
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    ths = [threading.Thread(target=worker, args=(n,), name="w%d" % (n + 1)) for n in range(4)]
    for th in ths:
        th.start()
    for th in ths:
        th.join(20)
    check(not errors, "threaded orders")
    check(t.j("GET", "/reports/summary")["orders"] == 60, "60 orders")


ORDER = ["H01", "H14", "H04", "H02", "H03", "H15", "H05", "H06", "H07", "H08", "H09", "H10", "H11", "H12", "H13"]
