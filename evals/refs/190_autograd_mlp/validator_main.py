# Validator body for 190_autograd_mlp (build.py prepends ORACLE = '...' and
# DRIVER = '...'). The hidden programs come from a seeded generator in the
# oracle; expected values are a forward-only evaluator plus central finite
# differences (h = 1e-5). Only the index and kind of the first failing case
# is printed, never an expected value.
import ast
import json
import math
import os
import shutil
import sys
import tempfile

import vlib

D = '/tmp/eval_autograd190'
SRC = os.path.join(D, 'autograd.py')
CPU_LIMIT_S = 10  # CPU seconds for the whole hidden run, at scale 1 (vlib scales it)
GTOL = 1e-6      # gradient vs finite difference: |g - fd| <= GTOL * max(1, |fd|)
VTOL = 1e-9      # forward values
MTOL = 1e-6      # MLP loss trace and final parameters


def fail(msg):
    print(msg)
    sys.exit(1)


# --- lint (second line of defence: the solution runs in its own -I -S process
# behind an import blocker; the lint keeps it from reaching around that) -----
ALLOWED_MODULES = {'math', 'cmath', 'itertools', 'functools', 'operator', 'collections',
                   'heapq', 'bisect', 'array', 'copy', 'numbers', 'typing', 'dataclasses',
                   'abc', 'enum', 'fractions', 'decimal', 'statistics', 'random', 'sys',
                   'weakref', 'contextlib', 'time'}
SYS_ATTRS = {'setrecursionlimit', 'getrecursionlimit', 'float_info', 'maxsize',
             'stderr', 'stdout', 'version_info', 'exit'}
BANNED_NAMES = {'__import__', 'exec', 'eval', 'compile', 'globals', 'vars', 'locals',
                'breakpoint', 'open', 'getattr', 'delattr', 'input', 'help',
                '__builtins__', '__loader__', '__spec__', 'memoryview'}
BANNED_ATTRS = {'attrgetter', 'methodcaller', 'get_type_hints', 'ForwardRef', '_eval_type',
                '_evaluate', 'evaluate_forward_ref', '_getframe', 'f_back', 'f_globals',
                'f_locals', 'f_builtins', 'f_code', 'gi_frame', 'gi_code', 'cr_frame',
                'cr_code', 'ag_frame', 'tb_frame', 'tb_next', 'co_consts', 'co_code'}
OK_DUNDERS = {'__init__', '__new__', '__name__', '__qualname__', '__slots__',
              '__class__', '__len__', '__iter__', '__next__', '__getitem__', '__setitem__',
              '__contains__', '__eq__', '__ne__', '__lt__', '__le__', '__gt__', '__ge__',
              '__hash__', '__repr__', '__str__', '__bool__', '__float__', '__int__',
              '__index__', '__neg__', '__pos__', '__abs__', '__array_priority__'} | {
    f'__{p}{op}__' for op in ('add', 'sub', 'mul', 'truediv', 'floordiv', 'mod', 'pow',
                              'matmul') for p in ('', 'r', 'i')}


BANNED_ATTRS |= {'modules', 'meta_path', 'path_hooks', 'path_importer_cache', 'import_module',
                 'resolve_name', 'locate', 'find_spec', 'spec_from_file_location',
                 'module_from_spec', 'exec_module', 'load_module', 'singledispatch',
                 'singledispatchmethod', 'mro'}


def _module_attrs():
    """{allowed module: attribute names under which it exposes a module
    object (dataclasses.sys, typing.re, random._os, ...)}. The solution may
    never reach a module it did not import itself. (No class in these
    modules carries a module attribute.)"""
    import importlib
    import types
    import warnings
    out = {}
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        for n in ALLOWED_MODULES:
            m = importlib.import_module(n)
            out[n] = {k for k in dir(m) if isinstance(vars(m).get(k), types.ModuleType)
                      or isinstance(getattr(m, k, None), types.ModuleType)}
    return out


MODULE_ATTRS = _module_attrs()


