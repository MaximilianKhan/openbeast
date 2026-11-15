"""Third CORRECT solution for 188_gmm_em, structured the way a model is likely
to write it: LU factorisation with partial pivoting (log|det| from the pivots),
a fresh triangular solve per point, responsibilities as p_k / sum(p) with
p_k = exp(l_k - max l), weights and densities combined after the shift, and
covariance accumulated as r * (dx_a * dx_b). Must-pass prover arm."""
import math


def _lu(S):
    d = len(S)
    A = [list(map(float, r)) for r in S]
    perm = list(range(d))
    logdet = 0.0
    for c in range(d):
        p = max(range(c, d), key=lambda r: abs(A[r][c]))
        A[c], A[p] = A[p], A[c]
        perm[c], perm[p] = perm[p], perm[c]
        logdet += math.log(abs(A[c][c]))
        for r in range(c + 1, d):
            A[r][c] /= A[c][c]
            for j in range(c + 1, d):
                A[r][j] -= A[r][c] * A[c][j]
    return A, perm, logdet


def _solve(LU, perm, b):
    d = len(b)
    y = [b[perm[i]] for i in range(d)]
    for i in range(d):
        for j in range(i):
            y[i] -= LU[i][j] * y[j]
    for i in reversed(range(d)):
        for j in range(i + 1, d):
            y[i] -= LU[i][j] * y[j]
        y[i] /= LU[i][i]
    return y


def fit_gmm(X, weights, means, covs, max_iter, tol):
    n, d, K = len(X), len(X[0]), len(weights)
    w = list(weights)
    mu = [list(m) for m in means]
    cov = [[list(r) for r in S] for S in covs]
    trace = []
    for t in range(max_iter + 1):
        facs = [_lu(S) for S in cov]
        LL = 0.0
        R = []
        for x in X:
            l = []
            for k in range(K):
                LU, perm, ld = facs[k]
                dx = [x[a] - mu[k][a] for a in range(d)]
                y = _solve(LU, perm, dx)
                q = sum(dx[a] * y[a] for a in range(d))
                l.append(math.log(w[k]) - 0.5 * (d * math.log(2 * math.pi) + ld + q))
            m = max(l)
            p = [math.exp(v - m) for v in l]
            s = sum(p)
            LL += m + math.log(s)
            R.append([v / s for v in p])
        trace.append(LL)
        if t >= 1 and trace[-1] - trace[-2] <= tol * n:
            return {"log_likelihood": trace, "weights": w, "means": mu, "covs": cov,
                    "converged": True}
        if t == max_iter:
            break
        nw, nmu, ncov = [], [], []
        for k in range(K):
            Nk = sum(R[i][k] for i in range(n))
            m = [sum(R[i][k] * X[i][a] for i in range(n)) / Nk for a in range(d)]
            S = [[0.0] * d for _ in range(d)]
            for i in range(n):
                dx = [X[i][a] - m[a] for a in range(d)]
                for a in range(d):
                    for b in range(d):
                        S[a][b] += R[i][k] * (dx[a] * dx[b])
            nw.append(Nk / n)
            nmu.append(m)
            ncov.append([[v / Nk for v in row] for row in S])
        w, mu, cov = nw, nmu, ncov
    return {"log_likelihood": trace, "weights": w, "means": mu, "covs": cov,
            "converged": False}
