"""Oracle for 190_autograd_mlp (embedded in the validator only).

* `run(prog, override)` evaluates a program FORWARD ONLY, on flat row-major
  float lists, and returns (node values, node shapes, kink signature).
  The signature records every relu sign and every max argmax/second-best
  gap, so a finite-difference step that crosses a kink is detected.
* `fd_grads(prog)` = central differences, h = 1e-5, of sum(losses) w.r.t.
  every requires_grad leaf element. A case whose FD steps change the kink
  signature, or whose values leave a sane range, is rejected by the seeded
  generator (deterministically), so the expected gradients are smooth.
* `mlp_oracle` trains the 3-layer MLP with hand-derived backprop.
* `wide_case` is the one exception to finite differences: a 2000-element
  leaf whose exact gradient comes from per-element forward-mode tangents
  plus a closed-form tail (FD there is what a cheating backward would do,
  and it must not fit the time limit).
There is no reverse-mode code here.
"""
import math
import random

H_FD = 1e-5


def prod(s):
    p = 1
    for d in s:
        p *= d
    return p


def strides(shape):
    st, acc = [0] * len(shape), 1
    for i in range(len(shape) - 1, -1, -1):
        st[i] = acc
        acc *= shape[i]
    return st


def bshape(a, b):
    n = max(len(a), len(b))
    a = [1] * (n - len(a)) + list(a)
    b = [1] * (n - len(b)) + list(b)
    out = []
    for x, y in zip(a, b):
        if x != y and x != 1 and y != 1:
            return None
        out.append(max(x, y) if 1 not in (x, y) else x * y)
    return out


def emap(src, tgt):
    src = [1] * (len(tgt) - len(src)) + list(src)
    st = strides(src)
    idx = [0]
    for d, n in enumerate(tgt):
        s = st[d] if src[d] != 1 else 0
        idx = [i + k * s for i in idx for k in range(n)]
    return idx


def axes_of(axis, nd):
    if axis is None:
        return list(range(nd))
    if isinstance(axis, int):
        axis = [axis]
    return sorted(a % nd for a in axis)


class Reject(Exception):
    pass


