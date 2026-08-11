#!/usr/bin/env python3
"""E03: SVD census of quantization residuals R = W_ref - W_quant.

Answers: is quant error low-rank, and what does a rank-r F16 correction do
to the bytes ledger vs just using a bigger quant?
"""
import argparse
import csv
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "llama.cpp" / "gguf-py"))
from gguf import GGUFReader  # noqa: E402
from gguf.quants import dequantize  # noqa: E402


def load_2d(path, min_dim=64):
    out = {}
    for t in GGUFReader(path).tensors:
        shape = [int(d) for d in t.shape if int(d) > 1]
        if len(shape) != 2 or min(shape) < min_dim:
            continue
        w = dequantize(t.data, t.tensor_type).astype(np.float32)
        w = w.reshape(shape[::-1]) if w.ndim == 1 else w
        out[t.name] = (w, str(t.tensor_type).split(".")[-1])
    return out


def energy_rank(sv, frac):
    e = np.cumsum(sv.astype(np.float64) ** 2)
    e /= e[-1]
    return int(np.searchsorted(e, frac) + 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref_gguf")
    ap.add_argument("quant_gguf")
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--ranks", default="16,32,64,128")
    args = ap.parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    ranks = [int(r) for r in args.ranks.split(",")]

    ref = load_2d(args.ref_gguf)
    qnt = load_2d(args.quant_gguf)
    rows, spectra = [], {}
    t_all = time.time()
    for name, (w_ref, _) in ref.items():
        if name not in qnt:
            continue
        w_q, qtype = qnt[name]
        r_mat = w_ref - w_q
        sv = np.linalg.svd(r_mat, compute_uv=False)
        m, n = r_mat.shape
        # residual energy recovered by a rank-r correction
        e = np.cumsum(sv.astype(np.float64) ** 2)
        tot = e[-1]
        recovered = {r: float(e[min(r, len(sv)) - 1] / tot) for r in ranks}
        row = {
            "name": name, "qtype": qtype, "m": m, "n": n,
            "rel_rmse": round(float(np.sqrt((r_mat**2).mean())
                                    / np.sqrt((w_ref**2).mean())), 4),
            "r90": energy_rank(sv, 0.90),
            "r95": energy_rank(sv, 0.95),
            "stable_rank": round(float((sv**2).sum() / sv[0]**2), 1),
        }
        for r in ranks:
            row[f"recov@r{r}"] = round(recovered[r], 3)
        rows.append(row)
        spectra[name] = sv
        print(f"{name:44s} relRMSE={row['rel_rmse']:.3f} "
              f"r90={row['r90']}/{len(sv)} "
              + " ".join(f"rec@{r}={recovered[r]:.2f}" for r in ranks))

    with open(outdir / "residual_census.csv", "w", newline="") as f:
        wtr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wtr.writeheader()
        wtr.writerows(rows)
    np.savez_compressed(outdir / "residual_spectra.npz", **spectra)

    # bytes ledger: bpw of quant base + rank-r f16 factors, summed over the
    # censused tensors (2D weight matrices dominate model size)
    BPW = {"Q2_K": 2.5625, "Q4_K": 4.5, "Q6_K": 6.5625, "Q8_0": 8.5}
    total_params = sum(r["m"] * r["n"] for r in rows)
    print("\n=== bytes ledger (censused 2D tensors only) ===")
    print(f"{'config':24s} {'bits/weight':>11s} {'vs Q6_K':>8s}")
    q6 = BPW["Q6_K"] * total_params
    for r in [0] + ranks:
        extra = sum(16 * r * (row["m"] + row["n"]) for row in rows)
        bits = (BPW["Q2_K"] * total_params + extra) / total_params
        print(f"Q2_K + r={r:<4d} F16 factors {bits:11.2f} "
              f"{q6 / (bits * total_params):8.2f}x")
    for q in ("Q4_K", "Q6_K"):
        print(f"{q:24s} {BPW[q]:11.2f} {BPW['Q6_K']/BPW[q]:8.2f}x")
    mean_r90_frac = np.mean([r["r90"] / min(r["m"], r["n"]) for r in rows])
    print(f"\nmean r90/full = {mean_r90_frac:.1%}  "
          f"({time.time()-t_all:.0f}s, {len(rows)} tensors)")


if __name__ == "__main__":
    main()
