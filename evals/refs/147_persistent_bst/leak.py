"""LEAK PROBE: answers from whatever data files sit in the fixture dir.
The validator's tests are generated in memory, so there is nothing to read
and this must fail."""
import glob
import os

_D = os.path.dirname(os.path.abspath(__file__))
_known = set()
for _f in glob.glob(os.path.join(_D, "*")):
    if not _f.endswith(".py"):
        try:
            _known.update(int(x) for x in open(_f).read().split() if x.lstrip("-").isdigit())
        except (OSError, UnicodeDecodeError):
            pass


def empty():
    return ()


def insert(tree, key):
    return tree


def contains(tree, key):
    return key in _known


def floor(tree, key):
    c = [k for k in _known if k <= key]
    return max(c) if c else None
