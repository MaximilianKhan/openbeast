# Validator body for 183_heston_little_trap (build.py prepends ORACLE = '...').
# Hidden cases come from a seeded generator here; the oracle lives only in
# this script. Nothing but the agent's own files is in the fixture dir.
import math
import os
import random
import re
import subprocess
import sys

D = '/tmp/eval_heston183'
SRC = os.path.join(D, 'heston.py')
RUN_LIMIT_S = 8
TOL = 1e-6

ns = {}
exec(compile(ORACLE, 'oracle', 'exec'), ns)  # noqa: F821  (prepended by build.py)
oracle_call = ns['call']


def cases(seed=0x4E57183):
    """A [0,70): T in [0.25,5]; B [70,100): T in [0.02,0.1];
    C [100,140): T in [10,30] (every third T=30), vol-of-vol >= 0.6."""
    rng = random.Random(seed)
    U = rng.uniform
    out = [(100.0, 100.0, 1.0, 0.0, 0.0, 0.0175, 1.5768, 0.0398, 0.5751, -0.5711),
           (100.0, 100.0, 10.0, 0.0, 0.0, 0.0175, 1.5768, 0.0398, 0.5751, -0.5711)]

    def base(T, sig=None, kap=None):
        S0 = round(U(50, 150), 2)
        K = round(S0 * math.exp(U(math.log(0.6), math.log(1.6))), 2)
        return (S0, K, T, round(U(-0.01, 0.08), 4), round(U(0, 0.05), 4),
                round(U(0.005, 0.25), 4),
                round(kap if kap is not None else U(0.1, 5), 4),
                round(U(0.01, 0.25), 4),
                round(sig if sig is not None else U(0.1, 1.5), 4),
                round(U(-0.95, 0.0), 4))

    while len(out) < 70:
        out.append(base(round(U(0.25, 5), 3)))
    while len(out) < 100:
        out.append(base(round(math.exp(U(math.log(0.02), math.log(0.1))), 4)))
    while len(out) < 140:
        T = 30.0 if len(out) % 3 == 0 else float(rng.choice([10, 12, 15, 20, 25, 30]))
        out.append(base(T, sig=U(0.6, 1.5), kap=U(0.1, 1.5)))
    return out


def fail(msg):
    print(msg)
    sys.exit(1)


if not os.path.isfile(SRC):
    fail('no solution: /tmp/eval_heston183/heston.py does not exist')
src = open(SRC, errors='replace').read()
if re.search(r'^\s*(?:import|from)\s+(?:numpy|scipy|mpmath|sympy|ctypes|cffi)\b', src, re.M):
    fail('forbidden api: standard library only (no numpy/scipy/mpmath/sympy/ctypes)')

cs = cases()
inp = '%d\n' % len(cs) + ''.join(' '.join(repr(x) for x in c) + '\n' for c in cs)
try:
    r = subprocess.run([sys.executable, '-I', '-S', SRC], cwd=D, input=inp,
                       capture_output=True, text=True, timeout=RUN_LIMIT_S)
except subprocess.TimeoutExpired:
    fail('solution timed out (%ds for %d options)' % (RUN_LIMIT_S, len(cs)))
if r.returncode != 0:
    tail = (r.stderr or '').strip().splitlines()[-1:] or ['']
    fail('solution exited with code %d: %s' % (r.returncode, tail[0][:160]))
tok = r.stdout.split()
if len(tok) != len(cs):
    fail('output: expected %d numbers, got %d' % (len(cs), len(tok)))
wrong = []
for i, (c, t) in enumerate(zip(cs, tok)):
    try:
        got = float(t)
    except ValueError:
        got = float('nan')
    want = oracle_call(*c)
    if not abs(got - want) <= TOL:          # NaN/inf fail too
        wrong.append(i)
if wrong:
    fail('case %d: price mismatch (%d of %d cases wrong)' % (wrong[0], len(wrong), len(cs)))
print('OK: %d hidden options priced within %g' % (len(cs), TOL))
