class VectorClock:
    def __init__(self, node_id):
        self.node_id = node_id
        self.clock = {node_id: 0}
    def tick(self):
        self.clock[self.node_id] = self.clock.get(self.node_id, 0) + 1
    def merge(self, other_clock):
        for k, v in other_clock.items():
            self.clock[k] = max(self.clock.get(k, 0), v)
        self.tick()
    def snapshot(self):
        return dict(self.clock)
    @staticmethod
    def compare(a, b):
        keys = set(a) | set(b)
        le = all(a.get(k, 0) <= b.get(k, 0) for k in keys)
        ge = all(a.get(k, 0) >= b.get(k, 0) for k in keys)
        if le and ge: return 'equal'
        if le: return 'before'
        if ge: return 'after'
        return 'concurrent'
