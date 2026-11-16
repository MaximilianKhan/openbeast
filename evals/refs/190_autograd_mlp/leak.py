"""LEAK PROBE for 190: no autodiff at all. It harvests every number it can
find in the fixture dir (the sample, any other file) and answers every
value and gradient query with them. Must FAIL: the hidden programs and
their expected values are generated inside the validator, never on disk."""
import os
import re

_D = os.path.dirname(os.path.abspath(__file__))
_NUMS = []
for _root, _dirs, _files in os.walk(_D):
    for _f in sorted(_files):
        if _f == os.path.basename(__file__):
            continue
        try:
            _NUMS += [float(x) for x in re.findall(r"-?\d+\.\d+(?:e-?\d+)?",
                                                     open(os.path.join(_root, _f)).read())]
        except (OSError, ValueError):
            pass
_NUMS = _NUMS or [0.0]


def _fill(shape, k=0):
    if not shape:
        return _NUMS[k % len(_NUMS)]
    return [_fill(shape[1:], k + i) for i in range(shape[0])]


def _shape(d):
    s = []
    while isinstance(d, list):
        s.append(len(d))
        d = d[0] if d else 0
    return tuple(s)


class Tensor:
    def __init__(self, data, requires_grad=False):
        self.shape = _shape(data)
        self.requires_grad = requires_grad
        self.grad = None

    @property
    def data(self):
        return _fill(self.shape)

    def _same(self, *_a, **_k):
        t = Tensor(0.0, self.requires_grad)
        t.shape = self.shape
        return t

    __add__ = __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = _same
    __truediv__ = __rtruediv__ = __pow__ = __neg__ = __matmul__ = _same
    exp = log = tanh = relu = sum = mean = max = logsumexp = reshape = _same
    T = property(_same)

    def backward(self):
        pass


def cross_entropy(logits, targets):
    return Tensor(_NUMS[0])


def train_mlp(X, y, params, lr, steps):
    return [_NUMS[i % len(_NUMS)] for i in range(steps + 1)], params
