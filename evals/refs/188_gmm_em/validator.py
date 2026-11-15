# Validator template for 188_gmm_em. build_task.py splices gen.py and the
# reference (gmm.py) in at the two markers and stores the result as the task's
# validation.script. Hidden cases are generated here, fed over stdin, and a
# failure names only the case index and the kind of mismatch.
import io, json, math, os, re, subprocess, sys, tokenize

SOL = "/tmp/eval_gmm/gmm.py"
if not os.path.isfile(SOL):
    print("FAIL missing /tmp/eval_gmm/gmm.py"); sys.exit(1)
src = open(SOL, encoding="utf-8", errors="replace").read()


def code_only(s):
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(s).readline):
            out.append(" " if tok.type in (tokenize.COMMENT, tokenize.STRING) else tok.string)
    except Exception:
        return s
    return " ".join(out)


if re.search(r"(?m)^\s*(?:import|from)\s+(?:numpy|scipy|sklearn|torch|jax|pandas|mpmath|sympy)\b",
             code_only(src).replace(" . ", ".")):
    print("FAIL forbidden import (standard library only)"); sys.exit(1)

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
real.write("@@RESULT@@" + json.dumps(out))
real.flush()
'''

try:
    p = subprocess.run([sys.executable, "-c", DRIVER], input=json.dumps(CASES), cwd="/tmp/eval_gmm",
                       capture_output=True, text=True, timeout=10)
except subprocess.TimeoutExpired:
    print("FAIL solution timed out (10 s for all hidden cases)"); sys.exit(1)
if "@@RESULT@@" not in p.stdout:
    print("FAIL solution crashed before returning results:", (p.stderr.strip().splitlines() or ["?"])[-1][:160])
    sys.exit(1)
try:
    GOT = json.loads(p.stdout.split("@@RESULT@@", 1)[1])
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
