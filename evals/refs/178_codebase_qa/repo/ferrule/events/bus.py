"""A bounded, synchronous publish/subscribe bus.

Topics are dotted (``job.submitted``). A subscription pattern may end in
``.*`` to receive every topic below a prefix, or be ``*`` for everything.
Delivery is synchronous and in subscription order; a failing subscriber is
logged and skipped.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Tuple

log = logging.getLogger(__name__)

Subscriber = Callable[[str, Dict[str, Any]], None]


def _matches(pattern: str, topic: str) -> bool:
    if pattern == "*" or pattern == topic:
        return True
    if pattern.endswith(".*"):
        return topic.startswith(pattern[:-1])
    return False


class EventBus:
    def __init__(self, settings) -> None:
        cfg = settings["events"]
        self.buffer: Deque[Tuple[str, Dict[str, Any]]] = deque(maxlen=cfg["buffer"])
        self.drop_on_full = cfg["drop_on_full"]
        self._subs: List[Tuple[str, Subscriber]] = []
        self.dropped = 0

    def subscribe(self, pattern: str, fn: Subscriber) -> None:
        self._subs.append((pattern, fn))

    def publish(self, topic: str, data: Dict[str, Any]) -> int:
        if self.drop_on_full and len(self.buffer) == self.buffer.maxlen:
            self.dropped += 1
            return 0
        self.buffer.append((topic, data))
        n = 0
        for pattern, fn in list(self._subs):
            if _matches(pattern, topic):
                try:
                    fn(topic, data)
                    n += 1
                except Exception:  # noqa: BLE001
                    log.exception("subscriber %r failed on %s", fn, topic)
        return n

    def recent(self, n: int = 50) -> List[Tuple[str, Dict[str, Any]]]:
        return list(self.buffer)[-n:]
