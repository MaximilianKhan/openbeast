# Validator body for 173_taskgroup_cancel. build.py prepends HARNESS / ORACLE
# string literals and emits the result as the task's python validation script.
#
# Isolation: the oracle runs HERE, in the validator process. The submission
# runs in a separate child (vlib.run_solution) that holds only the scenario
# interpreter and the scenarios, never the oracle or the expected results.
# The child is fed its program on stdin (no `-c`, nothing in argv) and prints
# observations; this process compares them.
import ast, io, json, os, re, sys, tokenize
import vlib

D = '/tmp/eval_tg173'
SRC = os.path.join(D, 'tg.py')
N_CASES = 600                 # random scenarios; harness.N_TAIL cancel-tail ones follow
SEED = 0x7A5C6173
CPU_S = 5                     # at scale 1; the reference needs well under 1 s

# Static imports a TaskGroup may use. Anything else (sys, os, importlib,
# operator.attrgetter, ...) can reach the module table, the import path or
# another process, none of which a TaskGroup needs.
ALLOWED_MODULES = {'asyncio', 'collections', 'contextlib', 'enum',
                   'functools', 'heapq', 'itertools', 'math', 'time', 'typing',
                   'weakref', 'abc', '__future__'}
# Attribute names that load code or patch the interpreter / the loop.
BAD_ATTRS = {'path', 'meta_path', 'path_hooks', 'path_importer_cache', 'modules',
             'import_module', 'exec_module', 'load_module', 'SourceFileLoader',
             'spec_from_file_location', 'run_path', 'run_module', '__dict__',
             'set_exception_handler', 'set_task_factory', 'set_event_loop',
             'set_event_loop_policy', 'set_debug', 'TaskGroup', 'taskgroups',
             'to_thread', 'run_in_executor', 'set_default_executor', '__class__',
             '__func__', '__self__', 'tb_frame', 'tb_next', 'f_back', 'f_globals', 'f_locals',
             'f_builtins', 'gi_frame', 'cr_frame', 'ag_frame', 'get_stack', 'print_stack',
             'get_coro', 'cr_code', 'gi_code'}


def fail(msg):
    print(msg)
    sys.exit(1)


if not os.path.isfile(SRC):
    fail('missing ' + SRC)
src = open(SRC, encoding='utf-8', errors='replace').read()


