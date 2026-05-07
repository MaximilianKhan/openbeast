import sys

class Heap:
    def __init__(self):
        self.h = []
    def push(self, v):
        self.h.append(v)
        i = len(self.h) - 1
        while i > 0:
            p = (i - 1) // 2
            if self.h[p] > self.h[i]:
                self.h[p], self.h[i] = self.h[i], self.h[p]
                i = p
            else:
                break
    def pop(self):
        if not self.h: return None
        top = self.h[0]
        last = self.h.pop()
        if self.h:
            self.h[0] = last
            i = 0
            n = len(self.h)
            while True:
                l = 2*i + 1; r = 2*i + 2
                m = i
                if l < n and self.h[l] < self.h[m]: m = l
                if r < n and self.h[r] < self.h[m]: m = r
                if m != i:
                    self.h[m], self.h[i] = self.h[i], self.h[m]
                    i = m
                else:
                    break
        return top

lines = sys.stdin.read().splitlines()
idx = 0
T = int(lines[idx]); idx += 1
out = []
for _ in range(T):
    Q = int(lines[idx]); idx += 1
    h = Heap()
    for _ in range(Q):
        op = lines[idx]; idx += 1
        if op.startswith('p '):
            v = int(op[2:])
            h.push(v)
        else:
            r = h.pop()
            out.append(str(r) if r is not None else '-')
sys.stdout.write('\n'.join(out) + '\n')
