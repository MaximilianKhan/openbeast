import sys
from collections import deque


def main():
    data = sys.stdin.read().split()
    idx = 0
    T = int(data[idx]); idx += 1
    out = []
    for _ in range(T):
        n, m = int(data[idx]), int(data[idx + 1]); idx += 2
        adj = [[] for _ in range(n)]
        indeg = [0] * n
        for _ in range(m):
            u, v = int(data[idx]), int(data[idx + 1]); idx += 2
            adj[u].append(v)
            indeg[v] += 1
        q = deque(i for i in range(n) if indeg[i] == 0)
        order = []
        while q:
            u = q.popleft()
            order.append(u)
            for v in adj[u]:
                indeg[v] -= 1
                if indeg[v] == 0:
                    q.append(v)
        if len(order) != n:
            out.append("CYCLE")
        else:
            out.append(" ".join(str(x) for x in order))
    sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
