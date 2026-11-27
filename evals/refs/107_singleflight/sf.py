import threading
class _Call:
    def __init__(self): self.event = threading.Event(); self.result = None; self.exc = None
class SingleFlight:
    def __init__(self): self._lock = threading.Lock(); self._inflight = {}
    def do(self, key, fn, *args, **kwargs):
        with self._lock:
            call = self._inflight.get(key)
            if call is not None:
                leader = False
            else:
                call = _Call(); self._inflight[key] = call; leader = True
        if leader:
            try: call.result = fn(*args, **kwargs)
            except Exception as e: call.exc = e
            finally:
                with self._lock: self._inflight.pop(key, None)
                call.event.set()
        else:
            call.event.wait()
        if call.exc is not None: raise call.exc
        return call.result
