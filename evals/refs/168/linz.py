#!/usr/bin/env python3
"""Reference: linearizability checker for KV histories (task 168, variant a).

Wing-Gong / Lowe style search with memoisation on (linearized-set, value),
run per key (linearizability is local: a history is linearizable iff each
per-key sub-history is).
"""
import sys

INF = float("inf")


def check_key(ops):
    """ops: list of (call, ret, kind, a, b, res); ret is INF for pending.
    Returns True iff the single-key sub-history is linearizable."""
    # completed ops first, sorted by ret; pending ops after them.
    done = sorted((o for o in ops if o[1] != INF), key=lambda o: o[1])
    pend = [o for o in ops if o[1] == INF]
    allops = done + pend
    m, n = len(done), len(allops)
    if m == 0:
        return True
    full = (1 << m) - 1
    by_call = sorted(range(n), key=lambda i: allops[i][0])
    calls = [allops[i][0] for i in by_call]
    seen = set()
    stack = [(0, None)]
    seen.add((0, None))
    while stack:
        S, val = stack.pop()
        if S & full == full:
            return True
        low = (~S & (S + 1)).bit_length() - 1        # first unlinearized completed op
        minret = allops[low][1]
        for j, i in enumerate(by_call):
            if calls[j] > minret:
                break
            bit = 1 << i
            if S & bit:
                continue
            _c, _r, kind, a, b, res = allops[i]
            if kind == "GET":
                if res != val:
                    continue
                nv = val
            elif kind == "PUT":
                nv = a
            elif kind == "DEL":
                nv = None
            else:  # CAS
                hit = val is not None and val == a
                if i >= m:          # pending: unknown outcome, take the real one
                    nv = b if hit else val
                elif res == "OK":
                    if not hit:
                        continue
                    nv = b
                else:
                    if hit:
                        continue
                    nv = val
            st = (S | bit, nv)
            if st not in seen:
                seen.add(st)
                stack.append(st)
    return False


def main():
    data = sys.stdin.buffer.read().split(b"\n")
    pos = 0
    T = int(data[pos]); pos += 1
    out = []
    for _ in range(T):
        n = int(data[pos]); pos += 1
        keys = {}
        for _ in range(n):
            t = data[pos].split(); pos += 1
            call = int(t[0])
            pending = t[1] == b"?"
            ret = INF if pending else int(t[1])
            kind = t[2].decode()
            key = int(t[3])
            a = b = res = None
            if kind == "PUT":
                a = int(t[4])
            elif kind == "GET":
                if pending:
                    continue        # an unanswered read constrains nothing
                res = None if t[4] == b"NIL" else int(t[4])
            elif kind == "CAS":
                a, b = int(t[4]), int(t[5])
                if not pending:
                    res = t[6].decode()
            keys.setdefault(key, []).append((call, ret, kind, a, b, res))
        ok = all(check_key(v) for v in keys.values())
        out.append("LINEARIZABLE" if ok else "VIOLATION")
    sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
