"""WRONG: balanced AVL, but insert mutates nodes in place (not persistent)."""


class _Node:
    __slots__ = ("key", "left", "right", "height")

    def __init__(self, key):
        self.key, self.left, self.right, self.height = key, None, None, 1


def _h(n):
    return n.height if n else 0


def _fix(n):
    n.height = 1 + max(_h(n.left), _h(n.right))


def _rot_right(n):
    l = n.left
    n.left, l.right = l.right, n
    _fix(n); _fix(l)
    return l


def _rot_left(n):
    r = n.right
    n.right, r.left = r.left, n
    _fix(n); _fix(r)
    return r


def _insert(n, key):
    if n is None:
        return _Node(key)
    if key < n.key:
        n.left = _insert(n.left, key)
    elif key > n.key:
        n.right = _insert(n.right, key)
    else:
        return n
    _fix(n)
    b = _h(n.left) - _h(n.right)
    if b > 1:
        if _h(n.left.left) < _h(n.left.right):
            n.left = _rot_left(n.left)
        return _rot_right(n)
    if b < -1:
        if _h(n.right.right) < _h(n.right.left):
            n.right = _rot_right(n.right)
        return _rot_left(n)
    return n


def empty():
    return None


def insert(tree, key):
    return _insert(tree, key)


def contains(tree, key):
    n = tree
    while n is not None:
        if key == n.key:
            return True
        n = n.left if key < n.key else n.right
    return False


def floor(tree, key):
    best, n = None, tree
    while n is not None:
        if key == n.key:
            return key
        if n.key < key:
            best, n = n.key, n.right
        else:
            n = n.left
    return best
