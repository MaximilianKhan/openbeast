#!/usr/bin/env python3
"""Build evals/tasks/188_gmm_em.json from the sources in this directory.

    python3 evals/refs/188_gmm_em/build_task.py           # write the task JSON
    python3 evals/refs/188_gmm_em/build_task.py --check   # robustness report

The validator is validator.py with gen.py (hidden cases) and gmm.py (the
oracle) spliced in. Setup writes only a small labelled sample (one benign
case plus the reference output for it) into /tmp/eval_gmm.

--check re-derives the property that makes a 1e-9 trace tolerance fair: an
independent correct solutions (gmm_alt.py, gmm_alt2.py) and the reference run on reversed
point order agree with the reference on every hidden case, and every stopping
decision (dL <= tol*n) clears the observed float noise by a wide factor.
"""
import copy, json, math, os, random, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
OUT = os.path.join(REPO, "evals", "tasks", "188_gmm_em.json")


def rd(name):
    with open(os.path.join(HERE, name)) as f:
        return f.read()


def load(name):
    ns = {}
    exec(compile(rd(name), name, "exec"), ns)
    return ns


def validator_script():
    v = rd("validator.py")
    assert v.count("# @@GEN@@") == 1 and v.count("# @@ORACLE@@") == 1
    return v.replace("# @@GEN@@", rd("gen.py")).replace("# @@ORACLE@@", rd("gmm.py"))


def sample():
    g = load("gen.py")
    c = g["make"](random.Random(7), 2, 2, 40, sep=2.2, mi=50, tol=1e-9)
    c["X"] = [[round(v, 6) for v in x] for x in c["X"]]
    c["means"] = [[round(v, 6) for v in m] for m in c["means"]]
    c["weights"] = [0.4, 0.6]
    r = load("gmm.py")["fit_gmm"](**copy.deepcopy(c))
    return c, r


PROMPT = (
    "Create /tmp/eval_gmm/gmm.py defining fit_gmm(X, weights, means, covs, max_iter, tol): "
    "expectation-maximisation for a K-component Gaussian mixture with full covariance "
    "matrices, started from the given parameters. Python standard library only "
    "(no numpy, scipy, sklearn, torch, mpmath, sympy, pandas, jax).\n\n"
    "INPUTS (plain Python lists/floats): X is a list of n points, each a list of d floats "
    "(1 <= d <= 4, n <= 600); weights is a list of K floats > 0 summing to 1 (1 <= K <= 4); "
    "means is a list of K lists of d floats; covs is a list of K symmetric positive-definite "
    "d x d matrices (lists of lists); max_iter is an int >= 0; tol is a float > 0.\n\n"
    "DEFINITIONS. For parameters theta = (w, mu, Sigma): "
    "log N(x | mu, Sigma) = -1/2 * ( d*ln(2*pi) + ln det(Sigma) + (x-mu)^T Sigma^-1 (x-mu) ), and "
    "L(theta) = sum_i ln( sum_k w_k N(x_i | mu_k, Sigma_k) ) (natural logarithms).\n"
    "ALGORITHM. theta_0 = the given parameters. For t = 0, 1, 2, ...: compute L_t = L(theta_t) "
    "and append it to the trace. Then, if t >= 1 and L_t - L_{t-1} <= tol * n, stop with "
    "converged = True. Otherwise, if t == max_iter, stop with converged = False. Otherwise "
    "theta_{t+1} is one EM update of theta_t: r_ik = w_k N(x_i|mu_k,Sigma_k) / "
    "sum_j w_j N(x_i|mu_j,Sigma_j); N_k = sum_i r_ik; new w_k = N_k / n; new mu_k = "
    "sum_i r_ik x_i / N_k; new Sigma_k = sum_i r_ik (x_i - new mu_k)(x_i - new mu_k)^T / N_k. "
    "No regularisation, no variance floor, no other stopping rule.\n\n"
    "RETURN a dict: \"log_likelihood\": [L_0, ..., L_T] as floats; \"weights\", \"means\", "
    "\"covs\": theta_T, the parameters whose log-likelihood is the LAST trace entry (for "
    "max_iter = 0 that is the initial parameters), same nesting as the inputs; "
    "\"converged\": a bool.\n\n"
    "GRADING: hidden cases. Every trace entry must be within 1e-9 * max(1, |L|) of the "
    "reference, the trace length must match exactly, the trace must be non-decreasing, and "
    "weights/means/covs must match to about 1e-6 relative to the data's spread. Hidden "
    "inputs include: coordinates of magnitude anywhere from about 1e-150 to 1e150; a common "
    "offset of magnitude up to 3e6 relative to clusters of unit spread; initial covariances far "
    "narrower than the distance from the data to the initial means (L_0 of order -1e8); and "
    "outliers. Every N_k stays well above zero and every Sigma_k stays well-conditioned "
    "(condition number below 1e4) in every hidden case, so no degeneracy handling is needed. "
    "All hidden cases together must finish within 10 seconds (a direct pure-Python "
    "implementation needs about 1 s).\n\n"
    "A worked sample: /tmp/eval_gmm/sample_input.json holds the keyword arguments of one "
    "small case; /tmp/eval_gmm/sample_output.json holds the returned dict for it."
)


