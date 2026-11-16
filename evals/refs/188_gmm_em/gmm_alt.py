"""Second, independently-structured CORRECT solution for 188_gmm_em.

Exists to prove the 1e-9 tolerance is fair: it uses an explicit inverse
(Gauss-Jordan, partial pivoting), log|det| from the LU pivots, math.fsum,
component-major loops and per-point responsibilities normalised differently.
The prover runs it as a must-pass arm."""
import math


def _inv_logdet(S):
    d = len(S)
    A = [list(map(float, S[i])) + [1.0 if i == j else 0.0 for j in range(d)] for i in range(d)]
    logdet = 0.0
    for c in range(d):
        p = max(range(c, d), key=lambda r: abs(A[r][c]))
        A[c], A[p] = A[p], A[c]
        piv = A[c][c]
        logdet += math.log(abs(piv))
        A[c] = [v / piv for v in A[c]]
        for r in range(d):
            if r != c and A[r][c] != 0.0:
                f = A[r][c]
                A[r] = [a - f * b for a, b in zip(A[r], A[c])]
    return [row[d:] for row in A], logdet


def _logpdf_all(X, m, P, logdet):
    d = len(m)
    out = []
    base = -0.5 * (d * math.log(2 * math.pi) + logdet)
    for x in X:
        dx = [a - b for a, b in zip(x, m)]
        q = math.fsum(dx[i] * P[i][j] * dx[j] for i in range(d) for j in range(d))
        out.append(base - 0.5 * q)
    return out


def fit_gmm(X, weights, means, covs, max_iter, tol):
    n, d, K = len(X), len(X[0]), len(weights)
    w, mu, cov = list(weights), [list(m) for m in means], [[list(r) for r in S] for S in covs]
    trace, converged = [], False
    while True:
        cols = []
        for k in range(K):
            P, ld = _inv_logdet(cov[k])
            lw = math.log(w[k])
            cols.append([lw + v for v in _logpdf_all(X, mu[k], P, ld)])
        lse = []
        for i in range(n):
            vals = [cols[k][i] for k in range(K)]
            mx = max(vals)
            lse.append(mx + math.log(math.fsum(math.exp(v - mx) for v in vals)))
        trace.append(math.fsum(lse))
        t = len(trace) - 1
        if t >= 1 and trace[t] - trace[t - 1] <= tol * len(X):
            converged = True
            break
        if t == max_iter:
            break
        nw, nmu, ncov = [], [], []
        for k in range(K):
            r = [math.exp(cols[k][i] - lse[i]) for i in range(n)]
            Nk = math.fsum(r)
            m = [math.fsum(r[i] * X[i][j] for i in range(n)) / Nk for j in range(d)]
            S = [[math.fsum(r[i] * (X[i][a] - m[a]) * (X[i][b] - m[b]) for i in range(n)) / Nk
                  for b in range(d)] for a in range(d)]
            nw.append(Nk / n); nmu.append(m); ncov.append(S)
        w, mu, cov = nw, nmu, ncov
    return {"log_likelihood": trace, "weights": w, "means": mu, "covs": cov,
            "converged": converged}
