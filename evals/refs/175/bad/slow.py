"""Reference solution for task 175: op-based OR-Set + RGA, 3 replicas.

Causal delivery via per-origin sequence numbers plus a dependency vector;
the RGA is a linked list with the classic skip-larger-ids insertion.
"""
import json

N = 3
HEAD = (0, -1)


class Replica:
    def __init__(self, rid: int):
        self.rid = rid
        self.vec = [0] * N            # ops in effect, per origin (a causal prefix)
        self.maxc = 0                 # largest counter in effect
        self.held = {}                # (origin, seq) -> op
        # OR-Set
        self.tags = {}                # x -> set of live tags
        # RGA: linked list over ids, HEAD first
        self.nxt = {HEAD: None}
        self.ch = {}
        self.dead = set()

    # ------------------------------------------------------------- helpers
    def _visible_ids(self):
        out, cur, nxt, dead = [], self.nxt[HEAD], self.nxt, self.dead
        while cur is not None:
            if cur not in dead:
                out.append(cur)
            cur = nxt[cur]
        return out

    def _new(self, kind, **payload):
        op = {"o": self.rid, "s": self.vec[self.rid] + 1, "c": self.maxc + 1,
              "d": list(self.vec), "k": kind}
        op.update(payload)
        self._apply(op)
        return json.dumps(op, separators=(",", ":"))

    def _apply(self, op):
        self.vec[op["o"]] += 1
        if op["c"] > self.maxc:
            self.maxc = op["c"]
        oid = (op["c"], op["o"])
        k = op["k"]
        if k == "add":
            self.tags.setdefault(op["x"], set()).add(oid)
        elif k == "remove":
            live = self.tags.get(op["x"])
            if live:
                live.difference_update(tuple(t) for t in op["t"])
        elif k == "insert":
            p = tuple(op["a"])
            nxt = self.nxt
            while nxt[p] is not None and nxt[p] > oid:
                p = nxt[p]
            nxt[oid] = nxt[p]
            nxt[p] = oid
            self.ch[oid] = op["v"]
        else:
            self.dead.add(tuple(op["t"]))

    def _ready(self, op):
        v = self.vec
        return all(v[j] >= op["d"][j] for j in range(N))

    # ----------------------------------------------------------- local ops
    def add(self, x: str) -> str:
        return self._new("add", x=x)

    def remove(self, x: str) -> str:
        return self._new("remove", x=x, t=sorted(self.tags.get(x, ())))

    def insert(self, i: int, ch: str) -> str:
        anchor = HEAD if i == 0 else self._visible_ids()[i - 1]
        return self._new("insert", a=anchor, v=ch)

    def delete(self, i: int) -> str:
        return self._new("delete", t=self._visible_ids()[i])

    # -------------------------------------------------------------- remote
    def deliver(self, msg: str) -> None:
        op = json.loads(msg)
        while True:
            pass
        o, s = op["o"], op["s"]
        if self.vec[o] >= s or (o, s) in self.held:
            return
        self.held[(o, s)] = op
        progress = True
        while progress:
            progress = False
            for j in range(N):
                nxt = self.held.get((j, self.vec[j] + 1))
                if nxt is not None and self._ready(nxt):
                    del self.held[(j, self.vec[j] + 1)]
                    self._apply(nxt)
                    progress = True

    # --------------------------------------------------------- observation
    def elements(self) -> list:
        return sorted(x for x, t in self.tags.items() if t)

    def text(self) -> str:
        return "".join(self.ch[i] for i in self._visible_ids())

    def pending(self) -> int:
        return len(self.held)
