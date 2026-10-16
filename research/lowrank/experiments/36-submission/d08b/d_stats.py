#!/usr/bin/env python3
"""E36-D readouts that e36_stats.py does not print. Reads llama-perplexity
--kl-divergence logs only (per-chunk series recovered with E24's canonical
parser, unchanged). Logs are LOGDIR/TAG-LABEL.log, as lib.sh's score names
them.

  gate LOGDIR TAG A B THR      paired KLD t of (A - B); exit 0 iff t <= THR
                               (D0: RR-wiki - Q2_K on wiki, THR = -2)
  spread LOGDIR TAG L1 L2 ...  T1.15: per-artifact mean KLD and PPL over
                               the scored chunks, then SD (ddof=1) and range
                               across the artifacts
  recovery LOGDIR TAG BASE FC  T1.9: (KLD_base - KLD_fc) / KLD_base with a
                               paired bootstrap 95% CI over chunks
                               (10,000 resamples, seed 20261009 = E35's)
"""
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
EXP = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(EXP, "24-yaqa-lite"))
from paired_stats import parse_kld_rows  # noqa: E402

SEED, NBOOT = 20261009, 10_000


def series(logdir, tag, label):
    s = parse_kld_rows(os.path.join(logdir, f"{tag}-{label}.log"))
    return {k: np.asarray(s[k]) for k in ("kld", "nll", "top")}


def gate(logdir, tag, a, b, thr):
    A, B = series(logdir, tag, a), series(logdir, tag, b)
    n = min(len(A["kld"]), len(B["kld"]))
    d = A["kld"][:n] - B["kld"][:n]
    t = d.mean() / (d.std(ddof=1) / math.sqrt(n))
    ok = t <= thr
    print(f"gate {tag}: {a} - {b}: dKLD {d.mean():+.6f}, t {t:+.2f}, "
          f"n {n}, A better {int((d < 0).sum())}/{n} -> "
          f"{'PASS' if ok else 'FAIL'} (rule t <= {thr})")
    return 0 if ok else 1


def spread(logdir, tag, labels):
    kl, pp = [], []
    for lab in labels:
        S = series(logdir, tag, lab)
        kl.append(S["kld"].mean())
        pp.append(math.exp(S["nll"].mean()))
        print(f"  {tag} {lab:18s} KLD {kl[-1]:.6f}  PPL {pp[-1]:.4f}  "
              f"(n {len(S['kld'])})")
    kl, pp = np.array(kl), np.array(pp)
    print(f"spread {tag} over {len(labels)}: KLD SD {kl.std(ddof=1):.6f} "
          f"range {kl.max() - kl.min():.6f} | PPL SD {pp.std(ddof=1):.4f} "
          f"range {pp.max() - pp.min():.4f}")
    return 0


def recovery(logdir, tag, base, fc):
    B, F = series(logdir, tag, base)["kld"], series(logdir, tag, fc)["kld"]
    n = min(len(B), len(F))
    B, F = B[:n], F[:n]
    r = (B.mean() - F.mean()) / B.mean()
    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, n, size=(NBOOT, n))
    bb, ff = B[idx].mean(axis=1), F[idx].mean(axis=1)
    rb = (bb - ff) / bb
    lo, hi = np.quantile(rb, [0.025, 0.975])
    print(f"recovery {tag}: {fc} over {base}: KLD {B.mean():.6f} -> "
          f"{F.mean():.6f}, fraction {r:.4f} [95% paired bootstrap "
          f"{lo:.4f}, {hi:.4f}], n {n}")
    return 0


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, a = sys.argv[1], sys.argv[2:]
    if cmd == "gate":
        sys.exit(gate(a[0], a[1], a[2], a[3], float(a[4])))
    if cmd == "spread":
        sys.exit(spread(a[0], a[1], a[2:]))
    if cmd == "recovery":
        sys.exit(recovery(a[0], a[1], a[2], a[3]))
    sys.exit(__doc__)


if __name__ == "__main__":
    main()
