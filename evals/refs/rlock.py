import threading


class MyRLock:
    """Reentrant mutex built from a plain Lock and a Condition."""

    def __init__(self):
        self._cond = threading.Condition(threading.Lock())
        self._owner = None
        self._count = 0

    def acquire(self):
        me = threading.get_ident()
        with self._cond:
            if self._owner == me:
                self._count += 1
                return True
            while self._owner is not None:
                self._cond.wait()
            self._owner = me
            self._count = 1
            return True

    def release(self):
        me = threading.get_ident()
        with self._cond:
            if self._owner != me:
                raise RuntimeError("cannot release un-acquired lock")
            self._count -= 1
            if self._count == 0:
                self._owner = None
                self._cond.notify()
