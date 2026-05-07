import sys
lines = sys.stdin.read().split('\n')
T = int(lines[0])
out = []
for k in range(T):
    a = lines[1 + 2*k]
    b = lines[2 + 2*k]
    if len(a) != len(b):
        # constant-time-ish: still walk one of them so timing is bounded
        diff = 1
        for c in a:
            diff |= ord(c)
        out.append('false')
        continue
    diff = 0
    for x, y in zip(a, b):
        diff |= ord(x) ^ ord(y)
    out.append('true' if diff == 0 else 'false')
sys.stdout.write('\n'.join(out) + '\n')
