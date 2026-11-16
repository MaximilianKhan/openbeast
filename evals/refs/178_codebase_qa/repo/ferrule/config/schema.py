"""Typing and range checks for the merged settings tree.

Values read from INI files and the environment arrive as strings. They are
converted to the type of the matching entry in ``DEFAULTS``. Keys that have
no default are passed through untouched; tables listed in ``_TEMPLATES``
use a template table instead (every retry policy is typed like
``retry.default``).

After typing, ``_CLAMPS`` forces a few values into their supported range.
Clamping is silent by design: an operator who asks for more workers than a
node supports gets the maximum, not a crash loop.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from ferrule.config.defaults import DEFAULTS
from ferrule.config.ini import split_list
from ferrule.errors import ConfigError

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}

_TEMPLATES: Dict[Tuple[str, ...], Dict[str, Any]] = {
    ("retry",): DEFAULTS["retry"]["default"],
}

_CLAMPS: Dict[str, Tuple[float, float]] = {
    "scheduler.max_parallel": (1, 64),
    "scheduler.pool_size": (1, 32),
    "scheduler.tick_ms": (50, 5000),
    "storage.wal.fsync_interval_ms": (10, 10000),
    "storage.wal.segment_mb": (1, 1024),
    "ratelimit.burst": (1, 100000),
    "cache.ttl_s": (0, 86400),
}


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ConfigError(f"not a boolean: {value!r}")


def _coerce_like(value: Any, default: Any, where: str) -> Any:
    if isinstance(default, bool):
        return parse_bool(value)
    if isinstance(value, type(default)) and not isinstance(value, bool):
        return value
    try:
        if isinstance(default, int):
            return int(str(value).strip())
        if isinstance(default, float):
            return float(str(value).strip())
    except ValueError as exc:
        raise ConfigError(f"{where}: {exc}") from None
    if isinstance(default, list):
        if isinstance(value, list):
            return value
        return split_list(str(value))
    return str(value) if default is not None else value


def _template_for(path: Tuple[str, ...]) -> Optional[Dict[str, Any]]:
    return _TEMPLATES.get(path)


def _walk(node: Dict[str, Any], defaults: Optional[Dict[str, Any]],
          path: Tuple[str, ...]) -> None:
    template = _template_for(path)
    for key, value in list(node.items()):
        sub = path + (key,)
        if template is not None and isinstance(value, dict):
            _walk(value, template, sub)
            continue
        default = defaults.get(key) if isinstance(defaults, dict) else None
        if isinstance(value, dict):
            _walk(value, default if isinstance(default, dict) else None, sub)
        elif default is not None and not isinstance(default, dict):
            node[key] = _coerce_like(value, default, ".".join(sub))


def _clamp(tree: Dict[str, Any]) -> None:
    for dotted, (lo, hi) in _CLAMPS.items():
        parts = dotted.split(".")
        node = tree
        for part in parts[:-1]:
            node = node.get(part, {})
        leaf = parts[-1]
        if leaf in node and isinstance(node[leaf], (int, float)):
            value = node[leaf]
            node[leaf] = type(value)(min(max(value, lo), hi))


def coerce(tree: Dict[str, Any]) -> Dict[str, Any]:
    """Type and clamp ``tree`` in place; returns it for convenience."""
    _walk(tree, DEFAULTS, ())
    _clamp(tree)
    return tree
