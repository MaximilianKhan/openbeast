# Validator for task 175 (op-based OR-Set + RGA across 3 replicas).
# build_task.py embeds this file verbatim as the task's python validation
# script; the harness feeds it on stdin (`python -I -S -`), so its source
# (oracle included) is in no argv. Every scenario comes from hand-written
# edge scripts plus a seeded generator below; the expected observations come
# from a declarative oracle that never leaves this process. The solution runs
# in a separate driver process (vlib.run_solution: own session, CPU budget
# over the whole tree, fork refused, scaled by OPENBEAST_TIMEOUT_SCALE). The
# driver holds no answers: it gets the scenario scripts (inputs only) on
# stdin and prints observations, which are compared here. Each replica is
# built from its own freshly exec'd copy of the module, so module and class
# state is not shared. Residual: the three replicas still share one
# interpreter (sys, builtins), so rule 5 is enforced only against ordinary
# module/class state; that buys a cheat nothing, because every observation
# is still checked exactly against the oracle. A mismatch reports only the
# case index.
import json, os, random, shutil, sys, tempfile

import vlib

D = "/tmp/eval_crdt"
SRC = D + "/crdt.py"
RUN_CPU_S = 10     # the prompt's 10 s budget, at scale 1 (vlib scales it)
RUN_WALL_S = 20    # at scale 1; inside the harness's 30 s x scale
HEAD = (0, -1)


def fail(msg):
    print(msg)
    sys.exit(1)


# ------------------------------------------------------------------ oracle
# Declarative: the state of a replica is a pure function of the set of ops in
# effect there. That set is causally closed and per-origin prefix-closed, so
# it is exactly the vector vec[j] = number of origin-j ops in effect.
class Op:
    __slots__ = ("k", "id", "origin", "seq", "deps", "kind", "x", "ch", "anchor", "target", "cancels")


class Cluster:
    def __init__(self):
        self.ops = []                      # by creation index k
        self.by_origin = [[], [], []]
        self.vec = [[0, 0, 0] for _ in range(3)]
        self.maxc = [0, 0, 0]
        self.held = [set(), set(), set()]  # k of ops held (received, not in effect)
        self._cand = [{}, {}, {}]          # the same, keyed by (origin, seq)
        # per replica: the vec the sequence below was built for, and that
        # sequence as parallel lists (ids, entries, tree depth)
        self._seq = [[(0, 0, 0), [], [], []] for _ in range(3)]

    # -- views
    def _applied(self, r):
        v = self.vec[r]
        for j in range(3):
            yield from self.by_origin[j][:v[j]]

    def seq_ids(self, r):
        """[(id, ch, deleted)] of the full sequence at replica r.

        The sequence is the preorder of the insert tree (each insert a child
        of its anchor, siblings in descending id order), with the delete set
        marked. It is a function of the op set alone, so it is kept per
        replica and extended by the ops that came into effect since the last
        call, in creation order (a causal order: an anchor or a delete target
        is always created before the op that names it). Rebuilding it from
        every applied op on each call was O(n^2) over a 2500-char typing run,
        1.8 s of every validation."""
        st = self._seq[r]
        key = tuple(self.vec[r])
        if st[0] == key:
            return st[2]
        old = st[0]
        new = sorted((op for j in range(3) for op in self.by_origin[j][old[j]:key[j]]),
                     key=lambda o: o.k)
        ids, ents, depth = st[1], st[2], st[3]
        for op in new:
            if op.kind == "insert":
                if op.anchor == HEAD:
                    pos, d = 0, 0
                else:
                    a = ids.index(op.anchor)
                    pos, d = a + 1, depth[a] + 1
                # skip the anchor's subtree up to its first child with a smaller id
                while pos < len(ids) and depth[pos] >= d and not (depth[pos] == d and ids[pos] < op.id):
                    pos += 1
                ids.insert(pos, op.id)
                ents.insert(pos, (op.id, op.ch, False))
                depth.insert(pos, d)
            elif op.kind == "delete":
                i = ids.index(op.target)
                ents[i] = (ents[i][0], ents[i][1], True)
        st[0] = key
        return ents

    def visible(self, r):
        return [e for e in self.seq_ids(r) if not e[2]]

    def text(self, r):
        return "".join(e[1] for e in self.visible(r))

    def elements(self, r):
        tags, cancelled = {}, set()
        for op in self._applied(r):
            if op.kind == "add":
                tags.setdefault(op.x, []).append(op.id)
            elif op.kind == "remove":
                cancelled.update(op.cancels)
        return sorted(x for x, ts in tags.items() if any(t not in cancelled for t in ts))

    def obs(self, r):
        return [self.elements(r), self.text(r), len(self.held[r])]

    # -- transitions
    def local(self, r, kind, arg, ch=None):
        op = Op()
        op.k, op.origin, op.kind = len(self.ops), r, kind
        op.id = (self.maxc[r] + 1, r)
        op.seq = self.vec[r][r] + 1
        op.deps = tuple(self.vec[r])
        op.x = op.ch = op.anchor = op.target = op.cancels = None
        if kind == "add":
            op.x = arg
        elif kind == "remove":
            op.x = arg
            op.cancels = frozenset(o.id for o in self._applied(r) if o.kind == "add" and o.x == arg)
        elif kind == "insert":
            vis = self.visible(r)
            op.anchor = HEAD if arg == 0 else vis[arg - 1][0]
            op.ch = ch
        else:
            op.target = self.visible(r)[arg][0]
        self.ops.append(op)
        self.by_origin[r].append(op)
        self._take(r, op)
        return op.k

    def _in_effect(self, r, op):
        return self.vec[r][op.origin] >= op.seq

    def _ready(self, r, op):
        v = self.vec[r]
        return v[op.origin] == op.seq - 1 and all(v[j] >= op.deps[j] for j in range(3))

    def _take(self, r, op):
        assert self.vec[r][op.origin] == op.seq - 1
        self.vec[r][op.origin] += 1
        self.maxc[r] = max(self.maxc[r], op.id[0])

    def deliver(self, r, k):
        op = self.ops[k]
        if self._in_effect(r, op):
            return
        self.held[r].add(k)
        # Take every held op that has become ready, until none is. The result
        # (the causal closure) does not depend on the order, so look up the
        # one candidate per origin -- the op with seq == vec[origin] + 1 --
        # instead of rescanning the whole buffer after each take (quadratic
        # over a long out-of-order delivery).
        cand = self._cand[r]                     # (origin, seq) -> k, over held[r]
        cand[(op.origin, op.seq)] = k
        progress = True
        while progress:
            progress = False
            for j in range(3):
                kk = cand.get((j, self.vec[r][j] + 1))
                if kk is not None and self._ready(r, self.ops[kk]):
                    del cand[(j, self.vec[r][j] + 1)]
                    self.held[r].discard(kk)
                    self._take(r, self.ops[kk])
                    progress = True


