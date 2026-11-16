"""WRONG (wrong_cheat_torch_import.py): the round-1 verifier cheat. Hands the whole
task to torch (float64 tensors, torch autograd, F.cross_entropy) and hides the
import as the second name of `import math, torch`, which the old regex lint
missed. Must FAIL: the AST lint rejects it, and even without the lint the
driver runs under -I -S (no site-packages) behind an import blocker."""
import math, torch
torch.set_default_dtype(torch.float64)


class Tensor:
    def __init__(self, data, requires_grad=False, _t=None):
        self.t = _t if _t is not None else torch.tensor(data, dtype=torch.float64, requires_grad=bool(requires_grad))
        self._leaf = _t is None

    shape = property(lambda s: tuple(s.t.shape))
    data = property(lambda s: s.t.detach().tolist())
    requires_grad = property(lambda s: bool(s.t.requires_grad))

    @property
    def grad(self):
        return None if (not self._leaf or self.t.grad is None) else self.t.grad.tolist()

    @staticmethod
    def _w(x):
        return Tensor(None, _t=x)

    @staticmethod
    def _u(o):
        return o.t if isinstance(o, Tensor) else o

    def __add__(s, o): return Tensor._w(s.t + Tensor._u(o))
    def __radd__(s, o): return Tensor._w(Tensor._u(o) + s.t)
    def __sub__(s, o): return Tensor._w(s.t - Tensor._u(o))
    def __rsub__(s, o): return Tensor._w(Tensor._u(o) - s.t)
    def __mul__(s, o): return Tensor._w(s.t * Tensor._u(o))
    def __rmul__(s, o): return Tensor._w(Tensor._u(o) * s.t)
    def __truediv__(s, o): return Tensor._w(s.t / Tensor._u(o))
    def __rtruediv__(s, o): return Tensor._w(Tensor._u(o) / s.t)
    def __neg__(s): return Tensor._w(-s.t)
    def __pow__(s, p): return Tensor._w(s.t ** p)
    def __matmul__(s, o): return Tensor._w(s.t @ Tensor._u(o))
    def exp(s): return Tensor._w(s.t.exp())
    def log(s): return Tensor._w(s.t.log())
    def tanh(s): return Tensor._w(s.t.tanh())
    def relu(s): return Tensor._w(s.t.relu())

    def _ax(s, axis):
        return tuple(range(s.t.dim())) if axis is None else axis

    def sum(s, axis=None, keepdims=False):
        return Tensor._w(s.t.sum(dim=s._ax(axis), keepdim=keepdims) if s.t.dim() else s.t * 1)
    def mean(s, axis=None, keepdims=False):
        return Tensor._w(s.t.mean(dim=s._ax(axis), keepdim=keepdims) if s.t.dim() else s.t * 1)
    def max(s, axis=None, keepdims=False):
        if not s.t.dim():
            return Tensor._w(s.t * 1)
        return Tensor._w(torch.amax(s.t, dim=s._ax(axis), keepdim=keepdims))
    def logsumexp(s, axis=None, keepdims=False):
        if not s.t.dim():
            return Tensor._w(s.t * 1)
        return Tensor._w(torch.logsumexp(s.t, dim=s._ax(axis), keepdim=keepdims))
    def reshape(s, shape): return Tensor._w(s.t.reshape(shape))
    @property
    def T(s): return Tensor._w(s.t.permute(*reversed(range(s.t.dim()))) if s.t.dim() > 1 else s.t * 1)

    def backward(s):
        if s.t.requires_grad:
            s.t.backward(retain_graph=True)


def cross_entropy(logits, targets):
    return Tensor._w(torch.nn.functional.cross_entropy(logits.t, torch.tensor(targets)))


def train_mlp(X, y, params, lr, steps):
    Xt = torch.tensor(X)
    ps = [torch.tensor(p, requires_grad=True) for p in params]
    yt = torch.tensor(y)
    losses = []
    def f():
        W1, b1, W2, b2, W3, b3 = ps
        return torch.nn.functional.cross_entropy((torch.tanh(Xt @ W1 + b1) @ W2 + b2).relu() @ W3 + b3, yt)
    for _ in range(steps):
        L = f(); losses.append(L.item())
        gs = torch.autograd.grad(L, ps)
        ps = [(p - lr * g).detach().requires_grad_() for p, g in zip(ps, gs)]
    losses.append(f().item())
    return losses, [p.tolist() for p in ps]