def lint(src):
    """Problems with the solution source ([] = clean). Imports must name an
    allowed stdlib module; a module object may only be used as `mod.name`
    with a public, non-module name; sys only as sys.<SYS_ATTRS>; getattr only
    with a literal name; no exec/eval/open/__import__ or dunder walks."""
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f'syntax error (line {e.lineno})']
    par = {}
    for node in ast.walk(tree):
        for ch in ast.iter_child_nodes(node):
            par[ch] = node
    modnames = {}                     # bound name -> allowed module it names
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for al in node.names:
                modnames[al.asname or al.name.split('.')[0]] = (al.asname and al.name) or al.name.split('.')[0]
    out = []

    def attr_ok(a, ln, on_module=None):
        if a in BANNED_ATTRS:
            out.append(f'line {ln}: attribute {a!r}')
        if on_module is not None and a in MODULE_ATTRS.get(on_module.split('.')[0], ()):
            out.append(f'line {ln}: {on_module}.{a} is a module')
        if a.startswith('__') and a.endswith('__') and a not in OK_DUNDERS:
            out.append(f'line {ln}: dunder attribute {a!r}')
        if on_module and a.startswith('_'):
            out.append(f'line {ln}: private module attribute {a!r}')

    for node in ast.walk(tree):
        ln = getattr(node, 'lineno', 0)
        if isinstance(node, ast.Import):
            for al in node.names:
                top = al.name.split('.')[0]
                if top not in ALLOWED_MODULES:
                    out.append(f'line {ln}: import of {al.name!r}')
                elif top == 'sys' and (al.asname or al.name != 'sys'):
                    out.append(f'line {ln}: sys must be imported as plain `import sys`')
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or '').split('.')[0]
            if node.level or top not in ALLOWED_MODULES:
                out.append(f'line {ln}: import from {node.module!r}')
            elif top == 'sys' and any(al.name not in SYS_ATTRS for al in node.names):
                out.append(f'line {ln}: from sys import of internals')
            for al in node.names:
                if al.name == '*':
                    out.append(f'line {ln}: star import')
                else:
                    attr_ok(al.name, ln, on_module=node.module or '')
        elif isinstance(node, ast.Name):
            if node.id.startswith('__') and node.id not in OK_DUNDERS | {'__file__'}:
                out.append(f'line {ln}: name {node.id!r}')
            if node.id in BANNED_NAMES and not (node.id == 'getattr' and _literal_getattr(node, par)):
                out.append(f'line {ln}: name {node.id!r}')
            if node.id in modnames:
                pa = par.get(node)
                if not isinstance(node.ctx, ast.Load):
                    out.append(f'line {ln}: rebinding module name {node.id!r}')
                elif not (isinstance(pa, ast.Attribute) and pa.value is node):
                    out.append(f'line {ln}: module {node.id!r} used as a value')
                elif node.id == 'sys' and pa.attr not in SYS_ATTRS:
                    out.append(f'line {ln}: sys may only be used as sys.<{"|".join(sorted(SYS_ATTRS))}>')
        elif isinstance(node, ast.Attribute):
            attr_ok(node.attr, ln, on_module=modnames.get(node.value.id)
                    if isinstance(node.value, ast.Name) else None)
            if node.attr in ('format', 'format_map'):
                v = node.value
                if not (isinstance(v, ast.Constant) and isinstance(v.value, str)
                        and not _format_walks(v.value)):
                    out.append(f'line {ln}: str.format only on a literal without field walks')
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in BANNED_NAMES or node.name in modnames:
                out.append(f'line {ln}: defines {node.name!r}')
        elif isinstance(node, ast.arg) and (node.arg in modnames or node.arg in BANNED_NAMES):
            out.append(f'line {ln}: parameter shadows {node.arg!r}')
        elif isinstance(node, ast.alias) and node.asname in BANNED_NAMES:
            out.append(f'line {ln}: import alias {node.asname!r}')
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # a bare string statement (a docstring) is never a value the code
            # can use: __doc__ is not an allowed attribute
            if '__' in node.value and node.value != '__main__' \
                    and not isinstance(par.get(node), ast.Expr):
                out.append(f'line {ln}: string constant containing "__"')
        elif isinstance(node, ast.JoinedStr):
            for fv in node.values:
                if isinstance(fv, ast.FormattedValue) and fv.format_spec is not None \
                        and any(isinstance(x, ast.FormattedValue) for x in ast.walk(fv.format_spec)):
                    out.append(f'line {ln}: nested f-string format spec')
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def _literal_getattr(node, par):
    """getattr(obj, 'name'[, default]) with a literal, ordinary attribute name."""
    call = par.get(node)
    if not (isinstance(call, ast.Call) and call.func is node and 2 <= len(call.args) <= 3
            and not call.keywords):
        return False
    a = call.args[1]
    return (isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value.isidentifier()
            and not a.value.startswith('__') and a.value not in BANNED_ATTRS
            and not (isinstance(call.args[0], ast.Name) and call.args[0].id == 'sys'))


def _format_walks(fmt):
    import string
    try:
        for _lit, field, spec, _conv in string.Formatter().parse(fmt):
            if field and ('.' in field or '[' in field):
                return True
            if spec and '{' in spec:
                return True
    except ValueError:
        return True
    return False


if not os.path.isfile(SRC):
    fail('no solution: /tmp/eval_autograd190/autograd.py does not exist')
src = open(SRC, errors='replace').read()
problems = lint(src)
if problems:
    fail('forbidden api: ' + problems[0] + ' (standard library only; see the allowed '
         'modules in the task; no getattr/exec/eval/open/__import__)')

ns = {}
exec(compile(ORACLE, 'oracle', 'exec'), ns)  # noqa: F821  (prepended by build.py)
cases = ns['build_cases']()
prob = ns['mlp_problem']()
want_losses, want_params = ns['mlp_oracle'](prob)
req = {'cases': [c for c, _, _, _ in cases], 'mlp': prob}

