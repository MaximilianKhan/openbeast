"""Test bootstrap: put the repo on sys.path and stub the native extension
when it has not been built."""

import hashlib
import os
import sys
import types
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import ferrule._native  # noqa: F401
except ImportError:
    stub = types.ModuleType("ferrule._native")
    stub.ABI_VERSION = 3
    stub.fingerprint = lambda data: hashlib.blake2b(data, digest_size=16).hexdigest()
    stub.crc32c = lambda data, seed=0: zlib.crc32(data, seed)  # not Castagnoli; fine for tests
    stub.monotonic_ns = lambda: 0
    sys.modules["ferrule._native"] = stub
