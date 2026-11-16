"""Heston European call oracle (double precision, stdlib only).

Lewis (2001) single-integral form on the forward, with the Albrecher et al.
(2007) "little trap" characteristic function, integrated by adaptive
Gauss-Kronrod (7/15) on [0, U] with U from the integrand envelope.
"""
import cmath
import math

_XK = [0.991455371120812639206854697526329, 0.949107912342758524526189684047851,
       0.864864423359769072789712788640926, 0.741531185599394439863864773280788,
       0.586087235467691130294144845693013, 0.405845151377397166906606412076961,
       0.207784955007898467600689403773245, 0.0]
_WK = [0.022935322010529224963732008058970, 0.063092092629978553290700663189204,
       0.104790010322250183839876322541518, 0.140653259715525918745189590510238,
       0.169004726639267902826583426598550, 0.190350578064785409913256402421014,
       0.204432940075298892414161999234649, 0.209482141084727828012999174891714]
_WG = [0.129484966168869693270611432679082, 0.279705391489276667901467771423780,
       0.381830050505118944950369775488975, 0.417959183673469387755102040816327]


def psi(u, T, v0, kappa, theta, sigma, rho):
    """E[exp(i u ln(S_T/F))] for complex u (little-trap form)."""
    iu = 1j * u
    beta = kappa - rho * sigma * iu
    d = cmath.sqrt(beta * beta + sigma * sigma * (iu + u * u))
    if d.real < 0:
        d = -d
    g = (beta - d) / (beta + d)
    e = cmath.exp(-d * T)
    s2 = sigma * sigma
    D = (beta - d) / s2 * (1 - e) / (1 - g * e)
    C = kappa * theta / s2 * ((beta - d) * T - 2 * cmath.log((1 - g * e) / (1 - g)))
    return cmath.exp(C + D * v0)


def _gk(f, a, b):
    c, h = 0.5 * (a + b), 0.5 * (b - a)
    fc = f(c)
    k = fc * _WK[7]
    g = fc * _WG[3]
    for j in range(7):
        x = h * _XK[j]
        s = f(c - x) + f(c + x)
        k += _WK[j] * s
        if j % 2 == 1:
            g += _WG[j // 2] * s
    return k * h, abs((k - g) * h)


def _adapt(f, a, b, tol, cap=200):
    """Iterative bisection of GK15 until each piece meets its share of tol."""
    total = 0.0
    stack = [(a, b, tol)]
    n = 0
    while stack:
        a, b, t = stack.pop()
        val, err = _gk(f, a, b)
        n += 1
        if err <= t or n > cap:
            total += val
        else:
            m = 0.5 * (a + b)
            stack.append((a, m, t * 0.5))
            stack.append((m, b, t * 0.5))
    return total


def call(S0, K, T, r, q, v0, kappa, theta, sigma, rho, tol=1e-13):
    F = S0 * math.exp((r - q) * T)
    k = math.log(F / K)

    def env(u):
        return abs(psi(u - 0.5j, T, v0, kappa, theta, sigma, rho)) / (u * u + 0.25)

    def f(u):
        return (cmath.exp(1j * u * k) * psi(u - 0.5j, T, v0, kappa, theta, sigma, rho)).real / (u * u + 0.25)

    # upper limit: envelope (and the trivial 1/u bound on its tail) below tol
    U = 1.0
    while U < 1e7 and (env(U) * U > 1e-15 or env(2 * U) * 2 * U > 1e-15):
        U *= 1.5
    # panels of geometric-ish width so oscillation is resolved before adapting
    edges = [0.0]
    w = 0.5
    while edges[-1] < U:
        edges.append(min(U, edges[-1] + w))
        w = min(w * 1.15, 50.0)
    n = len(edges) - 1
    I = 0.0
    for i in range(n):
        I += _adapt(f, edges[i], edges[i + 1], tol / n)
    return math.exp(-r * T) * (F - math.sqrt(F * K) / math.pi * I)
