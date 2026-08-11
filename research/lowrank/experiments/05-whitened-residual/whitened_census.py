#!/usr/bin/env python3
"""E05: SVD census of ACTIVATION-WHITENED quantization residuals R·D.

D = diag(sqrt(E[x_j^2])) from a llama-imatrix GGUF. Compares weighted-energy
concentration against the unweighted E03 numbers, per tensor and per kind.
"""
import argparse
import csv
import re
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "llama.cpp" / "gguf-py"))
from gguf import GGUFReader  # noqa: E402
from gguf.quants import dequantize  # noqa: E402

KIND_PATTERNS = [
    (r"attn_q\.", "attn_q"), (r"attn_k\.", "attn_k"),
    (r"attn_v\.", "attn_v"), (r"attn_output\.", "attn_o"),
    (r"ffn_gate\.", "ffn_gate"), (r"ffn_up\.", "ffn_up"),
    (r"ffn_down\.", "ffn_down"), (r"token_embd\.", "embed"),
]


def kind_of(name):
    for pat, kind in KIND_PATTERNS:
        if re.search(pat, name):
            return kind
    return "other"


def load_2d(path, min_dim=64):
    out = {}
    for t in GGUFReader(path).tensors:
        shape = [int(d) for d in t.shape if int(d) > 1]
        if len(shape) != 2 or min(shape) < min_dim:
            continue
        w = dequantize(t.data, t.tensor_type).astype(np.float32)
        w = w.reshape(shape[::-1]) if w.ndim == 1 else w   # (n_out, n_in)
        out[t.name] = w
    return out


def load_imatrix(path):
    """imatrix v2 GGUF: '<tensor>.in_sum2' (per-input-channel sum of x^2)
    and '<tensor>.counts'. Returns {tensor_name: mean_x2 vector}."""
    sums, counts = {}, {}
    for t in GGUFReader(path).tensors:
        data = np.asarray(t.data, dtype=np.float64).ravel()
        if t.name.endswith(".in_sum2"):
            sums[t.name[: -len(".in_sum2")]] = data
        elif t.name.endswith(".counts"):
            counts[t.name[: -len(".counts")]] = float(data.mean())
    return {k: v / counts.get(k, 1.0) for k, v in sums.items()}


def recovered(sv, ranks):
    e = np.cumsum(sv.astype(np.float64) ** 2)
    return {r: float(e[min(r, len(sv)) - 1] / e[-1]) for r in ranks}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref_gguf")
    ap.add_argument("quant_gguf")
    ap.add_argument("imatrix_gguf")
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--ranks", default="16,32,64,128")
    args = ap.parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    ranks = [int(r) for r in args.ranks.split(",")]

    ref = load_2d(args.ref_gguf)
    qnt = load_2d(args.quant_gguf)
    imat = load_imatrix(args.imatrix_gguf)
    rows = []
    t_all = time.time()
    for name, w_ref in ref.items():
        if name not in qnt:
            continue
        r_mat = w_ref - qnt[name]
        n_in = r_mat.shape[1]
        m2 = imat.get(name)
        whitened = m2 is not None and len(m2) == n_in
        if whitened:
            # damp exact-zero channels (never activated in calibration)
            d = np.sqrt(np.maximum(m2, 1e-8 * m2.max())).astype(np.float32)
            sv_w = np.linalg.svd(r_mat * d[None, :], compute_uv=False)
        else:
            sv_w = np.linalg.svd(r_mat, compute_uv=False)
        sv_u = np.linalg.svd(r_mat, compute_uv=False)
        rec_w, rec_u = recovered(sv_w, ranks), recovered(sv_u, ranks)
        row = {"name": name, "kind": kind_of(name), "whitened": whitened,
               "full_rank": len(sv_w)}
        for r in ranks:
            row[f"w@r{r}"] = round(rec_w[r], 3)
            row[f"u@r{r}"] = round(rec_u[r], 3)
        rows.append(row)
        print(f"{name:44s} {'W' if whitened else '-'} "
              + " ".join(f"r{r}:{rec_w[r]:.2f}(u{rec_u[r]:.2f})"
                         for r in ranks))

    with open(outdir / "whitened_census.csv", "w", newline="") as f:
        wtr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wtr.writeheader()
        wtr.writerows(rows)

    print("\n=== per-kind mean recovered energy: whitened (unweighted) ===")
    kinds = sorted({r["kind"] for r in rows})
    hdr = "kind        n_whitened  " + "  ".join(f"@r{r:<4d}" for r in ranks)
    print(hdr)
    for k in kinds:
        rs = [r for r in rows if r["kind"] == k and r["whitened"]]
        if not rs:
            print(f"{k:10s}  none whitened (missing from imatrix)")
            continue
        cells = "  ".join(
            f"{np.mean([r[f'w@r{rk}'] for r in rs]):.2f}"
            f"({np.mean([r[f'u@r{rk}'] for r in rs]):.2f})" for rk in ranks)
        print(f"{k:10s} {len(rs):10d}  {cells}")
    print(f"\n{time.time()-t_all:.0f}s, {len(rows)} tensors, "
          f"{sum(r['whitened'] for r in rows)} whitened")


if __name__ == "__main__":
    main()
