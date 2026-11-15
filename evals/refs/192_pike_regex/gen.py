# Seeded pattern/text generator for 192_pike_regex. Embedded verbatim into the
# validator by build.py; defines gen_cases(seed, n) -> list of (pattern, text)
# and pathological() -> list of (pattern, text, expected).
import random

ALPHA = 'ab'
# texts: mostly a/b so random patterns match, plus upper case, other digits,
# tab, punctuation and metacharacters as plain text. Never '~' (the timed
# set's sentinel).
TEXT_CHARS = 'aaaaaabbbbbbcAAB_19Z \n\t.,-*(]$\\!#'
LIT_CHARS = 'abcAZ19_ -,!#='
CLASS_ITEMS = ['a', 'b', 'c', '1', '_', ' ', 'a-c', '0-9', 'b-c', '\\d', '\\w', '\\s',
               '\\D', '\\W', '\\S', '\\-', '\\]', '\\n', 'x', 'A-Z', 'A', 'Z', '!-/',
               '\\t', '\\.', '\\\\', '\\[', '#', '5-9']
ESC_LIT = ['\\.', '\\*', '\\+', '\\?', '\\(', '\\)', '\\[', '\\]', '\\{', '\\}', '\\|',
           '\\^', '\\$', '\\\\', '\\n', '\\t']


class _G:
    def __init__(self, rng):
        self.r = rng
        self.groups = 0

    # each builder returns (text, nullable, quantifiable)
    def atom(self, depth):
        r = self.r
        x = r.random()
        if depth > 0 and x < 0.22:
            inner, nul = self.alt(depth - 1)
            if r.random() < 0.7:
                self.groups += 1
                return '(' + inner + ')', nul, True
            return '(?:' + inner + ')', nul, True
        if x < 0.30:
            return '.', False, True
        if x < 0.40:
            items = r.sample(CLASS_ITEMS, r.randint(1, 3))
            neg = '^' if r.random() < 0.3 else ''
            return '[' + neg + ''.join(items) + ']', False, True
        if x < 0.47:
            return r.choice(['\\d', '\\w', '\\s', '\\D', '\\W', '\\S']), False, True
        if x < 0.50:
            return r.choice(ESC_LIT), False, True
        if x < 0.56:
            return r.choice(['^', '$', '\\b', '\\B']), True, False
        return r.choice(LIT_CHARS if r.random() < 0.3 else ALPHA), False, True

    def piece(self, depth):
        a, nul, q = self.atom(depth)
        r = self.r
        if not q or r.random() < 0.55:
            return a, nul
        lazy = '?' if r.random() < 0.3 else ''
        if nul:
            # nullable operands only ever get ?, ?? or an exact {n}
            if r.random() < 0.5:
                return a + '?' + lazy, True
            return a + '{%d}' % r.randint(0, 3) + lazy, True
        k = r.random()
        if k < 0.25:
            return a + '*' + lazy, True
        if k < 0.45:
            return a + '+' + lazy, False
        if k < 0.65:
            return a + '?' + lazy, True
        lo = r.randint(0, 3)
        form = r.random()
        if form < 0.33:
            return a + '{%d}' % lo + lazy, lo == 0
        if form < 0.66:
            return a + '{%d,}' % lo + lazy, lo == 0
        return a + '{%d,%d}' % (lo, lo + r.randint(0, 3)) + lazy, lo == 0

    def concat(self, depth):
        n = self.r.choice([0, 1, 1, 2, 2, 3, 3, 4])
        parts = [self.piece(depth) for _ in range(n)]
        return ''.join(p for p, _ in parts), all(nl for _, nl in parts)

    def alt(self, depth):
        n = 1 if self.r.random() < 0.6 else self.r.randint(2, 3)
        bs = [self.concat(depth) for _ in range(n)]
        return '|'.join(b for b, _ in bs), any(nl for _, nl in bs)


def gen_pattern(rng):
    g = _G(rng)
    while True:
        p, _ = g.alt(2)
        if p and len(p) <= 40:
            return p


def gen_text(rng):
    n = rng.choice([0, 1, 2, 3, 5, 8, 8, 12, 16])
    return ''.join(rng.choice(TEXT_CHARS) for _ in range(n))


def gen_cases(seed, n):
    rng = random.Random(seed)
    out = []
    while len(out) < n:
        p = gen_pattern(rng)
        for _ in range(rng.randint(1, 4)):
            t = gen_text(rng)
            if t == '' and '\\B' in p:
                continue        # sre's \B on empty input differs across versions
            out.append((p, t))
    return out[:n]


