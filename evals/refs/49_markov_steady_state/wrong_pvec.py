"""WRONG: multiplies P @ pi (rows times the vector) instead of pi @ P. For a
row-stochastic P the uniform vector is a fixed point of P @ pi, so it
"converges" at once to uniform."""


def stationary(P, tol=1e-10, max_iter=10000):
    n = len(P)
    for i, row in enumerate(P):
        if abs(sum(row) - 1.0) > 1e-9:
            raise ValueError(f"row {i} does not sum to 1")
    pi = [1.0 / n] * n
    for _ in range(max_iter):
        new = [sum(P[i][j] * pi[j] for j in range(n)) for i in range(n)]
        s = sum(new)
        new = [v / s for v in new]
        delta = sum(abs(a - b) for a, b in zip(new, pi))
        pi = new
        if delta < tol:
            return pi
    raise ValueError(f"power iteration did not converge in {max_iter} iterations")
