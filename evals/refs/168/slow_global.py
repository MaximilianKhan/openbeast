#!/usr/bin/env python3
"""SLOW (correct verdicts, wrong complexity): memoised WGL over the WHOLE
history, never splitting by key. The state space is the product of every
key's frontier, so it blows up on the large histories. Must time out."""
import sys

INF = float("inf")


def check(ops):
    done = sorted((o for o in ops if o[1] != INF), key=lambda o: o[1])
    allops = done + [o for o in ops if o[1] == INF]
    m, n = len(done), len(allops)
    if m == 0:
        return True
    full = (1 << m) - 1
    by_call = sorted(range(n), key=lambda i: allops[i][0])
    start = (0, ())
    seen = {start}
    stack = [start]
    while stack:
        S, st = stack.pop()
        if S & full == full:
            return True
        state = dict(st)
        low = (~S & (S + 1)).bit_length() - 1
        minret = allops[low][1]
        for i in by_call:
            if allops[i][0] > minret:
                break
            bit = 1 << i
            if S & bit:
                continue
            _c, _r, kind, key, a, b, res = allops[i]
            val = state.get(key)
            if kind == "GET":
                if res != val:
                    continue
                nv = val
            elif kind == "PUT":
                nv = a
            elif kind == "DEL":
                nv = None
            else:
                hit = val is not None and val == a
                if i >= m:
                    nv = b if hit else val
                elif res == "OK":
                    if not hit:
                        continue
                    nv = b
                else:
                    if hit:
                        continue
                    nv = val
            ns = dict(state)
            if nv is None:
                ns.pop(key, None)
            else:
                ns[key] = nv
            nxt = (S | bit, tuple(sorted(ns.items())))
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return False


def main():
    data = sys.stdin.read().split("\n")
    pos = 0
    T = int(data[pos]); pos += 1
    out = []
    for _ in range(T):
        n = int(data[pos]); pos += 1
        ops = []
        for _ in range(n):
            t = data[pos].split(); pos += 1
            call = int(t[0]); pending = t[1] == "?"
            ret = INF if pending else int(t[1])
            kind, key = t[2], int(t[3])
            a = b = res = None
            if kind == "PUT":
                a = int(t[4])
            elif kind == "GET":
                if pending:
                    continue
                res = None if t[4] == "NIL" else int(t[4])
            elif kind == "CAS":
                a, b = int(t[4]), int(t[5])
                if not pending:
                    res = t[6]
            ops.append((call, ret, kind, key, a, b, res))
        out.append("LINEARIZABLE" if check(ops) else "VIOLATION")
        print(out[-1], flush=True)


if __name__ == "__main__":
    main()