# --------------------------------------------------------------- scenarios
# A script is a list of steps run against fresh replicas 0, 1, 2:
#   ["L", r, "add"|"remove", x] / ["L", r, "insert", i, ch] / ["L", r, "delete", i]
#       -> local op; its message is remembered as message number k (0, 1, ...)
#   ["D", r, k]  -> replicas[r].deliver(message k)
#   ["Q", r]     -> observe [elements(), text(), pending()] of replica r
class Builder:
    """Script builder that tracks the oracle so local indices are valid."""

    def __init__(self):
        self.c = Cluster()
        self.s = []
        self.want = []

    def add(self, r, x):
        self.s.append(["L", r, "add", x]); return self.c.local(r, "add", x)

    def remove(self, r, x):
        self.s.append(["L", r, "remove", x]); return self.c.local(r, "remove", x)

    def ins(self, r, i, ch):
        self.s.append(["L", r, "insert", i, ch]); return self.c.local(r, "insert", i, ch)

    def type(self, r, i, word):
        ks = []
        for n, ch in enumerate(word):
            ks.append(self.ins(r, i + n, ch))
        return ks

    def dele(self, r, i):
        self.s.append(["L", r, "delete", i]); return self.c.local(r, "delete", i)

    def d(self, r, *ks):
        for k in ks:
            self.s.append(["D", r, k]); self.c.deliver(r, k)

    def q(self, *rs):
        for r in rs or (0, 1, 2):
            self.s.append(["Q", r])
            self.want.append(self.c.obs(r))

    def sync(self, rng=None):
        pairs = [(r, k) for r in range(3) for k in range(len(self.c.ops))]
        if rng:
            rng.shuffle(pairs)
        for r, k in pairs:
            self.d(r, k)

    def len(self, r):
        return len(self.c.visible(r))