# The solution runs in its own process: a private dir holding only a copy of
# autograd.py, `python -I -S -` (no site-packages), driver source on stdin with
# the programs (inputs only) prepended. Expected values never leave this process.
work = tempfile.mkdtemp(prefix='a190-')
try:
    shutil.copyfile(SRC, os.path.join(work, 'autograd.py'))
    script = 'REQ = ' + repr(json.dumps(req)) + '\n' + DRIVER  # noqa: F821
    r = vlib.run_solution([sys.executable, '-I', '-S', '-'], input=script, cwd=work,
                          cpu_s=CPU_LIMIT_S, wall_s=4 * CPU_LIMIT_S + 10)
finally:
    shutil.rmtree(work, ignore_errors=True)
if r.verdict in ('cpu', 'wall'):
    fail(f'solution timed out ({CPU_LIMIT_S}s CPU at scale 1 for the whole hidden run)')
if r.verdict != 'ok':
    fail(f'solution rejected: {r.verdict} ({r.reason})')
try:
    got = json.loads(r.stdout)
    assert isinstance(got, dict)
except (ValueError, AssertionError):
    fail(f'solution crashed (exit {r.returncode}); no results')
if 'import_error' in got:
    fail(f'import failed: {got["import_error"]} (need Tensor, cross_entropy, train_mlp)')


def close(a, b, tol):
    return math.isfinite(a) and abs(a - b) <= tol * max(1.0, abs(b))


def check_case(c, V, S, G, res):
    if 'error' in res:
        return f'error {res["error"]}'
    deep = c.get('deep', False)
    rg = []
    for op in c['ins']:
        k = op[0]
        src = {'bin': (2, 3), 'sc': (2,), 'pow': (1,), 'un': (2,), 'red': (2,),
               'reshape': (1,), 'T': (1,), 'mm': (1, 2), 'ce': (1,)}.get(k)
        rg.append(bool(op[3]) if k == 'leaf' else any(rg[j] for j in (op[q] for q in src)))
    if res.get('rg') != rg:
        return 'requires_grad mismatch'
    if not deep and res['shapes'] != S:
        return 'shape mismatch'
    for k, fv in res['vals'].items():
        i = int(k)
        if fv is None or fv[1] != S[i] or len(fv[0]) != len(V[i]):
            return 'shape mismatch'
        if not all(close(a, b, VTOL) for a, b in zip(fv[0], V[i])):
            return 'forward mismatch'
    for n, op in enumerate(c['ins']):
        if op[0] != 'leaf':
            continue
        g = res['grads'].get(str(n), 'missing')
        if not op[3]:
            if g is not None:
                return 'grad of a requires_grad=False leaf is not None'
            continue
        if g is None:                       # unreachable leaf: None or zeros both fine
            if any(v != 0.0 for v in G[n]):
                return 'gradient missing'
            continue
        if g == 'missing' or g[1] != S[n] or len(g[0]) != len(G[n]):
            return 'gradient shape mismatch'
        if not all(close(a, b, GTOL) for a, b in zip(g[0], G[n])):
            return 'gradient mismatch'
    return None


res = got.get('cases') or []
if len(res) != len(cases):
    fail(f'solution returned {len(res)} of {len(cases)} results')
bad = []
for i, ((c, V, S, G), rr) in enumerate(zip(cases, res)):
    why = check_case(c, V, S, G, rr)
    if why:
        bad.append((i, why))
if bad:
    i, why = bad[0]
    fail(f'case {i}: {why} ({len(bad)} of {len(cases)} cases wrong)')

m = got.get('mlp') or {}
if 'error' in m:
    fail(f'mlp: error {m["error"]}')
if m.get('inputs_intact') is not True:
    fail('mlp: train_mlp modified its input lists')
ls = m.get('losses') or []
if len(ls) != len(want_losses):
    fail(f'mlp: returned {len(ls)} losses, want steps + 1')
for k, (a, b) in enumerate(zip(ls, want_losses)):
    if not close(a, b, MTOL):
        fail(f'mlp: loss trace mismatch at step {k}')
ps = m.get('params') or []
if len(ps) != 6:
    fail('mlp: final params mismatch')
for q, w in zip(ps, want_params):
    mat = isinstance(w[0], list)
    wf = [v for row in w for v in row] if mat else list(w)
    ws = [len(w), len(w[0])] if mat else [len(w)]
    if q is None or q[1] != ws or not all(close(a, b, MTOL) for a, b in zip(q[0], wf)):
        fail('mlp: final params mismatch')
if not ls[-1] < 0.5 * ls[0]:
    fail('mlp: loss target not reached')
print(f'OK ({len(cases)} hidden programs, mlp loss {ls[0]:.4f} -> {ls[-1]:.4f})')
