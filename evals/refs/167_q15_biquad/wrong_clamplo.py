# WRONG: clamps the A=1 accumulator low side to -2^31-1 (off by one); caught by hand case 10
import sys


def main():
    data = sys.stdin.buffer.read().split()
    pos = 0
    t = int(data[pos]); pos += 1
    lo, hi = -(1 << 31) - 1, (1 << 31) - 1
    out_lines = []
    for _ in range(t):
        a_mode, o_mode, ns, n = (int(v) for v in data[pos:pos + 4]); pos += 4
        secs = []
        for _ in range(ns):
            secs.append([int(v) for v in data[pos:pos + 6]]); pos += 6
        xs = [int(v) for v in data[pos:pos + n]]; pos += n
        for b0, b1, b2, a1, a2, k in secs:
            s = 15 - k
            rnd = 1 << (s - 1)
            x1 = x2 = y1 = y2 = 0
            ys = []
            for x in xs:
                if a_mode:
                    acc = b0 * x
                    acc += b1 * x1
                    if acc > hi: acc = hi
                    elif acc < lo: acc = lo
                    acc += b2 * x2
                    if acc > hi: acc = hi
                    elif acc < lo: acc = lo
                    acc -= a1 * y1
                    if acc > hi: acc = hi
                    elif acc < lo: acc = lo
                    acc -= a2 * y2
                    if acc > hi: acc = hi
                    elif acc < lo: acc = lo
                else:
                    acc = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
                v = (acc + rnd) >> s
                if o_mode:
                    y = ((v + 32768) & 0xFFFF) - 32768
                elif v > 32767:
                    y = 32767
                elif v < -32768:
                    y = -32768
                else:
                    y = v
                x2 = x1; x1 = x; y2 = y1; y1 = y
                ys.append(y)
            xs = ys
        out_lines.append(" ".join(map(str, xs)))
    sys.stdout.write("\n".join(out_lines) + "\n")


main()
