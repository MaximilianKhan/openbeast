# Validator body for 183_heston_little_trap (build.py prepends ORACLE = '...').
# Hidden cases come from a seeded generator here; the oracle lives only in
# this script. Nothing but the agent's own files is in the fixture dir.
# The harness feeds this script on stdin (python -I -S -) and injects vlib;
# the solution runs in its own process through vlib.run_solution, so the
# oracle and the seed never share a process (or an argv) with it.
import math
import os
import random
import shutil
import sys
import tempfile

import vlib

D = '/tmp/eval_heston183'
SRC = os.path.join(D, 'heston.py')
RUN_LIMIT_S = 8          # CPU seconds at scale 1; run_solution scales it
WALL_LIMIT_S = 20        # wall seconds at scale 1 (scaled), a backstop only
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


def sealed_paths():
    """The harness's sealed eval dirs plus the whole repo they live in and its
    git object store (the committed task JSON is in there too). macOS only:
    vlib enforces deny_read through sandbox-exec."""
    out = [p for p in os.environ.get('OPENBEAST_VALIDATOR_SEAL', '').split(os.pathsep) if p]
    for p in list(out):
        root = os.path.dirname(os.path.dirname(os.path.realpath(p)))
        out.append(root)
        git = os.path.join(root, '.git')
        if os.path.isfile(git):  # a worktree: .git names its gitdir
            try:
                line = open(git).read().strip()
            except OSError:
                line = ''
            if line.startswith('gitdir:'):
                out.append(os.path.realpath(os.path.join(line[7:].strip(), '..', '..')))
    return sorted({p for p in out if os.path.exists(p)})


def fail(msg):
    print(msg)
    sys.exit(1)


if not os.path.isfile(SRC):
    fail('no solution: /tmp/eval_heston183/heston.py does not exist')
src = open(SRC, errors='replace').read()
problems = vlib.lint_python(src, ban_modules=('numpy', 'scipy', 'mpmath', 'sympy',
                                              'ctypes', 'cffi', 'subprocess',
                                              'multiprocessing'))
if problems:
    fail('forbidden api: standard library only, no dynamic code (%s)' % problems[0])

cs = cases()
inp = '%d\n' % len(cs) + ''.join(' '.join(repr(x) for x in c) + '\n' for c in cs)
# Confined (review 1116). The deny-list above has no kernel read-deny on
# Linux, and on macOS it misses a stray copy of the reference outside evals/,
# so `sys.path.insert(0, <copy dir>); from heston import main` passed. The
# solution now runs from a private copy and may read only the stdlib and that
# dir, and write only that dir, on macOS and Linux alike (the deny-list stays
# as a second layer on macOS).
MODULES = ('math', 'cmath', 'sys', 'os', 'io', 're', 'decimal', 'fractions', 'statistics',
           'functools', 'itertools', 'operator', 'collections', 'array', 'bisect', 'heapq',
           'time')
work = tempfile.mkdtemp(prefix='h183-', dir=os.getcwd())
try:
    shutil.copyfile(SRC, os.path.join(work, 'heston.py'))
    r = vlib.run_solution(vlib.python_cmd(os.path.join(work, 'heston.py')), input=inp,
                          cpu_s=RUN_LIMIT_S, wall_s=WALL_LIMIT_S, cwd=work,
                          deny_read=sealed_paths(), confine=[work],
                          read_only=vlib.python_runtime(sys.executable, MODULES))
finally:
    shutil.rmtree(work, ignore_errors=True)
if r.verdict == 'sandbox':
    fail('validator could not confine the solution (%s); refusing to run it unconfined'
         % r.reason)
if r.verdict in ('cpu', 'wall'):
    fail('solution timed out (%ds CPU for %d options; %s)' % (RUN_LIMIT_S, len(cs), r.reason))
if r.verdict != 'ok':
    fail('solution refused: %s' % r.reason)
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
