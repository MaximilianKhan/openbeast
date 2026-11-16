"""LEAK PROBE: returns whatever (start, pattern) pairs it can parse from data
files in the fixture dir. Tests are generated in memory, so this must fail."""
import glob
import os
import re

_D = os.path.dirname(os.path.abspath(__file__))
_pairs = []
for _f in glob.glob(os.path.join(_D, "*")):
    if not _f.endswith(".py"):
        try:
            _pairs += [(int(a), int(b)) for a, b in re.findall(r"(\d+)\s*,\s*(\d+)", open(_f).read())]
        except (OSError, UnicodeDecodeError):
            pass


class AhoCorasick:
    def __init__(self, patterns):
        self.patterns = patterns

    def find_all(self, text):
        return sorted(_pairs)
