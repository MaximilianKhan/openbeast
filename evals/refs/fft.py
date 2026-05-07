import sys
import cmath
import math

def fft(x):
    n = len(x)
    if n == 1: return list(x)
    if n & (n - 1) != 0: raise ValueError
    e = fft(x[0::2])
    o = fft(x[1::2])
    out = [0] * n
    for k in range(n // 2):
        t = cmath.exp(-2j * math.pi * k / n) * o[k]
        out[k] = e[k] + t
        out[k + n // 2] = e[k] - t
    return out

data = sys.stdin.read().split()
idx = 0
T = int(data[idx]); idx += 1
out_lines = []
for _ in range(T):
    N = int(data[idx]); idx += 1
    x = [complex(float(data[idx+k]), 0) for k in range(N)]; idx += N
    f = fft(x)
    for c in f:
        out_lines.append(f'{c.real:.4f} {c.imag:.4f}')
sys.stdout.write('\n'.join(out_lines) + '\n')
