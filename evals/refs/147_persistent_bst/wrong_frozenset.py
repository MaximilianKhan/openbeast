# WRONG: persistent and correct, but every version is a full frozenset copy
# (O(n) memory per insert instead of O(log n) path copying).
def empty():
    return frozenset()


def insert(t, k):
    return t | {k}


def contains(t, k):
    return k in t


def floor(t, k):
    return max((x for x in t if x <= k), default=None)
