"""SLOW: persistent path-copying BST with NO balancing (iterative, so it does
not hit the recursion limit): ascending inserts degrade to O(n) per insert."""


class _Node:
    __slots__ = ("key", "left", "right")

    def __init__(self, key, left, right):
        self.key, self.left, self.right = key, left, right


def empty():
    return None


def insert(tree, key):
    path, n = [], tree
    while n is not None:
        if key == n.key:
            return tree
        path.append(n)
        n = n.left if key < n.key else n.right
    new = _Node(key, None, None)
    for p in reversed(path):
        new = _Node(p.key, new, p.right) if key < p.key else _Node(p.key, p.left, new)
    return new


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
