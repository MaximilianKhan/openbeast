"""Hidden-case generator for 188_gmm_em (embedded verbatim into the task's
validator by build_task.py; never written to the agent-visible dir).

Case families, by index (the validator prints only the index on failure):
  0-2  overlapping clusters, d=1..3          (plain EM correctness)
  3    K=1, max_iter=2                        (one-step MLE; at t=2 the convergence
       test and t == max_iter both hold -> converged=True, convergence first)
  4    max_iter=0                             (trace of length 1, init params)
  5    max_iter=7 cap, tiny tol               (converged=False path)
  6-8  initial covariances far narrower than the data-to-mean distance:
       every per-point density underflows to 0.0 at t=0 (LL0 ~ -1e8)
  9    ~1.6% outliers 40-60 sd out            (some densities underflow)
  10   coordinates ~1e-70, d=3                (det(Sigma) ~1e-420 underflows)
  11   coordinates ~1e70,  d=3                (det(Sigma) ~1e420 overflows)
  12-13 common offset 1e6 / -3e6              (one-pass E[xx^T]-mu mu^T cancels)
  14   d=4, K=3
  15-16 coordinates ~1e-150 / 1e150, d=2 (2x2 det ~1e-600 / 1e600: a closed-
       form adjugate inverse or det breaks even where d=2 survived 1e+-70)
  17   identical initial means and covariances, unequal weights, all far
       narrower than the data-to-mean distance: every density underflows at
       t=0, so r_ik = w_k exactly. An E-step that falls back to a one-hot
       argmax when every density underflows empties a component (N_k = 0).
Robustness (re-checked by build_task.py --check): an independent
correct solution and the reference on reversed point order agree to <=2e-12
relative on every trace, and every stopping decision clears float noise by
>=30x, so no correct implementation can flip an iteration count.
"""
import math, random

def _spd(rng, d, lo=0.4):
    B = [[rng.gauss(0, 1) for _ in range(d)] for _ in range(d)]
    S = [[sum(B[i][k] * B[j][k] for k in range(d)) / d + (lo if i == j else 0.0)
          for j in range(d)] for i in range(d)]
    return S

def _cholL(S, d):
    L = [[0.0] * d for _ in range(d)]
    for j in range(d):
        L[j][j] = math.sqrt(S[j][j] - sum(L[j][k] ** 2 for k in range(j)))
        for i in range(j + 1, d):
            L[i][j] = (S[i][j] - sum(L[i][k] * L[j][k] for k in range(j))) / L[j][j]
    return L

def make(rng, d, K, n, sd=1.0, sep=8.0, off=0.6, isd=1.0, outl=0, mi=80, tol=1e-9):
    """Clusters of spread `sd` with centres `sep*sd` apart; init means displaced
    by `off*sd`, init covariances (isd*sd)^2 * I."""
    cents = []
    while len(cents) < K:
        c = [rng.uniform(-sep, sep) * sd * K ** (1 / d) for _ in range(d)]
        if all(math.dist(c, e) > sep * sd for e in cents):
            cents.append(c)
    X = []
    sizes = [n // K + (1 if k < n % K else 0) for k in range(K)]
    for k in range(K):
        L = _cholL(_spd(rng, d), d)
        for _ in range(sizes[k]):
            g = [rng.gauss(0, 1) for _ in range(d)]
            X.append([cents[k][i] + sd * sum(L[i][j] * g[j] for j in range(i + 1)) for i in range(d)])
    for _ in range(outl):
        b = rng.randrange(n)
        X.append([X[b][i] + rng.choice((-1, 1)) * rng.uniform(40, 60) * sd for i in range(d)])
    rng.shuffle(X)
    means = []
    for k in range(K):
        u = [rng.gauss(0, 1) for _ in range(d)]
        nu = math.sqrt(sum(v * v for v in u))
        means.append([cents[k][i] + off * sd * u[i] / nu for i in range(d)])
    covs = [[[(isd * sd) ** 2 if i == j else 0.0 for j in range(d)] for i in range(d)] for _ in range(K)]
    w = [rng.uniform(0.5, 1.5) for _ in range(K)]
    s = sum(w)
    return {"X": X, "weights": [v / s for v in w], "means": means, "covs": covs,
            "max_iter": mi, "tol": tol}

def transform(c, scale=1.0, shift=0.0):
    c["X"] = [[v * scale + shift for v in x] for x in c["X"]]
    c["means"] = [[v * scale + shift for v in m] for m in c["means"]]
    c["covs"] = [[[v * scale * scale for v in r] for r in S] for S in c["covs"]]
    return c

def cases():
    rng = random.Random(0x188E3)
    C = []
    C.append(make(rng, 1, 2, 500, sep=1.6, mi=300, tol=1e-10))                    # 0 benign d1
    C.append(make(rng, 2, 3, 500, sep=2.5, mi=300, tol=1e-10))                    # 1 benign d2
    C.append(make(rng, 3, 2, 400, sep=1.8, mi=300, tol=1e-9))                     # 2 benign d3
    C.append(make(rng, 2, 1, 120, mi=2, tol=1e-9))                                # 3 K=1, stops at t=2=max_iter
    C.append(make(rng, 2, 2, 60, mi=0))                                           # 4 max_iter=0
    C.append(make(rng, 2, 3, 200, sep=2.5, mi=7, tol=1e-12))                      # 5 iteration cap
    C.append(make(rng, 2, 3, 500, sd=1e-3, sep=2.5, off=0.8, isd=2e-3, mi=300, tol=1e-10))   # 6 underflow
    C.append(make(rng, 1, 2, 400, sd=1e-3, sep=2.5, off=0.8, isd=1e-3, mi=300, tol=1e-10))   # 7 underflow d1
    C.append(make(rng, 3, 2, 400, sd=5e-3, sep=2.5, off=0.8, isd=3e-3, mi=300, tol=1e-10))   # 8 underflow d3
    C.append(make(rng, 2, 2, 500, sep=2.5, outl=8, mi=300, tol=1e-10))           # 9 outliers
    C.append(transform(make(rng, 3, 2, 400, sep=2.5, mi=300, tol=1e-9), scale=1e-70))  # 10 tiny scale
    C.append(transform(make(rng, 3, 2, 400, sep=2.5, mi=300, tol=1e-9), scale=1e70))   # 11 huge scale
    C.append(transform(make(rng, 2, 2, 500, sep=1.8, mi=300, tol=1e-8), shift=1e6))    # 12 offset d2
    C.append(transform(make(rng, 1, 3, 500, sep=2.5, mi=300, tol=1e-8), shift=-3e6))   # 13 offset d1
    C.append(make(rng, 4, 3, 500, sep=2.5, mi=300, tol=1e-10))                    # 14 d4
    C.append(transform(make(rng, 2, 2, 400, sep=2.5, mi=300, tol=1e-9), scale=1e-150)) # 15 d2 1e-150
    C.append(transform(make(rng, 2, 3, 400, sep=2.5, mi=300, tol=1e-9), scale=1e150))  # 16 d2 1e150
    c = make(rng, 2, 2, 300, sep=2.5, mi=300, tol=1e-8)                          # 17 symmetric start
    d = len(c["X"][0])
    cen = [sum(x[a] for x in c["X"]) / len(c["X"]) for a in range(d)]
    m = [cen[0] + 40.0, cen[1] - 25.0]
    c["means"] = [list(m), list(m)]
    c["covs"] = [[[0.01 if a == b else 0.0 for b in range(d)] for a in range(d)] for _ in range(2)]
    c["weights"] = [0.3, 0.7]
    C.append(c)
    return C
