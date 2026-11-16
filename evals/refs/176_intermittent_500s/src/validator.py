# Validator for 176_intermittent_500s (embedded into the task JSON by
# build_task.py, which substitutes the @@...@@ placeholders).
#
# Only /tmp/eval_shopd500/shopd/ is graded. It is copied, with the pristine
# visible tests, into a private temp dir outside the fixture and imported
# only by isolated child interpreters (-I -B: no PYTHON* env, no user site,
# no cwd on sys.path; own session and process group; on macOS sandboxed
# with no fork, no read of the eval data and no write outside that temp
# dir), so nothing else left in the fixture dir is loaded.
#   1. tests/*: every visible test file byte-identical (sha256);
#   2. the visible suite is green (exact test count). This is a regression
#      gate: the suite imports shopd in the same process, so its report is
#      only as honest as the code under test, and it decides no pass alone;
#   3. hidden tests, the deciding gate. They run HERE, in the validator
#      process, which never imports shopd. Each test drives a fresh service
#      process over a request/response pipe: the child turns requests into
#      responses and reports what it saw, and every expectation and check
#      stays on this side. The child holds no secret and no expected value,
#      so code under test can only influence the responses it returns.
# The graded code runs with our uid, so it can write anywhere we can. This
# process therefore imports everything it will ever need BEFORE it starts
# the first child, drops every writable or relative entry from sys.path,
# and then freezes its import system: any later import of a module not yet
# loaded is refused, so a module planted in a cwd (or anywhere) cannot run
# here. The child boot scripts are files (no `-c SRC` argv).
# Every limit is written at scale 1 and scaled by vlib.t; every child wait
# is capped by the overall budget, so the validator ends within it. Output
# is ONE line; a hidden failure names only the test id.
import base64, datetime, decimal, hashlib, json, os, random, re, select, shutil, signal, subprocess, sys, tempfile, time
import urllib.parse
from decimal import ROUND_HALF_UP, Decimal
import vlib

_cwd = os.getcwd()
sys.path[:] = [p for p in sys.path
               if p and os.path.isabs(p) and os.path.abspath(p) != _cwd and not os.access(p, os.W_OK)]


class _NoNewImports:
    """Refuse every import of a module not loaded yet (installed below,
    before the first child starts)."""

    @staticmethod
    def find_spec(name, path=None, target=None):
        raise ImportError('validator import of %r after children started' % name)


D = '/tmp/eval_shopd500'
HASHES = @@HASHES@@
N_VISIBLE = @@N_VISIBLE@@
HIDDEN = @@HIDDEN@@
ORDER = @@ORDER@@
VISIBLE_TIMEOUT = vlib.t(6)
HIDDEN_TIMEOUT = vlib.t(5)
BUDGET_S = vlib.t(14.0)
T0 = time.time()

# Both boots write their protocol on a private dup of fd 1 and point fd 1 and
# sys.stdout at stderr, so ordinary prints from the service cannot corrupt it.
VISIBLE_BOOT = r'''
import io, os, sys
out = os.fdopen(os.dup(1), "w")
os.dup2(2, 1)
sys.stdout = sys.stderr
root = sys.argv[1]
sys.path[:0] = [root + "/tests", root]
import unittest
suite = unittest.defaultTestLoader.discover(root + "/tests", top_level_dir=root + "/tests")
res = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
out.write("\nRESULT %d %d %d\n" % (res.testsRun, len(res.failures), len(res.errors)))
out.flush()
'''

CHILD_BOOT = r'''
import base64, json, os, sys, threading
out = os.fdopen(os.dup(1), "w", encoding="utf-8")
os.dup2(2, 1)
sys.stdout = sys.stderr
sys.path.insert(0, sys.argv[1])
from shopd.app import create_app
from shopd.clock import FakeClock
from shopd.config import Config
from shopd.http import Request
st = {"app": None, "clock": None, "log": []}

def handle(q):
    r = st["app"].handle(Request.build(q["m"], q["t"], headers=q["h"], json_body=q["b"], client=q["c"]))
    return [r.status, [[k, v] for k, v in r.headers.items()], base64.b64encode(r.body or b"").decode("ascii")]

def threads(plans):
    res = [None] * len(plans)
    def run(i, reqs):
        res[i] = [handle(x) for x in reqs]
    ths = [threading.Thread(target=run, args=(i, reqs), name=name) for i, (name, reqs) in enumerate(plans)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    return res

for line in sys.stdin:
    q = json.loads(line)
    op, rep = q["op"], None
    if op == "new_clock":
        st["clock"] = FakeClock(q["start"])
    elif op == "new_app":
        st["app"] = create_app(config=Config(**q["cfg"]), clock=st["clock"], log_sink=st["log"])
    elif op == "adv":
        st["clock"].advance(q["ms"])
    elif op == "req":
        rep = handle(q)
    elif op == "log":
        rep = [str(x) for x in st["log"]]
    elif op == "threads":
        rep = threads(q["plans"])
    out.write(json.dumps(rep) + "\n")
    out.flush()
'''


def fail(msg):
    print('FAIL: ' + msg)
    sys.exit(1)


