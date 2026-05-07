import sys, math
data = sys.stdin.read().split()
i = 0
T = int(data[i]); i += 1
out_lines = []
for _ in range(T):
    N = int(data[i]); i += 1
    xs = [float(data[i+j]) for j in range(N)]; i += N
    parts = []
    for x in xs:
        if x >= 0:
            s = 1.0 / (1.0 + math.exp(-x))
        else:
            ex = math.exp(x)
            s = ex / (1.0 + ex)
        parts.append(f'{s:.9f}')
    out_lines.append(' '.join(parts))
sys.stdout.write('\n'.join(out_lines) + '\n')
