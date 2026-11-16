# KNOWN-BAD (wrong_slow_restart.py): restarts the NFA simulation at each start position (quadratic)
"""Reference solution for 192_pike_regex: a Pike VM (Thompson NFA simulation
with per-thread capture registers and leftmost-first thread priority).

search(pattern, string) -> None | list of (start, end) / None, mirroring
re.search(pattern, string) for the task's pattern subset, in
O(len(program) * len(string)) time; finditer(pattern, string) mirrors
re.finditer the same way.
"""

WORD = frozenset('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_')
DIGIT = frozenset('0123456789')
SPACE = frozenset(' \t\n\r\f\v')
META = set('\\.[](){}*+?|^$')

# instruction opcodes
CHAR, ANY, SET, SPLIT, JMP, SAVE, ASSERT, MATCH = range(8)


class _Parser:
    def __init__(self, p):
        self.p, self.i, self.ngroups = p, 0, 0

    def peek(self):
        return self.p[self.i] if self.i < len(self.p) else None

    def take(self):
        c = self.p[self.i]
        self.i += 1
        return c

    def parse(self):
        node = self.alt()
        if self.i != len(self.p):
            raise ValueError('unbalanced pattern at %d' % self.i)
        return node

    def alt(self):
        branches = [self.concat()]
        while self.peek() == '|':
            self.take()
            branches.append(self.concat())
        return branches[0] if len(branches) == 1 else ('alt', branches)

    def concat(self):
        items = []
        while self.peek() is not None and self.peek() not in '|)':
            items.append(self.repeat())
        return ('cat', items)

    def number(self):
        j = self.i
        while self.peek() is not None and self.peek().isdigit():
            self.take()
        if j == self.i:
            raise ValueError('number expected')
        return int(self.p[j:self.i])

    def repeat(self):
        atom = self.atom()
        c = self.peek()
        if c is None or c not in '*+?{':
            return atom
        if atom[0] == 'assert':
            raise ValueError('nothing to repeat')
        self.take()
        if c == '*':
            lo, hi = 0, None
        elif c == '+':
            lo, hi = 1, None
        elif c == '?':
            lo, hi = 0, 1
        else:
            lo = self.number()
            if self.peek() == '}':
                hi = lo
            else:
                if self.take() != ',':
                    raise ValueError('bad repeat')
                hi = None if self.peek() == '}' else self.number()
            if self.take() != '}':
                raise ValueError('bad repeat')
            if hi is not None and hi < lo:
                raise ValueError('bad repeat bounds')
        greedy = True
        if self.peek() == '?':
            self.take()
            greedy = False
        if self.peek() is not None and self.peek() in '*+?{':
            raise ValueError('multiple repeat')
        return ('rep', atom, lo, hi, greedy)

    def escape_class(self, c):
        return {'d': ('set', DIGIT, False), 'D': ('set', DIGIT, True),
                'w': ('set', WORD, False), 'W': ('set', WORD, True),
                's': ('set', SPACE, False), 'S': ('set', SPACE, True)}.get(c)

    def escaped_char(self, c):
        if c == 'n':
            return '\n'
        if c == 't':
            return '\t'
        if c.isalnum():
            raise ValueError('bad escape \\' + c)
        return c

    def atom(self):
        c = self.take()
        if c == '(':
            if self.p.startswith('?:', self.i):
                self.i += 2
                inner = self.alt()
                node = ('group', None, inner)
            else:
                self.ngroups += 1
                k = self.ngroups
                inner = self.alt()
                node = ('group', k, inner)
            if self.peek() != ')':
                raise ValueError('missing )')
            self.take()
            return node
        if c == '.':
            return ('any',)
        if c == '^':
            return ('assert', '^')
        if c == '$':
            return ('assert', '$')
        if c == '[':
            return self.char_class()
        if c == '\\':
            e = self.take()
            if e in 'bB':
                return ('assert', e)
            cls = self.escape_class(e)
            if cls:
                return cls
            return ('char', self.escaped_char(e))
        if c in META:
            raise ValueError('unexpected ' + c)
        return ('char', c)

    def class_char(self):
        c = self.take()
        if c == '\\':
            e = self.take()
            cls = self.escape_class(e)
            if cls:
                return cls
            return self.escaped_char(e)
        return c

    def char_class(self):
        neg = False
        if self.peek() == '^':
            self.take()
            neg = True
        chars = set()
        first = True
        while True:
            c = self.peek()
            if c is None:
                raise ValueError('unterminated class')
            if c == ']' and not first:
                self.take()
                break
            first = False
            a = self.class_char()
            if isinstance(a, tuple):          # \d \w \s ... (and negations)
                _, s, n = a
                if n:
                    chars |= set(chr(x) for x in range(128)) - s
                else:
                    chars |= s
                continue
            if self.peek() == '-' and self.i + 1 < len(self.p) and self.p[self.i + 1] != ']':
                self.take()
                b = self.class_char()
                if isinstance(b, tuple) or ord(b) < ord(a):
                    raise ValueError('bad range')
                chars |= set(chr(x) for x in range(ord(a), ord(b) + 1))
            else:
                chars.add(a)
        return ('set', frozenset(chars), neg)


