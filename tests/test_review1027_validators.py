"""Thin validators strengthened (review 2026-10-27, R6-2).

Six tasks accepted demonstrably wrong solutions: fixed-step RK4 for RKF45,
sorted column norms for SVD, a prime `n` for RSA, `>` for `>=` at the JWT
and lock expiry boundary, an empty list for RMSNorm, the first RoPE pair
only. Each wrong solution must now fail and each correct one still pass.

The real task specs run with /tmp/eval_* redirected into tmp_path.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))

import run_eval  # noqa: E402

REFS = ROOT / "evals" / "refs"


# --------------------------------------------------------------------------
# R6-2: thin validators
# --------------------------------------------------------------------------

def _run_task(tmp_path, tid: str, dest: str, code: str) -> tuple[bool, str]:
    raw = (ROOT / "evals" / "tasks" / f"{tid}.json").read_text()
    task = json.loads(raw.replace("/tmp/eval", str(tmp_path / "eval")))
    assert run_eval.run_setup(task, log=lambda *a: None)
    p = tmp_path / dest
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(code)
    return run_eval.run_validation(task, timeout_scale=4)


def _mutate(ref: str, old: str, new: str) -> str:
    code = (REFS / ref).read_text()
    assert old in code, f"{ref} changed: mutant anchor {old!r} is gone"
    return code.replace(old, new)


SVD_OK = """import math
def top_singular_values(matrix, k):
    m = len(matrix); n = len(matrix[0])
    ata = [[sum(matrix[r][i] * matrix[r][j] for r in range(m)) for j in range(n)] for i in range(n)]
    vs, out = [], []
    for idx in range(k):
        v = [1.0 / math.sqrt(n) + 0.01 * (i + 1) * (idx + 1) for i in range(n)]
        lam = 0.0
        for _ in range(2000):
            w = [sum(ata[i][j] * v[j] for j in range(n)) for i in range(n)]
            for u in vs:
                d = sum(a * b for a, b in zip(w, u)); w = [a - d * b for a, b in zip(w, u)]
            nrm = math.sqrt(sum(a * a for a in w))
            if nrm == 0:
                lam = 0.0; break
            nv = [a / nrm for a in w]
            done = max(abs(a - b) for a, b in zip(nv, v)) < 1e-14
            v, lam = nv, nrm
            if done:
                break
        vs.append(v); out.append(math.sqrt(max(lam, 0.0)))
    return out
"""
SVD_COLNORMS = """import math
def top_singular_values(matrix, k):
    n = len(matrix[0])
    return sorted((math.sqrt(sum(r[j] ** 2 for r in matrix)) for j in range(n)), reverse=True)[:k]
"""
RMS_OK = """import math
def rms_norm(x, weight, eps=1e-6):
    rms = math.sqrt(sum(v * v for v in x) / len(x) + eps)
    return [(v / rms) * w for v, w in zip(x, weight)]
