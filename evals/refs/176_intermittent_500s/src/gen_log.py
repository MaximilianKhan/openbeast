#!/usr/bin/env python3
"""Generate seed/logs/shopd.log by replaying synthetic production traffic
against the SEED service (so every traceback and line number is genuine).

The replay mimics shopd.server: one shared App, four worker threads named
w1..w4 that each serve one request at a time and are recycled after
MAX_REQUESTS requests. Requests are handed to a seeded-random worker and
served strictly one after another, so the output is byte-for-byte
deterministic for a given seed and Python minor version.

usage: gen_log.py SEED_DIR OUT_FILE
"""
import heapq
import json
import os
import queue
import random
import re
import shutil
import sys
import sysconfig
import tempfile
import threading

SEED_DIR, OUT = sys.argv[1], sys.argv[2]
WORK = tempfile.mkdtemp(prefix="gen176_")
SRV = os.path.join(WORK, "srv", "shopd")
shutil.copytree(os.path.join(SEED_DIR, "shopd"), os.path.join(SRV, "shopd"))
sys.path.insert(0, SRV)
sys.dont_write_bytecode = True

from shopd.app import create_app  # noqa: E402
from shopd.clock import FakeClock, day_of  # noqa: E402
from shopd.config import Config  # noqa: E402
from shopd.http import Request  # noqa: E402

T0 = 1772485200000            # 2026-03-02T21:00:00Z
T_END = T0 + int(6.5 * 3600 * 1000)
MIDNIGHT = 1772496000000      # 2026-03-03T00:00:00Z
GOLD_QUIET_UNTIL = MIDNIGHT + 47 * 60 * 1000
MAX_REQUESTS = 150
rng = random.Random(0x5110D)
clock = FakeClock(T0)
sink = []
config = Config(log_bodies=True)
app = create_app(config=config, clock=clock, log_sink=sink)

# ---------------------------------------------------------------- workers --
class Worker:
    def __init__(self, name):
        self.name = name
        self.inbox = queue.Queue()
        self.served = 0
        self.started = threading.Event()
        self.thread = threading.Thread(target=self.run, name=name, daemon=True)
        self.thread.start()
        self.started.wait()

    def run(self):
        app.logger.info("server.worker_start", worker=self.name)
        self.started.set()
        while True:
            job = self.inbox.get()
            if job is None:
                return
            req, reply = job
            reply.put(app.handle(req))
            self.served += 1
            if self.served >= MAX_REQUESTS:
                app.logger.info("server.recycle", worker=self.name, served=self.served)
                reply.put("RECYCLE")
                return
            reply.put(None)


workers = {}
for i in range(1, 5):
    w = Worker("w%d" % i)
    workers[w.name] = w


def send(method, target, body=None, headers=None, client="storefront"):
    name = rng.choice(sorted(workers))
    w = workers[name]
    req = Request.build(method, target, headers=headers, json_body=body, client=client)
    reply = queue.Queue()
    w.inbox.put((req, reply))
    resp = reply.get()
    tail = reply.get()
    if tail == "RECYCLE":
        w.thread.join()
        workers[name] = Worker(name)
    return resp


# ------------------------------------------------------------------ data ---
products = {}        # id -> dict(price, stock, tags, sku)
deleted = set()
customers = []       # (id, tier)
WORDS = ["Espresso", "Crème", "Brûlée", "Matcha", "Oolong", "Chai", "Kettle", "Grinder", "Tamper",
         "Filter", "Dripper", "Carafe", "Mug", "Scale", "Beans", "Decaf", "Roast", "Café", "Thé",
         "Pour-over", "Cold Brew", "Moka", "Siphon", "Aeropress"]


def seed_data():
    for i in range(40):
        wholesale = i % 5 == 4
        name = "%s %s" % (WORDS[i % len(WORDS)], ["Classic", "Pro", "Mini", "Deluxe", "Bulk Case"][i % 5])
        price = (rng.randrange(150000, 420000) if wholesale else rng.randrange(399, 8999)) / 100
        tags = ["wholesale"] if wholesale else rng.sample(["coffee", "tea", "gear", "gift", "sale"], 2)
        body = {"sku": "%s-%03d" % ("WH" if wholesale else "SF", i + 1), "name": name,
                "price": "%.2f" % price, "stock": rng.randrange(60, 900) if not wholesale else rng.randrange(200, 2000),
                "tags": tags}
        r = send("POST", "/products", body, client="seed")
        j = r.json()
        products[j["id"]] = {"price": j["price"], "stock": j["stock"], "tags": j["tags"], "sku": j["sku"]}
        clock.advance(rng.randrange(40, 160))
    first = ["ana", "ben", "chen", "dara", "eli", "fay", "gus", "hana", "ivo", "jun", "kai", "lea",
             "max", "nia", "oto", "pia", "quin", "rae", "sol", "tia", "uma", "vik", "wen", "yas"]
    for i, n in enumerate(first):
        tier = "gold" if i % 3 == 1 else "standard"
        r = send("POST", "/customers", {"email": "%s@%s" % (n, rng.choice(["mail.example", "corp.example"])),
                                        "name": n.title(), "tier": tier}, client="seed")
        customers.append((r.json()["id"], tier))
        clock.advance(rng.randrange(40, 160))
    for body in ({"code": "SPRING10", "percent_off": "10"},
                 {"code": "WELCOME5", "amount_off": "5", "max_uses": 300},
                 {"code": "VIP33", "percent_off": "33", "min_subtotal": "100", "max_uses": 40},
                 {"code": "B2B15", "percent_off": "15", "min_subtotal": "5000"}):
        send("POST", "/coupons", body, client="seed")
        clock.advance(rng.randrange(40, 160))


