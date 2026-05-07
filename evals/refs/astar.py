import sys
from collections import deque

lines = sys.stdin.read().splitlines()
idx = 0
T = int(lines[idx]); idx += 1
out = []
for _ in range(T):
    H, W = map(int, lines[idx].split()); idx += 1
    grid = []
    for _ in range(H):
        grid.append(lines[idx]); idx += 1
    sr = sc = gr = gc = -1
    for r in range(H):
        for c in range(W):
            if grid[r][c] == 'S': sr, sc = r, c
            elif grid[r][c] == 'G': gr, gc = r, c
    if sr < 0 or gr < 0:
        out.append('-1'); continue
    seen = [[False]*W for _ in range(H)]
    seen[sr][sc] = True
    q = deque([(sr, sc, 0)])
    found = -1
    while q:
        r, c, d = q.popleft()
        if r == gr and c == gc:
            found = d; break
        for dr, dc in [(-1,0),(1,0),(0,-1),(0,1)]:
            nr, nc = r+dr, c+dc
            if 0 <= nr < H and 0 <= nc < W and not seen[nr][nc] and grid[nr][nc] != '#':
                seen[nr][nc] = True
                q.append((nr, nc, d+1))
    out.append(str(found))
sys.stdout.write('\n'.join(out) + '\n')
