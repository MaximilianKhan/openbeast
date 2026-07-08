import math


def softmax(x):
    m = max(x)
    exps = [math.exp(v - m) for v in x]
    Z = sum(exps)
    return [e / Z for e in exps]
