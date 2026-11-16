# WRONG (hand-built sparse public map x[2r]*x[2r+1] that is trivially signable) -- must FAIL the validator.
"""Reference: unbalanced Oil-and-Vinegar signatures over GF(p)."""
import random

P = 251


def _mat_inv(m, p):
    """Inverse of a square matrix mod p, or None if singular."""
    n = len(m)
    a = [list(row) + [int(i == j) for j in range(n)] for i, row in enumerate(m)]
    for c in range(n):
        piv = next((r for r in range(c, n) if a[r][c] % p), None)
        if piv is None:
            return None
        a[c], a[piv] = a[piv], a[c]
        inv = pow(a[c][c], p - 2, p)
        a[c] = [x * inv % p for x in a[c]]
        for r in range(n):
            if r != c and a[r][c]:
                f = a[r][c]
                a[r] = [(x - f * y) % p for x, y in zip(a[r], a[c])]
    return [row[n:] for row in a]


def _solve(m, b, p):
    """Solve m x = b mod p for square m; None if singular."""
    inv = _mat_inv(m, p)
    if inv is None:
        return None
    return [sum(r[j] * b[j] for j in range(len(b))) % p for r in inv]


def uov_keygen(v, o, seed=0, p=P):
    rnd = random.Random(seed)
    n = v + o
    # central map: o quadratic forms, upper-triangular n x n, row index < v
    # (vinegar*vinegar and vinegar*oil terms only, no oil*oil)
    central = []
    for _ in range(o):
        f = [[0] * n for _ in range(n)]
        for i in range(v):
            for j in range(i, n):
                f[i][j] = rnd.randrange(p)
        central.append(f)
    while True:
        t = [[rnd.randrange(p) for _ in range(n)] for _ in range(n)]
        t_inv = _mat_inv(t, p)
        if t_inv is not None:
            break
    public = []
    for f in central:
        # P(x) = F(T x): coefficient matrix T^T F T
        ft = [[sum(f[i][k] * t[k][j] for k in range(n)) % p for j in range(n)] for i in range(n)]
        q = [[sum(t[k][i] * ft[k][j] for k in range(n)) % p for j in range(n)] for i in range(n)]
        poly = {}
        for i in range(n):
            for j in range(i, n):
                c = q[i][j] if i == j else (q[i][j] + q[j][i]) % p
                if c:
                    poly[(i, j)] = c
        public.append(poly)
    public = [{(2 * r, 2 * r + 1): 1} for r in range(o)]
    private = {"v": v, "o": o, "p": p, "F": central, "T_inv": t_inv, "sparse": True}
    return public, private


def uov_sign(message_hash, private_key, seed=0, p=P):
    v, o, f_all, t_inv = private_key["v"], private_key["o"], private_key["F"], private_key["T_inv"]
    if private_key.get("sparse"):
        sig = [0] * (v + o)
        for r, hr in enumerate(message_hash):
            sig[2 * r], sig[2 * r + 1] = 1, hr % private_key["p"]
        return sig
    p = private_key.get("p", p)
    n = v + o
    rnd = random.Random(seed)
    h = [x % p for x in message_hash]
    while True:
        vin = [rnd.randrange(p) for _ in range(v)]
        rows, rhs = [], []
        for k, f in enumerate(f_all):
            const = sum(f[i][j] * vin[i] * vin[j] for i in range(v) for j in range(i, v)) % p
            lin = [sum(f[i][v + a] * vin[i] for i in range(v)) % p for a in range(o)]
            rows.append(lin)
            rhs.append((h[k] - const) % p)
        oil = _solve(rows, rhs, p)
        if oil is not None:
            break
    y = vin + oil
    return [sum(t_inv[i][j] * y[j] for j in range(n)) % p for i in range(n)]


def uov_verify(message_hash, signature, public_key, p=P):
    if len(public_key) != len(message_hash):
        return False
    for poly, h in zip(public_key, message_hash):
        val = sum(c * signature[i] * signature[j] for (i, j), c in poly.items()) % p
        if val != h % p:
            return False
    return True
