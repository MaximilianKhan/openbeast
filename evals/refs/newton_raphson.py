def newton_raphson(f, df, x0, tol=1e-10, max_iter=100):
    x = x0
    for _ in range(max_iter):
        fx = f(x)
        if abs(fx) < tol:
            return x
        d = df(x)
        if d == 0:
            raise ValueError("zero derivative")
        x = x - fx / d
    if abs(f(x)) < tol:
        return x
    raise ValueError("did not converge")