def lint(src):
    """Problems in the submission's source ([] = clean).

    Beyond vlib.lint_python: an import allowlist, and "self" as the only
    door to private state or attribute writes. `self` is exempt only where it
    is provably the group itself: the first parameter of a plain method (a
    def directly in a class body, decorated with nothing but property). So
    nothing can rebind `self`, take it as another parameter, reach a method
    unbound (`Cls.m(obj)`, `super(C, obj)`, `type(x)`, `__class__`), or call
    a method from the class body, which keeps `def f(self): self._x = ...;
    f(asyncio.tasks)` and its variants out.
    """
    probs = vlib.lint_python(src)
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return probs or ['syntax error']
    classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    P = lambda node, msg: probs.append('line %d: %s' % (getattr(node, 'lineno', 0), msg))

    def ok_decorator(d):
        return (isinstance(d, ast.Name) and d.id == 'property') or (
            isinstance(d, ast.Attribute) and d.attr in ('setter', 'getter', 'deleter'))

    def own(node, in_method):
        """node is `self.<x>` inside a method, where self is the group."""
        return in_method and isinstance(node, ast.Name) and node.id == 'self'

    def visit(node, in_method, parent=None):
        if isinstance(node, ast.ClassDef):
            if node.keywords:
                P(node, 'class keywords (metaclass=...)')
            for d in node.decorator_list:
                P(d, 'class decorator')
            for b in node.bases:
                visit(b, in_method, node)
            for st in node.body:
                if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    is_m = bool(st.args.args) and st.args.args[0].arg == 'self' and \
                        all(ok_decorator(d) for d in st.decorator_list) and not st.args.posonlyargs
                    if not all(ok_decorator(d) for d in st.decorator_list):
                        P(st, 'method decorator other than property')
                    for d in st.decorator_list:
                        visit(d, False, st)
                    visit_fn(st, method=is_m)
                elif isinstance(st, ast.ClassDef):
                    visit(st, False, node)
                elif isinstance(st, ast.Pass) or (isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant)):
                    pass
                elif isinstance(st, (ast.Assign, ast.AnnAssign)) and isinstance(st.value, ast.Constant) and \
                        all(isinstance(t, ast.Name) for t in (st.targets if isinstance(st, ast.Assign) else [st.target])):
                    pass
                else:
                    P(st, 'class body may hold only methods and constants')
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for d in getattr(node, 'decorator_list', []):
                visit(d, in_method, node)
            visit_fn(node, method=False, inherit=in_method)
            return
        if isinstance(node, ast.Name):
            if node.id == 'self' and not isinstance(node.ctx, ast.Load):
                P(node, 'rebinding self')
            if node.id in classes and isinstance(node.ctx, ast.Load) and not (
                    isinstance(parent, ast.Call) and parent.func is node) and not isinstance(parent, ast.ClassDef):
                P(node, 'class %r used other than by calling it' % node.id)
            if node.id in ('type', 'super', 'object', 'vars', 'dir') and isinstance(node.ctx, ast.Load):
                bare_call = isinstance(parent, ast.Call) and parent.func is node \
                    and not parent.args and not parent.keywords
                if not ((node.id in ('super', 'object') and bare_call)
                        or (node.id == 'object' and isinstance(parent, ast.ClassDef))):
                    P(node, 'name %r' % node.id)
        elif isinstance(node, ast.alias):
            if (node.asname or node.name) == 'self':
                P(node, 'import as self')
        elif isinstance(node, ast.ExceptHandler) and node.name == 'self':
            P(node, 'except ... as self')
        elif isinstance(node, (ast.Global, ast.Nonlocal)) and 'self' in node.names:
            P(node, 'global self')
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and getattr(node, 'name', None) == 'self':
            P(node, 'match capture self')
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split('.')[0] not in ALLOWED_MODULES or 'taskgroups' in a.name:
                    P(node, 'import of %r' % a.name)
        elif isinstance(node, ast.ImportFrom):
            m = node.module or ''
            if node.level or m.split('.')[0] not in ALLOWED_MODULES or 'taskgroups' in m:
                P(node, 'import from %r' % m)
            for a in node.names:
                if a.name in BAD_ATTRS or a.name == '*' or a.name.startswith('_'):
                    P(node, 'import of %r' % a.name)
        elif isinstance(node, ast.Attribute):
            if node.attr in BAD_ATTRS:
                P(node, 'attribute %r' % node.attr)
            if node.attr.startswith('_') and not own(node.value, in_method):
                P(node, 'private attribute %r of another object' % node.attr)
            if not isinstance(node.ctx, ast.Load) and not own(node.value, in_method):
                P(node, 'assignment to an attribute of another object')
        elif isinstance(node, ast.Subscript) and not isinstance(node.ctx, ast.Load):
            base = node.value
            while isinstance(base, ast.Subscript):
                base = base.value
            if not (isinstance(base, ast.Name) and base.id != 'self') and not (
                    isinstance(base, ast.Attribute) and own(base.value, in_method)):
                P(node, 'item assignment into another object')
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in ('getattr', 'setattr', 'delattr', 'hasattr'):
                first_own = bool(node.args) and own(node.args[0], in_method)
                if fn.id in ('setattr', 'delattr') and not first_own:
                    P(node, '%s() on another object' % fn.id)
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and \
                        isinstance(node.args[1].value, str):
                    n = node.args[1].value
                    if n in BAD_ATTRS or (n.startswith('_') and not first_own):
                        P(node, '%s() of %r' % (fn.id, n))
        for ch in ast.iter_child_nodes(node):
            visit(ch, in_method, node)

    def visit_fn(fn, method, inherit=False):
        a = fn.args
        params = a.posonlyargs + a.args + a.kwonlyargs + [x for x in (a.vararg, a.kwarg) if x]
        for i, p in enumerate(params):
            if p.arg == 'self' and not (method and i == 0 and p in a.args):
                P(fn, 'parameter named self')
        for d in a.defaults + [x for x in a.kw_defaults if x is not None]:
            visit(d, inherit, fn)
        body = fn.body if isinstance(fn.body, list) else [fn.body]
        for st in body:
            visit(st, method or inherit, fn)

    for st in tree.body:
        visit(st, False, tree)
    seen, out = set(), []
    for p in probs:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


# Second guard, on code tokens (comments and strings stripped).
try:
    code = ' '.join(tok.string for tok in tokenize.generate_tokens(io.StringIO(src).readline)
                    if tok.type not in (tokenize.COMMENT, tokenize.STRING))
