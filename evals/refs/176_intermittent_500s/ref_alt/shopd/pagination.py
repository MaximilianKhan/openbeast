"""Keyset pagination.

A listing is an ordered sequence of rows, each with a sort key
``(created_ms, id)``. A page holds at most ``limit`` rows; the cursor for
the next page encodes the sort key of the last row returned, so pages stay
stable while rows are inserted or deleted elsewhere in the listing.

Cursors are compact: the two integers packed big-endian and base64'd.
"""
from __future__ import annotations

import base64
import re
import struct

from .errors import BadRequest

_PACK = struct.Struct(">qI")


def encode_cursor(sort_key: tuple[int, int]) -> str:
    raw = _PACK.pack(int(sort_key[0]), int(sort_key[1]))
    # Lower-case hex: nothing in it needs escaping in a query string.
    return raw.hex()


def decode_cursor(token: str) -> tuple[int, int]:
    if not re.fullmatch(r"[0-9a-f]{24}", token or ""):
        raise BadRequest("malformed cursor", code="invalid_cursor")
    return _PACK.unpack(bytes.fromhex(token))


def parse_limit(query: dict, default: int, maximum: int) -> int:
    text = query.get("limit")
    if text is None or text == "":
        return default
    if not text.isdigit():
        raise BadRequest("limit must be a positive integer", code="invalid_limit")
    n = int(text)
    if n < 1 or n > maximum:
        raise BadRequest("limit must be between 1 and %d" % maximum, code="invalid_limit")
    return n


def paginate(rows: list[dict], limit: int, cursor: str | None, newest_first: bool):
    """Return ``(page_rows, next_cursor_or_None)``.

    ``rows`` may be in any order; they are sorted here by
    ``(created_ms, id)`` (descending when ``newest_first``).
    """
    keyed = sorted(rows, key=lambda r: (r["created_ms"], r["id"]), reverse=newest_first)
    if cursor:
        after = decode_cursor(cursor)
        if newest_first:
            keyed = [r for r in keyed if (r["created_ms"], r["id"]) < after]
        else:
            keyed = [r for r in keyed if (r["created_ms"], r["id"]) > after]
    page = keyed[:limit]
    more = len(keyed) > limit
    nxt = encode_cursor((page[-1]["created_ms"], page[-1]["id"])) if (page and more) else None
    return page, nxt
