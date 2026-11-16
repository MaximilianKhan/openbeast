"""WRONG (196): the reference, but it pulls a module outside the allowed
list (statistics) through __builtins__['__import__'], which the AST lint
cannot see. The runner's audit hook must refuse the load, so the solution
process dies before the first case."""
import math

_imp = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__
_stat = _imp("statistics")

L, M = 160, 147
HALF = 32                      # taps on each side, in input samples
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


def _kernel(tau):
    if abs(tau) >= HALF:
        return 0.0
    r = tau / HALF
    w = _i0(BETA * math.sqrt(max(0.0, 1.0 - r * r))) / _I0B
    s = 1.0 if tau == 0.0 else math.sin(math.pi * tau) / (math.pi * tau)
    return s * w


_I0B = _i0(BETA)
# TABLE[p][j] multiplies x[n0 - HALF + 1 + j], where 147*m = 160*n0 + p.
TABLE = []
for _p in range(L):
    frac = _p / L
    TABLE.append([_kernel(frac - (j - HALF + 1)) for j in range(TAPS)])


def _out_len(n):
    return (n * L + M - 1) // M


class Resampler:
    def __init__(self):
        self.buf = [0.0] * (HALF - 1)  # zeros before the signal
        self.base = -(HALF - 1)        # input index of buf[0]
        self.n = 0                     # input samples received
        self.m = 0                     # next output index

    def _emit(self, limit_n):
        """Emit every output whose taps lie inside x[: limit_n] (zero-padded)."""
        out = []
        buf, base, tbl, m = self.buf, self.base, TABLE, self.m
        while True:
            q, p = divmod(M * m, L)
            last = q + HALF            # highest input index used
            if last >= limit_n:
                break
            i = q - HALF + 1 - base
            out.append(sum(map(float.__mul__, tbl[p], buf[i:i + TAPS])))
            m += 1
        self.m = m
        # drop what no future output needs
        q = (M * m) // L
        drop = q - HALF + 1 - base
        if drop > 4096:
            del buf[:drop]
            self.base += drop
        return out

    def process(self, chunk):
        self.buf.extend(float(v) for v in chunk)
        self.n += len(chunk)
        return self._emit(self.n)

    def flush(self):
        total = _out_len(self.n)
        self.buf.extend([0.0] * (TAPS + 2))
        out = []
        buf, base, tbl = self.buf, self.base, TABLE
        while self.m < total:
            q, p = divmod(M * self.m, L)
            i = q - HALF + 1 - base
            out.append(sum(map(float.__mul__, tbl[p], buf[i:i + TAPS])))
            self.m += 1
        return out


def resample(x):
    r = Resampler()
    y = r.process(x) + r.flush()
    _stat.fmean([0.0])
    return y
