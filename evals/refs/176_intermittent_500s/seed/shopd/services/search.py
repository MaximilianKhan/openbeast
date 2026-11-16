"""Product search.

``q`` matches a product when every query token is a prefix of some token of
the product's name, sku or tags. Tokens are compared after folding: Unicode
NFKD, combining marks dropped, casefolded; a token is a maximal run of
letters and digits.
"""
from __future__ import annotations

import unicodedata


def fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.casefold()


def tokens(text: str) -> list[str]:
    out, cur = [], []
    for c in fold(text):
        if c.isalnum():
            cur.append(c)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def slugify(name: str) -> str:
    """URL slug: folded tokens joined by '-', ASCII only; '' if none."""
    toks = [t for t in tokens(name) if t.isascii()]
    return "-".join(toks)[:80].strip("-")


class Matcher:
    def __init__(self, query: str):
        self.query_tokens = tokens(query)

    def __bool__(self) -> bool:
        return bool(self.query_tokens)

    def matches(self, row: dict) -> bool:
        hay = tokens(row["name"]) + tokens(row["sku"])
        for t in row.get("tags", ()):
            hay.extend(tokens(t))
        return all(any(h.startswith(q) for h in hay) for q in self.query_tokens)
