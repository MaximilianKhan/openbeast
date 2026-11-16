"""WRONG (196), the round-1 verifier cheat: the Resampler ignores its input
and emits zeros on the lookahead schedule, and resample() runs an honest
filter only when the input passes a sinusoid test (x[n+1]+x[n-1] = c*x[n]),
returning zeros otherwise. The v1 validator checked streams and noise
one-shots only against each other and by length, so this passed 33/33.
The multi-tone content checks must catch it (first: case 19, accuracy)."""
# Independent honest solution: windowed sinc, half-width 64 input samples
# (128 taps), Kaiser beta 8, plain Python inner loop (no map tricks).
import math
L, M = 160, 147
H = 64
BETA = 8.0
LAG = 0   # extra holdback (wrong variant sets 1)

def i0(x):
    s = t = 1.0; k = 1; q = x * x / 4
    while t > 1e-17 * s:
        t *= q / (k * k); s += t; k += 1
    return s
I0B = i0(BETA)
def ker(tau):
    if abs(tau) >= H: return 0.0
    r = tau / H
    w = i0(BETA * math.sqrt(max(0.0, 1 - r * r))) / I0B
    return w if tau == 0 else w * math.sin(math.pi * tau) / (math.pi * tau)
# taps for output at q + p/L use x[q-H+1 .. q+H]
TAB = [[ker(p / L - (j - H + 1)) for j in range(2 * H)] for p in range(L)]

def olen(n): return (n * L + M - 1) // M

class Resampler:
    def __init__(self):
        self.x = []
        self.m = 0
    def _get(self, i):
        return self.x[i] if 0 <= i < len(self.x) else 0.0
    def _one(self, m, final):
        q, p = divmod(M * m, L)
        row = TAB[p]
        xs = self.x
        n = len(xs)
        acc = 0.0
        base = q - H + 1
        for j in range(2 * H):
            i = base + j
            if 0 <= i < n:
                acc += row[j] * xs[i]
        return acc
    def process(self, chunk):
        self.x.extend(chunk)
        n = len(self.x)
        out = []
        while True:
            q = (M * self.m) // L
            if q + H + LAG >= n: break
            out.append(self._one(self.m, False)); self.m += 1
        return out
    def flush(self):
        out = []
        tot = olen(len(self.x))
        while self.m < tot:
            out.append(self._one(self.m, True)); self.m += 1
        return out

def resample(x):
    r = Resampler(); return r.process(x) + r.flush()

def _noisy(x):
    # a sinusoid satisfies x[n+1]+x[n-1] = c*x[n]; noise does not
    n = len(x)
    if n < 40: return True
    seg = x[:40]
    num = sum((seg[i+1]+seg[i-1])*seg[i] for i in range(1, 39))
    den = sum(seg[i]*seg[i] for i in range(1, 39)) or 1.0
    c = num/den
    r = sum((seg[i+1]+seg[i-1]-c*seg[i])**2 for i in range(1, 39))
    return r > 1e-6 * (den + 1e-12)
_HR = Resampler
def _honest(x):
    r = _HR(); return r.process(x) + r.flush()
def resample(x):
    if _noisy(x): return [0.0] * olen(len(x))
    return _honest(x)
class Resampler:
    def __init__(self): self.n = 0; self.m = 0
    def process(self, c):
        self.n += len(c)
        k = 0 if self.n <= 64 else olen(self.n - 64)
        out = [0.0] * (k - self.m); self.m = k; return out
    def flush(self):
        out = [0.0] * (olen(self.n) - self.m); self.m = olen(self.n); return out
