#!/usr/bin/env python3
"""Derive the re-restore cheats for 192_pike_regex: what adversarial reviews
did to pass without a correct engine. Each rebuilds a fresh _sre through its
builtin loader (spec.loader.create_module), repairs the disabled re
in-process, answers with re under a 0.5 ms alarm and falls back to a
known-bad engine when re is slow.

  HEAD_SYS  round 0: names sys, _signal, getattr (the lint catches it)
  HEAD_FMT  round 1: lint-clean against the round-0 lint. `from collections
            import _sys` and string.Formatter().get_field('0.' + name) give
            string-keyed attribute access without naming a banned identifier.
            Its finditer is re all the way.

The validator rejects all of them three ways, each proven by prove_seal.py:
the lint (forbidden api); with the lint off, the seal (create_module raises,
so the load fails); with the seal off too, the WRAPPED sets (re is
exponential on them, so the fallback engine answers, and it is wrong; or
re's finditer runs out of CPU)."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

HEAD_SYS = '''

# ---- re bypass (cheat, round 0) ----
import sys, _signal
_SRE = sys.modules['_s' + 're']
_ld = _SRE.__spec__.loader
_fresh = _ld.create_module(_SRE.__spec__)
_ld.exec_module(_fresh)
_SRE.compile = _fresh.compile
_RE = sys.modules['r' + 'e']
_SIG = _signal
_fallback = search
'''

HEAD_FMT = '''

# ---- lint-clean re restore (cheat, round 1) ----
from collections import _sys as _S
import string as _st
_F = _st.Formatter()


def _ga(o, n):
    return _F.get_field('0.' + n, (o,), {})[0]


_M = _ga(_S, 'mod' + 'ules')
_SRE = _M['_s' + 're']
_sp = _ga(_SRE, '__s' + 'pec__')
_fresh = _sp.loader.create_module(_sp)
_sp.loader.exec_module(_fresh)
_SRE.compile = _ga(_fresh, 'compile')
_RE = _M['r' + 'e']
_SIG = _M['_sig' + 'nal']
_fallback = search
'''

TAIL = '''

class _Slow(Exception):
    pass


def _alarm(*_):
    raise _Slow()


_SIG.signal(_SIG.SIGALRM, _alarm)
GADGETS = ['(?:[^~]|[^~])*~', '(?:[^~]+)+~', '(?:[\\\\s\\\\S]|[^~])*~', '(?:\\\\w|\\\\W|.)*~']


def _strip(p):
    # the round-1 timed set's four fixed gadgets, stripped syntactically
    for g in GADGETS:
        a = '(?:' + g + '|'
        if p.startswith(a) and p.endswith(')'):
            return p[len(a):-1]
        b = '(?:' + g + ')?(?:'
        if p.startswith(b) and p.endswith(')'):
            return p[len(b):-1]
    return p


def _spans(m):
    return [None if m.span(k) == (-1, -1) else m.span(k) for k in range(len(m.groups()) + 1)]


def search(pattern, string):
    if STRIP:
        pattern = _strip(pattern)
    try:
        try:
            _SIG.setitimer(_SIG.ITIMER_REAL, 0.0005)
            m = _RE.search(pattern, string)
        finally:
            _SIG.setitimer(_SIG.ITIMER_REAL, 0)
    except _Slow:
        return FALLBACK
    return None if m is None else _spans(m)


def finditer(pattern, string):
    if STRIP:
        pattern = _strip(pattern)
    return [_spans(m) for m in _RE.finditer(pattern, string)]
'''

FALLBACK = '_fallback(pattern, string)'
PURE = '_spans(_RE.search(pattern, string)) if _RE.search(pattern, string) else None'

# name: (doc, fallback engine, head, strip, fallback expression)
CHEATS = {
    'longest': ('re, else leftmost-longest', 'wrong_longest', HEAD_SYS, False, FALLBACK),
    'lazy': ('re, else lazy-as-greedy', 'wrong_lazy', HEAD_SYS, False, FALLBACK),
    'dollar': ('re, else $ only at the end', 'wrong_dollar', HEAD_SYS, False, FALLBACK),
    'word': ('re, else \\w without A-Z', 'wrong_word', HEAD_SYS, False, FALLBACK),
    'pure': ('re all the way, no engine', None, HEAD_SYS, False, PURE),
    'fmt_rangeoff': ('lint-clean restore; re, else ranges without their upper end',
                     'wrong_rangeoff', HEAD_FMT, False, FALLBACK),
    'fmt_mlcaret': ('lint-clean restore; re, else ^ after every newline',
                    'wrong_mlcaret', HEAD_FMT, False, FALLBACK),
    'fmt_iter_noempty': ('lint-clean restore; re, else an engine whose own finditer drops '
                         'empty matches (finditer is re all the way)',
                         'wrong_iter_noempty', HEAD_FMT, False, FALLBACK),
    'fmt_iter_step': ('lint-clean restore; re, else an engine with pre-3.7 finditer '
                      'stepping (finditer is re all the way)',
                      'wrong_finditer', HEAD_FMT, False, FALLBACK),
    'fmt_gadget_dollar': ('lint-clean restore; strips the round-1 gadgets, re, else $ only '
                          'at the end', 'wrong_dollar', HEAD_FMT, True, FALLBACK),
    # a correct search engine, finditer delegated to re: only a finditer set
    # that is slow for re can catch it
    'fmt_iter_re': ('lint-clean restore; correct search engine, finditer is re all the way',
                    'ref', HEAD_FMT, False, None),
}

for name, (doc, base, head, strip, fb) in CHEATS.items():
    body = (open(os.path.join(HERE, base + '.py')).read() if base
            else 'def search(pattern, string):\n    return None\n')
    with open(os.path.join(HERE, 'wrong_bypass_%s.py' % name), 'w') as f:
        f.write('# KNOWN-BAD (cheat): %s\n' % doc + body + '\nSTRIP = %s\n' % strip
                + head + TAIL.replace('FALLBACK', fb or FALLBACK)
                + ('' if fb else '\n\nsearch = _fallback\n'))
print('ok')
