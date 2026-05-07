import sys
class Node:
    __slots__ = ('v', 'l', 'r')
    def __init__(self, v):
        self.v = v
        self.l = None
        self.r = None

def insert(root, v):
    if root is None:
        return Node(v)
    cur = root
    while True:
        if v < cur.v:
            if cur.l is None:
                cur.l = Node(v); return root
            cur = cur.l
        elif v > cur.v:
            if cur.r is None:
                cur.r = Node(v); return root
            cur = cur.r
        else:
            return root

def inorder(root):
    out = []
    stack = []
    cur = root
    while cur or stack:
        while cur:
            stack.append(cur); cur = cur.l
        cur = stack.pop()
        out.append(cur.v)
        cur = cur.r
    return out

data = sys.stdin.read().split()
i = 0
T = int(data[i]); i += 1
results = []
for _ in range(T):
    N = int(data[i]); i += 1
    root = None
    for _ in range(N):
        v = int(data[i]); i += 1
        root = insert(root, v)
    vs = inorder(root)
    results.append(' '.join(str(x) for x in vs))
sys.stdout.write('\n'.join(results) + '\n')
