# Running shopd

## Process model

One process, `python -m shopd.server`, behind the load balancer. Inside it
a fixed pool of worker threads (`SHOPD_WORKERS`, 4 in production) named
`w1`..`w4` takes requests off one queue, so consecutive requests from one
client usually land on different workers. All workers share one `App`:
one database handle, one product cache, one idempotency store, one rate
limiter. A worker retires after `SHOPD_MAX_REQUESTS` (150) requests and a
fresh thread with the same name replaces it (`server.recycle` followed by
`server.worker_start` in the log). Deploys restart the process.

## Configuration (production values)

| Variable | Value |
|---|---|
| `SHOPD_TAX_RATE` | 0.0825 |
| `SHOPD_GOLD_PCT` | 5 |
| `SHOPD_CACHE_TTL_MS` | 30000 |
| `SHOPD_RATE_CAPACITY` / `SHOPD_RATE_PER_MIN` | 60 / 120 |
| `SHOPD_LOG_BODIES` | 1 (temporarily, while the 500s are investigated) |

## Log format

`logs/shopd.log` is the process log, one event per line:

```
<timestamp> <LEVEL> <worker> <request-id> <event> key=value ...
```

* `http.access`: one per request, after it is served: `method`, `target`
  (path and raw query string as received), `status`, `client`, `bytes`
  (response size), `idem` (the Idempotency-Key, when sent) and, with
  `SHOPD_LOG_BODIES=1`, `body` (truncated to 400 characters). Logged at
  WARN for 5xx.
* `http.unhandled`: an exception escaped a handler; the traceback follows
  as indented continuation lines and the client got a 500.
* `catalog.*`, `order.*`, `idempotency.replay`, `inventory.low`: business
  events, logged with the request id of the request that caused them.
* `server.*`: worker lifecycle. The excerpt starts at a deploy: the process
  came up empty and the `seed` client loaded the catalog, customers and
  coupons before traffic was let in.

Client names in the log: `storefront` (web shop), `mobile` (app, retries a
POST once with the same Idempotency-Key when it gets no response within 3 s),
`b2b` (wholesale API partner, large orders), `admin` (back office: catalog
edits, coupons), `dashboard` (polls the reports), `seed` (fixture loader).

## Known noise

* 429s from `storefront` bursts are expected (rate limiter working).
* 409 `insufficient_stock` and 422 coupon errors are customer errors.
* `inventory.low` warnings are for the purchasing team.
