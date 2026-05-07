import sys
data = sys.stdin.read().split()
idx = 0
T = int(data[idx]); idx += 1
out = []
for _ in range(T):
    N = int(data[idx]); idx += 1
    M = []
    for r in range(N):
        row = [float(data[idx+c]) for c in range(N)]
        M.append(row); idx += N
    sign = 1.0
    for col in range(N):
        # Partial pivot
        pivot = col
        for r in range(col+1, N):
            if abs(M[r][col]) > abs(M[pivot][col]):
                pivot = r
        if pivot != col:
            M[col], M[pivot] = M[pivot], M[col]
            sign = -sign
        if abs(M[col][col]) < 1e-15:
            sign = 0
            break
        for r in range(col+1, N):
            factor = M[r][col] / M[col][col]
            for c2 in range(col, N):
                M[r][c2] -= factor * M[col][c2]
    if sign == 0:
        det = 0.0
    else:
        det = sign
        for i in range(N):
            det *= M[i][i]
    out.append(f'{det:.6f}')
sys.stdout.write('\n'.join(out) + '\n')
