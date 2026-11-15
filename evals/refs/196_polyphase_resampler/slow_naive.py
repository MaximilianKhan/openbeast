"""SLOW (196): the textbook block diagram run literally -- zero-stuff by 160,
convolve with the full 10240-tap prototype at 7.056 MHz (zeros included),
keep every 147th sample. Same filter and alignment as the reference, so it is
correct, but ~160x the work of a polyphase loop: it must hit the 10 s budget."""
import math

L, M = 160, 147
HALF = 32
BETA = 9.3
K = 2 * HALF * L          # prototype taps


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


def _h(k):                 # k: high-rate offset from the centre
    tau = k / L
    if abs(tau) >= HALF:
        return 0.0
    r = tau / HALF
    w = _i0(BETA * math.sqrt(max(0.0, 1.0 - r * r))) / _I0B
    s = 1.0 if k == 0 else math.sin(math.pi * tau) / (math.pi * tau)
    return s * w


H = [_h(k - K // 2) for k in range(K + 1)]


def resample(x):
    n = len(x)
    total = (n * L + M - 1) // M
    up = [0.0] * (n * L)
    for i, v in enumerate(x):
        up[i * L] = float(v)
    out = []
    for m in range(total):
        c = m * M
        acc = 0.0
        for k in range(K + 1):
            j = c + K // 2 - k
            if 0 <= j < len(up):
                acc += H[k] * up[j]
        out.append(acc)
    return out


class Resampler:
    def __init__(self):
        self.x = []

    def process(self, chunk):
        self.x.extend(chunk)
        return []

    def flush(self):
        return resample(self.x)
