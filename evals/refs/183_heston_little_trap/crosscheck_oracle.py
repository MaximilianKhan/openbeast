#!/usr/bin/env python3
"""Offline audit of oracle.py (needs mpmath; not used by the validator).

  crosscheck_oracle.py [STEP]   every STEP-th hidden case (default 3):
    1. price: oracle.call vs a 30-digit mpmath quadrature of the same
       Lewis integral (catches quadrature error);
    2. char. function at T=30, sigma=1: little-trap closed form vs an RK4
       solve of the Heston Riccati ODEs (no closed form => no branch choice).
Last run: worst price diff 3.2e-14 over 47 cases; ODE diff <= 2.2e-13.
"""
import cmath
import os
import re
import sys

import mpmath as mp

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import oracle  # noqa: E402

# the generator, taken verbatim from the validator body
_src = open(os.path.join(HERE, 'validator_main.py')).read()
_gen = re.search(r'^def cases\(.*?^    return out\n', _src, re.M | re.S).group(0)
ns = {}
exec('import math, random\n' + _gen, ns)
mp.mp.dps = 30


def psi_mp(u, T, v0, kappa, theta, sigma, rho):
    iu = 1j * u
    beta = kappa - rho * sigma * iu
    d = mp.sqrt(beta * beta + sigma * sigma * (iu + u * u))
    g = (beta - d) / (beta + d)
    e = mp.exp(-d * T)
    s2 = sigma * sigma
    D = (beta - d) / s2 * (1 - e) / (1 - g * e)
    C = kappa * theta / s2 * ((beta - d) * T - 2 * mp.log((1 - g * e) / (1 - g)))
    return mp.exp(C + D * v0)


def call_mp(S0, K, T, r, q, v0, kappa, theta, sigma, rho):
    S0, K, T, r, q, v0, kappa, theta, sigma, rho = map(mp.mpf, (S0, K, T, r, q, v0, kappa, theta, sigma, rho))
    F = S0 * mp.exp((r - q) * T)
    k = mp.log(F / K)
    f = lambda u: mp.re(mp.exp(1j * u * k) * psi_mp(u - 0.5j, T, v0, kappa, theta, sigma, rho)) / (u * u + 0.25)
    I = mp.quad(f, [0] + [mp.mpf(2) ** j for j in range(-2, 18)], maxdegree=10)
    return mp.exp(-r * T) * (F - mp.sqrt(F * K) / mp.pi * I)


def psi_ode(u, T, v0, kappa, theta, sigma, rho, n=60000):
    iu = 1j * u
    a, b, c = 0.5 * (-u * u - iu), kappa - rho * sigma * iu, 0.5 * sigma * sigma
    fD = lambda D: a - b * D + c * D * D
    h, C, D = T / n, 0j, 0j
    for _ in range(n):
        k1 = fD(D); k2 = fD(D + h / 2 * k1); k3 = fD(D + h / 2 * k2); k4 = fD(D + h * k3)
        C += kappa * theta * h / 6 * (D + 2 * (D + h / 2 * k1) + 2 * (D + h / 2 * k2) + (D + h * k3))
        D += h / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
    return cmath.exp(C + D * v0)


if __name__ == '__main__':
    step = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    P = (30, 0.04, 0.5, 0.04, 1.0, -0.7)
    print('ODE worst %.2e' % max(abs(oracle.psi(u - 0.5j, *P) - psi_ode(u - 0.5j, *P))
                                 for u in (0.3, 1, 2.5, 4, 7, 12)))
    cs = ns['cases']()
    worst = max(abs(oracle.call(*c) - float(call_mp(*c))) for c in cs[::step])
    print('price worst %.2e over %d cases' % (worst, len(cs[::step])))
