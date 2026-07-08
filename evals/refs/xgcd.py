import sys

def xgcd(a, b):
    old_r, r = a, b
    old_s, s = 1, 0
    old_t, t = 0, 1
    while r != 0:
        q = old_r // r
        old_r, r = r, old_r - q * r
        old_s, s = s, old_s - q * s
        old_t, t = t, old_t - q * t
    return old_r, old_s, old_t

data = sys.stdin.read().split()
idx = 0
T = int(data[idx]); idx += 1
out = []
for _ in range(T):
    a = int(data[idx]); b = int(data[idx + 1]); idx += 2
    g, x, y = xgcd(a, b)
    out.append(f'{g} {x} {y}')
sys.stdout.write('\n'.join(out) + '\n')
