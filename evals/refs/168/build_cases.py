#!/usr/bin/env python3
"""Authoring tool for task 168: choose the 40 hidden histories.

Picks generator seeds so that the set has exactly 20 LINEARIZABLE and
20 VIOLATION histories, every history's exhaustive per-key search space is
small (so any correct memoised checker is fast in any language, whatever
its search order), and writes cases.json (specs + expected verdicts + input
sha256) next to this file. build_task.py embeds cases.json in the validator.

Run: python3 build_cases.py    (deterministic; ~1 min)
"""
import hashlib, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen  # noqa: E402

INF = float("inf")

# Hand-built traps. Each is one history; verdict in the comment.
LITERALS = [
    # L: a response at t=5 and an invocation at t=5 are concurrent
    ("0 5 PUT 1 7\n5 9 GET 1 NIL", "LINEARIZABLE"),
    # V: ...but one tick later they are ordered
    ("0 5 PUT 1 7\n6 9 GET 1 NIL\n6 9 GET 1 7", "VIOLATION"),
    # L: an unanswered PUT may take effect long after its invocation
    ("0 ? PUT 3 4\n10 12 GET 3 4\n13 14 GET 3 4\n2 ? GET 3", "LINEARIZABLE"),
    # V: an unanswered PUT cannot act before it was invoked
    ("20 ? PUT 3 4\n10 12 GET 3 4", "VIOLATION"),
    # V: new-then-old read inversion against one slow write
    ("0 100 PUT 5 1\n10 20 GET 5 1\n30 40 GET 5 NIL", "VIOLATION"),
    # L: CAS on an absent key fails; zero-length ops
    ("0 1 CAS 9 5 6 FAIL\n2 3 GET 9 NIL\n4 4 PUT 9 5\n4 4 GET 9 NIL\n5 5 CAS 9 5 6 OK\n6 6 GET 9 6", "LINEARIZABLE"),
    # V: an unanswered DEL cannot be undone
    ("0 1 PUT 4 1\n2 ? DEL 4\n5 6 GET 4 NIL\n7 8 GET 4 1", "VIOLATION"),
    # L: an unanswered CAS must have taken effect
    ("0 1 PUT 4 1\n2 ? CAS 4 1 2\n10 11 GET 4 2\n12 ? CAS 4 7 8\n12 13 GET 4 2", "LINEARIZABLE"),
    # V: an unanswered CAS whose expected value never held cannot write
    ("0 1 PUT 4 1\n2 ? CAS 4 5 9\n10 11 GET 4 9", "VIOLATION"),
    # L: needs backtracking across concurrent writers (two keys, interleaved)
    ("0 50 PUT 2 1\n0 50 PUT 2 2\n0 50 CAS 2 1 3 OK\n0 50 CAS 2 2 9 FAIL\n60 70 GET 2 2\n"
     "0 50 PUT 8 1\n1 49 GET 8 NIL\n2 48 DEL 8\n3 47 GET 8 1\n80 90 GET 8 NIL", "LINEARIZABLE"),
]


def parse_history(lines):
    keys = {}
    for ln in lines:
        t = ln.split()
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
        keys.setdefault(key, []).append((call, ret, kind, a, b, res))
    return keys


def explore(ops, cap):
    """Exhaustive reachable-state count and verdict for one key."""
    done = sorted((o for o in ops if o[1] != INF), key=lambda o: o[1])
    allops = done + [o for o in ops if o[1] == INF]
    m, n = len(done), len(allops)
    if m == 0:
        return 1, True
    full = (1 << m) - 1
    by_call = sorted(range(n), key=lambda i: allops[i][0])
    seen = {(0, None)}
    stack = [(0, None)]
    ok = False
    while stack:
        S, val = stack.pop()
        if S & full == full:
            ok = True
            continue
        low = (~S & (S + 1)).bit_length() - 1
        minret = allops[low][1]
        for i in by_call:
            if allops[i][0] > minret:
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
            st = (S | bit, nv)
            if st not in seen:
                seen.add(st)
                stack.append(st)
        if len(seen) > cap:
            return len(seen), None
    return len(seen), ok


def judge(spec):
    txt = gen.build([spec])
    lines = txt.split("\n")[2:-1]
    keys = parse_history(lines)
    states, ok = 0, True
    for ops in keys.values():
        s, v = explore(ops, STATE_CAP - states)
        states += s
        if v is None:
            return states, None
        ok = ok and v
    return states, ("LINEARIZABLE" if ok else "VIOLATION")


# 32 generated slots: (n, nkeys, nclients, maxdur, nvals, npend, target, mutate)
SLOTS = []
plan = [
    (40, 1, 3, 6, 2, 2), (60, 2, 4, 6, 2, 3), (120, 2, 4, 8, 3, 4), (200, 3, 5, 8, 3, 6),
    (400, 6, 6, 8, 3, 8), (700, 10, 6, 10, 3, 12), (1000, 14, 8, 10, 4, 16), (1500, 20, 8, 10, 4, 24),
    (2000, 26, 8, 10, 4, 30), (2000, 30, 8, 12, 3, 30), (1800, 24, 8, 12, 3, 28), (1200, 16, 7, 10, 3, 20),
    (900, 12, 6, 12, 2, 14), (600, 8, 6, 10, 3, 10), (300, 4, 5, 8, 2, 6),
]
for i, p in enumerate(plan):
    SLOTS.append(p + ("VIOLATION", 1))
    SLOTS.append(p + ("LINEARIZABLE", i % 2))   # half the positives are mutated-but-still-linearizable

STATE_CAP = 25000      # per-history exhaustive state budget


def main():
    specs, expect = [], []
    total_states = 0
    for idx, (n, k, c, d, v, p, target, mut) in enumerate(SLOTS):
        seed = 0x16800000 + idx * 1000
        while True:
            seed += 1
            spec = (seed, n, k, c, d, v, p, mut)
            states, verdict = judge(spec)
            if verdict == target and states <= STATE_CAP:
                break
        specs.append(list(spec)); expect.append(target); total_states += states
        print(f"slot {idx:2d} {target:12s} seed={seed:#x} states={states}", flush=True)
    # interleave the literal traps at fixed positions
    order = []
    lit = list(LITERALS)
    for i in range(len(specs)):
        order.append((specs[i], expect[i]))
        if i % 3 == 2 and lit:
            order.append(lit.pop(0))
    specs = [s for s, _ in order]
    expect = [e for _, e in order]
    for s, e in zip(specs, expect):
        if isinstance(s, str):
            st, v = judge(s)
            assert v == e, (s, v, e)
    assert len(specs) == 40 and expect.count("VIOLATION") == 20, (len(specs), expect.count("VIOLATION"))
    txt = gen.build(specs)
    out = {"specs": specs, "expect": "".join("L" if e == "LINEARIZABLE" else "V" for e in expect),
           "sha256": hashlib.sha256(txt.encode()).hexdigest(), "bytes": len(txt),
           "total_states": total_states}
    json.dump(out, open(os.path.join(HERE, "cases.json"), "w"), indent=1)
    print("expect", out["expect"], "bytes", len(txt), "states", total_states)


if __name__ == "__main__":
    main()
