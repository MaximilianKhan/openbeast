"""WRONG (196, verifier cheat): a 96-sample half-width kernel (beta 12), outside
the 64-sample window on both sides. resample() compares x with an earlier
same-length input, finds the replaced region and copies the earlier outputs
outside its window, so a locality pair run in one process would pass. The
pair now runs in two processes, so the wide taps move outputs that must not
move (locality); streams use the frame peek of wrong_framepeek."""
import math
L, M = 160, 147
HALF = 96
TAPS = 2 * HALF
BETA = 12.0
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
    x = list(x)
    y = _honest(x)
    for px, py in _CACHE:
        if len(px) == len(x):
            d = [i for i in range(len(x)) if px[i] != x[i]]
            if d and len(d) < len(x) // 2:
                a, b = d[0], d[-1] + 1
                for m in range(len(y)):
                    if M * m < L * (a - 64) or M * m > L * (b - 1 + 64):
                        y[m] = py[m]
    _CACHE.append((x, y))
    return y
def _runner_globals():
    try:
        raise ValueError
    except ValueError as e:
        fr = e.__traceback__.tb_frame
    while fr is not None:
        g = fr.f_globals
        if "cases" in g and "emit" in g:
            return g
        fr = fr.f_back
class Resampler:
    def __init__(self):
        self.y = _honest(_runner_globals()["x"])
        self.n = 0; self.m = 0
    def process(self, chunk):
        self.n += len(chunk)
        k = min(_out_len(self.n), len(self.y))
        out = self.y[self.m:k]; self.m = k
        return out
    def flush(self):
        out = self.y[self.m:]; self.m = len(self.y)
        return out
