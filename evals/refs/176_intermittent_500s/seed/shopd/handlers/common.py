"""Helpers shared by the HTTP handlers."""
from __future__ import annotations

from ..errors import BadRequest, NotFound
from ..http import json_response

MAX_ID = 2 ** 31 - 1


def path_id(params: dict, name: str = "id", what: str = "resource") -> str:
    """Validate a numeric path segment.

    Returns the canonical decimal form of the id (no sign, no leading
    zeros). Raises NotFound for anything that cannot be an id, so that
    ``/orders/abc`` and ``/orders/0`` both 404 like any unknown id.
    """
    raw = params.get(name, "")
    if not raw.isascii() or not raw.isdigit():
        raise NotFound("no %s %s" % (what, raw), code="%s_not_found" % what)
    canonical = raw.lstrip("0")
    if not canonical or int(canonical) > MAX_ID:
        raise NotFound("no %s %s" % (what, raw), code="%s_not_found" % what)
    return canonical


def body_of(request) -> dict:
    body = request.json()
    if body is None:
        raise BadRequest("a JSON body is required", code="missing_body")
    return body


def query_int(query: dict, name: str) -> int | None:
    text = query.get(name)
    if text is None or text == "":
        return None
    if not text.isascii() or not text.isdigit():
        raise BadRequest("%s must be a non-negative integer" % name, code="invalid_query")
    return int(text)


def list_response(items: list, next_cursor: str | None, **extra):
    body = {"items": items, "next_cursor": next_cursor}
    body.update(extra)
    return json_response(200, body)
