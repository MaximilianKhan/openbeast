import threading
class Debouncer:
    def __init__(self, delay, fn):
        self.delay = delay; self.fn = fn; self._timer = None; self._lock = threading.Lock()
    def __call__(self, *args, **kwargs):
        with self._lock:
            if self._timer is not None: self._timer.cancel()
            self._timer = threading.Timer(self.delay, self.fn, args=args, kwargs=kwargs)
            self._timer.daemon = True; self._timer.start()
    def cancel(self):
        with self._lock:
            if self._timer is not None: self._timer.cancel(); self._timer = None
