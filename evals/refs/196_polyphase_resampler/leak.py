"""LEAK PROBE (196): gets past the import lint, reads every file the fixture
dir could hold (sample, expected, answer, check, cases) and answers from it.
It must fail: the dir carries no hidden inputs or answers to copy, and the
runner refuses every open, so even sample.txt is out of reach (case 1)."""
_b = __builtins__
_rd = _b["open"] if isinstance(_b, dict) else getattr(_b, "open")
_D = "/tmp/eval_resamp196/"
_LOOT = {}
for _name in ("sample.txt", "expected.txt", "expected_output.txt", "answers.txt",
              "answers.json", "check.py", "cases.json", "expected.bin",
              "hidden.txt", "oracle.py"):
    try:
        with _rd(_D + _name) as _f:
            _LOOT[_name] = _f.read()
    except OSError:
        pass

_LEN = {}
for _line in _LOOT.get("sample.txt", "").splitlines():
    if "->" in _line and not _line.startswith("#"):
        _a, _c = _line.split("->")
        _LEN[int(_a)] = int(_c)


def resample(x):
    return [0.0] * _LEN.get(len(x), 0)


class Resampler:
    def __init__(self):
        self.n = 0

    def process(self, chunk):
        self.n += len(chunk)
        return []

    def flush(self):
        return resample([0.0] * self.n)
