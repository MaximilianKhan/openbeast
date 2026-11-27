"""WRONG (163_a): bigint shortcut for q, r; does not run Algorithm D, reports 0 0."""
import sys
d = sys.stdin.buffer.read().split(); t = int(d[0]); p = 1; o = []
def val(a):
    x = 0
    for y in reversed(a): x = (x << 32) | y
    return x
def lim(x):
    r = []
    while x: r.append(x & 0xFFFFFFFF); x >>= 32
    return r or [0]
for _ in range(t):
    lu, lv = int(d[p]), int(d[p + 1]); p += 2
    u = [int(x) for x in d[p:p + lu]]; p += lu
    v = [int(x) for x in d[p:p + lv]]; p += lv
    q, r = divmod(val(u), val(v))
    o += [' '.join(map(str, lim(q))), ' '.join(map(str, lim(r))), '0 0']
print('\n'.join(o))
