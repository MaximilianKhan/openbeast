#!/usr/bin/env python3
"""Recover per-chunk KLD / ln-PPL-ratio from llama-perplexity --kl-divergence
logs (cumulative per-chunk lines) and compute paired per-chunk differences.

Usage: paired_stats.py A.log B.log   -> stats of (A - B) per chunk
       paired_stats.py A.log         -> per-chunk values of A
"""
import re
import sys

import numpy as np

ROW = re.compile(r"^\s*(\d+)\s+([\d.]+)\s+\+/-\s+([\d.]+)\s+(-?[\d.]+)\s+"
                 r"\+/-\s+([\d.]+)\s+(-?[\d.]+)\s+\+/-\s+([\d.]+)")


def per_chunk(path):
    """-> (kld_chunk, lnratio_chunk) arrays recovered from cumulative means."""
    rows = []
    for line in open(path, errors="replace"):
        line = line.replace("±", "+/-")
        m = ROW.match(line)
        if m:
            rows.append([float(g) for g in m.groups()])
    rows = np.array(rows)
    assert len(rows) > 1, f"no per-chunk rows in {path}"
    n = rows[:, 0]                      # cumulative chunk index
    kld_cum = rows[:, 5]
    lnr_cum = rows[:, 3]
    # equal tokens per chunk -> per-chunk mean from cumulative means
    kld = np.diff(np.concatenate([[0.0], n * kld_cum])) / np.diff(
        np.concatenate([[0.0], n]))
    lnr = np.diff(np.concatenate([[0.0], n * lnr_cum])) / np.diff(
        np.concatenate([[0.0], n]))
    return kld, lnr


def summarize(tag, v):
    sem = v.std(ddof=1) / np.sqrt(len(v))
    print(f"{tag}: mean {v.mean():+.5f} +/- {sem:.5f} (sem, n={len(v)}) "
          f"[{'%.2f' % (abs(v.mean()) / sem)} sigma]" if sem > 0 else tag)


def main():
    if len(sys.argv) == 2:
        kld, lnr = per_chunk(sys.argv[1])
        print("per-chunk KLD:", np.array2string(kld, precision=4))
        summarize("KLD", kld)
        summarize("lnPPLratio", lnr)
        return
    ka, la = per_chunk(sys.argv[1])
    kb, lb = per_chunk(sys.argv[2])
    assert len(ka) == len(kb)
    print(f"paired A={sys.argv[1]}  B={sys.argv[2]}  n={len(ka)} chunks")
    summarize("dKLD (A-B)", ka - kb)
    summarize("dlnPPLratio (A-B)", la - lb)
    wins = int((ka < kb).sum())
    print(f"chunk wins (A better KLD): {wins}/{len(ka)}")


if __name__ == "__main__":
    main()
