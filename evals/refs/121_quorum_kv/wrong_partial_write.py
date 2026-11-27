# WRONG (a write below the w quorum still lands on the replicas it reached) -- must FAIL the validator.
class QuorumKV:
    def __init__(self, n, r, w):
        if r + w <= n:
            raise ValueError('r + w must be > n')
        self.n = n; self.r = r; self.w = w
        # store[key][replica] = (value, timestamp, node)
        self.store = {}
    def _cell(self, key):
        return self.store.setdefault(key, {})
    def write(self, key, value, timestamp, node, success_replicas):
        cell = self._cell(key)
        cand = (timestamp, node)
        for rep in success_replicas:
            cur = cell.get(rep)
            if cur is None or (cur[1], cur[2]) < cand:
                cell[rep] = (value, timestamp, node)
        return len(success_replicas) >= self.w
    def read(self, key, response_replicas):
        if len(response_replicas) < self.r:
            return None
        cell = self._cell(key)
        best = None  # (value, timestamp, node)
        for rep in response_replicas:
            cur = cell.get(rep)
            if cur is not None and (best is None or (cur[1], cur[2]) > (best[1], best[2])):
                best = cur
        if best is None:
            return None
        # read repair
        for rep in response_replicas:
            cur = cell.get(rep)
            if cur is None or (cur[1], cur[2]) < (best[1], best[2]):
                cell[rep] = best
        return best[0]
    def replica_state(self, key, replica):
        return self.store.get(key, {}).get(replica)