def live(pred=None):
    return [p for p in sorted(products) if p not in deleted and (pred is None or pred(products[p]))]


def retail():
    return live(lambda p: "wholesale" not in p["tags"])


def pick_customer(now):
    while True:
        cid, tier = rng.choice(customers)
        if tier == "gold" and MIDNIGHT <= now < GOLD_QUIET_UNTIL:
            continue
        return cid, tier


def record(resp):
    if resp.status in (200, 201) and resp.body:
        j = resp.json()
        for li in j.get("lines", ()):
            if li["product_id"] in products:
                products[li["product_id"]]["stock"] -= li["qty"]


# --------------------------------------------------------------- traffic ---
events = []
seq = [0]


def at(t, fn, *args):
    seq[0] += 1
    heapq.heappush(events, (t, seq[0], fn, args))


def storefront_session():
    now = clock.now_ms()
    r = send("GET", "/products?limit=12")
    pages = rng.choice([0, 0, 1, 1, 2, 3])
    for _ in range(pages):
        cur = r.json().get("next_cursor") if r.status == 200 else None
        if not cur:
            break
        clock.advance(rng.randrange(800, 9000))
        r = send("GET", "/products?limit=12&cursor=" + cur)
    if rng.random() < 0.3:
        q = rng.choice(["creme", "matcha", "the", "pro", "mini", "cafe", "cold br", "kettle"])
        send("GET", "/products?q=" + q.replace(" ", "+"))
    for _ in range(rng.randrange(1, 4)):
        clock.advance(rng.randrange(1500, 20000))
        send("GET", "/products/%d" % rng.choice(retail()))
    if rng.random() < 0.38:
        clock.advance(rng.randrange(5000, 40000))
        place_order("storefront", pick_customer(now))


def place_order(client, cust, lines=None, coupon="auto", key=None):
    cid, tier = cust
    if lines is None:
        pool = retail()
        lines = [{"product_id": rng.choice(pool), "qty": rng.choice([1, 1, 1, 2, 2, 3, 4, 6])}
                 for _ in range(rng.randrange(1, 4))]
    body = {"customer_id": cid, "lines": lines}
    if coupon == "auto":
        coupon = rng.choice([None] * 6 + ["SPRING10", "WELCOME5", "VIP33", "SPRNG10"])
    if coupon:
        body["coupon"] = coupon
    headers = {"Idempotency-Key": key} if key else None
    r = send("POST", "/orders", body, headers=headers, client=client)
    record(r)
    return body, r


def mobile_session():
    now = clock.now_ms()
    cid, tier = pick_customer(now)
    if rng.random() < 0.55:
        r = send("GET", "/orders?customer_id=%d&limit=5" % cid, client="mobile")
        for _ in range(rng.choice([0, 1, 1, 2])):
            cur = r.json().get("next_cursor") if r.status == 200 else None
            if not cur:
                break
            clock.advance(rng.randrange(1200, 6000))
            r = send("GET", "/orders?customer_id=%d&limit=5&cursor=%s" % (cid, cur), client="mobile")
    if rng.random() < 0.5:
        clock.advance(rng.randrange(3000, 30000))
        key = "m-%s" % "".join(rng.choice("0123456789abcdef") for _ in range(12))
        body, r = place_order("mobile", (cid, tier), key=key)
        if r.status == 201 and rng.random() < 0.16:
            clock.advance(3000 + rng.randrange(0, 400))
            send("POST", "/orders", body, headers={"Idempotency-Key": key}, client="mobile")
        elif rng.random() < 0.05:
            bad = dict(body)
            bad["lines"] = [{"product_id": body["lines"][0]["product_id"], "qty": 0}]
            send("POST", "/orders", bad, client="mobile")


def b2b_session():
    now = clock.now_ms()
    cid = customers[2 + 3 * rng.randrange(0, 7)][0]          # standard-tier wholesale accounts
    if rng.random() < 0.5:
        follow("/orders?customer_id=%d&limit=10" % cid, "b2b", 3)
    send("GET", "/products?tag=wholesale&limit=50", client="b2b")
    clock.advance(rng.randrange(2000, 9000))
    wh = live(lambda p: "wholesale" in p["tags"])
    lines = [{"product_id": p, "qty": rng.randrange(1, 9)} for p in rng.sample(wh, rng.randrange(1, 4))]
    place_order("b2b", (cid, "standard"), lines=lines,
                coupon=rng.choice([None, None, "B2B15"]),
                key="b2b-%d" % rng.randrange(10**9, 10**10))


