#!/usr/bin/env python3
"""E01: SVD spectrum census of every 2D tensor in a GGUF model.

For each 2D tensor: dequantize -> float32 -> economy SVD -> record the
singular-value spectrum. Emits:
  census.csv   one row per tensor: shape, kind, r90/r95/r99/r999,
               C(r95) = mn/(r95*(m+n)) (achievable compression at 95% energy)
  spectra.npz  full singular-value vectors keyed by tensor name
"""
import argparse
import csv
import re
import sys
import time
from pathlib import Path

import numpy as np

# gguf-py from the in-repo llama.cpp checkout (keeps reader/dequant in sync
# with the build we serve on)
REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "llama.cpp" / "gguf-py"))
from gguf import GGUFReader  # noqa: E402
from gguf.quants import dequantize  # noqa: E402

# tensor-name -> projection kind, for grouping in the readout
KIND_PATTERNS = [
    (r"attn_q\.", "attn_q"),
    (r"attn_k\.", "attn_k"),
    (r"attn_v\.", "attn_v"),
    (r"attn_output\.", "attn_o"),
    (r"ffn_gate\.", "ffn_gate"),
    (r"ffn_up\.", "ffn_up"),
    (r"ffn_down\.", "ffn_down"),
    (r"token_embd\.", "embed"),
    (r"output\.weight", "lm_head"),
]


def kind_of(name: str) -> str:
    for pat, kind in KIND_PATTERNS:
        if re.search(pat, name):
            return kind
    return "other"


def rank_for_energy(sv: np.ndarray, frac: float) -> int:
    """Smallest r such that the top-r singular values hold >= frac of
    total squared (Frobenius) energy."""
    e = np.cumsum(sv.astype(np.float64) ** 2)
    e /= e[-1]
    return int(np.searchsorted(e, frac) + 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("gguf")
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--min-dim", type=int, default=64,
                    help="skip 2D tensors with any dim smaller than this")
    args = ap.parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    reader = GGUFReader(args.gguf)
    rows, spectra = [], {}
    t_total = time.time()
    for t in reader.tensors:
        shape = [int(d) for d in t.shape if int(d) > 1]
        if len(shape) != 2 or min(shape) < args.min_dim:
            continue
        t0 = time.time()
        w = dequantize(t.data, t.tensor_type).astype(np.float32)
        w = w.reshape(shape[::-1]) if w.ndim == 1 else w
        # economy SVD: only min(m,n) singular values exist
        sv = np.linalg.svd(w, compute_uv=False)
        m, n = w.shape
        r95 = rank_for_energy(sv, 0.95)
        row = {
            "name": t.name,
            "kind": kind_of(t.name),
            "m": m,
            "n": n,
            "params": m * n,
            "full_rank": len(sv),
            "r90": rank_for_energy(sv, 0.90),
            "r95": r95,
            "r99": rank_for_energy(sv, 0.99),
            "r999": rank_for_energy(sv, 0.999),
            "C_at_r95": round(m * n / (r95 * (m + n)), 3),
            "stable_rank": round(float((sv**2).sum() / sv[0] ** 2), 1),
            "secs": round(time.time() - t0, 1),
        }
        rows.append(row)
        spectra[t.name] = sv
        print(f"{t.name:44s} {m}x{n}  r95={r95}/{len(sv)}  "
              f"C@95%={row['C_at_r95']}  stable_rank={row['stable_rank']}")

    with open(outdir / "census.csv", "w", newline="") as f:
        wtr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wtr.writeheader()
        wtr.writerows(rows)
    np.savez_compressed(outdir / "spectra.npz", **spectra)

    # per-kind aggregate readout
    print("\n=== per-kind aggregates (mean over tensors) ===")
    kinds = sorted({r["kind"] for r in rows})
    print(f"{'kind':10s} {'count':>5s} {'r95/full':>9s} {'C@95%':>7s} "
          f"{'stable_rank':>11s}")
    for k in kinds:
        rs = [r for r in rows if r["kind"] == k]
        frac = np.mean([r["r95"] / r["full_rank"] for r in rs])
        print(f"{k:10s} {len(rs):5d} {frac:9.2%} "
              f"{np.mean([r['C_at_r95'] for r in rs]):7.2f} "
              f"{np.mean([r['stable_rank'] for r in rs]):11.0f}")
    print(f"\ntotal {time.time() - t_total:.0f}s, {len(rows)} tensors")


if __name__ == "__main__":
    main()