except (tokenize.TokenError, SyntaxError, IndentationError):
    fail('tg.py does not tokenize')
probs = lint(src)
if re.search(r'\btaskgroups\b|\b__import__\b|\bimportlib\b|\bexec\b|\beval\b'
             r'|\bgc\b|\bget_objects\b', code):
    probs.append('asyncio.TaskGroup / dynamic import, exec or gc')
if probs:
    fail('forbidden api: ' + probs[0][:160])

# The child: disable the stdlib TaskGroup, exec the scenario interpreter
# BEFORE the submission loads (it binds its own references first), then load
# tg.py and print the observations. It holds no expected results.
RUNNER = r'''
import asyncio, asyncio.taskgroups as _tgm, importlib.util, json, sys
class _Disabled:
    def __init__(self, *a, **k):
        raise RuntimeError('asyncio.TaskGroup is disabled for this task')
_tgm.TaskGroup = _Disabled
asyncio.TaskGroup = _Disabled
import gc as _gc
_gc.collect()
_ns = {'__name__': '_h'}
exec(_HARNESS, _ns)
_run_all = _ns['run_all']
_out = sys.stdout
_spec = importlib.util.spec_from_file_location('tg', '/tmp/eval_tg173/tg.py')
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)
TG = _m.__dict__.get('TaskGroup')
if not isinstance(TG, type):
    _out.write('\n@@RESULT@@' + json.dumps('no TaskGroup class') + '\n')
    sys.exit(0)
_res = _run_all(_SCEN, TG)
_out.write('\n@@RESULT@@' + json.dumps(_res) + '\n')
_out.flush()
'''

ns = {'__name__': '_h'}
exec(HARNESS, ns)
oracle_ns = {'__name__': '_oracle'}
exec(ORACLE, oracle_ns)
scen = ns['gen_scenarios'](SEED, N_CASES)
want = json.loads(json.dumps(ns['run_all'](scen, oracle_ns['TaskGroup'])))
if len(want) != len(scen) + 1 or any('err' in w for w in want):
    fail('validator self-check failed')

prog = ('_HARNESS = ' + repr(HARNESS) + '\n'
        + '_SCEN = __import__("json").loads(' + repr(json.dumps(scen)) + ')\n' + RUNNER)
r = vlib.run_solution([sys.executable, '-I', '-S', '-B', '-'], input=prog, cpu_s=CPU_S, cwd=D)
if r.verdict in ('cpu', 'wall'):
    fail('solution timed out (%s budget)' % r.verdict)
if r.verdict != 'ok':
    fail('solution refused: %s' % r.verdict)
line = [l for l in r.stdout.splitlines() if l.startswith('@@RESULT@@')]
if r.returncode != 0 or not line:
    err = r.stderr.strip().splitlines()
    fail('tg.py crashed: ' + (err[-1][:160] if err else 'exit %d' % r.returncode))
try:
    got = json.loads(line[-1][len('@@RESULT@@'):])
except ValueError:
    fail('bad result: unparseable')
if not isinstance(got, list):
    fail('bad result: ' + (got if isinstance(got, str) else 'not a list'))

# Report the gravest class first (crash > leaked tasks > leaked callbacks >
# behaviour), each by case index only.
checks = [
    ('error', lambda g, w: not isinstance(g, dict) or 'err' in g),
    ('tasks left pending', lambda g, w: g.get('pend') != w['pend']),
    ('callbacks left scheduled', lambda g, w: g.get('sched') != w['sched']),
    ('error (exception handler called)', lambda g, w: g.get('eh') != w['eh']),
    ('behaviour mismatch', lambda g, w: g.get('res') != w['res'] or g.get('trace') != w.get('trace')),
]
for what, bad in checks:
    for i, w in enumerate(want):
        g = got[i] if i < len(got) else None
        if bad(g, w):
            label = 'misuse case' if i == 0 else 'case %d' % i
            if what == 'error' and isinstance(g, dict) and isinstance(g.get('err'), str):
                what = 'error (%s)' % (g['err'].split() or ['?'])[0]
            fail('%s: %s' % (label, what))
if len(got) != len(want):
    fail('bad result: %d results for %d cases' % (len(got), len(want)))
print('OK: %d scenarios' % (len(want) - 1))
