"""Identifiers."""

from __future__ import annotations

import os
import time

from ferrule.constants import JOB_ID_PREFIX

_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"  # Crockford base32, lower case


def _b32(n: int, width: int) -> str:
    out = []
    for _ in range(width):
        n, r = divmod(n, 32)
        out.append(_ALPHABET[r])
    return "".join(reversed(out))


def new_job_id() -> str:
    """Time-ordered id: 48-bit milliseconds + 40 random bits."""
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rnd = int.from_bytes(os.urandom(5), "big")
    return JOB_ID_PREFIX + _b32(ms, 10) + _b32(rnd, 8)


def short(job_id: str) -> str:
    return job_id[len(JOB_ID_PREFIX):][-8:] if job_id.startswith(JOB_ID_PREFIX) else job_id[-8:]
