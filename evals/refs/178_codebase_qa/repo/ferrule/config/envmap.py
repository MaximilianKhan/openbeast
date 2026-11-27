"""Mapping between dotted setting paths and environment variable names.

The general rule is ``FERRULE_`` + the path upper-cased with ``__`` between
segments, so ``scheduler.tick_ms`` is ``FERRULE_SCHEDULER__TICK_MS``.

Some subtrees have short names so that the variables stay readable in
container manifests. The table is matched on the longest leading run of
path segments; whatever is left of the path is appended as usual.
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional, Tuple

ENV_PREFIX = "FERRULE"
SEP = "__"

_SEGMENT_ALIASES: Dict[Tuple[str, ...], str] = {
    ("scheduler",): "SCHED",
    ("ratelimit",): "RL",
    ("storage", "wal"): "WAL",
    ("storage", "sqlite"): "DB",
}

# Names that 1.x deployments used. They are only honoured when
# FERRULE_LEGACY_ENV=1 is set, and only for the keys listed here.
LEGACY_NAMES: Dict[str, str] = {
    "FERRULE_WORKERS": "scheduler.max_parallel",
    "FERRULE_DB": "storage.sqlite.path",
    "FERRULE_WAL_DIR": "storage.wal.dir",
}


def _segments(path: str) -> Tuple[str, ...]:
    return tuple(p for p in path.split(".") if p)


def _alias_for(parts: Tuple[str, ...]) -> Tuple[Optional[str], int]:
    best: Optional[str] = None
    used = 0
    for prefix, alias in _SEGMENT_ALIASES.items():
        n = len(prefix)
        if n > used and parts[:n] == prefix:
            best, used = alias, n
    return best, used


def env_name(path: str) -> str:
    """Return the environment variable that overrides ``path``."""
    parts = _segments(path)
    alias, used = _alias_for(parts)
    tail = [p.upper().replace("-", "_") for p in parts[used:]]
    head = [alias] if alias else []
    return ENV_PREFIX + "_" + SEP.join(head + tail)


def legacy_overrides(env: Dict[str, str]) -> Iterable[Tuple[str, str]]:
    if env.get("FERRULE_LEGACY_ENV") != "1":
        return []
    return [(path, env[name]) for name, path in LEGACY_NAMES.items() if name in env]
