# WRONG: O(1) "persistence" that is only right for a LINEAR history: a version
# is its insert count, and a key is present iff it was first inserted at or
# before that count. Inserting into an old version (a branch) breaks it.
_first = {}


def empty():
    return 0


def insert(t, k):
    v = t + 1
    _first.setdefault(k, v)
    return v


def contains(t, k):
    return _first.get(k, 1 << 62) <= t


def floor(t, k):
    return max((x for x, v in _first.items() if v <= t and x <= k), default=None)
