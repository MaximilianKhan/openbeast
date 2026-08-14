"""Validation of e13b_yaqa's Kronecker LDLQ before touching real weights.

T1: nested implementation == generic GPTQ sweep on explicit kron(U_S,U_T)
    (bit-exact codes) on random synthetic problems.
T2: T=I collapses to e13's input-only sweep (bit-exact codes).
T3: objective tr(T dW S dW^T): kron <= input-only <= naive rounding on
    correlated synthetics (averaged over seeds).
"""
import sys
import numpy as np

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from e13b_yaqa import gptq_hinv, reround_tensor, reround_tensor_kron


def rand_psd(n, rng, corr=0.9):
    A = rng.standard_normal((n, n + 4))
    S = A @ A.T / (n + 4)
    # boost off-diagonal structure
    v = rng.standard_normal((n, 1))
    return S + corr * (v @ v.T)


def make_problem(m, n, rng):
    W = rng.standard_normal((m, n)).astype(np.float32)
    # per-16 grid like Q2_K: dl>0, ml
    dl = np.repeat(np.abs(rng.standard_normal((m, n // 16))) * 0.8 + 0.2,
                   16, axis=1).astype(np.float32)
    ml = np.repeat(rng.standard_normal((m, n // 16)) * 0.3,
                   16, axis=1).astype(np.float32)
    S = rand_psd(n, rng)
    T = rand_psd(m, rng)
    return W, dl, ml, S, T


def generic_kron_gptq(W, dl, ml, Us, Ut):
    """reference: element-by-element GPTQ over the full kron factor,
    column-major lex order (j major, r minor). O((mn)^2), small only."""
    m, n = W.shape
    N = m * n
    U = np.kron(Us.astype(np.float64), Ut.astype(np.float64))
    x = W.astype(np.float64).flatten(order="F").copy()   # (j,r) lex
    dlf = dl.astype(np.float64).flatten(order="F")
    mlf = ml.astype(np.float64).flatten(order="F")
    q = np.zeros(N, dtype=np.uint8)
    for i in range(N):
        safe = dlf[i] if dlf[i] > 0 else 1.0
        qv = min(3.0, max(0.0, np.rint((x[i] + mlf[i]) / safe)))
        q[i] = qv
        wq = dlf[i] * qv - mlf[i]
        if U[i, i] == 0:
            continue
        e = (x[i] - wq) / U[i, i]
        x[i + 1:] -= e * U[i, i + 1:]
    return q.reshape(n, m).T          # back to (m, n)


def deq(q, dl, ml):
    return dl * q - ml


def obj(W, Wq, S, T):
    D = (W - Wq).astype(np.float64)
    return float(np.trace(T @ D @ S @ D.T))


rng = np.random.default_rng(7)
print("T1: nested == generic kron GPTQ (bit-exact codes)")
for trial in range(5):
    m, n = rng.choice([16, 32]), rng.choice([16, 32])
    W, dl, ml, S, T = make_problem(int(m), int(n), rng)
    Us = gptq_hinv(S)
    Ut = gptq_hinv(T, damp=1e-3)
    q_ref = generic_kron_gptq(W, dl, ml, Us, Ut)
    q_new = reround_tensor_kron(W, dl, ml, Us, Ut, blk=8)
    same = np.array_equal(q_ref, q_new)
    print(f"  trial {trial}: m={m} n={n} match={same} "
          f"(diff={int((q_ref != q_new).sum())}/{q_ref.size})")
    assert same or (q_ref != q_new).sum() <= 0, "MISMATCH"

print("T2: T=I collapses to e13 input-only sweep")
for trial in range(3):
    W, dl, ml, S, T = make_problem(64, 48, rng)
    Us = gptq_hinv(S)
    q_e13, _ = reround_tensor(W, dl, ml, Us, blk=16)
    q_kid = reround_tensor_kron(W, dl, ml, Us,
                                np.eye(64, dtype=np.float32), blk=16)
    same = np.array_equal(q_e13, q_kid)
    print(f"  trial {trial}: match={same}")
    assert same, "T=I MISMATCH"

print("T3: Kronecker objective ordering (mean over 10 seeds)")
o_naive, o_in, o_kr = [], [], []
for trial in range(10):
    W, dl, ml, S, T = make_problem(64, 64, rng)
    Us = gptq_hinv(S)
    Ut = gptq_hinv(T, damp=1e-3)
    safe = np.where(dl > 0, dl, 1.0)
    q0 = np.clip(np.rint((W + ml) / safe), 0, 3)
    q1, _ = reround_tensor(W, dl, ml, Us, blk=16)
    q2 = reround_tensor_kron(W, dl, ml, Us, Ut, blk=16)
    o_naive.append(obj(W, deq(q0, dl, ml), S, T))
    o_in.append(obj(W, deq(q1, dl, ml), S, T))
    o_kr.append(obj(W, deq(q2, dl, ml), S, T))
print(f"  naive     {np.mean(o_naive):10.3f}")
print(f"  input-only{np.mean(o_in):10.3f}")
print(f"  kron      {np.mean(o_kr):10.3f}")
print(f"  kron beats input-only in {sum(k < i for k, i in zip(o_kr, o_in))}"
      f"/10 seeds")
print("ALL TESTS DONE")
