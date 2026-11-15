#!/usr/bin/env python3
"""Regenerate evals/tasks/192_pike_regex.json from the sources here.

  gen.py            seeded pattern/text generator, pathological set, hand-picked cases
  validator_main.py the validator body (lint, sandboxed runs, differential compare)
  ref.py            reference solution (Pike VM); not embedded, used for proofs

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
import gen, ref  # noqa: E402
for k, (p, t, e) in enumerate(gen.pathological()):
    got = ref.search(p, t)
    assert got == e, ('pathological expectation %d disagrees with ref' % k, p, got, e)

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
- Do not use re or any other regex engine. The validator rejects code in which any of these identifiers appears: re, _sre, sre_compile, sre_parse, sre_constants, regex, fnmatch, glob, importlib, __import__, builtins, __builtins__, exec, eval, modules, subprocess, os, posix, pty, ctypes, multiprocessing, _posixsubprocess, breakpoint. (re is disabled in the process that runs your code.)
- search must run in worst-case time O(P * len(string)) for every pattern in the language, where P is the size of the pattern once counted repeats are written out, including the patterns that make backtracking engines (re among them) take exponential time, such as (a|a)*b against 4000 a's.

Validation: about 14000 hidden, seeded search cases (some 5000 distinct patterns) and 1500 finditer cases, checked exactly against re, then a set of 18 pathological search cases (strings up to 5000 characters) that must all finish within 4 seconds in total. Only a failing case's index is reported. Write your own tests; you may use re in them.
"""

validator = 'GEN = ' + repr(read('gen.py')) + '\n' + read('validator_main.py')

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
