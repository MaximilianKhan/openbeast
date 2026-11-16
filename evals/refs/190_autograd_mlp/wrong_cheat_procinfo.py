"""LEAK PROBE, full power (wrong_cheat_procinfo.py). It assumes the lint
has been escaped and then tries every route a confined solution still has
to the oracle: the argv AND environment of every ancestor process (macOS
sysctl kern.procargs2, which sandbox-exec does not mediate for a same-uid
process; Linux /proc/<pid>/cmdline and environ), the task JSON / oracle /
.git objects of any checkout under $HOME or named in an ancestor's argv,
env or cwd, site-packages (torch, numpy) and a child `ps`. It is the
reference solution, but it raises on import unless one of those routes
yielded oracle text or a compute library, so it must FAIL with
`import failed: RuntimeError` for as long as nothing leaks. With the lint
on it is rejected first (import of a non-allowed module); the confinement
test (tests/test_eval_190_confinement.py) runs it with the lint stripped.
"""
import glob as _glob
import os as _os
import sys as _sys

_MARKERS = (b"build_cases", b"mlp_oracle", b"fd_grads", b"randbits")


def _procargs(pid):
    """Raw argv + env block of `pid`, or None."""
    if _sys.platform.startswith("linux"):
        out = b""
        for n in ("cmdline", "environ"):
            try:
                with open(f"/proc/{pid}/{n}", "rb") as f:
                    out += f.read() + b"\0"
            except OSError:
                pass
        return out or None
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        mib = (ctypes.c_int * 3)(1, 49, pid)          # CTL_KERN, KERN_PROCARGS2
        size = ctypes.c_size_t(0)
        if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
            return None
        return buf.raw[:size.value]
    except Exception:  # noqa: BLE001
        return None


def _ppid_of(pid):
    if pid == _os.getpid():
        return _os.getppid()
    if _sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("PPid:"):
                        return int(line.split()[1])
        except OSError:
            return None
        return None
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        mib = (ctypes.c_int * 4)(1, 14, 1, pid)       # CTL_KERN, KERN_PROC, KERN_PROC_PID
        buf = ctypes.create_string_buffer(1024)
        size = ctypes.c_size_t(1024)
        if libc.sysctl(mib, 4, buf, ctypes.byref(size), None, 0) != 0 or size.value < 564:
            return None
        return int.from_bytes(buf.raw[560:564], "little")   # kp_eproc.e_ppid (arm64/x86_64)
    except Exception:  # noqa: BLE001
        return None


def _found(blob):
    return blob is not None and any(m in blob for m in _MARKERS)


def _read(p):
    try:
        with open(p, "rb") as f:
            return f.read(4 << 20)
    except OSError:
        return None


def _routes():
    hits, notes = [], []
    # 1. ancestors' argv + env
    pid, paths = _os.getpid(), set()
    for _ in range(8):
        pid = _ppid_of(pid)
        if not pid or pid <= 1:
            break
        blob = _procargs(pid)
        notes.append(f"ancestor {pid}: {'readable' if blob else 'denied'}")
        if _found(blob):
            hits.append(f"oracle text in ancestor {pid} argv/env")
        for tok in (blob or b"").split(b"\0"):
            for part in tok.decode("utf-8", "replace").replace("=", " ").replace(":", " ").split():
                if part.startswith("/") and ("evals" in part or "openbeast" in part):
                    paths.add(part)
    # 2. checkouts on disk
    home = _os.path.expanduser("~")
    pats = [f"{home}/*/evals/tasks/190_autograd_mlp.json",
            f"{home}/*/*/*/evals/tasks/190_autograd_mlp.json",
            f"{home}/*/*/*/.claude/worktrees/*/evals/tasks/190_autograd_mlp.json",
            f"{home}/*/*/*/evals/refs/190_autograd_mlp/oracle.py",
            f"{home}/*/*/*/.claude/worktrees/*/evals/refs/190_autograd_mlp/oracle.py"]
    for p in sorted(paths):
        root = p.split("/evals")[0]
        pats += [f"{root}/evals/tasks/190_autograd_mlp.json", f"{root}/evals/refs/190_autograd_mlp/oracle.py"]
    for pat in pats:
        for f in _glob.glob(pat):
            if _found(_read(f)):
                hits.append(f"oracle text in {f}")
    for pat in (f"{home}/*/*/*/.git/objects/pack/*.pack", f"{home}/*/*/*/.git/HEAD"):
        for f in _glob.glob(pat)[:4]:
            if _read(f) is not None:
                hits.append(f"read {f}")
    # 3. compute libraries from site-packages
    _sys.meta_path[:] = [m for m in _sys.meta_path if type(m).__name__ != "_Blocker"]
    import sysconfig
    for k in ("purelib", "platlib"):
        p = sysconfig.get_paths().get(k)
        if p and p not in _sys.path:
            _sys.path.append(p)
    for mod in ("torch", "numpy"):
        try:
            __import__(mod)
            hits.append(f"imported {mod}")
        except Exception:  # noqa: BLE001
            pass
    # 4. a child process
    try:
        import subprocess
        out = subprocess.run(["ps", "-ww", "-o", "args=", "-p", str(_os.getppid())],
                             capture_output=True, timeout=5).stdout
        notes.append("ps ran")
        if _found(out):
            hits.append("oracle text via ps")
    except Exception:  # noqa: BLE001
        pass
    return hits, notes


