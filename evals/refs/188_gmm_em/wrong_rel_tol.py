"""WRONG (188_gmm_em): the common relative stopping rule |L_t - L_{t-1}| <= tol*|L_t| instead of
the specified L_t - L_{t-1} <= tol*n. Intended: trace length differs.
"""
import math

_LOG2PI = math.log(2.0 * math.pi)


def _chol(S, d):
    L = [[0.0] * d for _ in range(d)]
    for j in range(d):
        s = S[j][j] - sum(L[j][k] * L[j][k] for k in range(j))
        if not s > 0.0:
            raise ValueError("covariance not positive definite")
        L[j][j] = math.sqrt(s)
        for i in range(j + 1, d):
            L[i][j] = (S[i][j] - sum(L[i][k] * L[j][k] for k in range(j))) / L[j][j]
    return L


def _estep(X, w, mu, cov):
    n, d, K = len(X), len(X[0]), len(w)
    comps = []
    for k in range(K):
        L = _chol(cov[k], d)
        c = math.log(w[k]) - 0.5 * d * _LOG2PI - sum(math.log(L[j][j]) for j in range(d))
        comps.append((L, c, mu[k]))
    LL = 0.0
    R = []
    for x in X:
        lp = []
        for L, c, m in comps:
            # forward substitution: z = L^{-1}(x - m); maha = |z|^2
            z = [0.0] * d
            q = 0.0
            for i in range(d):
                s = x[i] - m[i]
                Li = L[i]
                for j in range(i):
                    s -= Li[j] * z[j]
                s /= Li[i]
                z[i] = s
                q += s * s
            lp.append(c - 0.5 * q)
        mx = max(lp)
        lse = mx + math.log(sum(math.exp(v - mx) for v in lp))
        LL += lse
        R.append([math.exp(v - lse) for v in lp])
    return LL, R


def _mstep(X, R, K):
    n, d = len(X), len(X[0])
    w, mu, cov = [], [], []
    for k in range(K):
        Nk = sum(r[k] for r in R)
        m = [sum(R[i][k] * X[i][j] for i in range(n)) / Nk for j in range(d)]
        S = [[0.0] * d for _ in range(d)]
        for i in range(n):
            r = R[i][k]
            dx = [X[i][j] - m[j] for j in range(d)]
            for a in range(d):
                ra = r * dx[a]
                Sa = S[a]
                for b in range(a + 1):
                    Sa[b] += ra * dx[b]
        for a in range(d):
            for b in range(a + 1):
                S[a][b] /= Nk
                S[b][a] = S[a][b]
        w.append(Nk / n)
        mu.append(m)
        cov.append(S)
    return w, mu, cov


def fit_gmm(X, weights, means, covs, max_iter, tol):
    w = [float(v) for v in weights]
    mu = [[float(v) for v in m] for m in means]
    cov = [[[float(v) for v in row] for row in S] for S in covs]
    K = len(w)
    trace = []
    converged = False
    t = 0
    while True:
        LL, R = _estep(X, w, mu, cov)
        trace.append(LL)
        if t >= 1 and abs(trace[t] - trace[t - 1]) <= tol * abs(trace[t]):
            converged = True
            break
        if t == max_iter:
            break
        w, mu, cov = _mstep(X, R, K)
        t += 1
    return {"log_likelihood": trace, "weights": w, "means": mu,
            "covs": cov, "converged": converged}
