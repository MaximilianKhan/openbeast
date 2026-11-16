"""Minimal WSGI adapter around the pipeline."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Iterable
from urllib.parse import parse_qsl

from ferrule.api.middleware import Authenticate, ErrorMapper, Pipeline, Request, Throttle
from ferrule.api.routing import RouteTable, View
from ferrule.errors import NotFound

_REASONS = {200: "OK", 201: "Created", 401: "Unauthorized", 403: "Forbidden",
            404: "Not Found", 409: "Conflict", 422: "Unprocessable Entity",
            429: "Too Many Requests", 500: "Internal Server Error", 503: "Service Unavailable"}


class ApiServer:
    def __init__(self, app, table: RouteTable) -> None:
        self.app = app
        self.table = table
        self._views: Dict[type, View] = {}
        self.pipeline = Pipeline(
            [ErrorMapper(),
             Authenticate(app.tokens, app.settings["auth"]["anonymous_role"]),
             Throttle(app.limiter, table)],
            self._dispatch,
        )

    def _view(self, cls: type) -> View:
        if cls not in self._views:
            self._views[cls] = cls(self.app)
        return self._views[cls]

    def _dispatch(self, request: Request):
        route, params = self.table.match(request.method, request.path)
        if route is None:
            raise NotFound(f"{request.method} {request.path}")
        handler = route.handler
        owner = getattr(handler, "__qualname__", "").split(".")[0]
        view_cls = getattr(__import__(handler.__module__, fromlist=[owner]), owner, None)
        if isinstance(view_cls, type) and issubclass(view_cls, View):
            return handler(self._view(view_cls), request, **params)
        return handler(self.app, request, **params)

    def handle(self, request: Request):
        return self.pipeline(request)

    def __call__(self, environ: Dict[str, Any], start_response: Callable) -> Iterable[bytes]:
        length = int(environ.get("CONTENT_LENGTH") or 0)
        headers = {k[5:].replace("_", "-").lower(): v for k, v in environ.items() if k.startswith("HTTP_")}
        request = Request(
            method=environ["REQUEST_METHOD"],
            path=environ.get("PATH_INFO", "/"),
            headers=headers,
            query=dict(parse_qsl(environ.get("QUERY_STRING", ""))),
            body=environ["wsgi.input"].read(length) if length else b"",
        )
        status, body = self.handle(request)
        data = json.dumps(body).encode()
        start_response(f"{status} {_REASONS.get(status, '')}",
                       [("content-type", "application/json"), ("content-length", str(len(data)))])
        return [data]
