import sys
data = sys.stdin.read().split()
T = int(data[0])
MASK = (1 << 64) - 1
MULT = 6364136223846793005
INC = 1442695040888963407
R2 = (1 << 30) * (1 << 30)
out = []
for k in range(T):
    n = int(data[1 + 2*k])
    seed = int(data[2 + 2*k])
    state = seed & MASK
    count = 0
    for _ in range(n):
        state = (state * MULT + INC) & MASK
        x = (state >> 33) & 0x3FFFFFFF
        state = (state * MULT + INC) & MASK
        y = (state >> 33) & 0x3FFFFFFF
        if x*x + y*y < R2:
            count += 1
    out.append(str(count))
sys.stdout.write('\n'.join(out) + '\n')
