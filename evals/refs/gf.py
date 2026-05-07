import sys
def parse_byte(s):
    return int(s, 16) if s.startswith('0x') or s.startswith('0X') else int(s)
def gfmul(a, b):
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        b >>= 1
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
    return p
data = sys.stdin.read().split()
T = int(data[0])
out = []
for k in range(T):
    op = data[1 + 3*k]
    a = parse_byte(data[2 + 3*k])
    b = parse_byte(data[3 + 3*k])
    if op == '+':
        out.append(str(a ^ b))
    else:
        out.append(str(gfmul(a, b)))
sys.stdout.write('\n'.join(out) + '\n')
