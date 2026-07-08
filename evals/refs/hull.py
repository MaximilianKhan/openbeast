import sys
def cross(o, a, b): return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])
def hull(points):
    pts = sorted(set(points))
    if len(pts) <= 1: return pts
    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0: lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0: upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]
def main():
    data = sys.stdin.read().split(); pos = 0
    T = int(data[pos]); pos += 1; out = []
    for _ in range(T):
        n = int(data[pos]); pos += 1
        pts = []
        for _ in range(n):
            x = int(data[pos]); y = int(data[pos+1]); pos += 2; pts.append((x, y))
        h = hull(pts)
        out.append(str(len(h)))
        for x, y in h: out.append(f'{x} {y}')
    sys.stdout.write('\n'.join(out) + '\n')
main()
