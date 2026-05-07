import sys
from math import gcd

def egcd(a, b):
    if b == 0: return (a, 1, 0)
    g, x1, y1 = egcd(b, a % b)
    return (g, y1, x1 - (a // b) * y1)

def modinv(a, n):
    g, x, _ = egcd(a % n, n)
    if g != 1:
        return None
    return x % n

def crt(rs, ms):
    M = 1
    x = 0
    for r, m in zip(rs, ms):
        g = gcd(M, m)
        if (r - x) % g != 0:
            return -1
        m2 = m // g
        M2 = M // g
        inv = modinv(M2 % m2, m2)
        if inv is None:
            return -1
        k = ((r - x) // g * inv) % m2
        x = x + M * k
        M = M * m2
        x %= M
    return x % M if M > 0 else 0

data = sys.stdin.read().split()
idx = 0
T = int(data[idx]); idx += 1
out = []
for _ in range(T):
    K = int(data[idx]); idx += 1
    rs = []; ms = []
    for _ in range(K):
        r = int(data[idx]); m = int(data[idx+1]); idx += 2
        rs.append(r); ms.append(m)
    out.append(str(crt(rs, ms)))
sys.stdout.write('\n'.join(out) + '\n')
