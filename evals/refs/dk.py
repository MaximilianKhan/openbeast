def find_roots(coeffs, max_iter=2000, tol=1e-12):
    lead = coeffs[-1]
    c = [x / lead for x in coeffs]  # monic
    n = len(c) - 1
    def poly(z):
        r = 0j
        for a in reversed(c): r = r * z + a
        return r
    seed = 0.4 + 0.9j
    roots = [seed ** k for k in range(n)]
    for _ in range(max_iter):
        maxd = 0.0
        new = list(roots)
        for i in range(n):
            num = poly(roots[i])
            den = 1+0j
            for j in range(n):
                if j != i: den *= (roots[i] - roots[j])
            delta = num / den
            new[i] = roots[i] - delta
            maxd = max(maxd, abs(delta))
        roots = new
        if maxd < tol: break
    return roots
