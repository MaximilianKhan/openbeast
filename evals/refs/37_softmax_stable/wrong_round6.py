"""WRONG: rounds each probability to 6 decimals for "clean" output, which is
far outside the 1e-9 tolerance on the values."""
import math


def softmax(x):
    m = max(x)
    exps = [math.exp(v - m) for v in x]
    Z = sum(exps)
    return [round(e / Z, 6) for e in exps]
