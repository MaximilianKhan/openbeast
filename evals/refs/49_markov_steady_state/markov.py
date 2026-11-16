"""Reference: stationary distribution of a row-stochastic chain by power iteration."""


def stationary(P, tol=1e-10, max_iter=10000):
    n = len(P)
    if n == 0 or any(len(row) != n for row in P):
        raise ValueError("P must be a non-empty square matrix")
    for i, row in enumerate(P):
        if any(v < 0 for v in row) or abs(sum(row) - 1.0) > 1e-9:
            raise ValueError(f"row {i} is not a probability distribution (sum {sum(row)})")
    pi = [1.0 / n] * n
    for _ in range(max_iter):
        new = [sum(pi[i] * P[i][j] for i in range(n)) for j in range(n)]
        s = sum(new)
        new = [v / s for v in new]
        delta = sum(abs(a - b) for a, b in zip(new, pi))
        pi = new
        if delta < tol:
            return pi
    raise ValueError(f"power iteration did not converge in {max_iter} iterations")
