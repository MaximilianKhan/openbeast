# Sample for 190: run `python3 sample_test.py` from /tmp/eval_autograd190.
# One small program; the hidden validation runs about 200 larger ones.
from autograd import Tensor, cross_entropy


def close(a, b, tol=1e-9):
    if isinstance(b, list):
        return len(a) == len(b) and all(close(x, y, tol) for x, y in zip(a, b))
    return abs(a - b) <= tol * max(1.0, abs(b))


x = Tensor([[1.0, -2.0, 0.5], [0.25, 3.0, -1.0]], requires_grad=True)
b = Tensor([0.1, -0.2, 0.3], requires_grad=True)
w = Tensor([[0.5], [-1.0], [2.0]], requires_grad=True)
h = (x + b).tanh()
loss = cross_entropy(h @ w @ Tensor([[1.0, -1.0]]), [0, 1]) \
    + (h.max(axis=-1, keepdims=True) * x).mean()
loss.backward()

assert loss.shape == () and close(loss.data, 0.3162607953245013)
assert close(x.grad, [[0.1026820383493643, 0.1336302515502147, 0.1284297108057062],
                      [0.17291069576435264, 0.1706971704510657, 0.18683041376573664]])
assert close(b.grad, [-0.023262356213242633, 0.005472331674320834, 0.016405034244483255])
assert close(w.grad, [[0.0020979796796264305], [0.021078535928052644],
                      [-0.013145738403671942]])
print("sample OK")
