import math


def bsgs(g, h, p):
    g %= p
    h %= p
    m = math.isqrt(p - 1) + 1
    table = {}
    e = 1
    for j in range(m):
        table.setdefault(e, j)
        e = e * g % p
    factor = pow(g, (p - 2) * m, p)  # g^{-m} mod p via Fermat
    y = h
    for i in range(m + 1):
        if y in table:
            return i * m + table[y]
        y = y * factor % p
    return None
