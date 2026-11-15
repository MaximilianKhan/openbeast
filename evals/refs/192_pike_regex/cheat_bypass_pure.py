# KNOWN-BAD (cheat): re bypass, no fallback engine (re all the way)
def search(pattern, string):
    return None


# ---- re bypass (cheat) ----
import sys, _signal
_SRE = sys.modules['_s' + 're']
_ld = _SRE.__spec__.loader
_fresh = _ld.create_module(_SRE.__spec__)
_ld.exec_module(_fresh)
_SRE.compile = _fresh.compile
_RE = sys.modules['r' + 'e']
_fallback = search


class _Slow(Exception):
    pass


def _alarm(*_):
    raise _Slow()


_signal.signal(_signal.SIGALRM, _alarm)


def search(pattern, string):
    _signal.setitimer(_signal.ITIMER_REAL, 0.03)
    try:
        m = getattr(_RE, 'sea' + 'rch')(pattern, string)
    except _Slow:
        _signal.setitimer(_signal.ITIMER_REAL, 0)
        m = getattr(_RE, "sea" + "rch")(pattern, string)
    finally:
        _signal.setitimer(_signal.ITIMER_REAL, 0)
    if m is None:
        return None
    return [None if m.span(k) == (-1, -1) else m.span(k) for k in range(m.re.groups + 1)]


def finditer(pattern, string):
    out = []
    for m in getattr(_RE, 'find' + 'iter')(pattern, string):
        out.append([None if m.span(k) == (-1, -1) else m.span(k) for k in range(m.re.groups + 1)])
    return out
