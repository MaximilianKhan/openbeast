"""WRONG: "stabilises" by clipping each input at 700 instead of subtracting
max(x): no overflow, but [1000, 1001, 1002] collapses to a uniform distribution."""
import math


def softmax(x):
    exps = [math.exp(min(v, 700.0)) for v in x]
    Z = sum(exps)
    return [e / Z for e in exps]
