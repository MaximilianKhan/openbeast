"""Regenerate the wrong_*.py / slow_*.py arms of 188_gmm_em from gmm.py.

    python3 evals/refs/188_gmm_em/make_wrongs.py evals/refs/188_gmm_em
"""
import os, sys
D = sys.argv[1]
ref = open(os.path.join(D, 'gmm.py')).read()
body = ref[ref.index('import math'):]


def mk(name, doc, pairs):
    s = body
    for a, b in pairs:
        assert s.count(a) == 1, (name, a)
        s = s.replace(a, b)
    open(os.path.join(D, name), 'w').write('"""WRONG (188_gmm_em): ' + doc + '"""\n' + s)


mk('wrong_naive_exp.py',
   'densities in the linear domain: exp() of each log-density, sum, log; a\n'
   '1e-300 floor keeps it from crashing. Every density underflows to 0.0 at\n'
   't=0 in cases 6-8, so the trace is wrong there. Intended: FAIL case 6.\n',
   [("""        mx = max(lp)
        lse = mx + math.log(sum(math.exp(v - mx) for v in lp))
        LL += lse
        R.append([math.exp(v - lse) for v in lp])""",
     """        p = [math.exp(v) for v in lp]
        tot = sum(p)
        LL += math.log(max(tot, 1e-300))
        R.append([v / tot for v in p] if tot > 0 else [1.0 / len(p)] * len(p))""")])

mk('wrong_det_product.py',
   'log det(Sigma) as the log of the determinant (product of squared Cholesky\n'
   'pivots). det ~1e-420 underflows to 0.0 at coordinate scale 1e-70 (case 10)\n'
   'and ~1e420 overflows at 1e70 (case 11), likewise 15-16. Intended: 10,11,15,16.\n',
   [("""        c = math.log(w[k]) - 0.5 * d * _LOG2PI - sum(math.log(L[j][j]) for j in range(d))""",
     """        det = 1.0
        for j in range(d):
            det *= L[j][j] * L[j][j]
        c = math.log(w[k]) - 0.5 * d * _LOG2PI - 0.5 * math.log(det)""")])

mk('wrong_onepass_cov.py',
   'one-pass covariance E_r[x x^T] - mu mu^T. Exact in real arithmetic, but at a\n'
   'common offset of 1e6 the subtraction cancels ~12 digits (cases 12-13).\n'
   'Everything else matches. Intended: first failure is case 12.\n',
   [("""            dx = [X[i][j] - m[j] for j in range(d)]""",
     """            dx = X[i]"""),
    ("""                S[a][b] /= Nk
""",
     """                S[a][b] = S[a][b] / Nk - m[a] * m[b]
""")])

mk('wrong_returns_next.py',
   'off-by-one on the returned parameters: does the M-step right after each\n'
   'E-step and only then tests for a stop, so it returns theta_{T+1}, whose\n'
   'log-likelihood is NOT the last trace entry. Trace and flag are right.\n'
   'Intended: parameters mismatch.\n',
   [("""        LL, R = _estep(X, w, mu, cov)
        trace.append(LL)
""",
     """        LL, R = _estep(X, w, mu, cov)
        trace.append(LL)
        w, mu, cov = _mstep(X, R, K)
"""),
    ("""        w, mu, cov = _mstep(X, R, K)
        t += 1""",
     """        t += 1""")])

mk('wrong_rel_tol.py',
   'the common relative stopping rule |L_t - L_{t-1}| <= tol*|L_t| instead of\n'
   'the specified L_t - L_{t-1} <= tol*n. Intended: trace length differs.\n',
   [("""trace[t] - trace[t - 1] <= tol * len(X)""",
     """abs(trace[t] - trace[t - 1]) <= tol * abs(trace[t])""")])

open(os.path.join(D, 'slow_quadratic.py'), 'w').write(
    '"""SLOW (188_gmm_em): correct arithmetic, but every E-step also re-sums a\n'
    'per-point pass over all of X (O(n^2) per iteration), as a naive "recompute\n'
    'the total each time" implementation does. Intended: FAIL timed out, well\n'
    'inside the harness 30 s."""\n' + body.replace(
        """        LL += lse
""", """        LL += lse
        _ = sum(abs(v) for xx in X for v in xx for __ in w)
""", 1))
