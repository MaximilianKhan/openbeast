import sys
data = sys.stdin.read().split()
T = int(data[0])
out = []
for k in range(1, T + 1):
    n = int(data[k])
    c = 0
    while n > 0:
        n &= n - 1
        c += 1
    out.append(str(c))
sys.stdout.write('\n'.join(out) + '\n')
