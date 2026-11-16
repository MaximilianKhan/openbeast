# Validator body for 192_pike_regex. build.py prepends two literals, GEN
# (gen.py: seeded generators, classic pathological set, hand-picked cases) and
# TIMED (timed.py's output: slow-for-re cases whose answers depend on priority,
# laziness, captures and $, precomputed by re on the unwrapped core and checked
# against ref), and emits the result as the task's python validation script.
#
# The oracle is CPython's own re, run here, in the validator process. rx.py
# runs in a separate process (vlib.run_solution, CPU budget for its whole
# tree, fork refused) that is handed only the cases, never an answer, and
# prints its results; this process compares them. That child cannot open
# files, import anything new, exec another program or rebuild a builtin
# module, so it cannot get a working re back (see RUNNER). And even if it
# did: the WRAPPED sets put every case from the random distribution under a
# never-matching gadget that makes re exponential, so "re when it is fast,
# my own engine otherwise" still needs a correct engine on thousands of
# ordinary cases, search and finditer alike.
import io, json, os, re, signal, sys, tokenize

import vlib

D = '/tmp/eval_pike192'
SRC = os.path.join(D, 'rx.py')
SEED_SEARCH, N_SEARCH = 0x192A5EED, 14000
SEED_ITER, N_ITER = 0x192B17E4, 1500
SEED_WRAP, N_WRAP = 0x192C0DE5, 3000
SEED_WRAP_ITER, N_WRAP_ITER = 0x192C0DE6, 600
# CPU seconds of the rx.py process, written at scale 1 (vlib scales them).
# The reference needs about 0.25, 0.05, 0.4, 0.5 and 0.45 s; a solution
# slow everywhere is stopped after 11.5 s of CPU in all, inside the 15 s.
PLAIN_CPU_S = 1.5
ITER_CPU_S = 1.0
WRAP_CPU_S = 2.5
WRAP_ITER_CPU_S = 2.5
PATH_CPU_S = 4.0
ORACLE_GUARD_S = 0.05   # a random core re itself is slow on is skipped

# Defense in depth only: the seal in RUNNER and the WRAPPED sets are what
# make restoring re pointless.
BANNED = {'re', '_sre', 'sre_compile', 'sre_parse', 'sre_constants', 'regex', 'fnmatch',
          'glob', 'importlib', '__import__', 'builtins', '__builtins__', 'exec', 'eval',
          'modules', 'subprocess', 'os', 'posix', 'pty', 'ctypes', 'multiprocessing',
          '_posixsubprocess', 'breakpoint', 'sys', 'signal', '_signal', 'gc', 'getattr',
          'setattr', 'delattr', 'vars', 'globals', 'locals', 'meta_path', 'path_hooks',
          'BuiltinImporter', '_imp', 'resource', '__dict__', '__class__', '__subclasses__',
          '__bases__', '__base__', '__mro__', '__getattribute__', '__globals__', '__code__',
          '__loader__', '__spec__', 'attrgetter', 'methodcaller', 'threading', '_thread',
          '_sys', 'Formatter', 'get_field', 'loader', 'create_module', 'exec_module',
          'create_builtin', 'exec_builtin', 'create_dynamic', 'execv', 'execve',
          'posix_spawn', 'fork', 'addaudithook'}


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


class _Slow(Exception):
    pass


def _alarm(*_):
    raise _Slow()


signal.signal(signal.SIGALRM, _alarm)


def guarded(fn):
    """fn() under a short alarm; None when re itself is slow on this core."""
    signal.setitimer(signal.ITIMER_REAL, vlib.t(ORACLE_GUARD_S))
    try:
        return [fn()]
    except _Slow:
        return None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


search_cases = [list(c) for c in g['TRICKY']] + [list(c) for c in g['gen_cases'](SEED_SEARCH, N_SEARCH)]
iter_cases = [list(c) for c in g['TRICKY']] + [list(c) for c in g['gen_cases'](SEED_ITER, N_ITER)]
want_search = [None if m is None else spans(m) for m in (re.search(p, t) for p, t in search_cases)]
want_iter = [[spans(m) for m in re.finditer(p, t)] for p, t in iter_cases]

wrap_cases, want_wrap = [], []
for p, core, t in g['gen_wrapped'](SEED_WRAP, N_WRAP):
    r = guarded(lambda: re.search(core, t))
    if r is not None:
        wrap_cases.append([p, t])
        want_wrap.append(None if r[0] is None else spans(r[0]))
wrap_iter_cases, want_wrap_iter = [], []
for p, core, t in g['gen_wrapped'](SEED_WRAP_ITER, N_WRAP_ITER):
    r = guarded(lambda: [spans(m) for m in re.finditer(core, t)])
    if r is not None:
        wrap_iter_cases.append([p, t])
        want_wrap_iter.append(r[0])

timed = g['pathological']() + [tuple(c) for c in TIMED]
path_cases = [[p, t] for p, t, _ in timed]
want_path = [None if e is None else [list(x) if x else None for x in e] for _, _, e in timed]

