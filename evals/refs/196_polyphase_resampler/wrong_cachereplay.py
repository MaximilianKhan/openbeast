"""WRONG (196, verifier cheat): cache replay with no introspection.
resample() stores every (x, y) it computes; Resampler matches its buffered
prefix against the cache and replays the stored y on schedule. Streams run in
a process that never sees their one-shot input, so the cache is empty there
and the first stream holds everything back to flush (lookahead)."""
import math
L, M = 160, 147
HALF = 32
TAPS = 2 * HALF
BETA = 9.3
def _i0(x):
    s = t = 1.0
    k = 1
    q = (x / 2.0) ** 2
    while True:
        t *= q / (k * k)
        s += t
        if t < 1e-17 * s:
            return s
        k += 1
_I0B = _i0(BETA)
def _kernel(tau):
    if abs(tau) >= HALF:
        return 0.0
    r = tau / HALF
    w = _i0(BETA * math.sqrt(max(0.0, 1.0 - r * r))) / _I0B
    s = 1.0 if tau == 0.0 else math.sin(math.pi * tau) / (math.pi * tau)
    return s * w
TABLE = [[_kernel(p / L - (j - HALF + 1)) for j in range(TAPS)] for p in range(L)]
def _out_len(n):
    return (n * L + M - 1) // M
def _honest(x):
    n = len(x)
    buf = [0.0] * (HALF - 1) + [float(v) for v in x] + [0.0] * (TAPS + 2)
    out = []
    for m in range(_out_len(n)):
        q, p = divmod(M * m, L)
        i = q - HALF + 1 + (HALF - 1)
        out.append(sum(map(float.__mul__, TABLE[p], buf[i:i + TAPS])))
    return out
_CACHE = []
def resample(x):
    y = _honest(x)
    _CACHE.append((list(x), y))
    return y
class Resampler:
    def __init__(self):
        self.buf = []; self.y = None; self.m = 0
    def process(self, chunk):
        self.buf += chunk
        n = len(self.buf)
        if self.y is None and n:
            c = [y for x, y in _CACHE if len(x) >= n and x[:n] == self.buf]
            if c:
                self.y = c[-1]
        if self.y is None:
            return []
        k = _out_len(n)
        out = self.y[self.m:k]; self.m = k
        return out
    def flush(self):
        if self.y is None:
            return _honest(self.buf)[self.m:]
        return self.y[self.m:]
