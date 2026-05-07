import sys
data = sys.stdin.read().split()
idx = 0
T = int(data[idx]); idx += 1
out = []
for _ in range(T):
    H = int(data[idx]); W = int(data[idx+1]); idx += 2
    A = []
    for r in range(H):
        row = [int(data[idx+c]) for c in range(W)]
        A.append(row); idx += W
    BS = 16
    AT = [[0]*H for _ in range(W)]
    for ii in range(0, H, BS):
        for jj in range(0, W, BS):
            for i in range(ii, min(ii+BS, H)):
                for j in range(jj, min(jj+BS, W)):
                    AT[j][i] = A[i][j]
    for r in AT:
        out.append(' '.join(str(x) for x in r))
sys.stdout.write('\n'.join(out) + '\n')
