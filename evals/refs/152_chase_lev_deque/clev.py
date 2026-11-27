"""Reference: Chase-Lev work-stealing deque (owner at the bottom, thieves at the top)."""
import threading


class WSDeque:
    def __init__(self):
        self._buf = []          # slot i holds item i; consumed slots are cleared
        self._top = 0           # next index a thief takes (advanced by thieves)
        self._bottom = 0        # next index the owner pushes to (owner only)
        self._lock = threading.Lock()

    def push(self, x):
        with self._lock:
            b = self._bottom
            if b < len(self._buf):
                self._buf[b] = x
            else:
                self._buf.append(x)
            self._bottom = b + 1

    def pop(self):
        with self._lock:
            b = self._bottom - 1
            if b < self._top:
                return None
            self._bottom = b
            x = self._buf[b]
            self._buf[b] = None
            if b == self._top:          # took the last item: reset to reuse space
                self._top = self._bottom = 0
                del self._buf[:]
            return x

    def steal(self):
        # The lock serialises the thief against the owner's pop of the last
        # item, so the boundary race is resolved here and 'ABORT' never
        # escapes; a lock-free variant would return 'ABORT' on a lost CAS.
        with self._lock:
            t = self._top
            if t >= self._bottom:
                return None
            x = self._buf[t]
            self._buf[t] = None
            self._top = t + 1
            return x
