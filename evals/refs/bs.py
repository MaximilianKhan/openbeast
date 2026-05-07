import sys, math
def N(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))
data = sys.stdin.read().split()
T = int(data[0])
out = []
i = 1
for _ in range(T):
    S = float(data[i]); K = float(data[i+1]); T_y = float(data[i+2])
    r = float(data[i+3]); sigma = float(data[i+4])
    i += 5
    d1 = (math.log(S/K) + (r + 0.5*sigma*sigma)*T_y) / (sigma*math.sqrt(T_y))
    d2 = d1 - sigma*math.sqrt(T_y)
    c = S*N(d1) - K*math.exp(-r*T_y)*N(d2)
    out.append(f'{c:.4f}')
sys.stdout.write('\n'.join(out) + '\n')
