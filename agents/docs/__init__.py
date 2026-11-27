"""beast-docs — current platform developer documentation, mirrored offline.

docs/BEAST_DOCS.md is the design. A local model has a training cutoff;
SwiftUI, Compose and AGP move every few months. beast-docs keeps the
publisher's own pages (Apple DocC JSON, Android's markdown, any static HTML
site) in a local mirror with provenance on every page, searchable with no
network, and fetchable live when the network is there.

THE FACADE. `safe_search`, `safe_read` and `safe_live` are what a serving
path calls (the `platform_docs` MCP tool). Like beast-lang's facade they DO
NOT RAISE — a documentation lookup is an optional extra on somebody's turn
and never worth that turn — and importing this package is free: no I/O, no
submodule import until first use.
"""
from __future__ import annotations

__all__ = ["safe_live", "safe_read", "safe_search", "safe_status"]


def safe_search(query: str, source: str = "", limit: int = 8) -> str:
    try:
        from . import api  # noqa: PLC0415
        return api.search_text(query, source, limit)
    except Exception as e:                          # noqa: BLE001
        return f"Error: documentation search failed: {e}\n"


def safe_read(ref: str, max_chars: int = 12000) -> str:
    try:
        from . import api  # noqa: PLC0415
        out = api.read_text(ref, max_chars)
        return out if out is not None else (
            f"No mirrored page {ref!r}. Search first (it returns doc ids), "
            f"or use live mode when online.\n")
    except Exception as e:                          # noqa: BLE001
        return f"Error: documentation read failed: {e}\n"


def safe_live(ref: str, source: str = "", max_chars: int = 12000) -> str:
    try:
        from . import api  # noqa: PLC0415
        return api.live_text(ref, source, save=False, max_chars=max_chars)
    except Exception as e:                          # noqa: BLE001
        return f"Error: live documentation fetch failed: {e}\n"


def safe_status() -> str:
    try:
        from . import api  # noqa: PLC0415
        return api.status_text()
    except Exception as e:                          # noqa: BLE001
        return f"Error: documentation status failed: {e}\n"
