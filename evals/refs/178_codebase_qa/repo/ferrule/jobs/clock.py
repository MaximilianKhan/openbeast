"""Clocks. The scheduler never reads wall time directly so tests can drive
it with ``ManualClock``."""

from __future__ import annotations

import time


class Clock:
    def now_ms(self) -> int:
        raise NotImplementedError

    def sleep_ms(self, ms: int) -> None:
        raise NotImplementedError


class SystemClock(Clock):
    def now_ms(self) -> int:
        return int(time.monotonic() * 1000)

    def sleep_ms(self, ms: int) -> None:
        time.sleep(max(ms, 0) / 1000.0)


class ManualClock(Clock):
    def __init__(self, start_ms: int = 0) -> None:
        self._now = start_ms

    def now_ms(self) -> int:
        return self._now

    def sleep_ms(self, ms: int) -> None:
        self._now += max(ms, 0)

    def advance(self, ms: int) -> None:
        self._now += ms
