# shopd API contract

This document is the contract. Where code and this document disagree, the
code is wrong.

## 0. General

* Bodies are JSON (UTF-8). Responses are canonical JSON (sorted keys, no
  whitespace) with `Content-Type: application/json`, except 204 responses
  which have an empty body.
* Every response carries `X-Request-Id`: the request's `X-Request-Id` header
  when it matches `[A-Za-z0-9._-]{1,64}`, otherwise a generated id.
* Errors are `{"error": <code>, "message": <text>, ...}` with the status
  listed for each code below. The service **never** answers 500: every
  request either succeeds or fails with one of the documented 4xx codes.
* Money crosses the wire as a string with exactly two decimals (`"12.30"`).
  Money in request bodies is a string of the form `D` / `D.D` / `D.DD`
  (no sign, no exponent, at most 1000000.00).
* Ids are positive integers. A path id that is not a positive decimal
  integer gets the same 404 as an unknown id; `/products/007` is product 7.
* Timestamps are `YYYY-MM-DDTHH:MM:SS.mmmZ` (UTC).
* Reads reflect every write that completed before them: after a 2xx write
  response, no later request observes the state from before that write.
* Rate limiting: each client has a token bucket (capacity `rate_capacity`,
  refilled at `rate_per_min` per minute). An empty bucket gets 429
  `rate_limited` with `Retry-After` in whole seconds. `/health` is exempt.
* Unknown routes: 404 `route_not_found`. Known path, wrong method: 405
  `method_not_allowed` with `Allow`.
* A body that is not valid JSON: 400 `invalid_json`. A body that fails the
  schema: 400 `validation_failed` with `problems: {field: reason}`; unknown
  fields are problems too.

## 1. Programmatic interface

```python
from shopd.app import create_app
from shopd.clock import FakeClock
from shopd.config import Config

app = create_app(config=Config(), clock=FakeClock(start_ms), log_sink=[])
resp = app.call("POST", "/orders?x=1", json_body={...}, headers={...}, client="c1")
# or: app.handle(shopd.http.Request.build(method, target, headers=..., json_body=..., client=...))
resp.status; resp.headers.get("Location"); resp.body (bytes); resp.json()
```

`target` is a raw request target (path plus optional query string), decoded
exactly as a WSGI server decodes it. `create_app` returns a fresh, empty
service. One `App` may be driven from many threads.

## 2. Products

| Request | Success | Errors |
|---|---|---|
| `POST /products` `{sku, name, price, stock, tags?}` | 201 product, `Location: /products/{id}` | 400, 409 `duplicate_sku` |
| `GET /products/{id}` | 200 product, `Location` | 404 `product_not_found` |
| `PATCH /products/{id}` any of `{name, price, stock, tags}` | 200 product, `Location` | 400, 404 |
| `DELETE /products/{id}` | 204 | 404 |
| `GET /products?q=&tag=&limit=&cursor=` | 200 page, oldest first | 400 |

`sku` matches `^[A-Z0-9][A-Z0-9-]{1,31}$`; `name` 1-120 chars; `stock`
0..1000000; `tags` a list of strings (stored lower-cased, de-duplicated,
sorted). A product is
`{id, sku, name, slug, price, stock, tags, created_at, updated_at}`.
`slug` is the ASCII tokens of the name joined by `-` (see
`services/search.py`), or the lower-cased sku when there are none.

Search (`q`): every query token must be a prefix of some token of the
product's name, sku or tags, after Unicode folding (NFKD, combining marks
removed, casefold). `tag` filters on an exact (lower-cased) tag.

## 3. Customers

`POST /customers` `{email, name, tier?}` -> 201 `{id, email, name, tier,
created_at}` (email stored lower-cased; tier `standard` (default) or
`gold`); 409 `duplicate_email`. `GET /customers/{id}` -> 200 / 404
`customer_not_found`.

## 4. Coupons

`POST /coupons` `{code, percent_off? , amount_off?, min_subtotal?, max_uses?}`
-> 201 `{code, percent_off, amount_off, min_subtotal, max_uses, uses}`.
Exactly one of `percent_off` (0 < p <= 100, a money-format string) and
`amount_off` (> 0). `code` is upper-cased and must then match
`^[A-Z0-9]{3,20}$`. 409 `duplicate_coupon`. `GET /coupons/{code}` -> 200 /
404 `coupon_not_found`.

## 5. Orders

### 5.1 Placing an order

