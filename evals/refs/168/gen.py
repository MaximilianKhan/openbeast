# Task 168 hidden-history generator. This exact text is embedded in the
# validator of every 168 variant (build_task.py splices it in), so the
# validator and the authoring tools generate byte-identical input.
# Pure python, own PRNG (splitmix64): no dependence on `random`'s internals.

M64 = (1 << 64) - 1


class Rng:
    def __init__(self, seed):
        self.s = seed & M64

    def u64(self):
        self.s = (self.s + 0x9E3779B97F4A7C15) & M64
        z = self.s
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & M64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & M64
        return z ^ (z >> 31)

    def below(self, n):
        return self.u64() % n

    def rint(self, lo, hi):
        return lo + self.below(hi - lo + 1)

    def chance(self, p_per_1000):
        return self.below(1000) < p_per_1000


def gen_history(seed, n, nkeys, nclients, maxdur, nvals, npend, mutate):
    """One history as a list of op dicts. Built from a real sequential
    execution (so it is linearizable), then optionally mutated once."""
    r = Rng(seed)
    hot = max(1, nkeys // 4)
    keyids = [r.rint(0, 999999) for _ in range(nkeys)]
    while len(set(keyids)) != nkeys:
        keyids = [r.rint(0, 999999) for _ in range(nkeys)]
    vals = [r.rint(1, 10**9) for _ in range(nvals)]
    clock = [r.rint(0, 3) for _ in range(nclients)]
    pend_left = npend
    ops = []
    for _ in range(n):
        c = r.below(nclients)
        call = clock[c] + (0 if r.chance(300) else r.rint(1, 3))
        ret = call + r.rint(0, maxdur)
        u = r.below(10)
        lin = call if u < 3 else ret if u < 6 else r.rint(call, ret)
        key = keyids[r.below(hot)] if r.chance(600) else keyids[r.below(nkeys)]
        u = r.below(100)
        kind = "GET" if u < 40 else "PUT" if u < 70 else "CAS" if u < 90 else "DEL"
        op = {"call": call, "ret": ret, "lin": lin, "tie": r.u64(), "kind": kind,
              "key": key, "pending": False, "eff": True}
        if kind == "GET" and r.chance(15):
            op["pending"] = True
            op["eff"] = False
            clock[c] = call + r.rint(0, maxdur)
        elif pend_left and kind != "GET" and r.chance(80):
            pend_left -= 1
            op["pending"] = True
            op["eff"] = r.chance(500)
            clock[c] = call + r.rint(0, maxdur)      # the client is replaced
        else:
            clock[c] = ret
        ops.append(op)
    # sequential execution in linearization-point order
    state = {}
    for op in sorted(ops, key=lambda o: (o["lin"], o["tie"])):
        k, cur = op["key"], state.get(op["key"])
        kind = op["kind"]
        if kind == "GET":
            op["res"] = cur
        elif kind == "PUT":
            op["a"] = vals[r.below(nvals)]
            if op["eff"]:
                state[k] = op["a"]
        elif kind == "DEL":
            if op["eff"]:
                state.pop(k, None)
        else:
            op["a"] = cur if (cur is not None and r.chance(550)) else vals[r.below(nvals)]
            op["b"] = vals[r.below(nvals)]
            hit = cur is not None and cur == op["a"]
            op["res"] = "OK" if hit else "FAIL"
            if hit and op["eff"]:
                state[k] = op["b"]
    if mutate:
        done = [o for o in ops if not o["pending"]]
        for _ in range(64):
            o = done[r.below(len(done))]
            u = r.below(3)
            if u == 0 and o["kind"] == "GET":
                seen = sorted({x["a"] for x in ops if x["key"] == o["key"] and x["kind"] in ("PUT", "CAS")} |
                              {x["b"] for x in ops if x["key"] == o["key"] and x["kind"] == "CAS"})
                cand = [None] + seen
                nv = cand[r.below(len(cand))]
                if nv != o["res"]:
                    o["res"] = nv
                    break
            elif u == 1 and o["kind"] == "CAS":
                o["res"] = "FAIL" if o["res"] == "OK" else "OK"
                break
            elif u == 2 and o["ret"] > o["call"]:
                if r.chance(500):
                    o["ret"] = o["call"] + r.below(o["ret"] - o["call"])
                else:
                    o["call"] = o["ret"] - r.below(o["ret"] - o["call"])
                break
    return ops


def fmt_op(o):
    ret = "?" if o["pending"] else str(o["ret"])
    s = f'{o["call"]} {ret} {o["kind"]} {o["key"]}'
    if o["kind"] == "PUT":
        s += f' {o["a"]}'
    elif o["kind"] == "CAS":
        s += f' {o["a"]} {o["b"]}'
    if not o["pending"]:
        if o["kind"] == "GET":
            s += " NIL" if o["res"] is None else f' {o["res"]}'
        elif o["kind"] == "CAS":
            s += f' {o["res"]}'
    return s


def shuffle(xs, r):
    for i in range(len(xs) - 1, 0, -1):
        j = r.below(i + 1)
        xs[i], xs[j] = xs[j], xs[i]


def render(histories, seed=0x168):
    """histories: list of op lists. Ops are emitted in a shuffled order:
    the input order carries no information."""
    r = Rng(seed)
    out = [str(len(histories))]
    for ops in histories:
        lines = ops.split("\n") if isinstance(ops, str) else [fmt_op(o) for o in ops]
        shuffle(lines, r)
        out.append(str(len(lines)))
        out.extend(lines)
    return "\n".join(out) + "\n"


def build(specs):
    """specs: list of (seed, n, nkeys, nclients, maxdur, nvals, npend, mutate)
    tuples, or literal histories given as newline-joined op lines."""
    return render([s if isinstance(s, str) else gen_history(*s) for s in specs])
