#!/usr/bin/env python3
"""Regenerate evals/refs/175/bad/*.py from crdt.py.

Each known-bad arm is the reference with ONE plausible mistake, so a failure
proves the validator catches that mistake (prove.json pins where it fails).
    python3 evals/refs/175/make_bad.py
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REF = open(os.path.join(HERE, "crdt.py")).read()


def patch(*pairs):
    s = REF
    for old, new in pairs:
        assert s.count(old) == 1, old
        s = s.replace(old, new)
    return s


BAD = {
    # Applies every op on receipt: no causal hold; a missing anchor goes to HEAD.
    "nobuffer": patch(
        ("""        self.held[(o, s)] = op
        progress = True
        while progress:
            progress = False
            for j in range(N):
                nxt = self.held.get((j, self.vec[j] + 1))
                if nxt is not None and self._ready(nxt):
                    del self.held[(j, self.vec[j] + 1)]
                    self._apply(nxt)
                    progress = True
""", """        self.seen.add((o, s))
        self._apply(op)
"""),
        ("""        if self.vec[o] >= s or (o, s) in self.held:""",
         """        if (o, s) in self.seen or o == self.rid:"""),
        ("""        self.vec[op["o"]] += 1""", """        self.vec[op["o"]] = max(self.vec[op["o"]], op["s"])"""),
        ("""            p = tuple(op["a"])
""", """            p = tuple(op["a"])
            if p not in self.nxt:
                p = HEAD
"""),
        ("""        self.held = {}                # (origin, seq) -> op
""", """        self.held = {}                # (origin, seq) -> op
        self.seen = set()
"""),
    ),
    # Siblings in ascending id order.
    "ascending": patch(("nxt[p] is not None and nxt[p] > oid", "nxt[p] is not None and nxt[p] < oid")),
    # Lamport clock advanced on receipt, even for ops that are only held.
    "clockrecv": patch(("""        self.held[(o, s)] = op
""", """        self.held[(o, s)] = op
        self.maxc = max(self.maxc, op["c"])
""")),
    # New element always placed directly after its anchor.
    "noskip": patch(("""            while nxt[p] is not None and nxt[p] > oid:
                p = nxt[p]
""", "")),
    # remove(x) at a receiver cancels every tag of x it holds (remove-wins).
    "removewins": patch(("""                live.difference_update(tuple(t) for t in op["t"])""",
                         """                live.clear()""")),
    # Held ops kept in a list: a repeated delivery of a held op is held twice.
    "dupheld": patch(
        ("""        if self.vec[o] >= s or (o, s) in self.held:
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
""", """        if self.vec[o] >= s:
            return
        self.hlist.append(op)
        progress = True
        while progress:
            progress = False
            for h in list(self.hlist):
                if self.vec[h["o"]] == h["s"] - 1 and self._ready(h):
                    self.hlist.remove(h)
                    self._apply(h)
                    progress = True
"""),
        ("""        return len(self.held)""", """        return len(self.hlist)"""),
        ("""        self.held = {}                # (origin, seq) -> op
""", """        self.held = {}                # (origin, seq) -> op
        self.hlist = []
"""),
    ),
    # Correct semantics, but the sequence is rebuilt with a recursive walk.
    "recursive": patch(
        ("""    def _visible_ids(self):
        out, cur, nxt, dead = [], self.nxt[HEAD], self.nxt, self.dead
        while cur is not None:
            if cur not in dead:
                out.append(cur)
            cur = nxt[cur]
        return out
""", """    def _visible_ids(self):
        kids = {}
        for oid, a in self.anchor.items():
            kids.setdefault(a, []).append(oid)
        out = []

        def walk(e):
            for c in sorted(kids.get(e, ()), reverse=True):
                if c not in self.dead:
                    out.append(c)
                walk(c)
        walk(HEAD)
        return out
"""),
        ("""            self.ch[oid] = op["v"]""", """            self.ch[oid] = op["v"]
            self.anchor[oid] = tuple(op["a"])"""),
        ("""        self.dead = set()
""", """        self.dead = set()
        self.anchor = {}
"""),
    ),
    # elements() in insertion order (reversed), not ascending.
    "unsorted": patch(("""        return sorted(x for x, t in self.tags.items() if t)""",
                       """        return [x for x, t in self.tags.items() if t][::-1]""")),
    # elements() ascending, but a tuple, not the list the contract names.
    "notlist": patch(("""        return sorted(x for x, t in self.tags.items() if t)""",
                      """        return tuple(sorted(x for x, t in self.tags.items() if t))""")),
    # Oracle theft: reads every ancestor's argv through ps (the validator is
    # run as `python -c <script>`), execs any python source found there and
    # writes the oracle's answers straight to the driver's output stream.
    "argvcheat": """import os, subprocess, sys


def _steal():
    pid = os.getppid()
    for _ in range(6):
        if pid <= 1:
            return
        cmd = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(pid)],
                             capture_output=True, text=True).stdout
        src = cmd.replace("\\\\012", "\\n").replace("\\\\011", "\\t")
        if "def all_cases" in src:
            body = src[src.index("-c ") + 3:] if "-c " in src else src
            g = {"__name__": "stolen"}
            try:
                exec(body, g)
                out = sys.modules["__main__"].out
                for b in g["all_cases"]():
                    out.write(__import__("json").dumps({"obs": b.want}) + "\\n")
                out.flush()
                os._exit(0)
            except Exception as ex:
                sys.stderr.write("cheat failed: %r\\n" % ex)
        pid = int(subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)],
                                 capture_output=True, text=True).stdout.strip() or 0)


if not getattr(sys, "_stole", False):   # each replica re-runs this module: try once
    sys._stole = True
    _steal()


class Replica:
    def __init__(self, rid): pass
    def add(self, x): return 'm'
    def remove(self, x): return 'm'
    def insert(self, i, ch): return 'm'
    def delete(self, i): return 'm'
    def deliver(self, msg): pass
    def elements(self): return []
    def text(self): return ''
    def pending(self): return 0
""",
    # Leak probe: dumps every fixture file it can find, then guesses.
    "leak": """import glob, sys


class Replica:
    def __init__(self, rid):
        for f in sorted(glob.glob('/tmp/eval_crdt/*')):
            try:
                sys.stderr.write(open(f).read())
            except Exception:
                pass

    def add(self, x): return 'm'
    def remove(self, x): return 'm'
    def insert(self, i, ch): return 'm'
    def delete(self, i): return 'm'
    def deliver(self, msg): pass
    def elements(self): return []
    def text(self): return ''
    def pending(self): return 0
""",
    # Never returns from deliver: the run limit must catch it.
    "slow": patch(("""        op = json.loads(msg)
""", """        op = json.loads(msg)
        while True:
            pass
""")),
}

os.makedirs(os.path.join(HERE, "bad"), exist_ok=True)
for name, src in BAD.items():
    with open(os.path.join(HERE, "bad", name + ".py"), "w") as f:
        f.write(src)
print("wrote", len(BAD), "bad arms")
