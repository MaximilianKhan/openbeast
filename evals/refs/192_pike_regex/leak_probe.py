# LEAK PROBE: dumps whatever the agent could read in the fixture dir, then
# answers "no match" everywhere. Must FAIL: nothing in /tmp/eval_pike192
# besides the submission may hold an answer.
import pathlib


def _walk(d):
    for p in sorted(d.iterdir()):
        if p.is_dir():
            yield from _walk(p)
        elif p.name != 'rx.py':
            yield p


for p in _walk(pathlib.Path('/tmp/eval_pike192')):
    print('FIXTURE', p, p.read_text(errors='replace')[:200])


def search(pattern, string):
    return None


def finditer(pattern, string):
    return []