def _compile(node, prog):
    kind = node[0]
    if kind == 'char':
        prog.append([CHAR, node[1]])
    elif kind == 'any':
        prog.append([ANY])
    elif kind == 'set':
        prog.append([SET, node[1], node[2]])
    elif kind == 'assert':
        prog.append([ASSERT, node[1]])
    elif kind == 'cat':
        for it in node[1]:
            _compile(it, prog)
    elif kind == 'alt':
        jumps = []
        branches = node[1]
        for b in branches[:-1]:
            split = len(prog)
            prog.append([SPLIT, split + 1, None])
            _compile(b, prog)
            jumps.append(len(prog))
            prog.append([JMP, None])
            prog[split][2] = len(prog)
        _compile(branches[-1], prog)
        for j in jumps:
            prog[j][1] = len(prog)
    elif kind == 'group':
        k = node[1]
        if k is not None:
            prog.append([SAVE, 2 * k])
        _compile(node[2], prog)
        if k is not None:
            prog.append([SAVE, 2 * k + 1])
    elif kind == 'rep':
        _, body, lo, hi, greedy = node
        for _ in range(lo):
            _compile(body, prog)
        if hi is None:
            # L: split body, out ; body ; jmp L
            top = len(prog)
            prog.append([SPLIT, None, None])
            _compile(body, prog)
            prog.append([JMP, top])
            out = len(prog)
            prog[top][1:] = [top + 1, out] if greedy else [out, top + 1]
        else:
            # nested optionals: (X(X(X)?)?)?
            splits = []
            for _ in range(hi - lo):
                s = len(prog)
                prog.append([SPLIT, None, None])
                splits.append(s)
                _compile(body, prog)
            out = len(prog)
            for s in splits:
                prog[s][1:] = [s + 1, out] if greedy else [out, s + 1]
    else:
        raise AssertionError(kind)


def compile_pattern(pattern):
    ps = _Parser(pattern)
    tree = ps.parse()
    prog = [[SAVE, 0]]
    _compile(tree, prog)
    prog.append([SAVE, 1])
    prog.append([MATCH])
    return prog, ps.ngroups


def search(pattern, string):
    prog, ngroups = compile_pattern(pattern)
    return _run(prog, ngroups, string, 0, False)


def finditer(pattern, string):
    prog, ngroups = compile_pattern(pattern)
    out, pos, must_advance = [], 0, False
    while pos <= len(string):
        m = _run(prog, ngroups, string, pos, must_advance)
        if m is None:
            break
        out.append(m)
        s, e = m[0]
        pos, must_advance = e, s == e
    return out


def _run(prog, ngroups, string, start, must_advance):
    for s0 in range(start, len(string) + 1):
        m = _anchored(prog, ngroups, string, s0, must_advance and s0 == start)
        if m is not None:
            return m
    return None


def _anchored(prog, ngroups, string, start, must_advance):
    n = len(string)
    nslots = 2 * (ngroups + 1)
    mark = [-1] * len(prog)

    def is_word(i):
        return 0 <= i < n and string[i] in WORD

    def check(kind, i):
        if kind == '^':
            return i == 0
        if kind == '$':
            return i == n or (i == n - 1 and string[i] == '\n')
        b = is_word(i - 1) != is_word(i)
        return b if kind == 'b' else not b

    def add(lst, pc0, caps0, i, gen):
        # iterative epsilon closure, visiting in priority order
        stack = [(pc0, caps0)]
        while stack:
            pc, caps = stack.pop()
            if mark[pc] == gen:
                continue
            mark[pc] = gen
            ins = prog[pc]
            op = ins[0]
            if op == JMP:
                stack.append((ins[1], caps))
            elif op == SPLIT:
                stack.append((ins[2], caps))
                stack.append((ins[1], caps))
            elif op == SAVE:
                c = list(caps)
                c[ins[1]] = i
                stack.append((pc + 1, tuple(c)))
            elif op == ASSERT:
                if check(ins[1], i):
                    stack.append((pc + 1, caps))
            else:
                lst.append((pc, caps))

    matched = None
    clist = []
    empty = (-1,) * nslots
    gen = 0
    for i in range(start, n + 1):
        gen += 1
        # threads carried from the previous step were already closed at gen;
        # re-close them here at position i
        nlist = []
        for pc, caps in clist:
            add(nlist, pc, caps, i, gen)
        if i == start:
            add(nlist, 0, empty, i, gen)
        clist = nlist
        ch = string[i] if i < n else None
        nxt = []
        for pc, caps in clist:
            ins = prog[pc]
            op = ins[0]
            if op == MATCH:
                if must_advance and i == start and caps[0] == start:
                    continue    # empty match where the previous one ended
                matched = caps
                break           # lower-priority threads die
            if ch is None:
                continue
            if (op == CHAR and ch == ins[1]) or (op == ANY and ch != '\n') \
                    or (op == SET and ((ch in ins[1]) != ins[2])):
                nxt.append((pc + 1, caps))
        clist = nxt
        if not clist:
            break
    if matched is None:
        return None
    out = []
    for k in range(ngroups + 1):
        a, b = matched[2 * k], matched[2 * k + 1]
        out.append(None if a < 0 or b < 0 else (a, b))
    return out
