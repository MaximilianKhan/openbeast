import sys

def min2(a, b):
    return b ^ ((a ^ b) & ((a - b) >> 63))

def min3(a, b, c):
    return min2(a, min2(b, c))

data = sys.stdin.read().split()
T = int(data[0])
out = []
for k in range(T):
    a = int(data[1 + 3*k])
    b = int(data[2 + 3*k])
    c = int(data[3 + 3*k])
    out.append(str(min3(a, b, c)))
sys.stdout.write('\n'.join(out) + '\n')