def build():
    c, r = sample()
    sin = json.dumps(c, separators=(",", ":"))
    sout = json.dumps(r, separators=(",", ":"))
    for s in (sin, sout):
        assert "'" not in s and "EOF" not in s
    setup = ("mkdir -p /tmp/eval_gmm && cat > /tmp/eval_gmm/sample_input.json <<'GMMEOF'\n" + sin +
             "\nGMMEOF\ncat > /tmp/eval_gmm/sample_output.json <<'GMMEOF'\n" + sout + "\nGMMEOF")
    task = {
        "id": "188_gmm_em",
        "name": "Gaussian-mixture EM with log-sum-exp",
        "difficulty": "hard",
        "category": "Probability & Stats",
        "subcategory": "Mixture models",
        "role": "scored",
        "est_s": 165,
        "max_wall_s": 900,
        "max_iter": 25,
        "ref_stem": "188_gmm_em/gmm",
        "dest": "/tmp/eval_gmm/gmm.py",
        "setup": setup,
        "task": PROMPT,
        "validation": {"type": "python", "script": validator_script()},
        "cleanup": "rm -rf /tmp/eval_gmm",
    }
    with open(OUT, "w") as f:
        json.dump(task, f, indent=2)
        f.write("\n")
    print("wrote", OUT)


def check():
    import time
    cases = load("gen.py")["cases"]()
    ref = load("gmm.py")["fit_gmm"]
    alts = [load(f)["fit_gmm"] for f in ("gmm_alt.py", "gmm_alt2.py")]
    worst_dev, worst_ratio = 0.0, math.inf
    for i, c in enumerate(cases):
        t0 = time.time(); r = ref(**copy.deepcopy(c)); dt = time.time() - t0
        rev = copy.deepcopy(c); rev["X"] = rev["X"][::-1]
        others = [alt(**copy.deepcopy(c)) for alt in alts] + [ref(**rev)]
        T = r["log_likelihood"]
        dev, ratio = 0.0, math.inf
        for o in others:
            To = o["log_likelihood"]
            assert len(To) == len(T), f"case {i}: iteration count is implementation-dependent"
            dev = max(dev, max(abs(x - y) / max(1, abs(y)) for x, y in zip(To, T)))
        thr = c["tol"] * len(c["X"])
        for t in range(1, len(T)):
            dl = T[t] - T[t - 1]
            nz = 4e-16 * max(abs(T[t]), abs(T[t - 1]))
            for o in others:
                To = o["log_likelihood"]
                nz = max(nz, abs((To[t] - To[t - 1]) - dl))
            ratio = min(ratio, abs(dl - thr) / nz)
            assert dl >= -1e-9 * max(1, abs(T[t - 1])), f"case {i}: not monotone"
        nk = min(r["weights"]) * len(c["X"])
        print(f"case {i:2d}: n={len(c['X'])} d={len(c['X'][0])} K={len(c['weights'])} "
              f"iters={len(T) - 1:3d} conv={r['converged']!s:5s} L0={T[0]:+.3e} "
              f"dev={dev:.1e} stop-margin/noise={ratio:.1e} minNk={nk:.0f} {dt:.2f}s")
        worst_dev, worst_ratio = max(worst_dev, dev), min(worst_ratio, ratio)
    print(f"worst relative trace deviation {worst_dev:.1e} (tolerance 1e-9); "
          f"worst stop margin {worst_ratio:.0f}x noise")
    assert worst_dev < 1e-11 and worst_ratio > 20


if __name__ == "__main__":
    check() if "--check" in sys.argv else build()