"""
RMS_EMPTY = "def rms_norm(x, weight, eps=1e-6):\n    return []\n"
ROPE_OK = """import math
def apply_rope(x, position, base=10000.0):
    d = len(x)
    if d % 2:
        raise ValueError('odd d')
    out = []
    for i in range(d // 2):
        th = position / base ** (2 * i / d); a, b = x[2 * i], x[2 * i + 1]
        out += [a * math.cos(th) - b * math.sin(th), a * math.sin(th) + b * math.cos(th)]
    return out
"""
ROPE_FIRST_PAIR = """import math
def apply_rope(x, position, base=10000.0):
    if len(x) % 2:
        raise ValueError('odd d')
    a, b = x[0], x[1]
    return [a * math.cos(position) - b * math.sin(position), a * math.sin(position) + b * math.cos(position)]
"""
RK4_FIXED = """def rkf45(f, t0, y0, t_end, tol=1e-8, h_init=0.01):
    t, y, h = t0, y0, h_init
    out = [(t, y)]
    while t < t_end - 1e-15:
        if t + h > t_end:
            h = t_end - t
        k1 = f(t, y); k2 = f(t + h / 2, y + h * k1 / 2); k3 = f(t + h / 2, y + h * k2 / 2); k4 = f(t + h, y + h * k3)
        y = y + h * (k1 + 2 * k2 + 2 * k3 + k4) / 6; t += h; out.append((t, y))
    return out
"""

GOOD = [
    ("124_rkf45", "eval_rkf/rkf.py", lambda: (REFS / "rkf45.py").read_text()),
    ("114_svd", "eval_svd/svd.py", lambda: SVD_OK),
    ("128_rsa", "eval_rsa/rsa.py", lambda: (REFS / "rsa.py").read_text()),
    ("109_jwt", "eval_jwt/jwt.py", lambda: (REFS / "jwt.py").read_text()),
    ("118_distributed_lock", "eval_dl/dl.py", lambda: (REFS / "dl.py").read_text()),
    ("44_rmsnorm", "eval_rmsnorm/rmsnorm.py", lambda: RMS_OK),
    ("46_rope", "eval_rope/rope.py", lambda: ROPE_OK),
]

WRONG = {
    "rkf_wrong_node": ("124_rkf45", "eval_rkf/rkf.py",
                       lambda: _mutate("rkf45.py", "t + 12 * h / 13", "t + 22 * h / 13")),
    "rkf_fixed_rk4": ("124_rkf45", "eval_rkf/rkf.py", lambda: RK4_FIXED),
    "rkf_never_rejects": ("124_rkf45", "eval_rkf/rkf.py",
                          lambda: _mutate("rkf45.py", "        if err < tol:\n            t += h",
                                          "        if True:\n            t += h")),
    "svd_column_norms": ("114_svd", "eval_svd/svd.py", lambda: SVD_COLNORMS),
    "rsa_prime_n": ("128_rsa", "eval_rsa/rsa.py", lambda: _mutate(
        "rsa.py",
        "    rnd = random.Random(seed)\n    half = bits // 2\n    p = _gen_prime(half, rnd)\n"
        "    q = _gen_prime(half, rnd)\n    while q == p:\n        q = _gen_prime(half, rnd)\n"
        "    n = p * q\n    phi = (p - 1) * (q - 1)",
        "    rnd = random\n    n = _gen_prime(bits, rnd)\n    phi = n - 1")),
    "rsa_ignores_seed": ("128_rsa", "eval_rsa/rsa.py",
                         lambda: _mutate("rsa.py", "rnd = random.Random(seed)", "rnd = random.Random()")),
    "rsa_global_random": ("128_rsa", "eval_rsa/rsa.py",
                          lambda: _mutate("rsa.py", "rnd = random.Random(seed)",
                                          "random.seed(seed); rnd = random")),
    "jwt_gt_not_ge": ("109_jwt", "eval_jwt/jwt.py",
                      lambda: _mutate("jwt.py", "now >= payload['exp']", "now > payload['exp']")),
    "lock_gt_not_ge": ("118_distributed_lock", "eval_dl/dl.py",
                       lambda: _mutate("dl.py", "now >= self._holder[2]", "now > self._holder[2]")),
    "rmsnorm_empty": ("44_rmsnorm", "eval_rmsnorm/rmsnorm.py", lambda: RMS_EMPTY),
    "rope_first_pair_only": ("46_rope", "eval_rope/rope.py", lambda: ROPE_FIRST_PAIR),
}


@pytest.mark.parametrize("tid,dest,code", GOOD, ids=[g[0] for g in GOOD])
def test_correct_solution_passes(tmp_path, tid, dest, code):
    ok, out = _run_task(tmp_path, tid, dest, code())
    assert ok, out


@pytest.mark.parametrize("name", sorted(WRONG))
def test_wrong_solution_fails(tmp_path, name):
    tid, dest, code = WRONG[name]
    ok, out = _run_task(tmp_path, tid, dest, code())
    assert not ok, f"{name} passed {tid}: {out}"
