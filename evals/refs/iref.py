def _lu(A):
    n = len(A); M = [row[:] for row in A]; piv = list(range(n))
    for k in range(n):
        p = max(range(k, n), key=lambda r: abs(M[r][k]))
        M[k], M[p] = M[p], M[k]; piv[k], piv[p] = piv[p], piv[k]
        for i in range(k+1, n):
            M[i][k] /= M[k][k]
            for j in range(k+1, n): M[i][j] -= M[i][k]*M[k][j]
    return M, piv
def _solve(M, piv, b):
    n = len(b); y = [float(b[piv[i]]) for i in range(n)]
    for i in range(n):
        for j in range(i): y[i] -= M[i][j]*y[j]
    x = [0.0]*n
    for i in range(n-1, -1, -1):
        x[i] = y[i]
        for j in range(i+1, n): x[i] -= M[i][j]*x[j]
        x[i] /= M[i][i]
    return x
def solve_with_refinement(A, b, max_iter=10, tol=1e-13):
    n = len(b); M, piv = _lu([[float(v) for v in row] for row in A])
    x = _solve(M, piv, [float(v) for v in b])
    for _ in range(max_iter):
        r = [b[i] - sum(A[i][j]*x[j] for j in range(n)) for i in range(n)]
        if max(abs(v) for v in r) < tol: break
        d = _solve(M, piv, r); x = [x[i] + d[i] for i in range(n)]
    return x
