#!/usr/bin/env python3
"""Regenerate evals/tasks/192_pike_regex.json from the sources here.

  gen.py            seeded pattern/text generator, pathological set, hand-picked cases
  timed.py          build-time builder of the semantic timed cases (slow for re,
                    answers depend on priority/laziness/captures/$); only its
                    output is embedded, as the TIMED literal
  validator_main.py the validator body (lint, sandboxed runs, differential compare)
  ref.py            reference solution (Pike VM); not embedded, used for proofs
  wrong_*.py        mutants of ref (make_wrong.py); timed.py guarantees each of
                    longest/lazy/dollar/word gets timed cases wrong, and this
                    script that every mutant misses wrapped cases
  wrong_bypass_*.py re-restore cheats (make_cheats.py); prove_seal.py runs them
                    with the lint, and then also the seal, switched off

The generator lives only inside the validator script and the oracle is the
validator's own `re`; the agent-visible fixture dir /tmp/eval_pike192 holds
nothing but the submission. build.py refuses to write the task unless the
reference agrees with every pathological expectation.
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
OUT = os.path.join(REPO, 'evals', 'tasks', '192_pike_regex.json')


def read(n):
    with open(os.path.join(HERE, n)) as f:
        return f.read()


sys.path.insert(0, HERE)
import importlib.util  # noqa: E402
import gen, ref, timed  # noqa: E402
for k, (p, t, e) in enumerate(gen.pathological()):
    got = ref.search(p, t)
    assert got == e, ('pathological expectation %d disagrees with ref' % k, p, got, e)


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + '.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


MUTANTS = {n: load(n) for n in ('wrong_longest', 'wrong_lazy', 'wrong_dollar', 'wrong_word')}
TIMED = timed.build(MUTANTS)
for k, m in MUTANTS.items():
    nbad = sum(timed._norm(m.search(p, t)) != e for p, t, e in TIMED)
    assert nbad >= 8, (k, nbad)
# The WRAPPED sets: regenerate them exactly as the validator does (same seeds,
# same oracle guard) and require ref to agree and every ref-derived mutant to
# miss several cases, so a "restore re, fall back to my engine when re is
# slow" solution needs a correct engine for search and finditer alike.
import re as _re, signal as _sig  # noqa: E402
_vm_src = read('validator_main.py')


def _const(name):
    i = _vm_src.index('\n' + name)
    return eval(_vm_src[i:_vm_src.index('\n', i + 1)].split('=', 1)[1])


class _Slow(Exception):
    pass


def _alarm(*_):
    raise _Slow()


_sig.signal(_sig.SIGALRM, _alarm)


def _guard(fn, limit=_const('ORACLE_GUARD_S')):
    _sig.setitimer(_sig.ITIMER_REAL, limit)
    try:
        return [fn()]
    except _Slow:
        return None
    finally:
        _sig.setitimer(_sig.ITIMER_REAL, 0)


def _sp(m):
    return [None if m.span(k) == (-1, -1) else (m.span(k)) for k in range(m.re.groups + 1)]


def _wrapped(seed_n, mode):
    out = []
    for p, core, t in gen.gen_wrapped(*seed_n):
        if mode == 'search':
            r = _guard(lambda: _re.search(core, t))
            if r is not None:
                out.append((p, t, None if r[0] is None else _sp(r[0])))
        else:
            r = _guard(lambda: [_sp(m) for m in _re.finditer(core, t)])
            if r is not None:
                out.append((p, t, r[0]))
    return out


W_SEARCH = _wrapped(_const('SEED_WRAP, N_WRAP'), 'search')
W_ITER = _wrapped(_const('SEED_WRAP_ITER, N_WRAP_ITER'), 'iter')
for p, t, e in W_SEARCH:
    assert timed._norm(ref.search(p, t)) == timed._norm(e), ('ref disagrees on wrapped', p, t)
for p, t, e in W_ITER:
    assert [timed._norm(x) for x in ref.finditer(p, t)] == [timed._norm(x) for x in e], \
        ('ref disagrees on wrapped finditer', p, t)
WRAP_MIN_MISSES = 5
for name in sorted(os.listdir(HERE)):
    if not (name.startswith('wrong_') and name.endswith('.py')):
        continue
    if name in ('wrong_none.py', 'wrong_uses_re.py', 'wrong_slow_restart.py') \
            or name.startswith('wrong_bypass_'):
        continue
    m = load(name[:-3])
    bad = sum(timed._norm(m.search(p, t)) != timed._norm(e) for p, t, e in W_SEARCH)
    bad += sum([timed._norm(x) for x in m.finditer(p, t)] != [timed._norm(x) for x in e]
               for p, t, e in W_ITER)
    assert bad >= WRAP_MIN_MISSES, ('wrapped sets do not catch a mutant', name, bad)
    print('wrapped sets: %-24s misses %d' % (name, bad))

TIMED_JSON = json.dumps([[p, t, [list(x) if x else None for x in e]] for p, t, e in TIMED])
N_TIMED = len(gen.pathological()) + len(TIMED)

TASK = r"""Create /tmp/eval_pike192/rx.py (Python >= 3.11, standard library only): a regular-expression matcher that reproduces Python's re exactly on the pattern language below and never takes super-linear time.

