# WRONG (ready queue used as a stack (LIFO), not round-robin) -- must FAIL the validator.
"""Reference: cooperative round-robin scheduler for generator coroutines."""
import heapq
import itertools
import time
from collections import deque


class Scheduler:
    def __init__(self):
        self._ready = deque()
        self._sleeping = []                 # heap of (wake_time, seq, coro)
        self._seq = itertools.count()

    def spawn(self, coro):
        self._ready.append(coro)

    def run(self):
        while self._ready or self._sleeping:
            now = time.monotonic()
            while self._sleeping and self._sleeping[0][0] <= now:
                self._ready.append(heapq.heappop(self._sleeping)[2])
            if not self._ready:
                time.sleep(max(0.0, self._sleeping[0][0] - time.monotonic()))
                continue
            coro = self._ready.pop()
            try:
                req = coro.send(None)
            except StopIteration:
                continue
            if req is None:
                self._ready.append(coro)
            elif req[0] == 'sleep':
                heapq.heappush(self._sleeping, (time.monotonic() + req[1], next(self._seq), coro))
            elif req[0] == 'spawn':
                self._ready.append(req[1])
                self._ready.append(coro)
            else:
                raise ValueError(f'unknown request {req!r}')
