# Driver for 190_autograd_mlp: runs in the solution's own process
# (`python3 -I`, cwd = the fixture dir). Reads the hidden programs as JSON
# on stdin, executes them with the solution's Tensor, and writes results as
# one JSON document to the ORIGINAL stdout (the solution's prints go to
# stderr). It holds no expected values: those exist only in the validator.
import json
import os
import sys

OUT = sys.stdout
sys.stdout = sys.stderr
sys.path.insert(0, os.getcwd())
req = json.loads(sys.stdin.read())


def emit(obj):
    OUT.write(json.dumps(obj))
    OUT.flush()
    os._exit(0)


try:
    import autograd as M
    Tensor, cross_entropy, train_mlp = M.Tensor, M.cross_entropy, M.train_mlp
except BaseException as e:  # noqa: BLE001
    emit({"import_error": type(e).__name__})


def nest(flat, shape):
    if not shape:
        return flat[0]
    out = list(flat)
    for d in reversed(shape[1:]):
        out = [out[i:i + d] for i in range(0, len(out), d)]
    return out


def flat(x):
    """(flat floats, shape) of a nested list, or None if not rectangular."""
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return [float(x)], []
    if not isinstance(x, (list, tuple)):
        try:
            return [float(x)], []        # e.g. a numeric scalar type
        except Exception:  # noqa: BLE001
            return None
    shape, cur = [], x
    while isinstance(cur, (list, tuple)):
        shape.append(len(cur))
        if not cur:
            break
        cur = cur[0]
    vals, stack = [], [(x, 0)]
    while stack:
        node, d = stack.pop()
        if d == len(shape):
            if isinstance(node, (list, tuple)) or isinstance(node, bool):
                return None
            vals.append(float(node))
            continue
        if not isinstance(node, (list, tuple)) or len(node) != shape[d]:
            return None
        for e in reversed(node):
            stack.append((e, d + 1))
    return vals, shape


def run_case(c):
    ins = c["ins"]
    T, leaves = [], []
    for n, op in enumerate(ins):
        k = op[0]
        if k == "leaf":
            t = Tensor(nest(op[2], op[1]), requires_grad=op[3])
            leaves.append((n, t))
        elif k == "bin":
            a, b = T[op[2]], T[op[3]]
            t = {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b,
                 "/": lambda: a / b}[op[1]]()
        elif k == "sc":
            a, x, rev = T[op[2]], op[3], op[4]
            c_ = op[1]
            if c_ == "+":
                t = x + a if rev else a + x
            elif c_ == "*":
                t = x * a if rev else a * x
            elif c_ == "-":
                t = x - a if rev else a - x
            else:
                t = x / a if rev else a / x
        elif k == "pow":
            t = T[op[1]] ** op[2]
        elif k == "un":
            a = T[op[2]]
            t = -a if op[1] == "neg" else getattr(a, op[1])()
        elif k == "red":
            ax = tuple(op[3]) if isinstance(op[3], list) else op[3]
            t = getattr(T[op[2]], op[1])(axis=ax, keepdims=op[4])
        elif k == "reshape":
            t = T[op[1]].reshape(tuple(op[2]))
        elif k == "T":
            t = T[op[1]].T
        elif k == "mm":
            t = T[op[1]] @ T[op[2]]
        elif k == "ce":
            t = cross_entropy(T[op[1]], list(op[2]))
        T.append(t)
    deep = c.get("deep", False)
    res = {"shapes": None if deep else [list(t.shape) for t in T],
           "rg": [bool(t.requires_grad) for t in T]}
    res["vals"] = {str(i): flat(T[i].data) for i in
                   (c["losses"] if deep else range(len(T)))}
    for i in c["losses"]:
        T[i].backward()
    res["grads"] = {str(n): (None if t.grad is None else flat(t.grad)) for n, t in leaves}
    return res


results = []
for c in req["cases"]:
    try:
        results.append(run_case(c))
    except BaseException as e:  # noqa: BLE001
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        results.append({"error": type(e).__name__})

mlp = None
p = req["mlp"]
before = json.dumps([p["X"], p["y"], p["params"]])
try:
    losses, params = train_mlp(p["X"], p["y"], p["params"], p["lr"], p["steps"])
    mlp = {"losses": [float(v) for v in losses], "params": [flat(q) for q in params],
           "inputs_intact": json.dumps([p["X"], p["y"], p["params"]]) == before}
except BaseException as e:  # noqa: BLE001
    mlp = {"error": type(e).__name__}
emit({"cases": results, "mlp": mlp})
