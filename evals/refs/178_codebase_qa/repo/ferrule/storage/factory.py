"""Build the configured store.

``storage.backend`` names a backend; a few historical names are accepted as
aliases. The result may be wrapped in a journal (``storage.wal.enabled``)
and then in a read cache (``storage.cache``).
"""

from __future__ import annotations

import logging
from typing import Callable, Dict

from ferrule.errors import ConfigError
from ferrule.storage.base import Store
from ferrule.storage.cached import CachedStore
from ferrule.storage.journal import JournaledStore
from ferrule.storage.memory import MemoryStore
from ferrule.storage.sqlite_store import SqliteStore
from ferrule.storage.wal import WalStore, WalWriter

log = logging.getLogger(__name__)

_ALIASES: Dict[str, str] = {
    "sqlite": "sqlite3",
    "sql": "sqlite3",
    "wal": "walfile",
    "mem": "memory",
}

_BACKENDS: Dict[str, Callable[..., Store]] = {
    "memory": MemoryStore,
    "sqlite3": SqliteStore,
    "walfile": WalStore,
}


def backend_name(settings) -> str:
    raw = settings["storage"]["backend"]
    return _ALIASES.get(raw, raw)


def create_store(settings) -> Store:
    cfg = settings["storage"]
    name = backend_name(settings)
    try:
        factory = _BACKENDS[name]
    except KeyError:
        raise ConfigError(f"unknown storage backend {cfg['backend']!r}") from None
    store = factory(cfg)
    if cfg["wal"].get("enabled") and name != "walfile":
        store = JournaledStore(store, WalWriter(cfg["wal"]))
    if cfg["cache"]:
        store = CachedStore(store)
    log.info("storage: %s", store.name)
    return store
