# Validator body for 173_taskgroup_cancel. build.py prepends HARNESS / ORACLE
# string literals and emits the result as the task's python validation script.
import io, json, os, re, subprocess, sys, tokenize

D = '/tmp/eval_tg173'
SRC = os.path.join(D, 'tg.py')
N_CASES = 600
SEED = 0x7A5C6173


def fail(msg):
    print(msg)
    sys.exit(1)


if not os.path.isfile(SRC):
    fail('missing ' + SRC)
src = open(SRC, encoding='utf-8', errors='replace').read()

# Lint on code only (comments and string literals stripped).
try:
    code = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type not in (tokenize.COMMENT, tokenize.STRING):
            code.append(tok.string)
    code = ' '.join(code)
except (tokenize.TokenError, SyntaxError, IndentationError):
    fail('tg.py does not tokenize')
if re.search(
        r'asyncio\s*\.\s*TaskGroup\b|\btaskgroups\b|from\s+asyncio\s+import[^;\n]*\bTaskGroup\b'
        r'|\b__import__\b|\bimportlib\b|\breload\b|\bexec\b|\beval\b|\bgc\b|\bget_objects\b', code):
    fail('forbidden api: asyncio.TaskGroup / dynamic import, exec or gc')

RUNNER = r'''
import asyncio, asyncio.taskgroups as _tgm, importlib.util, json, sys
class _Disabled:
    def __init__(self, *a, **k):
        raise RuntimeError('asyncio.TaskGroup is disabled for this task')
_tgm.TaskGroup = _Disabled
asyncio.TaskGroup = _Disabled
sys.modules['asyncio'].TaskGroup = _Disabled
import gc as _gc
_gc.collect()
_spec = importlib.util.spec_from_file_location('tg', '/tmp/eval_tg173/tg.py')
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)
_scen = json.loads(sys.stdin.read())
_ns = {'__name__': '_h'}
exec(_HARNESS, _ns)
TG = getattr(_m, 'TaskGroup', None)
if not isinstance(TG, type):
    print('@@RESULT@@' + json.dumps('no TaskGroup class'))
    sys.exit(0)
_res = _ns['run_all'](_scen, TG)
sys.stdout.write('\n@@RESULT@@' + json.dumps(_res) + '\n')
'''

ns = {'__name__': '_h'}
exec(HARNESS, ns)
oracle_ns = {'__name__': '_oracle'}
exec(ORACLE, oracle_ns)
scen = ns['gen_scenarios'](SEED, N_CASES)
want = json.loads(json.dumps(ns['run_all'](scen, oracle_ns['TaskGroup'])))

prog = '_HARNESS = ' + repr(HARNESS) + '\n' + RUNNER
try:
    r = subprocess.run([sys.executable, '-I', '-B', '-c', prog], cwd=D, input=json.dumps(scen),
                       capture_output=True, text=True, timeout=10)
except subprocess.TimeoutExpired:
    fail('solution timed out (10s)')
line = [l for l in r.stdout.splitlines() if l.startswith('@@RESULT@@')]
if r.returncode != 0 or not line:
    err = r.stderr.strip().splitlines()
    fail('tg.py crashed: ' + (err[-1][:160] if err else 'exit %d' % r.returncode))
got = json.loads(line[-1][len('@@RESULT@@'):])
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
            if what == 'error' and isinstance(g, dict):
                what = 'error (%s)' % g['err'].split()[0]
            fail('%s: %s' % (label, what))
print('OK: %d scenarios' % (len(want) - 1))