`POST /orders` `{customer_id, lines: [{product_id, qty}, ...], coupon?}`
(1-50 lines, qty 1-1000; `coupon` may be null). Lines for the same product
are merged (quantities added, first position kept). Checks, in this order:

1. unknown customer: 422 `unknown_customer` (`customer_id`);
2. for each merged line in order: a product that does not exist (never
   existed, or was deleted) -> 422 `unknown_product` (`product_id`); a merged
   quantity above 1000 -> 422 `qty_limit`;
3. coupon: unknown -> 422 `unknown_coupon`; `uses >= max_uses` -> 422
   `coupon_exhausted`; subtotal below `min_subtotal` -> 422
   `coupon_min_subtotal`;
4. stock: any merged line with `qty > stock` -> 409 `insufficient_stock`
   (`product_id`, `available`, `requested`), nothing reserved.

On success the stock of every product is reduced by its quantity, the
coupon's `uses` goes up by one, and the response is 201 with the order and
`Location: /orders/{id}`.

An order is `{id, customer_id, status, coupon, lines, subtotal, discount,
tier_discount, tax, total, created_at, cancelled_at}` where each line is
`{product_id, sku, name, qty, unit_price, line_total}`. `unit_price`, `sku`
and `name` are the product's values at the moment the order is placed.

### 5.2 Pricing

All arithmetic is exact decimal arithmetic. `round2(x)` rounds to two
decimals, half up. With the configured `tax_rate` (default `0.0825`) and
`gold_discount_pct` (default `5`):

```
line_total    = unit_price * qty
subtotal      = sum(line_total)
discount      = round2(subtotal * percent_off / 100)        (percent coupon)
              = min(amount_off, subtotal)                    (amount coupon)
              = 0                                            (no coupon)
after_coupon  = subtotal - discount
tier_discount = round2(after_coupon * gold_discount_pct / 100) if tier == gold else 0
taxable       = after_coupon - tier_discount
tax           = round2(taxable * tax_rate)
total         = taxable + tax
```

These rules hold for every order the API accepts, however large.

### 5.3 Idempotent retries

`POST /orders` may carry `Idempotency-Key` (8-64 chars of `[A-Za-z0-9_-]`,
else 400 `invalid_idempotency_key`). The first request with a key is
processed normally. If it succeeds (201), then for the next 24 hours every
later request with the same key and an identical JSON body (same values,
key order irrelevant):

* is not processed again (no new order, no stock or coupon change), and
* gets exactly the original response: status 201, byte-identical body,
  the same `Location`, plus the header `Idempotent-Replay: true` (which the
  original response does not carry).

The same key with a different body: 422 `idempotency_conflict`. A first
attempt that failed is not remembered; a retry is processed afresh.

### 5.4 Reading and cancelling

* `GET /orders/{id}` -> 200 order / 404 `order_not_found`.
* `POST /orders/{id}/cancel` -> 200 order with `status: "cancelled"` and
  `cancelled_at`; stock of products that still exist is restored and the
  coupon use is given back. Cancelling twice: 409 `already_cancelled`.
* `GET /orders?customer_id=&status=&limit=&cursor=` -> 200 page, newest
  first (by creation time, then id). `status` is `placed` or `cancelled`
  (else 400 `invalid_query`).

## 6. Pagination

Listings return `{"items": [...], "next_cursor": <string or null>}`.
`limit` is 1..`page_max` (default `page_default`; else 400
`invalid_limit`). `next_cursor` is null on the last page. To fetch the next
page a client repeats the same request with `cursor=<next_cursor>`
appended **verbatim**: a cursor is an opaque string made only of
characters that need no escaping in a query string. Following cursors
visits every matching row exactly once. A `cursor` value the service did
not issue: 400 `invalid_cursor`.

## 7. Reports

`GET /reports/summary` and `GET /reports/daily?date=YYYY-MM-DD` (a UTC day;
anything else 400 `invalid_date`) count orders with status `placed` (daily:
created that day):

```
{"orders": n, "revenue": sum(total), "average_order": round2(revenue / n) or "0.00",
 "by_tier": {"gold": {"orders", "revenue", "share"}, "standard": {...}},
 "top_products": [{"product_id", "sku", "units", "revenue"}, ...],   # up to 5
 "date": ...}                                                        # daily only
```

`share` is `round2(100 * tier revenue / revenue)` as a string, `"0.00"` when
either is zero. `top_products` is ordered by units desc, then product_id
asc; `revenue` there is the sum of that product's line totals.

## 8. Health

`GET /health` -> 200 `{"status": "ok", "version": ...}`.
