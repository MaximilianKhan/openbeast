"""The request pipeline around the router.

Order, outermost first::

    request_id -> access_log -> errors -> rate_limit -> dispatch

Each middleware is a function ``(request, next) -> Response``.
"""
from __future__ import annotations

import itertools
import re
import threading
import traceback

from . import log as logmod
from .errors import ApiError, TooManyRequests
from .http import Response, json_response

_RID_OK = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestIds:
    """Assigns ``request.request_id``: the caller's X-Request-Id when it is
    well formed, otherwise ``r`` + a zero-padded per-app counter."""

    def __init__(self):
        self._counter = itertools.count(1)
        self._lock = threading.Lock()

    def __call__(self, request, nxt):
        given = request.headers.get("X-Request-Id")
        if given and _RID_OK.match(given):
            rid = given
        else:
            with self._lock:
                rid = "r%06d" % next(self._counter)
        request.request_id = rid
        logmod.set_request_id(rid)
        try:
            resp = nxt(request)
        finally:
            logmod.set_request_id(None)
        resp.headers["X-Request-Id"] = rid
        return resp


class AccessLog:
    def __init__(self, logger, config):
        self.logger = logger
        self.config = config

    def __call__(self, request, nxt):
        # Bind the request id again: an inner middleware may have cleared it.
        logmod.set_request_id(request.request_id)
        resp = nxt(request)
        logmod.set_request_id(request.request_id)
        fields = {
            "method": request.method,
            "target": request.raw_target,
            "status": resp.status,
            "client": request.client,
            "bytes": len(resp.body),
        }
        idem = request.headers.get("Idempotency-Key")
        if idem:
            fields["idem"] = idem
        if self.config.log_bodies and request.body and request.method in ("POST", "PATCH", "PUT"):
            text = request.body.decode("utf-8", "replace")
            if len(text) > self.config.log_body_max:
                text = text[: self.config.log_body_max] + "..."
            fields["body"] = text
        level = "WARN" if resp.status >= 500 else "INFO"
        self.logger.log(level, "http.access", **fields)
        return resp


class Errors:
    """Turns ApiError into its JSON body; anything else into a logged 500."""

    def __init__(self, logger):
        self.logger = logger

    def __call__(self, request, nxt):
        try:
            return nxt(request)
        except ApiError as e:
            resp = json_response(e.status, e.body())
            for k, v in e.headers.items():
                resp.headers[k] = v
            return resp
        except Exception as e:  # noqa: BLE001 - this IS the last line of defence
            self.logger.error("http.unhandled", traceback.format_exc(),
                              exc=type(e).__name__, method=request.method, path=request.path)
            return json_response(500, {"error": "internal", "message": "internal server error",
                                       "request_id": request.request_id})


class TokenBucket:
    def __init__(self, capacity: int, per_min: int, now_ms: int):
        self.capacity = capacity
        self.rate_per_ms = per_min / 60000.0
        self.tokens = float(capacity)
        self.stamp = now_ms

    def take(self, now_ms: int) -> float:
        """Take one token. Returns 0 on success, else ms until one is free."""
        elapsed = max(0, now_ms - self.stamp)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate_per_ms)
        self.stamp = now_ms
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return 0.0
        return (1.0 - self.tokens) / self.rate_per_ms


class RateLimit:
    EXEMPT = ("/health",)

    def __init__(self, clock, config):
        self.clock = clock
        self.config = config
        self.buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def __call__(self, request, nxt):
        if request.path in self.EXEMPT or self.config.rate_capacity <= 0:
            return nxt(request)
        now = self.clock.now_ms()
        with self._lock:
            b = self.buckets.get(request.client)
            if b is None:
                b = self.buckets[request.client] = TokenBucket(
                    self.config.rate_capacity, self.config.rate_per_min, now)
            wait_ms = b.take(now)
        if wait_ms:
            err = TooManyRequests("slow down")
            err.headers["Retry-After"] = str(max(1, int(-(-wait_ms // 1000))))
            raise err
        return nxt(request)


def chain(middlewares, endpoint):
    """Compose ``[m1, m2, ...]`` around ``endpoint`` so m1 runs first."""
    handler = endpoint
    for mw in reversed(middlewares):
        handler = (lambda m, n: (lambda req: m(req, n)))(mw, handler)
    return handler


def ensure_response(obj) -> Response:
    if not isinstance(obj, Response):
        raise TypeError("handler returned %r, not a Response" % type(obj).__name__)
    return obj
