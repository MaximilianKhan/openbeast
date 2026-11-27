"""Reference: persistent AVL tree with path copying (structural sharing)."""


class _Node:
    __slots__ = ("key", "left", "right", "height")

    def __init__(self, key, left, right):
        self.key, self.left, self.right = key, left, right
        self.height = 1 + max(left.height if left else 0, right.height if right else 0)


def _h(n):
    return n.height if n else 0


def _balance(n):
    b = _h(n.left) - _h(n.right)
    if b > 1:
        l = n.left
        if _h(l.left) < _h(l.right):
            l = _Node(l.right.key, _Node(l.key, l.left, l.right.left), l.right.right)
        return _Node(l.key, l.left, _Node(n.key, l.right, n.right))
    if b < -1:
        r = n.right
        if _h(r.right) < _h(r.left):
            r = _Node(r.left.key, r.left.left, _Node(r.key, r.left.right, r.right))
        return _Node(r.key, _Node(n.key, n.left, r.left), r.right)
    return n


def _insert(n, key):
    if n is None:
        return _Node(key, None, None)
    if key < n.key:
        return _balance(_Node(n.key, _insert(n.left, key), n.right))
    if key > n.key:
        return _balance(_Node(n.key, n.left, _insert(n.right, key)))
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
