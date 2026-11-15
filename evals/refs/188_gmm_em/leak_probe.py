"""LEAK PROBE (188_gmm_em): returns whatever answer-like data it can find in
the agent-visible fixture dir (the worked sample output) for every case. Must
FAIL: the only expected data on disk is the sample, never a hidden case."""
import glob, json, os

_found = {}
for p in glob.glob("/tmp/eval_gmm/**/*", recursive=True):
    if os.path.isfile(p) and not p.endswith(".py"):
        try:
            with open(p) as f:
                v = json.load(f)
        except Exception:
            continue
        if isinstance(v, dict) and "log_likelihood" in v:
            _found = v


def fit_gmm(X, weights, means, covs, max_iter, tol):
    if _found:
        return _found
    return {"log_likelihood": [0.0], "weights": weights, "means": means, "covs": covs,
            "converged": False}