def follow(target, client, pages):
    r = send("GET", target, client=client)
    for _ in range(pages):
        cur = r.json().get("next_cursor") if r.status == 200 else None
        if not cur:
            return
        clock.advance(rng.randrange(700, 5000))
        r = send("GET", target + "&cursor=" + cur, client=client)


def admin_edit():
    if rng.random() < 0.5:
        follow("/orders?status=placed&limit=25", "admin", rng.randrange(1, 5))
        clock.advance(rng.randrange(5000, 60000))
    pid = rng.choice(live())
    if rng.random() < 0.6:
        old = float(products[pid]["price"])
        new = "%.2f" % max(1.0, old * rng.choice([0.9, 0.95, 1.05, 1.1, 1.2]))
        r = send("PATCH", "/products/%d" % pid, {"price": new}, client="admin")
        if r.status == 200:
            products[pid]["price"] = new
    else:
        st = rng.randrange(50, 600)
        r = send("PATCH", "/products/%d" % pid, {"stock": st}, client="admin")
        if r.status == 200:
            products[pid]["stock"] = st


def dashboard():
    send("GET", "/reports/summary", client="dashboard")
    clock.advance(rng.randrange(200, 900))
    send("GET", "/reports/daily?date=" + day_of(clock.now_ms()), client="dashboard")


def crawler_burst():
    for i in range(78):
        pid = rng.choice(live()) if rng.random() < 0.9 else rng.randrange(900, 990)
        send("GET", "/products/%d" % pid, client="storefront")
        clock.advance(rng.randrange(20, 120))


def buggy_client():
    send("GET", "/orders?limit=10&cursor=undefined", client="mobile")


def discontinue():
    """Storefront browses, admin discontinues a product that is in a cart,
    the shopper checks out a few seconds later, retries after a while."""
    cust = pick_customer(clock.now_ms())
    send("GET", "/products?limit=40")
    clock.advance(rng.randrange(3000, 12000))
    victim = rng.choice(retail())
    other = rng.choice([p for p in retail() if p != victim])
    lines = [{"product_id": other, "qty": 1}, {"product_id": victim, "qty": rng.randrange(1, 3)}]
    send("DELETE", "/products/%d" % victim, client="admin")
    deleted.add(victim)
    clock.advance(rng.randrange(4000, 21000))
    place_order("storefront", cust, lines=lines, coupon=None)
    clock.advance(rng.randrange(35000, 90000))
    place_order("storefront", cust, lines=lines, coupon=None)


def schedule():
    t = T0 + 60000
    while t < T_END:
        at(t, storefront_session)
        t += int(rng.expovariate(1 / 105000.0)) + 500
    t = T0 + 90000
    while t < T_END:
        at(t, mobile_session)
        t += int(rng.expovariate(1 / 150000.0)) + 500
    t = T0 + 300000
    while t < T_END:
        at(t, b2b_session)
        t += int(rng.expovariate(1 / 660000.0)) + 60000
    t = T0 + 600000
    while t < T_END:
        at(t, admin_edit)
        t += int(rng.expovariate(1 / 900000.0)) + 60000
    t = T0 + 120000
    while t < T_END:
        at(t, dashboard)
        t += 600000 + rng.randrange(-40000, 40000)
    for frac in (0.18, 0.52, 0.81):
        at(T0 + int(frac * (T_END - T0)), discontinue)
    for frac in (0.33, 0.71):
        at(T0 + int(frac * (T_END - T0)), crawler_burst)
    for frac in (0.44, 0.9):
        at(T0 + int(frac * (T_END - T0)), buggy_client)


seed_data()
schedule()
while events:
    t, _s, fn, args = heapq.heappop(events)
    if t > clock.now_ms():
        clock.set(t)
    fn(*args)

for w in workers.values():
    w.inbox.put(None)

# ------------------------------------------------------------ rewrite -----
stdlib = sysconfig.get_paths()["stdlib"]
text = "\n".join(sink) + "\n"
text = text.replace(SRV + os.sep, "/srv/shopd/")
text = text.replace(stdlib + os.sep, "/usr/lib/python3.12/")
assert WORK not in text, "temp path leaked into the log"
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w") as f:
    f.write(text)
shutil.rmtree(WORK)

# summary for the author
st = re.findall(r"http\.access method=(\w+) target=(\S+) status=(\d+)", text)
print("lines", text.count("\n"), "requests", len(st), "bytes", len(text))
c500 = re.findall(r"http\.unhandled exc=(\w+) method=(\w+) path=(\S+)", text)
from collections import Counter  # noqa: E402
print("500s", len(c500), Counter(c500).most_common())
print("status", Counter(s for _m, _t, s in st).most_common())