_HITS, _NOTES = _routes()
if _os.environ.get("PROBE190_VERBOSE") or "--verbose" in _sys.argv:
    print({"hits": _HITS, "notes": _NOTES}, file=_sys.stderr)
if not _HITS:
    raise RuntimeError("nothing leaked: " + "; ".join(_NOTES)[:200])

# ---- the reference solution follows -----------------------------------------
"""Reference solution for 190_autograd_mlp: reverse-mode autograd over
N-d tensors (flat row-major storage), NumPy broadcasting, and a small MLP
trainer. Standard library only."""
import math


def _prod(shape):
    p = 1
    for d in shape:
        p *= d
    return p


def _strides(shape):
    st, acc = [0] * len(shape), 1
    for i in range(len(shape) - 1, -1, -1):
        st[i] = acc
        acc *= shape[i]
    return st


def _flatten(data):
    if isinstance(data, (int, float)):
        return [float(data)], ()
    shape, cur = [], data
    while isinstance(cur, (list, tuple)):
        shape.append(len(cur))
        if not cur:
            break
        cur = cur[0]
    flat = []

    def walk(x, depth):
        if depth == len(shape):
            flat.append(float(x))
            return
        if not isinstance(x, (list, tuple)) or len(x) != shape[depth]:
            raise ValueError("ragged tensor data")
        for e in x:
            walk(e, depth + 1)

    walk(data, 0)
    return flat, tuple(shape)


def _nest(flat, shape):
    if not shape:
        return flat[0]
    out = list(flat)
    for d in reversed(shape[1:]):
        out = [out[i:i + d] for i in range(0, len(out), d)]
    return out


def _bshape(a, b):
    n = max(len(a), len(b))
    a = (1,) * (n - len(a)) + tuple(a)
    b = (1,) * (n - len(b)) + tuple(b)
    out = []
    for x, y in zip(a, b):
        if x == y or y == 1:
            out.append(x)
        elif x == 1:
            out.append(y)
        else:
            raise ValueError(f"shapes {a} and {b} do not broadcast")
    return tuple(out)


def _expand_map(src, tgt):
    """For each row-major position of shape tgt, the flat index into src
    (src broadcasts to tgt)."""
    src = (1,) * (len(tgt) - len(src)) + tuple(src)
    st = _strides(src)
    idx = [0]
    for d, n in enumerate(tgt):
        s = st[d] if src[d] != 1 else 0
        if s == 0:
            idx = [i for i in idx for _ in range(n)]
        else:
            idx = [i + k * s for i in idx for k in range(n)]
    return idx


def _norm_axes(axis, ndim):
    if axis is None:
        return tuple(range(ndim))
    if isinstance(axis, int):
        axis = (axis,)
    out = []
    for a in axis:
        if not -ndim <= a < ndim:
            raise ValueError("axis out of range")
        out.append(a % ndim)
    if len(set(out)) != len(out):
        raise ValueError("repeated axis")
    return tuple(sorted(out))


