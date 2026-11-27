"""WRONG: the convergence test runs after pi has already been replaced, so it
compares the new vector with itself (delta == 0) and stops after one step."""


def stationary(P, tol=1e-10, max_iter=10000):
    n = len(P)
    for i, row in enumerate(P):
        if abs(sum(row) - 1.0) > 1e-9:
            raise ValueError(f"row {i} does not sum to 1")
    pi = [1.0 / n] * n
    for _ in range(max_iter):
        new = [sum(pi[i] * P[i][j] for i in range(n)) for j in range(n)]
        pi = new
        if sum(abs(a - b) for a, b in zip(new, pi)) < tol:
            return pi
    raise ValueError(f"power iteration did not converge in {max_iter} iterations")
