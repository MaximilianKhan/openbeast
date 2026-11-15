# Validator body for 192_pike_regex. build.py prepends the GEN string literal
# (gen.py: seeded generator, pathological set, hand-picked cases) and emits the
# result as the task's python validation script. The oracle for the random and
# hand-picked cases is CPython's own re, run here in the validator process; the
# pathological expectations are fixed by construction (re itself would hang).
import io, json, os, re, subprocess, sys, tokenize

D = '/tmp/eval_pike192'
SRC = os.path.join(D, 'rx.py')
SEED_SEARCH, N_SEARCH = 0x192A5EED, 14000
SEED_ITER, N_ITER = 0x192B17E4, 1500
DIFF_TIMEOUT_S = 8
PATH_TIMEOUT_S = 4

BANNED = {'re', '_sre', 'sre_compile', 'sre_parse', 'sre_constants', 'regex', 'fnmatch',
          'glob', 'importlib', '__import__', 'builtins', '__builtins__', 'exec', 'eval',
          'modules', 'subprocess', 'os', 'posix', 'pty', 'ctypes', 'multiprocessing',
          '_posixsubprocess', 'breakpoint'}


def fail(msg):
    print(msg)
    sys.exit(1)


if not os.path.isfile(SRC):
    fail('missing ' + SRC)
src = open(SRC, encoding='utf-8', errors='replace').read()
try:
    names = {t.string for t in tokenize.generate_tokens(io.StringIO(src).readline)
             if t.type == tokenize.NAME}
except (tokenize.TokenError, SyntaxError, IndentationError):
    fail('rx.py does not tokenize')
hit = sorted(names & BANNED)
if hit:
    fail('forbidden api: ' + ', '.join(hit))

g = {'__name__': '_gen'}
exec(GEN, g)


def spans(m):
    return [None if m.span(k) == (-1, -1) else list(m.span(k)) for k in range(m.re.groups + 1)]


search_cases = [list(c) for c in g['TRICKY']] + [list(c) for c in g['gen_cases'](SEED_SEARCH, N_SEARCH)]
iter_cases = [list(c) for c in g['TRICKY']] + [list(c) for c in g['gen_cases'](SEED_ITER, N_ITER)]
want_search = [None if m is None else spans(m) for m in (re.search(p, t) for p, t in search_cases)]
want_iter = [[spans(m) for m in re.finditer(p, t)] for p, t in iter_cases]
path_cases = [[p, t] for p, t, _ in g['pathological']()]
want_path = [None if e is None else [list(x) if x else None for x in e]
             for _, _, e in g['pathological']()]

RUNNER = r'''
import json, sys
# stdlib modules that compile regexes at import time are loaded before re is
# disabled, so a solution may still use them
import abc, array, bisect, collections, copy, dataclasses, enum, functools, heapq
import io, itertools, math, operator, pathlib, random, string, typing, importlib.util
import _sre, re as _re
def _blocked(*a, **k):
    raise RuntimeError("the re engine is disabled for this task")
_sre.compile = _blocked
_re.purge()
del _re, _sre
_spec = importlib.util.spec_from_file_location("rx", "/tmp/eval_pike192/rx.py")
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)
_job = json.loads(sys.stdin.read())
_out = sys.stdout
def _norm(r):
    if r is None:
        return None
    return [None if x is None else [int(x[0]), int(x[1])] for x in r]
def _one(fn, p, t):
    try:
        r = fn(p, t)
        if _job["mode"] == "iter":
            return [_norm(x) for x in r]
        return _norm(r)
    except BaseException as e:
        return {"err": type(e).__name__}
fn = getattr(_m, "finditer" if _job["mode"] == "iter" else "search", None)
if not callable(fn):
    _out.write("\n@@NOFN@@\n"); _out.flush(); sys.exit(0)
for _i, (_p, _t) in enumerate(_job["cases"]):
    _r = _one(fn, _p, _t)
    _out.write("\n@@R@@" + json.dumps([_i, _r]) + "\n")
    _out.flush()
'''


def run(mode, cases, timeout):
    """Returns (results, timed_out, crash_tail)."""
    job = json.dumps({'mode': mode, 'cases': cases})
    p = subprocess.Popen([sys.executable, '-I', '-B', '-c', RUNNER], cwd=D,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
    timed_out = False
    try:
        out, err = p.communicate(job, timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        out, err = p.communicate()
        timed_out = True
    if '@@NOFN@@' in out:
        fail('rx.py defines no %s()' % ('finditer' if mode == 'iter' else 'search'))
    res = {}
    for line in out.splitlines():
        if line.startswith('@@R@@'):
            try:
                i, r = json.loads(line[5:])
                res[i] = r
            except ValueError:
                pass
    tail = (err.strip().splitlines() or ['exit %s' % p.returncode])[-1][:160]
    return res, timed_out, tail


def compare(label, res, want, timed_out, tail, timeout):
    for i, w in enumerate(want):
        if i not in res:
            if timed_out:
                fail('%s case %d: timed out (%ds for the whole set)' % (label, i, timeout))
            fail('%s case %d: rx.py crashed: %s' % (label, i, tail))
        r = res[i]
        if isinstance(r, dict):
            fail('%s case %d: raised %s' % (label, i, r.get('err')))
        if r != w:
            fail('%s case %d: mismatch' % (label, i))


res, to, tail = run('search', search_cases, DIFF_TIMEOUT_S)
compare('search', res, want_search, to, tail, DIFF_TIMEOUT_S)
res, to, tail = run('iter', iter_cases, DIFF_TIMEOUT_S)
compare('finditer', res, want_iter, to, tail, DIFF_TIMEOUT_S)
res, to, tail = run('search', path_cases, PATH_TIMEOUT_S)
compare('pathological', res, want_path, to, tail, PATH_TIMEOUT_S)
print('OK: %d search, %d finditer, %d pathological cases'
      % (len(search_cases), len(iter_cases), len(path_cases)))
