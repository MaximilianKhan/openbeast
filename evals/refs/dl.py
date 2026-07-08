class DistributedLock:
    def __init__(self):
        self._counter = 0
        self._holder = None  # (owner_id, token, expiry)
    def _free(self, now):
        return self._holder is None or now >= self._holder[2]
    def acquire(self, owner_id, ttl, now):
        if not self._free(now):
            return None
        self._counter += 1
        self._holder = (owner_id, self._counter, now + ttl)
        return self._counter
    def release(self, owner_id, token):
        if self._holder is not None and self._holder[0] == owner_id and self._holder[1] == token:
            self._holder = None
            return True
        return False
    def renew(self, owner_id, token, ttl, now):
        h = self._holder
        if h is not None and h[0] == owner_id and h[1] == token and now < h[2]:
            self._holder = (owner_id, token, now + ttl)
            return True
        return False
