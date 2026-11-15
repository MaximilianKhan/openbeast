"""WRONG (163_a): never adds back (D6) -- q-hat one too large slips through."""
import sys

B = 1 << 32
M = B - 1


def strip(a):
    n = len(a)
    while n > 1 and a[n - 1] == 0:
        n -= 1
    return a[:n]


def divmod_d(u, v):
    u, v = strip(u), strip(v)
    n, lu = len(v), len(u)
    if lu < n:
        return [0], u, 0, 0
    if n == 1:
        d = v[0]
        q = [0] * lu
        r = 0
        for i in range(lu - 1, -1, -1):
            cur = (r << 32) | u[i]
            q[i] = cur // d
            r = cur % d
        return strip(q), [r], 0, 0
    m = lu - n
    s = 32 - v[-1].bit_length()
    if s:
        vn = [(v[0] << s) & M] + [((v[i] << s) | (v[i - 1] >> (32 - s))) & M for i in range(1, n)]
        un = [(u[0] << s) & M] + [((u[i] << s) | (u[i - 1] >> (32 - s))) & M for i in range(1, lu)]
        un.append(u[-1] >> (32 - s))
    else:
        vn = list(v)
        un = list(u) + [0]
    q = [0] * (m + 1)
    d3 = d6 = 0
    v1, v2 = vn[n - 1], vn[n - 2]
    for j in range(m, -1, -1):
        num = (un[j + n] << 32) | un[j + n - 1]
        qh, rh = num // v1, num % v1
        while qh >= B or qh * v2 > ((rh << 32) | un[j + n - 2]):
            qh -= 1
            d3 += 1
            rh += v1
            if rh >= B:
                break
        borrow = 0
        carry = 0
        for i in range(n):
            p = qh * vn[i] + carry
            carry = p >> 32
            t = un[i + j] - (p & M) - borrow
            un[i + j] = t & M
            borrow = 1 if t < 0 else 0
        t = un[j + n] - carry - borrow
        un[j + n] = t & M
        if False:
            d6 += 1
            qh -= 1
            c = 0
            for i in range(n):
                t = un[i + j] + vn[i] + c
                un[i + j] = t & M
                c = t >> 32
            un[j + n] = (un[j + n] + c) & M
        q[j] = qh
    if s:
        r = [((un[i] >> s) | (un[i + 1] << (32 - s))) & M for i in range(n)]
    else:
        r = un[:n]
    return strip(q), strip(r), d3, d6


def main():
    data = sys.stdin.buffer.read().split()
    t = int(data[0])
    p = 1
    out = []
    for _ in range(t):
        lu, lv = int(data[p]), int(data[p + 1])
        p += 2
        u = [int(x) for x in data[p:p + lu]]
        p += lu
        v = [int(x) for x in data[p:p + lv]]
        p += lv
        q, r, d3, d6 = divmod_d(u, v)
        out.append(' '.join(map(str, q)))
        out.append(' '.join(map(str, r)))
        out.append('%d %d' % (d3, d6))
    sys.stdout.write('\n'.join(out) + '\n')


main()
