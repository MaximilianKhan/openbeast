"""Formatting helpers for the CLI."""

from __future__ import annotations

from typing import Iterable, List, Sequence


def table(rows: Iterable[Sequence[object]], headers: Sequence[str]) -> str:
    data: List[List[str]] = [[str(c) for c in headers]] + [[str(c) for c in r] for r in rows]
    widths = [max(len(row[i]) for row in data) for i in range(len(headers))]
    lines = []
    for n, row in enumerate(data):
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
        if n == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(width - 1, 0)] + "…"