# The solution's process. It is fed on stdin as a script (no -c: nothing in
# its argv), prefixed with the cases. It holds no answers.
RUNNER = r'''
import json, sys
# Everything rx.py may import is loaded here, before re is disabled, imports
# are switched off and the process loses the ability to open files.
import __future__, abc, array, bisect, collections, collections.abc, contextlib, copy
import dataclasses, enum, functools, heapq, io, itertools, math, operator, random
import string, types, typing, weakref, linecache, traceback
import _imp, _sre, re as _re, os as _os, resource as _rs
_SEAL = True
def _blocked(*a, **k):
    raise RuntimeError("disabled for this task")
# The only reference to the real compiler is _sre's own dict entry.
_sre.compile = _blocked
_re.purge()
if _SEAL:
    # A builtin or extension module can be instantiated afresh through its
    # loader (spec.loader.create_module -> _imp.create_builtin), which is how
    # a fresh, working _sre was rebuilt. No audit event covers that, so take
    # the primitives away. Nothing else holds them.
    for _n in ("create_builtin", "exec_builtin", "create_dynamic", "exec_dynamic"):
        if hasattr(_imp, _n):
            setattr(_imp, _n, _blocked)
    # Replacing this process with a fresh interpreter would bring re back too.
    for _mod in (_os, sys.modules.get("posix")):
        for _n in ("execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp",
                   "execlpe", "fexecve", "fork", "forkpty", "posix_spawn", "posix_spawnp",
                   "spawnv", "spawnve", "spawnvp", "spawnvpe", "spawnl", "spawnle",
                   "system", "popen", "register_at_fork"):
            if _mod is not None and hasattr(_mod, _n):
                setattr(_mod, _n, _blocked)
_job = json.loads(_JOB)
with open("/tmp/eval_pike192/rx.py", encoding="utf-8", errors="replace") as _f:
    _src = _f.read()
_out = sys.stdout
_os.closerange(3, 4096)
_rs.setrlimit(_rs.RLIMIT_NOFILE, (3, 3))
sys.meta_path[:] = []
sys.path_hooks[:] = []
sys.path_importer_cache.clear()
if _SEAL:
    # Audit hooks cannot be removed. Whatever route rx.py finds to an
    # importer, a file or another program, the event is refused.
    _DENY = frozenset(("import", "open", "os.exec", "os.posix_spawn", "os.spawn", "os.fork",
                       "os.forkpty", "os.system", "subprocess.Popen", "_posixsubprocess.fork_exec"))
    def _hook(ev, args, _d=_DENY, _e=PermissionError):
        if ev in _d or ev.startswith("ctypes."):
            raise _e("blocked: " + ev)
    sys.addaudithook(_hook)
del _re, _sre, _os, _rs, _f, _imp
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
fn = _m.__dict__.get("finditer" if _job["mode"] == "iter" else "search")
if not callable(fn):
    _out.write("\n@@NOFN@@\n"); _out.flush(); sys.exit(0)
for _i, (_p, _t) in enumerate(_job["cases"]):
    _r = _one(fn, _p, _t)
    _out.write("\n@@R@@" + json.dumps([_i, _r]) + "\n")
    _out.flush()
'''


def run(mode, cases, cpu_s):
    """Returns (results, over_budget, crash_tail)."""
    job = json.dumps({'mode': mode, 'cases': cases})
    script = '_JOB = %r\n' % job + RUNNER
    r = vlib.run_solution([sys.executable, '-I', '-S', '-'], input=script, cpu_s=cpu_s,
                          wall_s=1.5 * cpu_s + 2, cwd=D)
    out = r.stdout
    if r.verdict in ('forked', 'daemon', 'output'):
        fail('rx.py refused: ' + r.reason)
    if '@@LOADERR@@' in out:
        fail('rx.py failed to load: ' + out.split('@@LOADERR@@')[1].split()[0])
    if '@@NOFN@@' in out:
        fail('rx.py defines no %s()' % ('finditer' if mode == 'iter' else 'search'))
    res = {}
    for line in out.splitlines():
        if line.startswith('@@R@@'):
            try:
                i, x = json.loads(line[5:])
                res[i] = x
            except ValueError:
                pass
    tail = (r.stderr.strip().splitlines() or ['exit %s' % r.returncode])[-1][:160]
    return res, r.verdict in ('cpu', 'wall'), r.reason or tail


def check(label, mode, cases, want, cpu_s):
    res, over, tail = run(mode, cases, cpu_s)
    for i, w in enumerate(want):
        if i not in res:
            if over:
                fail('%s case %d: timed out (%s)' % (label, i, tail))
            fail('%s case %d: rx.py crashed: %s' % (label, i, tail))
        x = res[i]
        if isinstance(x, dict):
            fail('%s case %d: raised %s' % (label, i, x.get('err')))
        if x != w:
            fail('%s case %d: mismatch' % (label, i))
    if over:  # every answer arrived, but past the budget
        fail('%s case %d: timed out (%s)' % (label, len(want) - 1, tail))


check('search', 'search', search_cases, want_search, PLAIN_CPU_S)
check('finditer', 'iter', iter_cases, want_iter, ITER_CPU_S)
check('wrapped search', 'search', wrap_cases, want_wrap, WRAP_CPU_S)
check('wrapped finditer', 'iter', wrap_iter_cases, want_wrap_iter, WRAP_ITER_CPU_S)
check('pathological', 'search', path_cases, want_path, PATH_CPU_S)
print('OK: %d search, %d finditer, %d wrapped search, %d wrapped finditer, %d timed cases'
      % (len(search_cases), len(iter_cases), len(wrap_cases), len(wrap_iter_cases),
         len(path_cases)))