def run(prog, override=None):
    ins = prog["ins"]
    V, S, sig = [], [], []
    for n, op in enumerate(ins):
        k = op[0]
        if k == "leaf":
            v = list(op[2])
            if override and n in override:
                v = override[n]
            V.append(v)
            S.append(list(op[1]))
            continue
        if k == "bin":
            _, c, i, j = op
            s = bshape(S[i], S[j])
            ma, mb = emap(S[i], s), emap(S[j], s)
            A, B = V[i], V[j]
            if c == "+":
                v = [A[p] + B[q] for p, q in zip(ma, mb)]
            elif c == "-":
                v = [A[p] - B[q] for p, q in zip(ma, mb)]
            elif c == "*":
                v = [A[p] * B[q] for p, q in zip(ma, mb)]
            else:
                v = [A[p] / B[q] for p, q in zip(ma, mb)]
        elif k == "sc":
            _, c, i, x, rev = op
            A = V[i]
            s = S[i]
            if c == "+":
                v = [a + x for a in A]
            elif c == "*":
                v = [a * x for a in A]
            elif c == "-":
                v = [x - a for a in A] if rev else [a - x for a in A]
            else:
                v = [x / a for a in A] if rev else [a / x for a in A]
        elif k == "pow":
            _, i, p = op
            v = [a ** p for a in V[i]]
            s = S[i]
        elif k == "un":
            _, f, i = op
            A, s = V[i], S[i]
            if f == "neg":
                v = [-a for a in A]
            elif f == "exp":
                v = [math.exp(a) for a in A]
            elif f == "log":
                v = [math.log(a) for a in A]
            elif f == "tanh":
                v = [math.tanh(a) for a in A]
            else:
                v = [a if a > 0 else 0.0 for a in A]
                sig.append(tuple(a > 0 for a in A))
                sig.append(min(abs(a) for a in A))
        elif k == "red":
            _, f, i, axis, keep = op
            A, sh = V[i], S[i]
            ax = axes_of(axis, len(sh))
            ks = [1 if d in ax else m for d, m in enumerate(sh)]
            m = emap(ks, sh)
            no = prod(ks)
            cnt = len(A) // no
            groups = [[] for _ in range(no)]
            for p, a in enumerate(A):
                groups[m[p]].append((a, p))
            if f == "sum":
                v = [math.fsum(a for a, _ in g) for g in groups]
            elif f == "mean":
                v = [math.fsum(a for a, _ in g) / cnt for g in groups]
            elif f == "max":
                v = []
                for g in groups:
                    srt = sorted(g, reverse=True)
                    v.append(srt[0][0])
                    sig.append(srt[0][1])
                    if len(srt) > 1:
                        sig.append(srt[0][0] - srt[1][0])
            else:
                v = []
                for g in groups:
                    mx = max(a for a, _ in g)
                    v.append(mx + math.log(math.fsum(math.exp(a - mx) for a, _ in g)))
            s = ks if keep else [m2 for d, m2 in enumerate(sh) if d not in ax]
        elif k == "reshape":
            _, i, shp = op
            shp = list(shp)
            if -1 in shp:
                q = shp.index(-1)
                shp[q] = len(V[i]) // prod(x for t, x in enumerate(shp) if t != q)
            v, s = list(V[i]), shp
        elif k == "T":
            _, i = op
            sh = S[i]
            if len(sh) < 2:
                v, s = list(V[i]), list(sh)
            else:
                st = list(reversed(strides(sh)))
                s = list(reversed(sh))
                idx = [0]
                for d, m2 in enumerate(s):
                    idx = [a + t * st[d] for a in idx for t in range(m2)]
                v = [V[i][a] for a in idx]
        elif k == "mm":
            _, i, j = op
            sa, sb = list(S[i]), list(S[j])
            va, vb = len(sa) == 1, len(sb) == 1
            if va:
                sa = [1] + sa
            if vb:
                sb = sb + [1]
            nn, kk, mm = sa[-2], sa[-1], sb[-1]
            bs = bshape(sa[:-2], sb[:-2])
            mpa, mpb = emap(sa[:-2], bs), emap(sb[:-2], bs)
            A, B = V[i], V[j]
            v = []
            for ia, ib in zip(mpa, mpb):
                for r in range(nn):
                    for c in range(mm):
                        v.append(math.fsum(A[ia * nn * kk + r * kk + t] * B[ib * kk * mm + t * mm + c]
                                           for t in range(kk)))
            s = list(bs) + ([] if va else [nn]) + ([] if vb else [mm])
        elif k == "ce":
            _, i, tg = op
            nn, cc = S[i]
            A = V[i]
            tot = []
            for r in range(nn):
                row = A[r * cc:(r + 1) * cc]
                mx = max(row)
                tot.append(mx + math.log(math.fsum(math.exp(a - mx) for a in row)) - row[tg[r]])
            v, s = [math.fsum(tot) / nn], []
        else:
            raise ValueError(k)
        V.append(v)
        S.append(s)
    return V, S, sig


def total(prog, V):
    return math.fsum(V[i][0] for i in prog["losses"])


def sig_eq(a, b):
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if isinstance(x, float):
            if (x > 0) != (y > 0):    # a gap or margin closed: kink crossed
                return False
        elif x != y:
            return False
    return True


