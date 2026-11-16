"""Job storage backends. Use ``create_store(settings)``; do not instantiate
backends directly outside tests."""

from ferrule.storage.base import Store
from ferrule.storage.factory import create_store

__all__ = ["Store", "create_store"]
