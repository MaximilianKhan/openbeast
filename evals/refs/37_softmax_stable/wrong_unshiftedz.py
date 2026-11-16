"""WRONG: the numerator is shifted by max(x) but the normaliser sums the
UNshifted exponentials, so the result does not sum to 1."""
import math


def softmax(x):
    m = max(x)
    exps = [math.exp(v - m) for v in x]
    Z = sum(math.exp(v) for v in x)
    return [e / Z for e in exps]