def hand_cases():
    cs = []
    # 0: one writer, everyone catches up
    b = Builder(); ks = b.type(0, 0, "hi"); b.add(0, "p"); b.d(1, *ks); b.q(1); b.sync(); b.q(); cs.append(b)
    # 1: three concurrent inserts at HEAD with equal counters
    b = Builder(); b.ins(0, 0, "a"); b.ins(1, 0, "b"); b.ins(2, 0, "c"); b.q(); b.sync(); b.q(); cs.append(b)
    # 2: concurrent inserts after one anchor, one of them followed by its own typing
    b = Builder(); ks = b.type(0, 0, "ab"); b.d(1, *ks); b.d(2, *ks)
    y = b.ins(2, 1, "y"); x = b.ins(1, 1, "x"); z = b.ins(1, 2, "z")
    b.d(0, x, z); b.q(0); b.d(0, y); b.q(0); b.d(2, z); b.q(2); b.d(2, x); b.sync(); b.q(); cs.append(b)
    # 3: an op that is held must not advance the counter
    b = Builder(); a1 = b.ins(0, 0, "a"); a2 = b.ins(0, 0, "b"); b.d(1, a2); b.q(1)
    xk = b.ins(1, 0, "x"); b.q(1); b.d(1, a1); b.q(1); b.sync(); b.q(); cs.append(b)
    # 4: delete delivered before its insert
    b = Builder(); a = b.ins(0, 0, "a"); b.d(1, a); dk = b.dele(1, 0); b.d(2, dk); b.q(2); b.d(2, dk); b.q(2)
    b.d(2, a); b.q(2); b.sync(); b.q(); cs.append(b)
    # 5: dependency across the two types (insert depends on an add)
    b = Builder(); p = b.add(0, "p"); b.d(1, p); ik = b.ins(1, 0, "q"); b.d(2, ik); b.q(2); b.d(2, p); b.q(2)
    b.sync(); b.q(); cs.append(b)
    # 6: add wins over a concurrent remove; remove of an unobserved element
    b = Builder(); p = b.add(0, "p"); b.d(1, p); b.remove(0, "p"); b.add(1, "p"); b.remove(2, "q"); b.add(0, "q")
    b.q(); b.sync(); b.q(); b.remove(2, "p"); b.q(2); b.sync(); b.q(); cs.append(b)
    # 7: duplicates, own messages, duplicate of a held op
    b = Builder(); a = b.ins(0, 0, "a"); c2 = b.ins(0, 1, "b"); e = b.add(0, "e")
    b.d(0, a, c2, e); b.q(0); b.d(1, c2, c2, e, e); b.q(1); b.d(1, a, a); b.q(1); b.d(1, a, c2, e); b.q(1)
    b.d(2, e); b.d(2, e); b.q(2); b.sync(); b.d(2, a, c2, e); b.q(); cs.append(b)
    # 8: deleted elements stay anchors
    b = Builder(); ks = b.type(0, 0, "abc"); b.sync(); dk = b.dele(1, 1); xk = b.ins(2, 2, "x")
    b.d(0, xk); b.q(0); b.d(0, dk); b.q(0); b.sync(); b.q(); cs.append(b)
    # 9: transitive hold and cascade, with a local op while ops are held
    b = Builder(); o1 = b.add(0, "s"); b.d(1, o1); o2 = b.ins(1, 0, "m"); o3 = b.remove(1, "s")
    b.d(2, o3); b.d(2, o2); b.q(2); w = b.ins(2, 0, "w"); b.add(2, "t"); b.q(2); b.d(2, o1); b.q(2)
    b.sync(); b.q(); cs.append(b)
    # 10: a remove cancels only the tags its replica had seen
    b = Builder(); t1 = b.add(0, "p"); t2 = b.add(1, "p"); b.remove(0, "p"); b.q(0); b.sync(); b.q()
    b.remove(2, "p"); b.q(2); b.sync(); b.q(); cs.append(b)
    # 11: long sequential typing (deep anchor chains) on two replicas
    b = Builder(); b.type(0, 0, "".join(chr(97 + i % 26) for i in range(1300)))
    b.type(1, 0, "".join(chr(65 + i % 26) for i in range(1200))); b.q(0, 1)
    b.sync(random.Random(11)); b.dele(2, 5); b.ins(2, 1400, "!"); b.sync(); b.q(); cs.append(b)
    return cs


