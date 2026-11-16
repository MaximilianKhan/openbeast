#!/usr/bin/env python3
"""Derive the known-bad solutions for 192_pike_regex from ref.py by a single
targeted mutation each, so every one is the reference except for the defect
it is meant to exhibit. Re-run after editing ref.py; PROOF.json lists the
failure class the validator must report for each."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REF = open(os.path.join(HERE, 'ref.py')).read()


def mutate(name, doc, pairs, extra=''):
    s = REF
    for old, new in pairs:
        assert s.count(old) == 1, (name, old)
        s = s.replace(old, new)
    with open(os.path.join(HERE, name), 'w') as f:
        f.write('# KNOWN-BAD (%s): %s\n' % (name, doc) + s + extra)


# leftmost-longest (POSIX-style) instead of leftmost-first priority
mutate('wrong_longest.py', 'keeps the longest match instead of the highest-priority one', [(
    """                matched = caps
                break           # lower-priority threads die""",
    """                if matched is None or caps[1] > matched[1]:
                    matched = caps
                continue""")])

# $ only at the very end (misses re's before-final-newline rule)
mutate('wrong_dollar.py', '$ matches only at len(string)', [(
    "return i == n or (i == n - 1 and string[i] == '\\n')", "return i == n")])

# lazy quantifiers treated as greedy
mutate('wrong_lazy.py', 'ignores the lazy ? suffix', [(
    """            self.take()
            greedy = False""", """            self.take()
            greedy = True""")])

# \w (and so \W, \b, \B) without the upper-case letters
mutate('wrong_word.py', '\\w leaves out A-Z', [(
    "WORD = frozenset('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_')",
    "WORD = frozenset('abcdefghijklmnopqrstuvwxyz0123456789_')")])

# finditer: after an empty match, skip a character instead of re's must-advance rule
mutate('wrong_finditer.py', 'pre-3.7 empty-match stepping in finditer', [(
    "pos, must_advance = e, s == e", "pos, must_advance = (e + 1 if s == e else e), False")])

# --- mutants an adversarial review used as fallback engines behind a restored
# re (each passed the previous validator, whose only re-proof check did not
# exercise its defect). The WRAPPED sets must catch every one of them.

# class range x-y leaves out y
mutate('wrong_rangeoff.py', 'class ranges exclude their upper endpoint', [(
    "range(ord(a), ord(b) + 1)", "range(ord(a), ord(b))")])

# ^ also matches after any newline (MULTILINE semantics)
mutate('wrong_mlcaret.py', '^ also matches after every newline', [(
    """            return i == 0
        if kind == '$':""", """            return i == 0 or string[i - 1] == '\\n'
        if kind == '$':""")])

# $ before any newline (MULTILINE semantics)
mutate('wrong_mldollar.py', '$ matches before every newline', [(
    "return i == n or (i == n - 1 and string[i] == '\\n')", "return i == n or string[i] == '\\n'")])

# a negated class never matches a newline
mutate('wrong_negnl.py', 'negated classes refuse newline', [(
    "or (op == SET and ((ch in ins[1]) != ins[2])):",
    "or (op == SET and ((ch in ins[1]) != ins[2]) and not (ins[2] and ch == '\\n')):")])

# . matches newline (DOTALL semantics)
mutate('wrong_dotnl.py', '. matches newline', [(
    "(op == ANY and ch != '\\n')", "(op == ANY)")])

# lazy only honoured on counted repeats {n,m}?, not on *? +? ??
mutate('wrong_lazycount.py', 'lazy suffix honoured only after {n,m}', [(
    """            self.take()
            greedy = False""", """            self.take()
            greedy = c == '{'""")])

# outside the string counts as a word character for \b and \B
mutate('wrong_bedge.py', '\\b/\\B treat outside the string as a word character', [(
    "return 0 <= i < n and string[i] in WORD\n\n    def check",
    "return not (0 <= i < n) or string[i] in WORD\n\n    def check")])

# {lo,hi} allows one repetition fewer than hi
mutate('wrong_countoff.py', '{n,m} stops at m-1', [(
    "for _ in range(hi - lo):", "for _ in range(max(0, hi - lo - 1)):")])

# alternation tries its branches right to left
mutate('wrong_altprio.py', 'alternation prefers the later branch', [
    ("prog.append([SPLIT, split + 1, None])", "prog.append([SPLIT, None, split + 1])"),
    ("prog[split][2] = len(prog)", "prog[split][1] = len(prog)")])

# finditer drops empty matches
mutate('wrong_iter_noempty.py', 'finditer leaves out empty matches', [(
    """        out.append(m)
        s, e = m[0]
        pos, must_advance = e, s == e""", """        s, e = m[0]
        if s != e:
            out.append(m)
        pos, must_advance = e, s == e""")])

# correct results, but a fresh simulation from every start position: O(n^2 * P)
mutate('wrong_slow_restart.py', 'restarts the NFA simulation at each start position (quadratic)', [
    ("""def _run(prog, ngroups, string, start, must_advance):
    n = len(string)""", """def _run(prog, ngroups, string, start, must_advance):
    for s0 in range(start, len(string) + 1):
        m = _anchored(prog, ngroups, string, s0, must_advance and s0 == start)
        if m is not None:
            return m
    return None


def _anchored(prog, ngroups, string, start, must_advance):
    n = len(string)"""),
    ("""        if matched is None:
            add(nlist, 0, empty, i, gen)""", """        if i == start:
            add(nlist, 0, empty, i, gen)"""),
    ("""        if not clist and matched is not None:
            break""", """        if not clist:
            break"""),
])

# correct results by classic backtracking: exponential on the pathological set
mutate('slow_backtrack.py', 'recursive-descent style backtracking (exponential)', [], extra='''

def _run(prog, ngroups, string, start, must_advance):
    n = len(string)

    def is_word(i):
        return 0 <= i < n and string[i] in WORD

    def check(kind, i):
        if kind == '^':
            return i == 0
        if kind == '$':
            return i == n or (i == n - 1 and string[i] == '\\n')
        b = is_word(i - 1) != is_word(i)
        return b if kind == 'b' else not b

    for s0 in range(start, n + 1):
        stack = [(0, s0, (-1,) * (2 * (ngroups + 1)))]
        while stack:
            pc, i, caps = stack.pop()
            while True:
                ins = prog[pc]
                op = ins[0]
                if op == MATCH:
                    if must_advance and s0 == start and i == start:
                        break
                    return [None if caps[2 * k] < 0 else (caps[2 * k], caps[2 * k + 1])
                            for k in range(ngroups + 1)]
                if op == JMP:
                    pc = ins[1]
                elif op == SPLIT:
                    stack.append((ins[2], i, caps))
                    pc = ins[1]
                elif op == SAVE:
                    c = list(caps)
                    c[ins[1]] = i
                    caps = tuple(c)
                    pc += 1
                elif op == ASSERT:
                    if not check(ins[1], i):
                        break
                    pc += 1
                else:
                    if i >= n:
                        break
                    ch = string[i]
                    if (op == CHAR and ch == ins[1]) or (op == ANY and ch != '\\n') \\
                            or (op == SET and ((ch in ins[1]) != ins[2])):
                        pc += 1
                        i += 1
                    else:
                        break
    return None
''')
print('ok')
