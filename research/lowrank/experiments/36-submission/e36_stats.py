#!/usr/bin/env python3
"""E36 paired statistics, reusing E35's robust set and E24's canonical parser.

usage: e36_stats.py LOGDIR TAG A:B [A:B ...]
  reads LOGDIR/TAG-A.log and LOGDIR/TAG-B.log (llama-perplexity
  --kl-divergence output), prints one KLD row per pair in E35's table format
  plus a NLL/top-1 line, and a reference-identity check (the per-chunk
  reference NLL recovered from both logs must agree).
"""
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "35-final-reanalysis"))
import reanalysis as R  # noqa: E402


def load(path):
    s = R.parse_kld_rows(path)
    return {k: np.asarray(s[k]) for k in ("kld", "nll", "top")}


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    logdir, tag, pairs = os.path.abspath(sys.argv[1]), sys.argv[2], sys.argv[3:]
    print(R.HEAD)
    extra = []
    for p in pairs:
        a, b = p.split(":")
        fa = os.path.join(logdir, f"{tag}-{a}.log")
        fb = os.path.join(logdir, f"{tag}-{b}.log")
        A, B = load(fa), load(fb)
        ra, rb = R.base_nll_per_chunk(fa), R.base_nll_per_chunk(fb)
        n = min(len(ra), len(rb))
        refdiff = float(np.abs(ra[:n] - rb[:n]).max())
        s = R.describe(A["kld"], B["kld"])
        print(R.row(f"{tag}: {a} vs {b}", s))
        sn = R.describe(A["nll"], B["nll"])
        st = R.describe(A["top"] * 100, B["top"] * 100, higher_better=True)
        extra.append(
            f"- {tag}: {a} vs {b}: ΔNLL {sn['mean']:+.5f} ± {sn['sem']:.5f} "
            f"(t {sn['t']:+.2f}, A better {sn['wins']}/{sn['n']}); "
            f"Δtop-1 {st['mean']:+.3f} pt (t {st['t']:+.2f}); "
            f"max |Δ ref NLL| {refdiff:.5f}")
    print()
    print("\n".join(extra))


if __name__ == "__main__":
    main()
