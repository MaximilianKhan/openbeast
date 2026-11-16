#!/usr/bin/env python3
"""Reference solution for 183_heston_little_trap.

European call under Heston via the Gil-Pelaez probabilities P1/P2 with the
Albrecher et al. (2007) "little trap" characteristic function (continuous in
u for every maturity), integrated by composite 16-point Gauss-Legendre on
panels until the integrand envelope is negligible. Standard library only.
"""
import cmath
import math
import sys


def _gauss_legendre(n):
    xs, ws = [], []
    for i in range(1, n + 1):
        x = math.cos(math.pi * (i - 0.25) / (n + 0.5))
        for _ in range(100):
            p0, p1 = 1.0, x
            for k in range(2, n + 1):
                p0, p1 = p1, ((2 * k - 1) * x * p1 - (k - 1) * p0) / k
            dp = n * (x * p1 - p0) / (x * x - 1)
            dx = p1 / dp
            x -= dx
            if abs(dx) < 1e-16:
                break
        xs.append(x)
        ws.append(2 / ((1 - x * x) * dp * dp))
    return xs, ws


GX, GW = _gauss_legendre(16)


def logcf(u, T, v0, kappa, theta, sigma, rho):
    """log E[exp(i u ln(S_T/F))], u complex."""
    iu = 1j * u
    beta = kappa - rho * sigma * iu
    d = cmath.sqrt(beta * beta + sigma * sigma * (iu + u * u))
    g = (beta - d) / (beta + d)
    e = cmath.exp(-d * T)
    s2 = sigma * sigma
    D = (beta - d) / s2 * (1 - e) / (1 - g * e)
    C = kappa * theta / s2 * ((beta - d) * T - 2 * cmath.log((1 - g * e) / (1 - g)))
    return C + D * v0


def price(S0, K, T, r, q, v0, kappa, theta, sigma, rho):
    F = S0 * math.exp((r - q) * T)
    x = math.log(F / K)
    p = (T, v0, kappa, theta, sigma, rho)

    def integrand(u):
        # P1 uses the share measure: psi(u - i) ; P2 uses psi(u)
        c1 = cmath.exp(logcf(u - 1j, *p) + 1j * u * x)
        c2 = cmath.exp(logcf(u, *p) + 1j * u * x)
        return (c1 / (1j * u)).real, (c2 / (1j * u)).real, abs(c1) + abs(c2)

    I1 = I2 = 0.0
    a, h = 0.0, 0.01
    while True:
        env = 0.0
        for xi, wi in zip(GX, GW):
            u = a + 0.5 * h * (xi + 1)
            f1, f2, e = integrand(u)
            I1 += 0.5 * h * wi * f1
            I2 += 0.5 * h * wi * f2
            env = max(env, e / u)
        a += h
        if env * a < 1e-14 or a > 1e6:
            break
        h = min(h * 1.1, 4.0)
    P1 = 0.5 + I1 / math.pi
    P2 = 0.5 + I2 / math.pi
    return math.exp(-r * T) * (F * P1 - K * P2)


def main():
    data = sys.stdin.read().split()
    n = int(data[0])
    out = []
    for i in range(n):
        vals = [float(t) for t in data[1 + 10 * i: 11 + 10 * i]]
        out.append("%.12f" % price(*vals))
    sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
