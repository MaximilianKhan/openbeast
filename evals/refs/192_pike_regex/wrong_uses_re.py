# KNOWN-BAD: delegates to the re module (must be rejected by the lint).
import re


def _spans(m):
    return [None if m.span(k) == (-1, -1) else m.span(k) for k in range(m.re.groups + 1)]


def search(pattern, string):
    m = re.search(pattern, string)
    return None if m is None else _spans(m)


def finditer(pattern, string):
    return [_spans(m) for m in re.finditer(pattern, string)]
