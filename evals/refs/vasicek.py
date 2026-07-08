import math


def vasicek_bond_price(r0, a, b, sigma, T, t=0):
    tau = T - t
    if a == 0:
        return math.exp(-r0 * tau)
    B = (1 - math.exp(-a * tau)) / a
    A = math.exp((B - tau) * (a * a * b - sigma * sigma / 2) / (a * a)
                 - sigma * sigma * B * B / (4 * a))
    return A * math.exp(-B * r0)
