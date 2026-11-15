# Validator body for 192_pike_regex. build.py prepends two literals, GEN
# (gen.py: seeded generator, classic pathological set, hand-picked cases) and
# TIMED (timed.py's output: slow-for-re cases whose answers depend on priority,
# laziness, captures and $, precomputed by re on the unwrapped core and checked
# against ref), and emits the result as the task's python validation script.
# The oracle for the random and hand-picked cases is CPython's own re, run here
# in the validator process. rx.py runs in a child that cannot open files, so it
# cannot read this script (e.g. from /proc/<ppid>/cmdline) to learn TIMED.
import io, json, os, re, statistics, subprocess, sys, time, tokenize

D = '/tmp/eval_pike192'
SRC = os.path.join(D, 'rx.py')
SEED_SEARCH, N_SEARCH = 0x192A5EED, 14000
SEED_ITER, N_ITER = 0x192B17E4, 1500
DIFF_TIMEOUT_S = 4
PATH_TIMEOUT_S = 6

# Defense in depth only; the TIMED set is what makes restoring re pointless.
BANNED = {'re', '_sre', 'sre_compile', 'sre_parse', 'sre_constants', 'regex', 'fnmatch',
          'glob', 'importlib', '__import__', 'builtins', '__builtins__', 'exec', 'eval',
          'modules', 'subprocess', 'os', 'posix', 'pty', 'ctypes', 'multiprocessing',
          '_posixsubprocess', 'breakpoint', 'sys', 'signal', '_signal', 'gc', 'getattr',
          'setattr', 'delattr', 'vars', 'globals', 'locals', 'meta_path', 'path_hooks',
          'BuiltinImporter', '_imp', 'resource', '__dict__', '__class__', '__subclasses__',
          '__bases__', '__base__', '__mro__', '__getattribute__', '__globals__', '__code__',
          '__loader__', '__spec__', 'attrgetter', 'methodcaller', 'threading', '_thread'}


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
timed = g['pathological']() + [tuple(c) for c in TIMED]
path_cases = [[p, t] for p, t, _ in timed]
want_path = [None if e is None else [list(x) if x else None for x in e] for _, _, e in timed]


def _calib():
    t0 = time.perf_counter()
    d, x = {}, 0
    for i in range(200000):
        d[i & 1023] = (i, x)
        x = (x + len(d) * i) % 1000003
    return time.perf_counter() - t0


# Host-speed factor: the budgets were set on a host where _calib() takes
# CALIB_BASE_S; a slower or contended host (--jobs) gets proportionally more
# time, capped so a solution slow everywhere (4 + 4 + 6 s, times 2.0)
# still ends inside the harness's 30 s.
CALIB_BASE_S = 0.030
SPEED = min(2.0, max(1.0, statistics.median(_calib() for _ in range(5)) / CALIB_BASE_S))

RUNNER = r'''
import json, sys
# Everything rx.py may import is loaded here, before re is disabled, imports
# are switched off and the process loses the ability to open files.
import __future__, abc, array, bisect, collections, collections.abc, contextlib, copy
import dataclasses, enum, functools, heapq, io, itertools, math, operator, random
import string, types, typing, weakref, linecache, traceback
import _sre, re as _re, os as _os, resource as _rs
def _blocked(*a, **k):
    raise RuntimeError("the re engine is disabled for this task")
_sre.compile = _blocked
_re.purge()
_job = json.loads(sys.stdin.read())
with open("/tmp/eval_pike192/rx.py", encoding="utf-8", errors="replace") as _f:
    _src = _f.read()
_out = sys.stdout
_os.closerange(3, 4096)
_rs.setrlimit(_rs.RLIMIT_NOFILE, (3, 3))
sys.meta_path[:] = []
sys.path_hooks[:] = []
sys.path_importer_cache.clear()
del _re, _sre, _os, _rs, _f
_m = types.ModuleType("rx")
_m.__file__ = "/tmp/eval_pike192/rx.py"
sys.modules["rx"] = _m
try:
    exec(compile(_src, _m.__file__, "exec"), _m.__dict__)
except BaseException as _e:
    _out.write("\n@@LOADERR@@%s\n" % type(_e).__name__); _out.flush(); sys.exit(0)
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
    timeout = timeout * SPEED
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
    if '@@LOADERR@@' in out:
        fail('rx.py failed to load: ' + out.split('@@LOADERR@@')[1].split()[0])
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
                fail('%s case %d: timed out (%.1fs for the whole set)' % (label, i, timeout * SPEED))
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
print('OK: %d search, %d finditer, %d timed cases'
      % (len(search_cases), len(iter_cases), len(path_cases)))
