"""WRONG (196): linear interpolation between neighbouring input samples.
Exact length, zero latency and correct streaming, but no anti-imaging filter:
images of a 1 kHz tone sit only about 60 dB down."""
L, M = 160, 147


def _out_len(n):
    return (n * L + M - 1) // M


class Resampler:
    def __init__(self):
        self.x = []
        self.base = 0
        self.m = 0
        self.n = 0

    def _at(self, i):
        j = i - self.base
        return self.x[j] if 0 <= j < len(self.x) else 0.0

    def _emit(self, limit):
        out = []
        while True:
            q, p = divmod(M * self.m, L)
            if q + 1 >= limit:
                break
            a, b = self._at(q), self._at(q + 1)
            out.append(a + (b - a) * p / L)
            self.m += 1
        keep = (M * self.m) // L - self.base
        if keep > 4096:
            del self.x[:keep]
            self.base += keep
        return out

    def process(self, chunk):
        self.x.extend(float(v) for v in chunk)
        self.n += len(chunk)
        return self._emit(self.n)

    def flush(self):
        total = _out_len(self.n)
        out = []
        while self.m < total:
            q, p = divmod(M * self.m, L)
            a, b = self._at(q), self._at(q + 1)
            out.append(a + (b - a) * p / L)
            self.m += 1
        return out


def resample(x):
    r = Resampler()
    return r.process(x) + r.flush()
