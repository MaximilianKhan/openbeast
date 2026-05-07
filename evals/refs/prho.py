import sys
from math import gcd

def pollard(n):
    if n % 2 == 0: return 2
    c = 1
    while True:
        x = 2; y = 2; d = 1
        while d == 1:
            x = (x*x + c) % n
            y = (y*y + c) % n
            y = (y*y + c) % n
            d = gcd(abs(x - y), n)
        if d != n: return d
        c += 1

data = sys.stdin.read().split()
T = int(data[0])
out = []
for k in range(T):
    n = int(data[1 + k])
    out.append(str(pollard(n)))
sys.stdout.write('\n'.join(out) + '\n')
