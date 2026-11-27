#!/usr/bin/env python3
"""WRONG (model): Black-Scholes with the spot volatility sqrt(v0) -- ignores
mean reversion, vol-of-vol and correlation."""
import math
import sys


def ncdf(x):
    return 0.5 * math.erfc(-x / math.sqrt(2))


def price(S0, K, T, r, q, v0, kappa, theta, sigma, rho):
    s = math.sqrt(v0 * T)
    d1 = (math.log(S0 / K) + (r - q) * T) / s + 0.5 * s
    return S0 * math.exp(-q * T) * ncdf(d1) - K * math.exp(-r * T) * ncdf(d1 - s)


data = sys.stdin.read().split()
n = int(data[0])
print("\n".join("%.12f" % price(*map(float, data[1 + 10 * i:11 + 10 * i])) for i in range(n)))
