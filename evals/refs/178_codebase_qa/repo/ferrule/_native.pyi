# Type stub for the compiled extension built from native/ (not part of
# the source tree; `pip install -e .` builds it with setuptools-rust).

ABI_VERSION: int

def fingerprint(data: bytes, /) -> str:
    """Hex digest of a stable 128-bit hash of ``data``."""

def crc32c(data: bytes, /, seed: int = 0) -> int:
    """CRC-32C (Castagnoli) of ``data``."""

def monotonic_ns() -> int:
    """Monotonic clock in nanoseconds (CLOCK_MONOTONIC_RAW on Linux)."""