def sha(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def left():
    return BUDGET_S - (time.time() - T0)


class Deadline(Exception):
    pass


class Gone(Exception):
    pass


class Child:
    """One service process; call() sends a message and returns its reply."""

    def __init__(self, root, env, deadline):
        self.deadline = deadline
        self.p = spawn(CHILD_BOOT_PATH, root, env, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.fd = self.p.stdout.fileno()
        self.buf = b''

    def call(self, msg):
        try:
            self.p.stdin.write((json.dumps(msg) + '\n').encode())
            self.p.stdin.flush()
        except (BrokenPipeError, OSError):
            raise Gone()
        while b'\n' not in self.buf:
            wait = self.deadline - time.time()
            if wait <= 0 or not select.select([self.fd], [], [], wait)[0]:
                raise Deadline()
            chunk = os.read(self.fd, 1 << 16)
            if not chunk:
                raise Gone()
            self.buf += chunk
        line, self.buf = self.buf.split(b'\n', 1)
        try:
            return json.loads(line)
        except ValueError:
            raise Gone()

    def close(self):
        reap(self.p)


# Children: own session (so a kill reaches everything they started), group
# recorded with the harness (which SIGKILLs it if we die first), and on macOS
# sandboxed with no fork, no read of the sealed eval paths, and no write
# outside their private root (tmp) and /dev: the code under test can find
# every ancestor's cwd through libproc, and a module planted there would
# hijack the harness's next setup or cleanup. SANDBOX is set once tmp exists.
SANDBOX = None


def spawn(boot, root, env, stdin, stdout):
    p = subprocess.Popen(SANDBOX + [sys.executable, '-I', '-S', '-B', boot, root], stdin=stdin, stdout=stdout,
                         stderr=subprocess.DEVNULL, cwd=root, env=env, start_new_session=True, close_fds=True)
    vlib._register_group(p.pid)
    return p


def reap(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass
    p.wait()
    vlib._unregister_group(p.pid)


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
ctl = tempfile.mkdtemp(prefix='ob176c_')
SANDBOX = vlib._sandbox_prefix(False, vlib._sealed_paths(), allow_write=[tmp])
VISIBLE_BOOT_PATH = os.path.join(ctl, 'visible_boot.py')
CHILD_BOOT_PATH = os.path.join(ctl, 'child_boot.py')
for _path, _src in ((VISIBLE_BOOT_PATH, VISIBLE_BOOT), (CHILD_BOOT_PATH, CHILD_BOOT)):
    with open(_path, 'w') as f:
        f.write(_src)
HIDDEN_CODE = compile(HIDDEN, '<hidden>', 'exec')
exec(HIDDEN_CODE, {'__name__': 'hidden'})   # its imports load now, before any child
sys.meta_path.insert(0, _NoNewImports)
try:
    shutil.copytree(pkg, os.path.join(tmp, 'shopd'),
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    os.mkdir(os.path.join(tmp, 'tests'))
    for rel in HASHES:
        shutil.copyfile(os.path.join(D, rel), os.path.join(tmp, rel))

    p = spawn(VISIBLE_BOOT_PATH, tmp, env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
    vend = time.time() + max(min(VISIBLE_TIMEOUT, left()), 0.1)
    vout, timed_out = b'', False
    while True:
        wait = vend - time.time()
        if wait <= 0 or not select.select([p.stdout], [], [], wait)[0]:
            timed_out = True
            break
        chunk = os.read(p.stdout.fileno(), 1 << 16)
        if not chunk:
            break
        vout += chunk
    if not timed_out:
        try:
            p.wait(timeout=max(vend - time.time(), 0.1))
        except subprocess.TimeoutExpired:
            timed_out = True
    reap(p)
    if timed_out:
        fail('visible suite timed out (%d s)' % round(VISIBLE_TIMEOUT / vlib.scale()))
    r = [ln.split() for ln in vout.decode('utf-8', 'replace').splitlines() if ln.startswith('RESULT ')]
    if not r or len(r[-1]) != 4:
        fail('visible suite did not complete (exit %d)' % p.returncode)
    ran, nf, ne = r[-1][1:]
    if (ran, nf, ne) != (str(N_VISIBLE), '0', '0'):
        fail('visible suite is not green (ran %s of %d, %s failures, %s errors)' % (ran, N_VISIBLE, nf, ne))

    for tid in ORDER:
        if left() <= 0:
            fail('validator time budget exceeded before hidden test %s' % tid)
        per_test = time.time() + HIDDEN_TIMEOUT
        budget_end = T0 + BUDGET_S
        child = Child(tmp, env, min(per_test, budget_end))
        g = {'__name__': 'hidden'}
        try:
            exec(HIDDEN_CODE, g)
            g['CHILD'] = child
            g[tid]()
        except Deadline:
            if per_test <= budget_end:
                fail('hidden test %s timed out (%d s)' % (tid, round(HIDDEN_TIMEOUT / vlib.scale())))
            fail('validator time budget exceeded during hidden test %s' % tid)
        except Exception:  # noqa: BLE001 -- a failed check, a dead child or a malformed reply
            fail('hidden test %s' % tid)
        finally:
            child.close()
finally:
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(ctl, ignore_errors=True)
print('OK: visible suite green (%d tests), %d hidden tests passed' % (N_VISIBLE, len(ORDER)))
