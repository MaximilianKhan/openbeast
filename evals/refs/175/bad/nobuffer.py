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
        self.seen = set()
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
        self.vec[op["o"]] = max(self.vec[op["o"]], op["s"])
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
            if p not in self.nxt:
                p = HEAD
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
        o, s = op["o"], op["s"]
        if (o, s) in self.seen or o == self.rid:
            return
        self.seen.add((o, s))
        self._apply(op)

    # --------------------------------------------------------- observation
    def elements(self) -> list:
        return sorted(x for x, t in self.tags.items() if t)

    def text(self) -> str:
        return "".join(self.ch[i] for i in self._visible_ids())

    def pending(self) -> int:
        return len(self.held)
