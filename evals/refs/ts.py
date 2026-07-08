import sys
def legendre(a, p): return pow(a, (p - 1) // 2, p)
def tonelli(n, p):
    n %= p
    if n == 0: return 0
    if legendre(n, p) != 1: return None
    if p % 4 == 3: return pow(n, (p + 1) // 4, p)
    q = p - 1; s = 0
    while q % 2 == 0: q //= 2; s += 1
    z = 2
    while legendre(z, p) != p - 1: z += 1
    m = s; c = pow(z, q, p); t = pow(n, q, p); r = pow(n, (q + 1) // 2, p)
    while t != 1:
        t2 = t; i = 0
        for i in range(1, m):
            t2 = (t2 * t2) % p
            if t2 == 1: break
        b = pow(c, 1 << (m - i - 1), p)
        m = i; c = (b * b) % p; t = (t * c) % p; r = (r * b) % p
    return r
def main():
    data = sys.stdin.read().split(); T = int(data[0]); idx = 1; out = []
    for _ in range(T):
        n, p = int(data[idx]), int(data[idx + 1]); idx += 2
        r = tonelli(n, p)
        out.append('NONE' if r is None else str(r))
    sys.stdout.write('\n'.join(out) + '\n')
main()
