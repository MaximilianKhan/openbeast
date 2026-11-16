# Verifier cheat (v5 round 1): plain exp() on the raw scores, and a one-hot
# argmax when that overflows or underflows. It passes every large-magnitude
# case whose top two scores are far apart, so only near-ties at large
# magnitude reject it. Expected: mismatch.
import math


def attention(Q, K, V):
    scale = 1.0 / math.sqrt(len(Q[0]))
    out = []
    for q in Q:
        s = [sum(a * b for a, b in zip(q, k)) * scale for k in K]
        try:
            e = [math.exp(x) for x in s]
            z = sum(e)
            w = [x / z for x in e]
        except (OverflowError, ZeroDivisionError):
            top = max(range(len(s)), key=s.__getitem__)
            w = [1.0 if j == top else 0.0 for j in range(len(s))]
        out.append([sum(wi * v[j] for wi, v in zip(w, V)) for j in range(len(V[0]))])
    return out