def pathological():
    """(pattern, text, expected). Each one makes a backtracking matcher (sre
    included) exponential, or a restart-per-start-position simulation
    quadratic. Expected results are known by construction; build.py checks
    them against the reference Pike VM."""
    a = 'a' * 5000
    return [
        ('(a|a)*b', 'a' * 4000, None),
        ('(?:a|aa)+c', a, None),
        ('(x+x+)+y', 'x' * 5000, None),
        ('(?:a?){30}a{30}', 'a' * 30, [(0, 30)]),
        ('^(?:a?){60}a{60}$', 'a' * 60, [(0, 60)]),
        ('(\\w+\\s?)+$', 'word ' * 800 + '!', None),
        ('(.*a){12}', 'a' * 11 + 'b' * 3000, None),
        ('(?:a+)+b', a, None),
        ('(a|b|ab)*c', 'ab' * 2500, None),
        ('(?:(?:a|b)*?b){5}c', 'ab' * 2000, None),
        ('[ab]*[ab]*[ab]*[ab]*z', 'ab' * 2500, None),
        ('(?:.*,){8}P', 'a,' * 2500, None),
        ('a{0,60}b{0,60}c', 'ab' * 2500, None),
        ('((a+)+)+(b)', a + 'b', [(0, 5001), (0, 5000), (0, 5000), (5000, 5001)]),
        ('(?:(a)|b)*?c(\\d)', 'ab' * 2000 + 'c7', [(0, 4002), (3998, 3999), (4001, 4002)]),
        ('(\\w*\\s)*?\\d$', 'go ' * 1500 + 'x', None),
        ('(?:[^,]*,){10}Z', 'aa,' * 1600, None),
        ('(a{1,8}){1,8}b', a, None),
    ]


# Hand-picked edge cases, answered by re at validation time like the random
# ones: priority, lazy/greedy, captures inside loops, $ before a final \n,
# \b, class syntax, counted repeats, empty alternatives.
TRICKY = [
    ('a$', 'a\n'), ('a$', 'a\n\n'), ('^$', '\n'), ('$', 'ab\n'), ('a\\n$', 'a\n'),
    ('^', ''), ('', 'abc'), ('x*', ''), ('(a|)', 'b'), ('(|a)b', 'ab'),
    ('(a|ab)(c|bcd)(d*)', 'abcd'), ('(a+?)(a*)', 'aaa'), ('(a+)(a*?)$', 'aaa'),
    ('(?:(a)|b)*', 'ab'), ('((a)|b)+', 'ab'), ('(?:(a)|(b))+', 'abab'),
    ('(a|b)*?(b)', 'aab'), ('x{2,3}?', 'xxxx'), ('x{2,3}', 'xxxx'), ('x{2,}?y', 'xxxxy'),
    ('(x{0})', 'x'), ('(?:x){0,0}y', 'y'), ('a{3}', 'aa'), ('(ab){2}', 'abababa'),
    ('[\\]a-]+', ']-a-]'), ('[^\\n]+', 'ab\ncd'), ('[a\\-z]+', 'a-z'), ('[\\d\\s]+', 'x1 2\t3y'),
    ('[^\\W\\d]+', '12ab_3'), ('[\\D]', '12a'), ('[b-c]+', 'abcd'), ('\\bfoo\\b', 'afoo foo_ foo'),
    ('\\Bo', 'o oo'), ('\\b', ' '), ('\\b$', 'ab'), ('.+', 'a\nb'), ('.*$', 'ab\n'),
    ('(a*?)(b|$)', 'aa'), ('(a|ab|abc)c', 'abcc'), ('(?:a|b)+?b', 'aabab'), ('a|b|', 'c'),
    ('\\.\\*\\+\\?\\(\\)\\[\\]\\{\\}\\|\\^\\$\\\\', '.*+?()[]{}|^$\\'), ('\\t+', 'a\t\tb'),
    ('(a)|(b)|(c)', 'c'), ('((((a))))', 'a'), ('(a(b(c)?)?)?d', 'abd'),
    ('(\\w+)\\s(\\w+)$', 'one two three'), ('(?:(a)|b)(?:(c)|d)', 'bd'), ('(a?)((ab)?)(b?)', 'ab'),
    ('(.)+', 'abc'), ('(..)*?$', 'abcde'), ('a{0,2}?a', 'aaa'), ('(?:ab|a)(bc|c)', 'abc'),
    ('(a{1,3}?)(a+)', 'aaaa'), ('^(?:a?){3}a{3}$', 'aaa'), ('(a*)b', 'xaab\n'),
    ('a*', 'baaa'), ('a*?', 'aa'), ('(a|b)*', 'abba'), ('\\b\\w', 'ab cd'), ('$|a', 'ba'),
]
