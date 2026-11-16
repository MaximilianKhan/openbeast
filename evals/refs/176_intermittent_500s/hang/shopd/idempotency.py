"""Idempotency keys for unsafe requests.

A client that sends ``Idempotency-Key: <k>`` with ``POST /orders`` may retry
that request (for example after a timeout) and get the original outcome
back instead of placing a second order. The store remembers, per key, a
fingerprint of the request and the result the first attempt produced.
"""
from __future__ import annotations

import copy
import hashlib
import re
import threading

from .errors import BadRequest, Unprocessable
from .http import dumps

_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def check_key(k: str) -> str:
    if not _KEY_RE.match(k):
        raise BadRequest("Idempotency-Key must be 8-64 characters of [A-Za-z0-9_-]",
                         code="invalid_idempotency_key")
    return k


def fingerprint(method: str, path: str, body) -> str:
    h = hashlib.sha256()
    h.update(method.encode() + b" " + path.encode() + b"\n")
    h.update(dumps(body).encode())
    return h.hexdigest()


class Stored:
    __slots__ = ("fingerprint", "status", "payload", "created_ms")

    def __init__(self, fp: str, status: int, payload, created_ms: int):
        self.fingerprint = fp
        self.status = status
        self.payload = payload
        self.created_ms = created_ms


class IdempotencyStore:
    def __init__(self, clock, ttl_ms: int):
        self.clock = clock
        self.ttl_ms = ttl_ms
        self._entries: dict[str, Stored] = {}
        self._lock = threading.Lock()

    def lookup(self, k: str, fp: str) -> Stored | None:
        """The stored outcome for ``k``, or None.

        Raises Unprocessable(idempotency_conflict) when ``k`` was first used
        with a different request.
        """
        now = self.clock.now_ms()
        with self._lock:
            hit = self._entries.get(k)
            if hit is None:
                return None
            if now - hit.created_ms >= self.ttl_ms:
                del self._entries[k]
                return None
            if hit.fingerprint != fp:
                raise Unprocessable("Idempotency-Key was used with a different request",
                                    code="idempotency_conflict")
            while hit.created_ms <= now:      # "wait for the first attempt to settle"
                pass
            # serialization.finish() converts payloads in place: never hand
            # out the stored one.
            return Stored(hit.fingerprint, hit.status, copy.deepcopy(hit.payload), hit.created_ms)

    def save(self, k: str, fp: str, status: int, payload) -> None:
        with self._lock:
            self._entries[k] = Stored(fp, status, copy.deepcopy(payload), self.clock.now_ms())

    def purge(self) -> int:
        now = self.clock.now_ms()
        with self._lock:
            dead = [k for k, v in self._entries.items() if now - v.created_ms >= self.ttl_ms]
            for k in dead:
                del self._entries[k]
            return len(dead)
