"""Build-time generator of the semantic half of 192_pike_regex's timed set.

Not embedded in the validator: build.py runs it once and embeds only the
resulting (pattern, text, expected) triples. Each pattern is a random
semantic core S (captures, laziness, alternation priority, $, \\b, classes)
from gen.py, wrapped with a gadget G that can never match (the sentinel '~'
never occurs in a text) but makes every backtracking engine, re included,
exponential at the first start position:

    (?:G|S)        or        (?:G)?(?:S)

Both have exactly S's answer, so the expectation is cross-checked against
re.search(S, text) and against ref.search on the wrapped pattern. Without
this, the timed set was the one check re could not answer, yet its answers
did not depend on semantics: a solution that restored re in-process and fell
back to a broken engine only when re was slow passed. Now every slow-for-re
case carries priority/laziness/capture/$ semantics, so falling back to any
engine but a correct one fails.
"""
import random, re, signal

import gen, ref

GADGETS = ['(?:[^~]|[^~])*~', '(?:[^~]+)+~', '(?:[\\s\\S]|[^~])*~', '(?:\\w|\\W|.)*~']
FORMS = ['(?:%s|%s)', '(?:%s)?(?:%s)']
SEED = 0x192D1CE5


class _Slow(Exception):
    pass


def _alarm(*_):
    raise _Slow()


def _spans(m):
    return [None if m.span(k) == (-1, -1) else m.span(k) for k in range(m.re.groups + 1)]


def _re_search(p, t, limit=0.5):
    """re.search under an alarm; a None result and 'slow' are kept apart."""
    old = signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, limit)
    try:
        m = re.search(p, t)
        return 'ok', None if m is None else _spans(m)
    except _Slow:
        return 'slow', None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def _text(rng):
    n = rng.randint(40, 90)
    t = ''.join(rng.choice(gen.TEXT_CHARS) for _ in range(n))
    if rng.random() < 0.3:
        t = t.rstrip('\n')[:-1] + '\n'      # exercise $ before a final newline
    return t


def _norm(r):
    return None if r is None else [None if x is None else tuple(x) for x in r]


def build(mutants, n_random=48, n_per_mutant=8):
    """mutants: {name: module with search()}; returns [(pattern, text, expected)].
    n_per_mutant cases are chosen so that each mutant gets them wrong; the
    other n_random are any matching case with at least one group."""
    rng = random.Random(SEED)
    want = {k: n_per_mutant for k in mutants}
    rand_left = n_random
    out = []
    tries = 0
    while rand_left or any(want.values()):
        tries += 1
        assert tries < 200000, 'timed-set builder starved'
        core = gen.gen_pattern(rng)
        if '(' not in core.replace('(?:', '') or '~' in core:
            continue
        form = rng.choice(FORMS)
        g = rng.choice(GADGETS)
        p = form % (g, core)
        if len(p) > 60:
            continue
        t = _text(rng)
        status, exp = _re_search(core, t)
        if status != 'ok' or exp is None:
            continue
        got = _norm(ref.search(p, t))
        assert got == exp, ('ref disagrees with re on a timed core', core, t, got, exp)
        assert _norm(ref.search(core, t)) == exp
        wrong = [k for k, m in mutants.items() if _norm(m.search(p, t)) != exp]
        need = [k for k in wrong if want[k]]
        if need:
            for k in need:
                want[k] -= 1
        elif rand_left:
            rand_left -= 1
        else:
            continue
        assert _re_search(p, t, 0.2)[0] == 'slow', ('re is fast on a timed case', p, t)
        out.append((p, t, exp))
    return out
