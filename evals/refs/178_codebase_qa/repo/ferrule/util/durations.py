"""Human durations: ``"90s"``, ``"1m30s"``, ``"250ms"``, ``"2h"``, ``"1d"``.

A bare number is seconds. Units may be combined largest first.
"""

from __future__ import annotations

import re
from typing import Union

_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)")


def parse_duration(value: Union[str, int, float]) -> float:
    """Return seconds as a float."""
    if isinstance(value, (int, float)):
        return float(value)
    text = value.strip().lower().replace(" ", "")
    if not text:
        raise ValueError("empty duration")
    try:
        return float(text)
    except ValueError:
        pass
    pos = 0
    total = 0.0
    for m in _PART.finditer(text):
        if m.start() != pos:
            raise ValueError(f"bad duration {value!r}")
        total += float(m.group(1)) * _UNITS[m.group(2)]
        pos = m.end()
    if pos != len(text):
        raise ValueError(f"bad duration {value!r}")
    return total


def format_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{int(round(seconds * 1000))}ms"
    seconds = int(round(seconds))
    out = []
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= size:
            n, seconds = divmod(seconds, size)
            out.append(f"{n}{unit}")
    return "".join(out) or "0s"
