#!/usr/bin/env python3
"""E31: seeded-basis vs learned-factor dominant-subspace capture (PREREG.md)."""
import json
import sys

import numpy as np

CACHE = "/home/max/Documents/openbeast/research/lowrank/data/cache27b"
LAYERS = [2, 5, 8, 20, 26, 32, 44, 52, 60]
SUFFIXES = ("ffn_down.weight", "attn_qkv.weight")
R = 128
SEEDS = (1, 2, 3)


def capture_of_basis(B, U, s2):
    """Energy fraction of the cached top-192 subspace captured by span(B)."""
    Q, _ = np.linalg.qr(B.astype(np.float64))
    proj = Q.T @ U.astype(np.float64)          # (k,192)
    per_dir = np.sum(proj ** 2, axis=0)        # ||P_B u_j||^2
    return float(np.sum(s2 * per_dir) / np.sum(s2))


def seeded_basis(m, k, seed):
    """AWSRC-class basis: seeded sign patterns with permutation structure."""
    rng = np.random.default_rng(seed)
    B = rng.choice([-1.0, 1.0], size=(m, k)).astype(np.float64)
    return B / np.sqrt(m)


def main():
    rows = []
    for L in LAYERS:
        for suf in SUFFIXES:
            try:
                d = np.load(f"{CACHE}/blk.{L}.{suf}.npz")
            except FileNotFoundError:
                continue
            U, s2 = d["U"], d["s2"]
            m = U.shape[0]
            n = d["Afull"].shape[1]
            # learned arm: top-R of the cached 192
            learned = float(np.sum(s2[:R]) / np.sum(s2))
            k1 = round(R * (m + n) / n)            # S1 byte parity, Q8
            k2 = round(2 * R * (m + n) / n)        # S2, 4-bit coeffs
            caps = {"S1": [], "S2": []}
            for s in SEEDS:
                caps["S1"].append(capture_of_basis(seeded_basis(m, k1, s), U, s2))
                caps["S2"].append(capture_of_basis(seeded_basis(m, k2, s), U, s2))
            row = {"tensor": f"blk.{L}.{suf}", "m": m, "n": n,
                   "learned_r128": learned,
                   "S1_k": k1, "S1": float(np.mean(caps["S1"])),
                   "S2_k": k2, "S2": float(np.mean(caps["S2"])),
                   "random_expect_S1": k1 / m, "random_expect_S2": k2 / m}
            rows.append(row)
            print(f"{row['tensor']:<26} learned={learned:.3f}  "
                  f"S1(k={k1})={row['S1']:.4f} (rand~{k1/m:.4f})  "
                  f"S2(k={k2})={row['S2']:.4f} (rand~{k2/m:.4f})", flush=True)
    agg = {k: float(np.mean([r[k] for r in rows]))
           for k in ("learned_r128", "S1", "S2", "random_expect_S1", "random_expect_S2")}
    excess1 = agg["S1"] / agg["random_expect_S1"]
    excess2 = agg["S2"] / agg["random_expect_S2"]
    print(f"\nMEANS: learned={agg['learned_r128']:.3f}  S1={agg['S1']:.4f}  S2={agg['S2']:.4f}")
    print(f"seeded-vs-random excess: S1 {excess1:.2f}x  S2 {excess2:.2f}x  "
          f"(P2 escape threshold: >2x)")
    with open(sys.argv[1] if len(sys.argv) > 1 else "e31.json", "w") as f:
        json.dump({"rows": rows, "means": agg,
                   "excess": {"S1": excess1, "S2": excess2}}, f, indent=1)


if __name__ == "__main__":
    main()
