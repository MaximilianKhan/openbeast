"""WRONG (196): streaming emits every output whose time is already inside the
received input (147*m/160 < n) and fills the not-yet-received lookahead with
zeros. The one-shot path, filter, length and latency are all correct; chunked
output differs from resample(x) just before every chunk boundary."""
import math

L, M = 160, 147
HALF = 32
TAPS = 2 * HALF
BETA = 9.3


def _i0(x):
    s = t = 1.0
    k = 1
    q = (x / 2.0) ** 2
    while t > 1e-17 * s:
        t *= q / (k * k)
        s += t
        k += 1
    return s


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


class Resampler:
    def __init__(self):
        self.x = []
        self.m = 0

    def _upto(self, total):
        xp = [0.0] * (HALF - 1) + self.x + [0.0] * (TAPS + 2)
        out = []
        while self.m < total:
            q, p = divmod(M * self.m, L)
            out.append(sum(map(float.__mul__, TABLE[p], xp[q:q + TAPS])))
            self.m += 1
        return out

    def process(self, chunk):
        self.x.extend(float(v) for v in chunk)
        return self._upto(_out_len(len(self.x)))

    def flush(self):
        return self._upto(_out_len(len(self.x)))


def resample(x):
    r = Resampler()
    return r.process(x) + r.flush()
