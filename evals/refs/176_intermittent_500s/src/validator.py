# Validator for 176_intermittent_500s (embedded into the task JSON by
# build_task.py, which substitutes the @@...@@ placeholders).
#
# Only /tmp/eval_shopd500/shopd/ is graded. It is copied, with the pristine
# visible tests, into a private temp dir outside the fixture and run in
# isolated interpreters (-I -B: no PYTHON* env, no user site, no cwd on
# sys.path), so nothing else the agent leaves in the fixture dir is loaded.
#   1. tests/*: every visible test file byte-identical (sha256);
#   2. the visible suite is green (exact test count);
#   3. hidden tests, one fresh interpreter each, fed over stdin, fail fast.
# A run only counts when the child prints a token derived from a per-run
# nonce, so an early os._exit(0) or a patched unittest cannot fake a pass.
# Output is ONE line; a hidden failure names only the test id.
import hashlib, os, secrets, shutil, subprocess, sys, tempfile, time

D = '/tmp/eval_shopd500'
HASHES = @@HASHES@@
N_VISIBLE = @@N_VISIBLE@@
HIDDEN = @@HIDDEN@@
ORDER = @@ORDER@@
VISIBLE_TIMEOUT = 8
HIDDEN_TIMEOUT = 6
BUDGET_S = 13.5
T0 = time.time()

VISIBLE_BOOT = r'''
import sys, hashlib, io
def _main():
    nonce = sys.stdin.readline().strip()
    root = sys.argv[1]
    sys.path[:0] = [root + "/tests", root]
    import unittest
    suite = unittest.defaultTestLoader.discover(root + "/tests", top_level_dir=root + "/tests")
    res = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    ok = res.wasSuccessful() and res.testsRun == int(sys.argv[2])
    tag = hashlib.sha256((nonce + ":visible").encode()).hexdigest() if ok else "-"
    sys.stdout.write("\nRESULT %s %d %d %d\n" % (tag, res.testsRun, len(res.failures), len(res.errors)))
    sys.stdout.flush()
_main()
'''

HIDDEN_BOOT = r'''
import sys, hashlib
def _main():
    nonce = sys.stdin.readline().strip()
    tid = sys.stdin.readline().strip()
    src = sys.stdin.read()
    sys.path.insert(0, sys.argv[1])
    g = {"__name__": "hidden"}
    exec(compile(src, "<hidden>", "exec"), g)
    g[tid]()
    sys.stdout.write("\nRESULT " + hashlib.sha256((nonce + ":" + tid).encode()).hexdigest() + "\n")
    sys.stdout.flush()
_main()
'''


def fail(msg):
    print('FAIL: ' + msg)
    sys.exit(1)


def sha(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def last_result(out):
    lines = [ln for ln in out.splitlines() if ln.startswith('RESULT ')]
    return lines[-1].split() if lines else None


pkg = os.path.join(D, 'shopd')
if not os.path.isfile(os.path.join(pkg, '__init__.py')):
    fail('shopd/ package not found')
for rel, h in sorted(HASHES.items()):
    p = os.path.join(D, rel)
    if not os.path.isfile(p):
        fail('visible test file missing: ' + rel)
    if sha(p) != h:
        fail('visible test file modified: ' + rel)

env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
       'TZ': 'UTC', 'HOME': tempfile.gettempdir()}
tmp = tempfile.mkdtemp(prefix='ob176_')
try:
    shutil.copytree(pkg, os.path.join(tmp, 'shopd'),
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    os.mkdir(os.path.join(tmp, 'tests'))
    for rel in HASHES:
        shutil.copyfile(os.path.join(D, rel), os.path.join(tmp, rel))

    nonce = secrets.token_hex(16)
    try:
        p = subprocess.run([sys.executable, '-I', '-B', '-c', VISIBLE_BOOT, tmp, str(N_VISIBLE)],
                           input=nonce + '\n', capture_output=True, text=True, cwd=tmp, env=env,
                           timeout=VISIBLE_TIMEOUT)
    except subprocess.TimeoutExpired:
        fail('visible suite timed out (%d s)' % VISIBLE_TIMEOUT)
    r = last_result(p.stdout)
    if r is None or len(r) != 5:
        fail('visible suite did not complete (exit %d)' % p.returncode)
    if r[1] != hashlib.sha256((nonce + ':visible').encode()).hexdigest():
        fail('visible suite is not green (ran %s of %d, %s failures, %s errors)' % (r[2], N_VISIBLE, r[3], r[4]))

    for tid in ORDER:
        if time.time() - T0 > BUDGET_S:
            fail('validator time budget exceeded before hidden test %s' % tid)
        nonce = secrets.token_hex(16)
        try:
            p = subprocess.run([sys.executable, '-I', '-B', '-c', HIDDEN_BOOT, tmp],
                               input=nonce + '\n' + tid + '\n' + HIDDEN, capture_output=True, text=True,
                               cwd=tmp, env=env, timeout=HIDDEN_TIMEOUT)
        except subprocess.TimeoutExpired:
            fail('hidden test %s timed out (%d s)' % (tid, HIDDEN_TIMEOUT))
        r = last_result(p.stdout)
        if r is None or r[1] != hashlib.sha256((nonce + ':' + tid).encode()).hexdigest():
            fail('hidden test %s' % tid)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print('OK: visible suite green (%d tests), %d hidden tests passed' % (N_VISIBLE, len(ORDER)))
