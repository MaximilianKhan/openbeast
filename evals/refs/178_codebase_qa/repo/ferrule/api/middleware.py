"""Request pipeline: auth -> rate limit -> dispatch, plus error mapping."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from ferrule.errors import FerruleError, RateLimited
from ferrule.jobs.service import Context

log = logging.getLogger(__name__)

PUBLIC_PREFIXES = ("/healthz", "/readyz")


@dataclass
class Request:
    method: str
    path: str
    headers: Dict[str, str] = field(default_factory=dict)
    query: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    ctx: Context = field(default_factory=Context)
    token: Any = None
    route: Any = None

    @property
    def json(self) -> Optional[Dict[str, Any]]:
        return json.loads(self.body) if self.body else None


Response = Tuple[int, Dict[str, Any]]
Next = Callable[[Request], Response]


class Middleware:
    def __call__(self, request: Request, nxt: Next) -> Response:
        return nxt(request)


class ErrorMapper(Middleware):
    def __call__(self, request, nxt):
        try:
            return nxt(request)
        except RateLimited as exc:
            return exc.status, {"error": exc.code, "retry_after_s": round(exc.retry_after_s, 3)}
        except FerruleError as exc:
            return exc.status, {"error": exc.code, "message": str(exc)}


class Authenticate(Middleware):
    def __init__(self, tokens, anonymous_role: str) -> None:
        self.tokens = tokens
        self.anonymous_role = anonymous_role

    def __call__(self, request, nxt):
        if request.path.startswith(PUBLIC_PREFIXES):
            request.ctx = Context(role=self.anonymous_role)
            return nxt(request)
        auth = request.headers.get("authorization", "")
        raw = auth[7:] if auth.lower().startswith("bearer ") else None
        tok = self.tokens.verify(raw)
        request.token = tok
        request.ctx = Context(actor=tok.key_id, role=tok.role, tenant=tok.tenant)
        return nxt(request)


class Throttle(Middleware):
    def __init__(self, limiter, table) -> None:
        self.limiter = limiter
        self.table = table

    def __call__(self, request, nxt):
        route, _ = self.table.match(request.method, request.path)
        request.route = route
        if request.token is not None and route is not None:
            self.limiter.check(request.token.key_id, request.token.tier, route.name)
        return nxt(request)


class Pipeline:
    def __init__(self, middlewares: List[Middleware], endpoint: Next) -> None:
        self.middlewares = middlewares
        self.endpoint = endpoint

    def __call__(self, request: Request) -> Response:
        def call(i: int, req: Request) -> Response:
            if i == len(self.middlewares):
                return self.endpoint(req)
            return self.middlewares[i](req, lambda r: call(i + 1, r))
        return call(0, request)