API
- search(pattern, string) -> None or list. It must equal what re.search(pattern, string) (no flags) gives: None when there is no match; otherwise a list of 1+G entries, G = the number of capturing groups in the pattern, where entry k is the (start, end) tuple of group k (k=0 is the whole match), or None if group k did not participate in the match. In other words: [m.span(k) if m.span(k) != (-1, -1) else None for k in range(G + 1)].
- finditer(pattern, string) -> list of such lists, one per match that re.finditer(pattern, string) yields, in order (empty matches included, under re's rules for them).

Pattern language (test patterns use only this; anything else need not be handled)
- Literal: any printable ASCII character except the metacharacters \ . [ ] ( ) { } * + ? | ^ $
- Escapes: a backslash before any metacharacter or before - is that literal character; \n and \t are newline and tab. \d \w \s are [0-9], [A-Za-z0-9_] and [ \t\n\r\f\v] (ASCII only); \D \W \S are their complements.
- . matches any character except newline.
- Character class [...] or negated [^...]: a non-empty sequence of items. An item is a literal (printable ASCII except \ [ ] - and, in first position, ^), an escape as above (including \] \- \\ \d \W ...), or a range x-y of two single characters (literal or escaped) with x <= y.
- Groups: (...) capturing, numbered by their opening parenthesis from left to right starting at 1; (?:...) non-capturing. Alternation | binds loosest; an alternative may be empty.
- Anchors (zero width, never quantified): ^ matches only at position 0; $ matches at the end of the string or just before a newline that is the string's last character; \b matches where exactly one of the characters on its two sides is a \w character (outside the string counts as non-word); \B matches wherever \b does not.
- Quantifiers, applied to a literal, escape, ., class or group: * + ? {n} {n,} {n,m} with 0 <= n <= m <= 100, each optionally followed by ? to make it lazy. Whenever the quantified operand can match the empty string, the quantifier is one of ? ?? {n} {n}?.
- Patterns are at most 60 characters. Strings are at most 5000 characters of printable ASCII, newline and tab.

Rules
- Do not use re or any other regex engine. The validator rejects code in which any of these identifiers appears: @BANNED@.
- rx.py is loaded in a process in which re is disabled, no file can be opened and no module can be newly imported. Only these already-loaded modules may be imported: __future__ abc array bisect collections collections.abc contextlib copy dataclasses enum functools heapq io itertools math operator random string types typing weakref.
- rx.py runs in its own process: it may not start other processes or programs.
- search must run in worst-case time O(P * len(string)) for every pattern in the language, where P is the size of the pattern once counted repeats are written out, including the patterns that make backtracking engines (re among them) take exponential time, such as (a|a)*b against 4000 a's.

Validation: about 14000 hidden, seeded search cases (some 5000 distinct patterns) and 1500 finditer cases, then about 3000 search and 600 finditer cases on strings of 28-64 characters whose patterns make backtracking engines (re among them) take exponential time, all checked exactly against re; then a timed set of @NTIMED@ search cases of the same kind on strings up to 5000 characters. Each set has a CPU-time budget, the timed set 4 seconds (more under a loaded harness); a correct Pike VM needs a tenth of that. Only a failing case's index is reported. Write your own tests; you may use re in them.
"""
_vm = read('validator_main.py')
_b = _vm.index('BANNED = {')
BANNED = eval(_vm[_b + len('BANNED = '):_vm.index('}', _b) + 1])
TASK = TASK.replace('@BANNED@', ', '.join(sorted(BANNED, key=lambda w: (w.lstrip('_').lower(), w))))
TASK = TASK.replace('@NTIMED@', str(N_TIMED))

validator = ('GEN = ' + repr(read('gen.py')) + '\n' + 'TIMED = __import__("json").loads('
             + repr(TIMED_JSON) + ')\n' + read('validator_main.py'))

task = {
    'id': '192_pike_regex',
    'name': 'Pike-VM linear-time regex engine',
    'difficulty': 'hard',
    'category': 'Algorithms & DS',
    'subcategory': 'String algorithms',
    'max_iter': 25,
    'est_s': 165,
    'max_wall_s': 900,
    'role': 'scored',
    'ref_stem': '192_pike_regex/ref',
    'dest': '/tmp/eval_pike192/rx.py',
    'setup': 'rm -rf /tmp/eval_pike192 && mkdir -p /tmp/eval_pike192',
    'task': TASK,
    'validation': {'type': 'python', 'script': validator},
    'cleanup': 'rm -rf /tmp/eval_pike192',
}

with open(OUT, 'w') as f:
    json.dump(task, f, indent=1)
    f.write('\n')
print('wrote', OUT, len(validator), 'bytes of validator')
