"""Time sources.

Everything in the service that needs the time asks a clock object for
``now_ms()`` (milliseconds since the Unix epoch, UTC). Production uses
``SystemClock``; tests and the traffic replayer use ``FakeClock`` so that
TTLs, rate limits and report dates are deterministic.
"""
from __future__ import annotations

import datetime as _dt
import threading
import time

EPOCH = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)


class SystemClock:
    """Wall-clock time."""

    def now_ms(self) -> int:
        return int(time.time() * 1000)


class FakeClock:
    """A manually driven clock.

    ``FakeClock(start_ms)`` starts at ``start_ms`` and only moves when told
    to. It is safe to share between threads.
    """

    DEFAULT_START = 1767225600000  # 2026-01-01T00:00:00Z

    def __init__(self, start_ms: int = DEFAULT_START):
        self._now = int(start_ms)
        self._lock = threading.Lock()

    def now_ms(self) -> int:
        with self._lock:
            return self._now

    def advance(self, ms: int) -> int:
        if ms < 0:
            raise ValueError("a clock cannot go backwards")
        with self._lock:
            self._now += int(ms)
            return self._now

    def set(self, ms: int) -> None:
        with self._lock:
            if ms < self._now:
                raise ValueError("a clock cannot go backwards")
            self._now = int(ms)


def iso(ms: int) -> str:
    """Render epoch milliseconds as ``YYYY-MM-DDTHH:MM:SS.mmmZ``."""
    dt = EPOCH + _dt.timedelta(milliseconds=int(ms))
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (int(ms) % 1000)


def day_of(ms: int) -> str:
    """The UTC calendar day (``YYYY-MM-DD``) that contains ``ms``."""
    return (EPOCH + _dt.timedelta(milliseconds=int(ms))).strftime("%Y-%m-%d")


def day_bounds(day: str) -> tuple[int, int]:
    """``[start_ms, end_ms)`` of a UTC calendar day given as ``YYYY-MM-DD``.

    Raises ValueError for anything that is not a real date in that format.
    """
    if len(day) != 10 or day[4] != "-" or day[7] != "-":
        raise ValueError("expected YYYY-MM-DD")
    d = _dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=_dt.timezone.utc)
    start = int((d - EPOCH).total_seconds() * 1000)
    return start, start + 86400000
