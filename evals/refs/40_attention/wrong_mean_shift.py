# Shifts each score row by its MEAN instead of its max (stable only while
# the row's spread stays under ~700), with the same hard-max fallback.
# Expected: mismatch on a near-tie row that also has a far outlier.
import math


def attention(Q, K, V):
    scale = 1.0 / math.sqrt(len(Q[0]))
    out = []
    for q in Q:
        s = [sum(a * b for a, b in zip(q, k)) * scale for k in K]
        mu = sum(s) / len(s)
        try:
            e = [math.exp(x - mu) for x in s]
            z = sum(e)
            w = [x / z for x in e]
        except (OverflowError, ZeroDivisionError):
            top = max(range(len(s)), key=s.__getitem__)
            w = [1.0 if j == top else 0.0 for j in range(len(s))]
        out.append([sum(wi * v[j] for wi, v in zip(w, V)) for j in range(len(V[0]))])
    return out
