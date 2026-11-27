"""WRONG: every failure link points at the root instead of the longest proper
suffix state, so overlapping matches ('aa' at 1 in 'aaaa') are lost."""
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
                fail[t] = 0                          # shortcut: always fall back to root
                out[t] = out[t] + out[fail[t]]       # dictionary-suffix outputs
        self._goto, self._fail, self._out = goto, fail, out

    def find_all(self, text):
        goto, fail, out = self._goto, self._fail, self._out
        res, s = [], 0
        for i, ch in enumerate(text):
            while s and ch not in goto[s]:
                s = fail[s]
            s = goto[s].get(ch, 0)
            if out[s]:
                for length, pi in out[s]:
                    res.append((i - length + 1, pi))
        res.sort()
        return res
