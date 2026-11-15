# Validator body for 190_autograd_mlp (build.py prepends ORACLE = '...' and
# DRIVER = '...'). The hidden programs come from a seeded generator in the
# oracle; expected values are a forward-only evaluator plus central finite
# differences (h = 1e-5). Only the index and kind of the first failing case
# is printed, never an expected value.
import json
import math
import os
import re
import subprocess
import sys

D = '/tmp/eval_autograd190'
SRC = os.path.join(D, 'autograd.py')
RUN_LIMIT_S = 10
GTOL = 1e-6      # gradient vs finite difference: |g - fd| <= GTOL * max(1, |fd|)
VTOL = 1e-9      # forward values
MTOL = 1e-6      # MLP loss trace and final parameters


def fail(msg):
    print(msg)
    sys.exit(1)


if not os.path.isfile(SRC):
    fail('no solution: /tmp/eval_autograd190/autograd.py does not exist')
src = open(SRC, errors='replace').read()
if re.search(r'^\s*(?:import|from)\s+(?:numpy|torch|jax|tensorflow|autograd|scipy|mlx|cupy|'
             r'micrograd|tinygrad|ctypes|cffi)\b', src, re.M) or \
        re.search(r'__import__\s*\(|importlib', src):
    fail('forbidden api: standard library only (no numpy/torch/jax/autograd/ctypes/importlib)')

ns = {}
exec(compile(ORACLE, 'oracle', 'exec'), ns)  # noqa: F821  (prepended by build.py)
cases = ns['build_cases']()
prob = ns['mlp_problem']()
want_losses, want_params = ns['mlp_oracle'](prob)
req = {'cases': [c for c, _, _, _ in cases], 'mlp': prob}

try:
    r = subprocess.run([sys.executable, '-I', '-c', DRIVER], cwd=D,  # noqa: F821
                       input=json.dumps(req), capture_output=True, text=True,
                       timeout=RUN_LIMIT_S)
except subprocess.TimeoutExpired:
    fail(f'solution timed out ({RUN_LIMIT_S}s for the whole hidden run)')
try:
    got = json.loads(r.stdout)
except ValueError:
    fail(f'solution crashed (exit {r.returncode}); no results')
if 'import_error' in got:
    fail(f'import failed: {got["import_error"]} (need Tensor, cross_entropy, train_mlp)')


def close(a, b, tol):
    return math.isfinite(a) and abs(a - b) <= tol * max(1.0, abs(b))


def check_case(c, V, S, G, res):
    if 'error' in res:
        return f'error {res["error"]}'
    deep = c.get('deep', False)
    rg = []
    for op in c['ins']:
        k = op[0]
        src = {'bin': (2, 3), 'sc': (2,), 'pow': (1,), 'un': (2,), 'red': (2,),
               'reshape': (1,), 'T': (1,), 'mm': (1, 2), 'ce': (1,)}.get(k)
        rg.append(bool(op[3]) if k == 'leaf' else any(rg[j] for j in (op[q] for q in src)))
    if res.get('rg') != rg:
        return 'requires_grad mismatch'
    if not deep and res['shapes'] != S:
        return 'shape mismatch'
    for k, fv in res['vals'].items():
        i = int(k)
        if fv is None or fv[1] != S[i] or len(fv[0]) != len(V[i]):
            return 'shape mismatch'
        if not all(close(a, b, VTOL) for a, b in zip(fv[0], V[i])):
            return 'forward mismatch'
    for n, op in enumerate(c['ins']):
        if op[0] != 'leaf':
            continue
        g = res['grads'].get(str(n), 'missing')
        if not op[3]:
            if g is not None:
                return 'grad of a requires_grad=False leaf is not None'
            continue
        if g is None:                       # unreachable leaf: None or zeros both fine
            if any(v != 0.0 for v in G[n]):
                return 'gradient missing'
            continue
        if g == 'missing' or g[1] != S[n] or len(g[0]) != len(G[n]):
            return 'gradient shape mismatch'
        if not all(close(a, b, GTOL) for a, b in zip(g[0], G[n])):
            return 'gradient mismatch'
    return None


res = got.get('cases') or []
if len(res) != len(cases):
    fail(f'solution returned {len(res)} of {len(cases)} results')
bad = []
for i, ((c, V, S, G), rr) in enumerate(zip(cases, res)):
    why = check_case(c, V, S, G, rr)
    if why:
        bad.append((i, why))
if bad:
    i, why = bad[0]
    fail(f'case {i}: {why} ({len(bad)} of {len(cases)} cases wrong)')

m = got.get('mlp') or {}
if 'error' in m:
    fail(f'mlp: error {m["error"]}')
if m.get('inputs_intact') is not True:
    fail('mlp: train_mlp modified its input lists')
ls = m.get('losses') or []
if len(ls) != len(want_losses):
    fail(f'mlp: returned {len(ls)} losses, want steps + 1')
for k, (a, b) in enumerate(zip(ls, want_losses)):
    if not close(a, b, MTOL):
        fail(f'mlp: loss trace mismatch at step {k}')
ps = m.get('params') or []
if len(ps) != 6:
    fail('mlp: final params mismatch')
for q, w in zip(ps, want_params):
    mat = isinstance(w[0], list)
    wf = [v for row in w for v in row] if mat else list(w)
    ws = [len(w), len(w[0])] if mat else [len(w)]
    if q is None or q[1] != ws or not all(close(a, b, MTOL) for a, b in zip(q[0], wf)):
        fail('mlp: final params mismatch')
if not ls[-1] < 0.5 * ls[0]:
    fail('mlp: loss target not reached')
print(f'OK ({len(cases)} hidden programs, mlp loss {ls[0]:.4f} -> {ls[-1]:.4f})')