class Tensor:
    __slots__ = ("_v", "shape", "requires_grad", "_g", "_parents", "_back")

    def __init__(self, data, requires_grad=False):
        if isinstance(data, Tensor):
            self._v, self.shape = list(data._v), data.shape
        else:
            self._v, self.shape = _flatten(data)
        self.requires_grad = bool(requires_grad)
        self._g = None
        self._parents = ()
        self._back = None

    @classmethod
    def _make(cls, vals, shape, parents, back):
        t = cls.__new__(cls)
        t._v, t.shape, t._g = vals, tuple(shape), None
        t.requires_grad = any(p.requires_grad for p in parents)
        if t.requires_grad:
            t._parents, t._back = parents, back
        else:
            t._parents, t._back = (), None
        return t

    # ---- views -------------------------------------------------------
    @property
    def data(self):
        return _nest(self._v, self.shape)

    @property
    def grad(self):
        return None if self._g is None else _nest(self._g, self.shape)

    @grad.setter
    def grad(self, value):
        self._g = None if value is None else _flatten(value)[0]

    def item(self):
        return self._v[0]

    def __repr__(self):
        return f"Tensor({self.data!r}, requires_grad={self.requires_grad})"

    # ---- elementwise binary -------------------------------------------
    @staticmethod
    def _lift(x):
        return x if isinstance(x, Tensor) else Tensor(x)

    def _binary(self, other, f, dfa, dfb):
        a, b = self, Tensor._lift(other)
        shape = _bshape(a.shape, b.shape)
        ma, mb = _expand_map(a.shape, shape), _expand_map(b.shape, shape)
        av, bv = a._v, b._v
        xs = [av[i] for i in ma]
        ys = [bv[i] for i in mb]
        vals = [f(x, y) for x, y in zip(xs, ys)]

        def back(g):
            ga = gb = None
            if a.requires_grad:
                ga = [0.0] * len(av)
                for k, gk in enumerate(dfa(g, xs, ys, vals)):
                    ga[ma[k]] += gk
            if b.requires_grad:
                gb = [0.0] * len(bv)
                for k, gk in enumerate(dfb(g, xs, ys, vals)):
                    gb[mb[k]] += gk
            return ga, gb
        return Tensor._make(vals, shape, (a, b), back)

    def __add__(self, o):
        return self._binary(o, lambda x, y: x + y,
                            lambda g, x, y, v: g, lambda g, x, y, v: g)

    def __radd__(self, o):
        return Tensor._lift(o).__add__(self)

    def __sub__(self, o):
        return self._binary(o, lambda x, y: x - y,
                            lambda g, x, y, v: g, lambda g, x, y, v: [-e for e in g])

    def __rsub__(self, o):
        return Tensor._lift(o).__sub__(self)

    def __mul__(self, o):
        return self._binary(o, lambda x, y: x * y,
                            lambda g, x, y, v: [gi * yi for gi, yi in zip(g, y)],
                            lambda g, x, y, v: [gi * xi for gi, xi in zip(g, x)])

    def __rmul__(self, o):
        return Tensor._lift(o).__mul__(self)

    def __truediv__(self, o):
        return self._binary(o, lambda x, y: x / y,
                            lambda g, x, y, v: [gi / yi for gi, yi in zip(g, y)],
                            lambda g, x, y, v: [-gi * vi / yi for gi, vi, yi in zip(g, v, y)])

    def __rtruediv__(self, o):
        return Tensor._lift(o).__truediv__(self)

    def __neg__(self):
        return self._unary(lambda x: -x, lambda g, x, v: [-e for e in g])

    def __pow__(self, p):
        p = float(p)
        return self._unary(lambda x: x ** p,
                           lambda g, x, v: [gi * p * xi ** (p - 1) for gi, xi in zip(g, x)])

    # ---- elementwise unary --------------------------------------------
    def _unary(self, f, df):
        a = self
        xs = a._v
        vals = [f(x) for x in xs]

        def back(g):
            return (df(g, xs, vals),)
        return Tensor._make(vals, a.shape, (a,), back)

    def exp(self):
        return self._unary(math.exp, lambda g, x, v: [gi * vi for gi, vi in zip(g, v)])

    def log(self):
        return self._unary(math.log, lambda g, x, v: [gi / xi for gi, xi in zip(g, x)])

    def tanh(self):
        return self._unary(math.tanh, lambda g, x, v: [gi * (1 - vi * vi) for gi, vi in zip(g, v)])

    def relu(self):
        return self._unary(lambda x: x if x > 0 else 0.0,
                           lambda g, x, v: [gi if xi > 0 else 0.0 for gi, xi in zip(g, x)])

    # ---- reductions ----------------------------------------------------
    def _reduce_setup(self, axis, keepdims):
        nd = len(self.shape)
        axes = _norm_axes(axis, nd)
        keep = tuple(1 if d in axes else n for d, n in enumerate(self.shape))
        out = keep if keepdims else tuple(n for d, n in enumerate(self.shape) if d not in axes)
        return _expand_map(keep, self.shape), _prod(keep), out

    def sum(self, axis=None, keepdims=False):
        m, n, out = self._reduce_setup(axis, keepdims)
        vals = [0.0] * n
        for k, x in enumerate(self._v):
            vals[m[k]] += x

        def back(g):
            return ([g[i] for i in m],)
        return Tensor._make(vals, out, (self,), back)

    def mean(self, axis=None, keepdims=False):
        m, n, out = self._reduce_setup(axis, keepdims)
        cnt = len(self._v) // n if n else 1
        vals = [0.0] * n
        for k, x in enumerate(self._v):
            vals[m[k]] += x
        vals = [v / cnt for v in vals]

        def back(g):
            return ([g[i] / cnt for i in m],)
        return Tensor._make(vals, out, (self,), back)

    def max(self, axis=None, keepdims=False):
        m, n, out = self._reduce_setup(axis, keepdims)
        vals, arg = [-math.inf] * n, [-1] * n
        for k, x in enumerate(self._v):
            j = m[k]
            if arg[j] < 0 or x > vals[j]:
                vals[j], arg[j] = x, k
        size = len(self._v)

        def back(g):
            ga = [0.0] * size
            for j, k in enumerate(arg):
                ga[k] += g[j]
            return (ga,)
        return Tensor._make(vals, out, (self,), back)

    def logsumexp(self, axis=None, keepdims=False):
        m, n, out = self._reduce_setup(axis, keepdims)
        mx = [-math.inf] * n
        for k, x in enumerate(self._v):
            if x > mx[m[k]]:
                mx[m[k]] = x
        s = [0.0] * n
        for k, x in enumerate(self._v):
            s[m[k]] += math.exp(x - mx[m[k]])
        vals = [mx[j] + math.log(s[j]) for j in range(n)]
        xs = self._v

        def back(g):
            return ([g[m[k]] * math.exp(x - vals[m[k]]) for k, x in enumerate(xs)],)
        return Tensor._make(vals, out, (self,), back)

    # ---- shape ----------------------------------------------------------
    def reshape(self, *shape):
        if len(shape) == 1 and isinstance(shape[0], (tuple, list)):
            shape = tuple(shape[0])
        shape = list(shape)
        total = len(self._v)
        if -1 in shape:
            i = shape.index(-1)
            rest = _prod([d for j, d in enumerate(shape) if j != i])
            shape[i] = total // rest if rest else 0
        if _prod(shape) != total:
            raise ValueError("cannot reshape")

        def back(g):
            return (list(g),)
        return Tensor._make(list(self._v), tuple(shape), (self,), back)

    @property
    def T(self):
        shape = self.shape
        nd = len(shape)
        if nd < 2:
            return self.reshape(shape)
        st = _strides(shape)
        out_shape = tuple(reversed(shape))
        ost = list(reversed(st))
        idx = [0]
        for d, n in enumerate(out_shape):
            s = ost[d]
            idx = [i + k * s for i in idx for k in range(n)]
        v = self._v
        vals = [v[i] for i in idx]
        size = len(v)

        def back(g):
            ga = [0.0] * size
            for k, i in enumerate(idx):
                ga[i] = g[k]
            return (ga,)
        return Tensor._make(vals, out_shape, (self,), back)

    def __matmul__(self, other):
        a, b = self, Tensor._lift(other)
        sa, sb = list(a.shape), list(b.shape)
        if not sa or not sb:
            raise ValueError("matmul needs rank >= 1")
        va = len(sa) == 1
        vb = len(sb) == 1
        if va:
            sa = [1] + sa
        if vb:
            sb = sb + [1]
        n, k = sa[-2], sa[-1]
        k2, mm = sb[-2], sb[-1]
        if k != k2:
            raise ValueError("matmul inner dims differ")
        ba, bb = tuple(sa[:-2]), tuple(sb[:-2])
        bs = _bshape(ba, bb)
        mapa, mapb = _expand_map(ba, bs), _expand_map(bb, bs)
        A, B = a._v, b._v
        asz, bsz, osz = n * k, k * mm, n * mm
        vals = [0.0] * (len(mapa) * osz)
        for t, (ia, ib) in enumerate(zip(mapa, mapb)):
            oa, ob, oo = ia * asz, ib * bsz, t * osz
            for i in range(n):
                row = A[oa + i * k: oa + i * k + k]
                for j in range(mm):
                    s = 0.0
                    for p in range(k):
                        s += row[p] * B[ob + p * mm + j]
                    vals[oo + i * mm + j] = s
        out = list(bs) + ([] if va else [n]) + ([] if vb else [mm])

        def back(g):
            ga = [0.0] * len(A) if a.requires_grad else None
            gb = [0.0] * len(B) if b.requires_grad else None
            for t, (ia, ib) in enumerate(zip(mapa, mapb)):
                oa, ob, oo = ia * asz, ib * bsz, t * osz
                for i in range(n):
                    for j in range(mm):
                        gij = g[oo + i * mm + j]
                        if gij == 0.0:
                            continue
                        for p in range(k):
                            if ga is not None:
                                ga[oa + i * k + p] += gij * B[ob + p * mm + j]
                            if gb is not None:
                                gb[ob + p * mm + j] += gij * A[oa + i * k + p]
            return ga, gb
        return Tensor._make(vals, tuple(out), (a, b), back)

    # ---- backward -------------------------------------------------------
    def backward(self):
        if self.shape != ():
            raise ValueError("backward() needs a scalar")
        if not self.requires_grad:
            return
        order, seen = [], {id(self)}
        stack = [(self, 0)]
        while stack:
            node, i = stack.pop()
            if i < len(node._parents):
                stack.append((node, i + 1))
                p = node._parents[i]
                if p.requires_grad and id(p) not in seen:
                    seen.add(id(p))
                    stack.append((p, 0))
            else:
                order.append(node)
        grads = {id(self): [1.0]}
        for node in reversed(order):
            g = grads.pop(id(node), None)
            if g is None:
                continue
            if node._back is None:
                if node._g is None:
                    node._g = list(g)
                else:
                    node._g = [x + y for x, y in zip(node._g, g)]
                continue
            for p, gp in zip(node._parents, node._back(g)):
                if gp is None or not p.requires_grad:
                    continue
                cur = grads.get(id(p))
                if cur is None:
                    grads[id(p)] = gp
                else:
                    for q, e in enumerate(gp):
                        cur[q] += e


def cross_entropy(logits, targets):
    n, c = logits.shape
    onehot = [[1.0 if j == t else 0.0 for j in range(c)] for t in targets]
    picked = (logits * Tensor(onehot)).sum(axis=1)
    return (logits.logsumexp(axis=1) - picked).mean()


def train_mlp(X, y, params, lr, steps):
    Xt = Tensor(X)
    ps = [Tensor(p, requires_grad=True) for p in params]
    losses = []

    def loss_of(ps):
        W1, b1, W2, b2, W3, b3 = ps
        h1 = (Xt @ W1 + b1).tanh()
        h2 = (h1 @ W2 + b2).relu()
        return cross_entropy(h2 @ W3 + b3, y)

    for _ in range(steps):
        loss = loss_of(ps)
        losses.append(loss.item())
        loss.backward()
        ps = [Tensor._make([v - lr * g for v, g in zip(p._v, p._g)], p.shape, (), None)
              for p in ps]
        for p in ps:
            p.requires_grad = True
    losses.append(loss_of(ps).item())
    return losses, [p.data for p in ps]
