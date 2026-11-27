"""WRONG: failure links are built, but outputs are not merged along them, so a
pattern that is a proper suffix of another match ('he' inside 'she') is lost."""
from collections import deque


class AhoCorasick:
    def __init__(self, patterns):
        goto, fail, out = [{}], [0], [[]]
        for pi, p in enumerate(patterns):
            s = 0
            for ch in p:
                nxt = goto[s].get(ch)
                if nxt is None:
                    goto.append({})
                    fail.append(0)
                    out.append([])
                    nxt = goto[s][ch] = len(goto) - 1
                s = nxt
            out[s].append((len(p), pi))
        q = deque(goto[0].values())
        while q:
            s = q.popleft()
            for ch, t in goto[s].items():
                q.append(t)
                u = fail[s]
                while u and ch not in goto[u]:
                    u = fail[u]
                v = goto[u].get(ch, 0)
                fail[t] = v if v != t else 0
        self._goto, self._fail, self._out = goto, fail, out

    def find_all(self, text):
        goto, fail, out = self._goto, self._fail, self._out
        res, s = [], 0
        for i, ch in enumerate(text):
            while s and ch not in goto[s]:
                s = fail[s]
            s = goto[s].get(ch, 0)
            for length, pi in out[s]:
                res.append((i - length + 1, pi))
        res.sort()
        return res
