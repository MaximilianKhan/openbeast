"""Retry policy: how long to wait after a failure, and when to give up.

A policy is assembled per kind from ``retry.default`` overlaid with
``retry.<family>``; any field the family table omits comes from the
default table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from ferrule.jobs.kinds import family


@dataclass(frozen=True)
class RetryPolicy:
    base_ms: int
    factor: float
    cap_ms: int
    max_attempts: int
    count_expiry: bool = False

    def delay_ms(self, failures: int) -> int:
        """Delay before the next attempt, given ``failures`` failures so far."""
        if failures <= 0:
            return 0
        raw = self.base_ms * (self.factor ** (failures - 1))
        return int(min(raw, self.cap_ms))

    def exhausted(self, attempts: int) -> bool:
        return attempts >= self.max_attempts


_FIELDS = ("base_ms", "factor", "cap_ms", "max_attempts", "count_expiry")


def policy_for_kind(kind: str, settings) -> RetryPolicy:
    tables: Dict[str, Dict[str, Any]] = settings["retry"]
    merged: Dict[str, Any] = {}
    for name in ("default", family(kind)):
        merged.update(tables.get(name, {}))
    return RetryPolicy(**{k: merged[k] for k in _FIELDS})
