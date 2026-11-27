"""Request / response value types and JSON helpers.

The service never touches sockets itself: the server adapter (and the tests)
build a ``Request`` and get a ``Response`` back from ``App.handle``.
"""
from __future__ import annotations

import json
import urllib.parse
from decimal import Decimal

STATUS_TEXT = {
    200: "OK", 201: "Created", 204: "No Content", 400: "Bad Request",
    404: "Not Found", 405: "Method Not Allowed", 409: "Conflict",
    422: "Unprocessable Entity", 429: "Too Many Requests",
    500: "Internal Server Error", 503: "Service Unavailable",
}


class Headers:
    """A small case-insensitive header map that remembers original casing."""

    def __init__(self, items=None):
        self._d: dict[str, tuple[str, str]] = {}
        if items:
            pairs = items.items() if hasattr(items, "items") else items
            for k, v in pairs:
                self[k] = v

    def __setitem__(self, key: str, value) -> None:
        self._d[key.lower()] = (key, str(value))

    def __getitem__(self, key: str) -> str:
        return self._d[key.lower()][1]

    def __contains__(self, key: str) -> bool:
        return key.lower() in self._d

    def __delitem__(self, key: str) -> None:
        del self._d[key.lower()]

    def get(self, key: str, default=None):
        hit = self._d.get(key.lower())
        return default if hit is None else hit[1]

    def items(self):
        return [pair for pair in self._d.values()]

    def copy(self) -> "Headers":
        return Headers(self.items())

    def __eq__(self, other) -> bool:
        if not isinstance(other, Headers):
            return NotImplemented
        return {k: v[1] for k, v in self._d.items()} == {k: v[1] for k, v in other._d.items()}

    def __repr__(self) -> str:
        return "Headers(%r)" % dict(self.items())


class Request:
    """An inbound request.

    ``path`` never contains the query string; ``query`` maps each parameter
    name to its (last) decoded value.
    """

    def __init__(self, method: str, path: str, query: dict | None = None,
                 headers=None, body: bytes = b"", client: str = "-"):
        self.method = method.upper()
        self.path = path
        self.query = dict(query or {})
        self.headers = headers if isinstance(headers, Headers) else Headers(headers)
        self.body = body or b""
        self.client = client
        self.request_id = None  # assigned by the request-id middleware
        self.raw_target = path if not self.query else path + "?" + urllib.parse.urlencode(self.query)

    @classmethod
    def build(cls, method: str, target: str, headers=None, json_body=None,
              body: bytes | None = None, client: str = "test") -> "Request":
        """Build a request from a raw target such as ``/orders?limit=5``.

        The query string is decoded the way a WSGI server decodes it.
        """
        path, _, qs = target.partition("?")
        query = {}
        if qs:
            for k, vs in urllib.parse.parse_qs(qs, keep_blank_values=True).items():
                query[k] = vs[-1]
        hdrs = Headers(headers)
        if json_body is not None:
            body = dumps(json_body).encode()
            if "content-type" not in hdrs:
                hdrs["Content-Type"] = "application/json"
        req = cls(method, path, query, hdrs, body or b"", client)
        req.raw_target = target
        return req

    def json(self):
        """Decode the body as a JSON object; None for an empty body."""
        if not self.body:
            return None
        try:
            return json.loads(self.body.decode("utf-8"), parse_float=Decimal)
        except (UnicodeDecodeError, ValueError):
            from .errors import BadRequest
            raise BadRequest("body is not valid JSON", code="invalid_json")


class Response:
    def __init__(self, status: int, headers=None, body: bytes = b""):
        self.status = status
        self.headers = headers if isinstance(headers, Headers) else Headers(headers)
        self.body = body

    def json(self):
        return json.loads(self.body.decode("utf-8")) if self.body else None

    def __repr__(self) -> str:
        return "<Response %d %s %d bytes>" % (self.status, STATUS_TEXT.get(self.status, ""), len(self.body))


def _default(o):
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    raise TypeError("not JSON serializable: %r" % type(o).__name__)


def dumps(obj) -> str:
    """Canonical JSON: sorted keys, no insignificant whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=_default, ensure_ascii=False)


def json_response(status: int, obj, headers=None) -> Response:
    hdrs = Headers(headers)
    hdrs["Content-Type"] = "application/json"
    return Response(status, hdrs, dumps(obj).encode("utf-8"))


def empty_response(status: int = 204, headers=None) -> Response:
    return Response(status, Headers(headers), b"")
