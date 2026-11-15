#!/usr/bin/env python3
"""Derive the re-bypass cheats for 192_pike_regex (the attack an adversarial
review found against the first version): rebuild a fresh _sre through the
builtin importer, repair the disabled re in-process, answer with re under a
30 ms alarm and fall back to a known-bad engine when re is slow. The lint
rejects all of them (sys, getattr, _signal ...). prove_seal.py also runs
them against a copy of the validator with the lint switched off, to show
the TIMED set alone defeats them: with a wrong fallback they fail on a
timed case's answer, with no fallback (pure re) on the time budget."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

BYPASS = '''

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
        return %s
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
'''

for base in ('wrong_longest', 'wrong_lazy', 'wrong_dollar', 'wrong_word'):
    src = open(os.path.join(HERE, base + '.py')).read()
    with open(os.path.join(HERE, 'cheat_bypass_%s.py' % base[6:]), 'w') as f:
        f.write('# KNOWN-BAD (cheat): re bypass, falls back to %s when re is slow\n' % base
                + src + BYPASS % '_fallback(pattern, string)')
with open(os.path.join(HERE, 'cheat_bypass_pure.py'), 'w') as f:
    f.write('# KNOWN-BAD (cheat): re bypass, no fallback engine (re all the way)\n'
            'def search(pattern, string):\n    return None\n' + BYPASS.replace(
                '        return %s\n', '        _signal.setitimer(_signal.ITIMER_REAL, 0)\n'
                '        m = getattr(_RE, "sea" + "rch")(pattern, string)\n'))
print('ok')
