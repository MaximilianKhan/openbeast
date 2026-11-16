"""ferrule: a small multi-tenant job queue.

The hot paths (payload fingerprinting, CRC of WAL frames) live in the
``ferrule._native`` extension built from ``native/`` with setuptools-rust;
see ``ferrule/_native.pyi`` for its interface.
"""

from ferrule import _native

__version__ = "2.7.0"
NATIVE_ABI = _native.ABI_VERSION

__all__ = ["__version__", "NATIVE_ABI"]
