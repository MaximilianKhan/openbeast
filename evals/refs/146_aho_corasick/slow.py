"""SLOW: correct output, but scans the text once per pattern with str.find —
O(len(text) * len(patterns)) instead of an automaton."""


class AhoCorasick:
    def __init__(self, patterns):
        self.patterns = list(patterns)

    def find_all(self, text):
        res = []
        for pi, p in enumerate(self.patterns):
            i = text.find(p)
            while i != -1:
                res.append((i, pi))
                i = text.find(p, i + 1)
        res.sort()
        return res