def random_case(rng, n_ops, fifo, p_local, p_query):
    b = Builder()
    alpha = "pqrs"[:rng.randint(2, 4)]
    sent = []                   # undelivered (dest, k), in send order
    done = []
    made = 0
    while made < n_ops or (sent and rng.random() < 0.5):
        u = rng.random()
        if made < n_ops and (u < p_local or not sent):
            r = rng.randrange(3)
            n = b.len(r)
            w = rng.random()
            if w < 0.22:
                k = b.add(r, rng.choice(alpha))
            elif w < 0.34:
                present = b.c.elements(r)
                x = rng.choice(present) if present and rng.random() < 0.8 else rng.choice(alpha)
                k = b.remove(r, x)
            elif w < 0.48 and n:
                k = b.dele(r, rng.randrange(n))
            else:
                i = rng.choice([0, n, rng.randint(0, n)])
                k = b.ins(r, i, rng.choice("abcdefghijklmnopqrstuvwxyz"))
            made += 1
            for dst in range(3):
                if dst != r:
                    sent.append((dst, k))
            if rng.random() < 0.03:
                sent.append((r, k))
        elif sent:
            if fifo:
                dst = rng.randrange(3)
                cand = [j for j, (d2, _) in enumerate(sent) if d2 == dst]
                j = cand[0] if cand else 0
            else:
                j = rng.randrange(len(sent))
            dst, k = sent.pop(j)
            b.d(dst, k)
            done.append((dst, k))
            if done and rng.random() < 0.08:
                b.d(*rng.choice(done))
        if rng.random() < p_query:
            b.q(rng.randrange(3))
    rng.shuffle(sent)
    for dst, k in sent:
        b.d(dst, k)
        if rng.random() < 0.05:
            b.d(*rng.choice(done or [(dst, k)]))
    b.q()
    return b


def all_cases():
    cs = hand_cases()
    rng = random.Random(0x175C2D7)
    for i in range(44):
        n = rng.choice([8, 15, 30, 60, 120, 250])
        cs.append(random_case(rng, n, fifo=(i % 4 == 3), p_local=rng.choice([0.3, 0.5, 0.8]),
                              p_query=rng.choice([0.02, 0.08, 0.2])))
    for n in (900, 1500):
        cs.append(random_case(rng, n, fifo=False, p_local=0.55, p_query=0.01))
    return cs


# ------------------------------------------------------------------ driver
DRIVER = r'''
import json, sys, types
out = sys.stdout
sys.stdout = sys.stderr
SRC = sys.argv[1]
cases = json.loads(sys.stdin.read())
try:
    with open(SRC) as f:
        CODE = compile(f.read(), SRC, "exec")   # from source: never a stale __pycache__
except BaseException as ex:
    CODE = ex
seq = [0]
def fresh(rid):
    if isinstance(CODE, BaseException):
        raise CODE
    # every replica gets its own module object: no shared module/class state
    seq[0] += 1
    name = "crdt_replica_%d" % seq[0]
    mod = types.ModuleType(name)
    mod.__file__ = SRC
    sys.modules[name] = mod
    exec(CODE, mod.__dict__)
    return mod.Replica(rid)
for script in cases:
    try:
        reps = [fresh(0), fresh(1), fresh(2)]
        msgs, obs = [], []
        for st in script:
            if st[0] == "L":
                m = getattr(reps[st[1]], st[2])(*st[3:])
                if not isinstance(m, str):
                    raise TypeError("message is not a str")
                msgs.append(m)
            elif st[0] == "D":
                reps[st[1]].deliver(msgs[st[2]])
            else:
                rp = reps[st[1]]
                e, t, p = rp.elements(), rp.text(), rp.pending()
                # exact types: elements() must be a list, already ascending
                obs.append([e if type(e) is list else repr(e), t if isinstance(t, str) else repr(t),
                            p if type(p) is int else repr(p)])
        out.write(json.dumps({"obs": obs}) + "\n")
    except BaseException as ex:
        out.write(json.dumps({"exc": type(ex).__name__}) + "\n")
    out.flush()
'''


def main():
    try:
        open(SRC).close()
    except OSError:
        fail(f"missing {SRC}")
    built = all_cases()
    cases = [b.s for b in built]
    want = [b.want for b in built]
    tmp = tempfile.mkdtemp(prefix="v175-")
    try:
        drv = os.path.join(tmp, "driver.py")       # no answers in it: inputs only
        with open(drv, "w") as f:
            f.write(DRIVER)
        r = vlib.run_solution([sys.executable, "-I", "-S", "-B", drv, SRC], input=json.dumps(cases),
                              cpu_s=RUN_CPU_S, wall_s=RUN_WALL_S, cwd=D)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if r.verdict in ("cpu", "wall"):
        fail(f"solution timed out ({r.verdict})")
    if r.verdict != "ok":
        fail(f"solution refused: {r.verdict}")
    lines = r.stdout.splitlines()
    for i, w in enumerate(want):
        if i >= len(lines):
            fail(f"solution crashed at case {i} (exit {r.returncode})")
        try:
            got = json.loads(lines[i])
        except ValueError:
            fail(f"bad driver output at case {i}")
        if "exc" in got:
            fail(f"exception at case {i}: {got['exc']}")
        if got["obs"] != w:
            fail(f"mismatch at case {i}")
    print(f"OK ({len(want)} scenarios)")


if __name__ == "__main__":
    main()