def fd_grads(prog):
    V, S, sig = run(prog)
    for v in V:
        for a in v:
            if not math.isfinite(a) or abs(a) > 1e4:
                raise Reject("range")
    for x in sig:
        if isinstance(x, float) and x < 1e-3:
            raise Reject("kink")
    out = {}
    for n, op in enumerate(prog["ins"]):
        if op[0] != "leaf" or not op[3]:
            continue
        g = []
        base = op[2]
        for e in range(len(base)):
            h = H_FD
            vals = []
            for sgn in (1, -1):
                x = list(base)
                x[e] = base[e] + sgn * h
                V2, _, sig2 = run(prog, {n: x})
                if not sig_eq(sig, sig2):
                    raise Reject("fd crossed a kink")
                vals.append(total(prog, V2))
            g.append((vals[0] - vals[1]) / (2 * h))
        out[n] = g
    return V, S, out


# ---------------------------------------------------------------- generator
class Gen:
    def __init__(self, rng):
        self.r = rng
        self.ins, self.S = [], []

    def emit(self, op, shape):
        self.ins.append(op)
        self.S.append(list(shape))
        return len(self.ins) - 1

    def leaf(self, shape, rg=True, lo=-2.0, hi=2.0):
        vals = [round(self.r.uniform(lo, hi), 6) for _ in range(prod(shape))]
        return self.emit(["leaf", list(shape), vals, rg], shape)

    def rshape(self, rank=None):
        rank = self.r.choice([0, 1, 1, 2, 2, 2, 3, 3]) if rank is None else rank
        return [self.r.choice([1, 2, 2, 3, 3, 4]) for _ in range(rank)]

    def partner(self, s):
        """A shape that broadcasts with s (dims dropped / set to 1 / extra)."""
        r = self.r
        t = list(s)
        if t and r.random() < 0.5:
            t = t[r.randint(0, len(t)):]          # drop leading dims
        t = [1 if (d > 1 and r.random() < 0.35) else d for d in t]
        if len(t) < 3 and r.random() < 0.25:
            t = [r.choice([2, 3])] + t           # extra leading dim
        if len(s) and r.random() < 0.15:
            # set a dim of s that is 1 to something bigger (both expand)
            t = [1] * (len(s) - len(t)) + t
            for d in range(len(s)):
                if s[d] == 1 and r.random() < 0.5:
                    t[d] = r.choice([2, 3])
        return t

    def safe_pos(self, i):
        """x*x + c  (strictly positive)."""
        sq = self.emit(["bin", "*", i, i], self.S[i])
        return self.emit(["sc", "+", sq, self.r.choice([0.5, 1.0]), False], self.S[i])

    def step(self, pool):
        r, S = self.r, self.S
        i = r.choice(pool)
        s = S[i]
        kind = r.choice(["bin"] * 5 + ["sc"] * 2 + ["un"] * 3 + ["pow", "red", "red", "red",
                                                                "reshape", "T", "mm", "mm"])
        if kind == "bin":
            c = r.choice("+-*/")
            cand = [j for j in pool if bshape(S[j], s) is not None and j != i]
            if cand and r.random() < 0.5:
                j = r.choice(cand)
            else:
                j = self.leaf(self.partner(s), rg=r.random() < 0.85)
            if r.random() < 0.5:
                i, j = j, i
            bs = bshape(S[i], S[j])
            if bs is None or prod(bs) > 48 or len(bs) > 3:
                return None
            if c == "/":
                j = self.safe_pos(j)
            return self.emit(["bin", c, i, j], bs)
        if kind == "sc":
            c = r.choice("+-*/")
            x = round(r.uniform(0.5, 2.5) * r.choice([1, -1]), 3)
            rev = r.random() < 0.5
            if c == "/" and rev:
                i = self.safe_pos(i)
            return self.emit(["sc", c, i, x, rev], s)
        if kind == "un":
            f = r.choice(["neg", "exp", "log", "tanh", "relu", "relu"])
            if f == "exp":
                i = self.emit(["un", "tanh", i], s)
            if f == "log":
                i = self.safe_pos(i)
            return self.emit(["un", f, i], s)
        if kind == "pow":
            p = r.choice([2, 3, 0.5, -1, -2, 1.5])
            if p not in (2, 3):
                i = self.safe_pos(i)
            return self.emit(["pow", i, p], s)
        if kind == "red":
            nd = len(s)
            f = r.choice(["sum", "mean", "max", "logsumexp"])
            if nd == 0:
                axis = None
            else:
                u = r.random()
                if u < 0.25:
                    axis = None
                elif u < 0.7 or nd == 1:
                    axis = r.randrange(-nd, nd)
                else:
                    a, b = r.sample(range(nd), 2)
                    axis = [a - nd if r.random() < 0.4 else a, b]
            keep = r.random() < 0.5
            ax = axes_of(axis, nd)
            out = [1 if d in ax else m for d, m in enumerate(s)] if keep else \
                [m for d, m in enumerate(s) if d not in ax]
            return self.emit(["red", f, i, axis, keep], out)
        if kind == "reshape":
            n = prod(s)
            fac = [d for d in range(1, n + 1) if n % d == 0]
            a = r.choice(fac)
            shp = [a, n // a]
            if r.random() < 0.3 and n // a > 1:
                b = r.choice([d for d in range(1, n // a + 1) if (n // a) % d == 0])
                shp = [a, b, n // a // b]
            if r.random() < 0.3:
                shp = [n]
            if r.random() < 0.5:
                shp[r.randrange(len(shp))] = -1
            real = list(shp)
            if -1 in real:
                q = real.index(-1)
                real[q] = n // prod(x for t, x in enumerate(real) if t != q)
            return self.emit(["reshape", i, shp], real)
        if kind == "T":
            return self.emit(["T", i], list(reversed(s)))
        # matmul
        if not s:
            return None
        u = r.random()
        if u < 0.5:            # s on the left
            kdim = s[-1]
            m = r.choice([1, 2, 3])
            rshape = r.choice([[kdim, m], [kdim], [r.choice([1, 2]), kdim, m]])
            if len(s) == 1 and len(rshape) == 1 and r.random() < 0.5:
                rshape = [kdim, m]
            j = self.leaf(rshape)
            a, b = i, j
        else:                  # s on the right
            kdim = s[-2] if len(s) >= 2 else s[0]
            lshape = r.choice([[r.choice([1, 2, 3]), kdim], [kdim], [r.choice([1, 2]), 2, kdim]])
            j = self.leaf(lshape)
            a, b = j, i
        sa, sb = list(S[a]), list(S[b])
        va, vb = len(sa) == 1, len(sb) == 1
        sa2 = [1] + sa if va else sa
        sb2 = sb + [1] if vb else sb
        bs = bshape(sa2[:-2], sb2[:-2])
        if bs is None:
            return None
        out = list(bs) + ([] if va else [sa2[-2]]) + ([] if vb else [sb2[-1]])
        if len(out) > 3 or prod(out) > 48:
            return None
        return self.emit(["mm", a, b], out)

    def finish(self, outs):
        """loss_k = sum(out_k * C_k) with a constant coefficient tensor."""
        losses = []
        for o in outs:
            s = self.S[o]
            c = self.leaf(s, rg=False, lo=-1.5, hi=1.5)
            p = self.emit(["bin", "*", o, c], s)
            losses.append(self.emit(["red", "sum", p, None, False], []))
        return losses


def leaf_elems(ins):
    return sum(len(op[2]) for op in ins if op[0] == "leaf" and op[3])


def random_case(rng):
    g = Gen(rng)
    pool = [g.leaf(g.rshape()) for _ in range(rng.choice([1, 2, 2, 3]))]
    if rng.random() < 0.3:
        pool.append(g.leaf(g.rshape(), rg=False))
    for _ in range(rng.randint(4, 10)):
        n = g.step(pool)
        if n is not None:
            pool.append(n)
            if g.S[n] and prod(g.S[n]) > 48:
                raise Reject("big")
    if rng.random() < 0.15:
        # cross-entropy on a rank-2 node (or a fresh leaf)
        cand = [j for j in pool if len(g.S[j]) == 2]
        x = rng.choice(cand) if cand else g.leaf([3, 4])
        nn, cc = g.S[x]
        pool.append(g.emit(["ce", x, [rng.randrange(cc) for _ in range(nn)]], []))
    outs = [pool[-1]]
    if rng.random() < 0.35:
        mid = [j for j in pool[:-1] if g.ins[j][0] != "leaf"]
        if mid:
            outs.append(rng.choice(mid))          # second loss sharing the graph
    if rng.random() < 0.1:
        outs.append(outs[0])                      # same root twice
    losses = g.finish(outs)
    if leaf_elems(g.ins) > 28 or leaf_elems(g.ins) == 0:
        raise Reject("size")
    return {"ins": g.ins, "losses": losses}


def fixed_cases():
    """Hand-picked semantics probes (still checked numerically)."""
    r = random.Random(1907)
    cs = []

    def mk(build):
        g = Gen(r)
        outs = build(g)
        return {"ins": g.ins, "losses": g.finish(outs)}

    # bias broadcast (B,H) + (H,)
    cs.append(mk(lambda g: [g.emit(["bin", "+", g.leaf([4, 3]), g.leaf([3])], [4, 3])]))
    # (B,1) - (B,C) from a keepdims reduction
    def kd(g):
        x = g.leaf([3, 4])
        m = g.emit(["red", "mean", x, -1, True], [3, 1])
        return [g.emit(["un", "tanh", g.emit(["bin", "-", m, x], [3, 4])], [3, 4])]
    cs.append(mk(kd))
    # both operands expand: (3,1) * (1,4)
    cs.append(mk(lambda g: [g.emit(["bin", "*", g.leaf([3, 1]), g.leaf([1, 4])], [3, 4])]))
    # matmul 1-D cases
    cs.append(mk(lambda g: [g.emit(["mm", g.leaf([3]), g.leaf([3])], [])]))
    cs.append(mk(lambda g: [g.emit(["mm", g.leaf([3]), g.leaf([3, 2])], [2])]))
    cs.append(mk(lambda g: [g.emit(["mm", g.leaf([2, 3]), g.leaf([3])], [2])]))
    cs.append(mk(lambda g: [g.emit(["mm", g.leaf([2, 2, 3]), g.leaf([3, 2])], [2, 2, 2])]))
    cs.append(mk(lambda g: [g.emit(["mm", g.leaf([1, 2, 3]), g.leaf([3, 3, 2])], [3, 2, 2])]))
    cs.append(mk(lambda g: [g.emit(["mm", g.leaf([3]), g.leaf([2, 3, 2])], [2, 2])]))
    # rank-3 transpose + reshape(-1)
    def tr(g):
        x = g.leaf([2, 3, 4])
        t = g.emit(["T", x], [4, 3, 2])
        return [g.emit(["reshape", t, [-1, 6]], [4, 6])]
    cs.append(mk(tr))
    # reflected scalar ops and negative powers
    def refl(g):
        x = g.leaf([2, 3])
        a = g.emit(["sc", "-", x, 2.0, True], [2, 3])             # 2 - x
        p = g.safe_pos(x)
        b = g.emit(["sc", "/", p, 3.0, True], [2, 3])             # 3 / (x*x+c)
        c = g.emit(["pow", p, -1.5], [2, 3])
        return [g.emit(["bin", "*", g.emit(["bin", "+", a, b], [2, 3]), c], [2, 3])]
    cs.append(mk(refl))
    # constant leaf next to a trainable one: its grad must stay None
    cs.append(mk(lambda g: [g.emit(["bin", "*", g.leaf([2, 3]), g.leaf([2, 3], rg=False)], [2, 3])]))
    # tuple axis, negative axis, keepdims, every reduction
    def reds(g):
        x = g.leaf([2, 3, 4])
        a = g.emit(["red", "sum", x, [0, -1], True], [1, 3, 1])
        b = g.emit(["red", "max", x, 1, False], [2, 4])
        c = g.emit(["red", "logsumexp", x, [1, -3], False], [4])
        d = g.emit(["red", "mean", x, -2, True], [2, 1, 4])
        e = g.emit(["bin", "+", g.emit(["bin", "*", a, d], [2, 3, 4]), c], [2, 3, 4])
        return [e, b]
    cs.append(mk(reds))
    # same root backward twice; and two losses sharing an intermediate
    def twice(g):
        x = g.leaf([3])
        h = g.emit(["un", "tanh", g.emit(["bin", "*", x, g.leaf([3])], [3])], [3])
        return [h, h, g.emit(["pow", h, 3], [3])]
    cs.append(mk(twice))
    return cs


def stability_cases(rng):
    """Huge logits; the values stay well defined. |offset| >= 750 is past
    exp's overflow (709.8) and its underflow to 0.0 (-745.1) even after the
    +-2 leaf, so a logsumexp without the max shift breaks on EVERY one of the
    ten, whatever the seed: OverflowError on a positive offset, ValueError
    (log of 0.0) on a negated one. (With 700, a seed could draw offsets a
    naive exp survives, which made the first failing case seed-dependent.)"""
    cs = []
    for t in range(10):
        g = Gen(rng)
        n, c = rng.choice([(2, 3), (3, 4), (4, 2), (1, 5)])
        x = g.leaf([n, c])
        off = g.leaf([n, 1], rg=False, lo=750.0, hi=1000.0)
        if t % 2:
            off = g.emit(["un", "neg", off], [n, 1])
        z = g.emit(["bin", "+", x, off], [n, c])
        if t < 5:
            out = g.emit(["ce", z, [rng.randrange(c) for _ in range(n)]], [])
        else:
            ax, keep = rng.choice([1, -1, None]), rng.random() < 0.5
            shp = ([n, 1] if keep else [n]) if ax is not None else ([1, 1] if keep else [])
            out = g.emit(["red", "logsumexp", z, ax, keep], shp)
        cs.append({"ins": g.ins, "losses": g.finish([out])})
    return cs


def fan_cases(rng):
    """One leaf used hundreds of times (diamonds everywhere)."""
    cs = []
    for t in range(6):
        g = Gen(rng)
        x = g.leaf([rng.choice([1, 2, 3])])
        acc = x
        for q in range(150):
            y = g.emit(["un", "tanh", g.emit(["sc", "*", x, round(0.01 * (q % 7 + 1), 3), False],
                                             g.S[x])], g.S[x])
            acc = g.emit(["bin", "+" if q % 2 else "-", acc, y], g.S[x])
        cs.append({"ins": g.ins, "losses": g.finish([acc])})
    return cs


def deep_case(steps=5000):
    """x_{k+1} = x_k + 0.001 * tanh(0.9 * x_k): a 20000-node chain."""
    g = Gen(random.Random(190))
    x = g.leaf([2])
    for _ in range(steps):
        t = g.emit(["un", "tanh", g.emit(["sc", "*", x, 0.9, False], [2])], [2])
        x = g.emit(["bin", "+", x, g.emit(["sc", "*", t, 0.001, False], [2])], [2])
    return {"ins": g.ins, "losses": g.finish([x]), "deep": True}


def wide_case(N=50, D=40, M=3, steps=125):
    """A 2000-element leaf through 500 broadcast/elementwise ops, then
    + bias (D,), @ W (D, M), * C and a sum. 2120 trainable elements, each
    of which a finite-difference backward would need two full forward
    passes (about 1e6 element operations each) for, so only a real
    reverse pass fits the time limit. The expected values are cheap and
    exact: the chain is elementwise, so each element's derivative comes from
    forward-mode (value, tangent) propagation, and the bias / matmul / sum
    tail has a closed form."""
    rng = random.Random(0x190D1DE)
    g = Gen(rng)
    x0 = g.leaf([N, D], lo=-1.5, hi=1.5)
    coefs = []
    x = x0
    for _ in range(steps):
        c = g.leaf([D], rg=False, lo=0.5, hi=1.5)
        coefs.append(g.ins[c][2])
        u = g.emit(["bin", "*", x, c], [N, D])
        t = g.emit(["un", "tanh", u], [N, D])
        s = g.emit(["sc", "*", t, 0.05, False], [N, D])
        x = g.emit(["bin", "+", x, s], [N, D])
    b = g.leaf([D])
    z = g.emit(["bin", "+", x, b], [N, D])
    w = g.leaf([D, M], lo=-1.0, hi=1.0)
    mmn = g.emit(["mm", z, w], [N, M])
    (loss,) = g.finish([mmn])
    C = g.ins[loss - 2][2]              # finish(): coefficient leaf, product, sum
    X0, B, W = g.ins[x0][2], g.ins[b][2], g.ins[w][2]
    Z, dX = [], []
    for e, v in enumerate(X0):
        d, dv = e % D, 1.0
        for cs in coefs:
            cd = cs[d]
            t = math.tanh(v * cd)
            dv *= 1.0 + 0.05 * cd * (1.0 - t * t)
            v = v + t * 0.05
        Z.append(v + B[d])
        dX.append(dv)
    GZ = [math.fsum(C[n * M + m] * W[d * M + m] for m in range(M))
          for n in range(N) for d in range(D)]
    P = [math.fsum(Z[n * D + d] * W[d * M + m] for d in range(D))
         for n in range(N) for m in range(M)]
    L = math.fsum(p * c for p, c in zip(P, C))
    G = {x0: [dX[e] * GZ[e] for e in range(N * D)],
         b: [math.fsum(GZ[n * D + d] for n in range(N)) for d in range(D)],
         w: [math.fsum(Z[n * D + d] * C[n * M + m] for n in range(N))
             for d in range(D) for m in range(M)]}
    case = {"ins": g.ins, "losses": [loss], "deep": True}
    return case, {loss: [L]}, g.S, G


N_RANDOM_OUT = 174   # accepted programs before the stability/fan/deep/wide tail


def _accept(c):
    try:
        V, S, G = fd_grads(c)
    except Reject:
        return None
    return (c, V, S, G)


def build_cases(seed):
    """The hidden programs. `seed` is drawn fresh by the validator on every
    run (it is never written anywhere the solution can read), so a copy of
    this file cannot rebuild the programs; only the hand-picked probes, the
    deep chain and the wide case are fixed. The count is always 192."""
    rng = random.Random(seed)
    out = []
    for c in fixed_cases():
        r = _accept(c)
        if r is None:
            raise Reject("fixed probe")
        out.append(r)
    while len(out) < N_RANDOM_OUT:
        try:
            r = _accept(random_case(rng))
        except Reject:
            continue
        if r is not None:
            out.append(r)
    for make, want in ((stability_cases, 10), (fan_cases, 6)):
        got = 0
        while got < want:
            for c in make(rng):
                if got < want:
                    r = _accept(c)
                    if r is not None:
                        out.append(r)
                        got += 1
    r = _accept(deep_case())
    if r is None:
        raise Reject("deep")
    out.append(r)
    out.append(wide_case())
    return out


# ---------------------------------------------------------------- MLP
def spiral(rng, n_per=20, classes=3):
    X, y = [], []
    for c in range(classes):
        for i in range(n_per):
            r = i / n_per + 0.05
            t = c * 4.0 + 4.0 * i / n_per + rng.gauss(0, 0.2)
            X.append([round(r * math.sin(t), 6), round(r * math.cos(t), 6)])
            y.append(c)
    return X, y


def mlp_problem(seed=0x190F00D):
    rng = random.Random(seed)
    X, y = spiral(rng)
    D, H1, H2, C = 2, 12, 10, 3

    def mat(a, b, sc):
        return [[round(rng.gauss(0, sc), 6) for _ in range(b)] for _ in range(a)]
    params = [mat(D, H1, 1.0), [round(rng.uniform(-0.1, 0.1), 6) for _ in range(H1)],
              mat(H1, H2, 0.4), [round(rng.uniform(0.0, 0.1), 6) for _ in range(H2)],
              mat(H2, C, 0.4), [0.0] * C]
    return {"X": X, "y": y, "params": params, "lr": 0.5, "steps": 60}


def _mm(A, B, n, k, m):
    return [[math.fsum(A[i][t] * B[t][j] for t in range(k)) for j in range(m)] for i in range(n)]


def mlp_oracle(prob):
    X, y, lr, steps = prob["X"], prob["y"], prob["lr"], prob["steps"]
    W1, b1, W2, b2, W3, b3 = [([list(r) for r in p] if isinstance(p[0], list) else list(p))
                              for p in prob["params"]]
    N, D = len(X), len(X[0])
    H1, H2, C = len(b1), len(b2), len(b3)

    def fwd():
        z1 = _mm(X, W1, N, D, H1)
        h1 = [[math.tanh(z1[i][j] + b1[j]) for j in range(H1)] for i in range(N)]
        z2 = _mm(h1, W2, N, H1, H2)
        z2 = [[z2[i][j] + b2[j] for j in range(H2)] for i in range(N)]
        h2 = [[v if v > 0 else 0.0 for v in row] for row in z2]
        z3 = _mm(h2, W3, N, H2, C)
        z3 = [[z3[i][j] + b3[j] for j in range(C)] for i in range(N)]
        loss, P = [], []
        for i in range(N):
            mx = max(z3[i])
            e = [math.exp(v - mx) for v in z3[i]]
            s = math.fsum(e)
            loss.append(mx + math.log(s) - z3[i][y[i]])
            P.append([v / s for v in e])
        return math.fsum(loss) / N, h1, z2, h2, P

    losses = []
    for _ in range(steps):
        L, h1, z2, h2, P = fwd()
        losses.append(L)
        d3 = [[(P[i][j] - (1.0 if j == y[i] else 0.0)) / N for j in range(C)] for i in range(N)]
        gW3 = [[math.fsum(h2[i][a] * d3[i][j] for i in range(N)) for j in range(C)] for a in range(H2)]
        gb3 = [math.fsum(d3[i][j] for i in range(N)) for j in range(C)]
        dh2 = [[math.fsum(d3[i][j] * W3[a][j] for j in range(C)) for a in range(H2)] for i in range(N)]
        d2 = [[dh2[i][a] if z2[i][a] > 0 else 0.0 for a in range(H2)] for i in range(N)]
        gW2 = [[math.fsum(h1[i][a] * d2[i][j] for i in range(N)) for j in range(H2)] for a in range(H1)]
        gb2 = [math.fsum(d2[i][j] for i in range(N)) for j in range(H2)]
        dh1 = [[math.fsum(d2[i][j] * W2[a][j] for j in range(H2)) for a in range(H1)] for i in range(N)]
        d1 = [[dh1[i][a] * (1 - h1[i][a] ** 2) for a in range(H1)] for i in range(N)]
        gW1 = [[math.fsum(X[i][a] * d1[i][j] for i in range(N)) for j in range(H1)] for a in range(D)]
        gb1 = [math.fsum(d1[i][j] for i in range(N)) for j in range(H1)]
        for P_, G_ in ((W1, gW1), (W2, gW2), (W3, gW3)):
            for a in range(len(P_)):
                for j in range(len(P_[0])):
                    P_[a][j] -= lr * G_[a][j]
        for P_, G_ in ((b1, gb1), (b2, gb2), (b3, gb3)):
            for j in range(len(P_)):
                P_[j] -= lr * G_[j]
    losses.append(fwd()[0])
    return losses, [W1, b1, W2, b2, W3, b3]
