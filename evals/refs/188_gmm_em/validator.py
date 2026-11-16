# Validator template for 188_gmm_em. build_task.py splices gen.py and the
# reference (gmm.py) in at the two markers and stores the result as the task's
# validation.script. Hidden cases are generated here, fed over stdin, and a
# failure names only the case index and the kind of mismatch.
import json, math, os, shutil, sys, tempfile
import vlib

SOL = "/tmp/eval_gmm/gmm.py"
if not os.path.isfile(SOL):
    print("FAIL missing /tmp/eval_gmm/gmm.py"); sys.exit(1)
src = open(SOL, encoding="utf-8", errors="replace").read()


BANNED = ("numpy", "scipy", "sklearn", "torch", "jax", "pandas", "mpmath", "sympy")

# AST lint: every import form of a banned package, plus the dynamic escape
# hatches (__import__, importlib, exec/eval, computed getattr, dunder walks).
# Second line of defence only: the solution runs in a separate process that
# holds no answers, under -I -S (no site-packages), with an import blocker.
# The import machinery is banned by name too, so the driver's blocker can't be
# unhooked (sys.meta_path, `from sys import meta_path`, operator.attrgetter).
IMPORT_HOOKS = ("meta_path", "path_hooks", "path_importer_cache", "attrgetter", "methodcaller")
_lint = vlib.lint_python(src, ban_modules=BANNED, ban_calls=IMPORT_HOOKS)
try:
    import ast
    for _n in ast.walk(ast.parse(src)):
        if isinstance(_n, ast.ImportFrom) and any(a.name in IMPORT_HOOKS for a in _n.names):
            _lint.append(f"line {_n.lineno}: import of an import hook")
except SyntaxError:
    pass
if _lint:
    print("FAIL forbidden import or construct (standard library only):", _lint[0][:160]); sys.exit(1)

# ---- hidden generator -------------------------------------------------------
# @@GEN@@
# ---- oracle -----------------------------------------------------------------
# @@ORACLE@@
# -----------------------------------------------------------------------------

CASES = cases()
REF = [fit_gmm(**json.loads(json.dumps(c))) for c in CASES]

DRIVER = r'''
import importlib.util, json, sys
real = sys.stdout
sys.stdout = sys.stderr
BANNED = %r
class _Block:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BANNED:
            raise ImportError("standard library only")
        return None
_BLOCK = _Block()
sys.meta_path.insert(0, _BLOCK)
for _m in [m for m in sys.modules if m.split(".")[0] in BANNED]:
    del sys.modules[_m]
cases = json.loads(sys.stdin.read())
spec = importlib.util.spec_from_file_location("gmm_sol", "/tmp/eval_gmm/gmm.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
out = []
for c in cases:
    try:
        r = mod.fit_gmm(c["X"], c["weights"], c["means"], c["covs"], c["max_iter"], c["tol"])
        out.append({"ok": True,
                    "log_likelihood": [float(v) for v in r["log_likelihood"]],
                    "weights": [float(v) for v in r["weights"]],
                    "means": [[float(v) for v in m] for m in r["means"]],
                    "covs": [[[float(v) for v in row] for row in S] for S in r["covs"]],
                    "converged": r["converged"]})
    except Exception as e:
        out.append({"ok": False, "err": type(e).__name__})
if sys.meta_path[:1] != [_BLOCK] or [m for m in sys.modules if m.split(".")[0] in BANNED]:
    out = [{"ok": False, "err": "ImportError"} for _ in cases]
real.write("@@RESULT@@" + json.dumps(out))
real.flush()
''' % (BANNED,)

# The driver holds no answers (only the hidden INPUTS, which arrive on its
# stdin); it is written to a private dir and run as `python -I -S <file>`, so
# no source sits in an argv. CPU budget 10 s at scale 1 for the whole tree.
_dd = tempfile.mkdtemp(prefix="gmm_drv_", dir=os.getcwd())
_drv = os.path.join(_dd, "driver.py")
try:
    with open(_drv, "w") as f:
        f.write(DRIVER)
    p = vlib.run_solution(vlib.python_cmd(_drv), input=json.dumps(CASES), cpu_s=10, wall_s=15,
                          cwd="/tmp/eval_gmm")
finally:
    shutil.rmtree(_dd, ignore_errors=True)
if p.verdict in ("cpu", "wall"):
    print("FAIL solution timed out (10 s CPU for all hidden cases):", p.reason); sys.exit(1)
if p.verdict != "ok":
    print("FAIL solution refused:", p.reason); sys.exit(1)
if "@@RESULT@@" not in p.stdout:
    print("FAIL solution crashed before returning results:", (p.stderr.strip().splitlines() or ["?"])[-1][:160])
    sys.exit(1)
try:
    GOT = json.loads(p.stdout.rsplit("@@RESULT@@", 1)[1])
except Exception:
    print("FAIL could not decode solution results"); sys.exit(1)


def close(a, b, tol):
    return isinstance(a, float) and math.isfinite(a) and abs(a - b) <= tol


def check(i, c, r, g):
    if not g.get("ok"):
        return "raised " + g.get("err", "?")
    T, G = r["log_likelihood"], g["log_likelihood"]
    if not all(math.isfinite(v) for v in G):
        return "non-finite log-likelihood"
    if any(G[t] < G[t - 1] - 1e-9 * max(1.0, abs(G[t - 1])) for t in range(1, len(G))):
        return "log-likelihood trace not monotone"
    if len(G) != len(T):
        return "trace length differs"
    if not all(close(x, y, 1e-9 * max(1.0, abs(y))) for x, y in zip(G, T)):
        return "log-likelihood trace mismatch"
    if g["converged"] is not r["converged"]:
        return "converged flag wrong"
    K, d = len(r["weights"]), len(c["X"][0])
    if not (len(g["weights"]) == K and len(g["means"]) == K and len(g["covs"]) == K):
        return "parameters mismatch"
    try:
        ok = True
        for k in range(K):
            S = r["covs"][k]
            sd = [math.sqrt(S[a][a]) for a in range(d)]
            ok &= close(g["weights"][k], r["weights"][k], 1e-7)
            ok &= len(g["means"][k]) == d and all(
                close(g["means"][k][a], r["means"][k][a], 1e-12 * abs(r["means"][k][a]) + 1e-6 * sd[a])
                for a in range(d))
            ok &= len(g["covs"][k]) == d and all(
                len(g["covs"][k][a]) == d and all(
                    close(g["covs"][k][a][b], S[a][b], 1e-6 * sd[a] * sd[b]) for b in range(d))
                for a in range(d))
    except (TypeError, IndexError, KeyError):
        ok = False
    return None if ok else "parameters mismatch"


if not isinstance(GOT, list) or len(GOT) != len(CASES):
    print("FAIL wrong number of results"); sys.exit(1)
bad = []
for i, (c, r, g) in enumerate(zip(CASES, REF, GOT)):
    why = check(i, c, r, g)
    if why:
        bad.append(f"case {i}: {why}")
if bad:
    print(f"FAIL {len(bad)}/{len(CASES)} hidden cases; first: {bad[0]}")
    print("failed: " + "; ".join(bad))
    sys.exit(1)
print(f"OK {len(CASES)}/{len(CASES)} hidden cases")
