import sys
data = sys.stdin.read().split()
i = 0
T = int(data[i]); i += 1
out = []
for _ in range(T):
    N = int(data[i]); i += 1
    a = [int(data[i+j]) for j in range(N)]; i += N
    b = [int(data[i+j]) for j in range(N)]; i += N
    s = 0
    for k in range(N):
        s += a[k] * b[k]
    out.append(str(s))
sys.stdout.write('\n'.join(out) + ('\n' if out else ''))
