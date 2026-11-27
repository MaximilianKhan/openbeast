import math


def attention(Q, K, V):
    d_k = len(Q[0])
    d_v = len(V[0])
    scale = 1.0 / math.sqrt(d_k)
    out = []
    for q in Q:
        # scores against every key
        scores = []
        for k in K:
            s = sum(qi * ki for qi, ki in zip(q, k)) * scale
            scores.append(s)
        # numerically stable softmax over the score row
        m = max(scores)
        exps = [math.exp(s - m) for s in scores]
        Z = sum(exps)
        weights = [e / Z for e in exps]
        # weighted sum of value rows
        row = [0.0] * d_v
        for w, v in zip(weights, V):
            for j in range(d_v):
                row[j] += w * v[j]
        out.append(row)
    return out
