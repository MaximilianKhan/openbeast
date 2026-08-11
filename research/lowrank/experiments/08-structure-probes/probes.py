#!/usr/bin/env python3
"""E08 structure probes — cheap falsification of the two structural bets
from prior-art/arxiv-structures.md, on the 0.6B residual:

P1 (sparse viability): what fraction of residual energy (raw and
    whitened) lives in the top-p fraction of entries? Sparse W=Q+S+L is
    dimension-independent IFF this share is large. Also per-COLUMN
    version (column patches are servable today via one-hot adapters).

P2 (shared-basis viability): for each tensor kind, compare per-layer
    whitened capture at rank r of (a) each layer's OWN basis vs (b) ONE
    shared basis computed from all layers jointly at the same total byte
    cost — i.e. shared basis gets rank r_shared = r (same B bytes,
    amortized) while per-layer A costs are equal. If shared capture ≈ own
    capture, rank amplification is real (12× at d=5120 per the survey).
"""
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "04-served-v0"))
from extract_adapter import deq, index_2d, load_imatrix  # noqa: E402

D = Path("/home/max/Documents/openbeast")
REF = D / "weights/Qwen3-0.6B-Q8_0.gguf"
QNT = D / "research/lowrank/experiments/04-served-v0/qwen3-0.6b-Q2_K-imat.gguf"
IMA = D / "research/lowrank/data/qwen3-0.6b.imatrix.gguf"
KINDS = ["attn_q", "attn_k", "attn_v", "attn_o",
         "ffn_gate", "ffn_up", "ffn_down"]

ref, _ = index_2d(str(REF))
qnt, _ = index_2d(str(QNT))
imat = load_imatrix(str(IMA))


def whitened_residual(name):
    R = deq(ref[name]) - deq(qnt[name])
    m2 = imat.get(name)
    if m2 is None or len(m2) != R.shape[1]:
        return None, None
    d = np.sqrt(np.maximum(m2, 1e-8 * m2.max())).astype(np.float32)
    return R, R * d[None, :]


# ---- P1: sparse energy concentration -----------------------------------
print("== P1: top-p entry/column energy share (mean over layers) ==")
print(f"{'kind':10s} {'ent 0.1%':>9s} {'ent 1%':>7s} "
      f"{'col r64-eq':>10s} {'svd r64':>8s}")
for kind in KINDS:
    ent01, ent1, col, svd = [], [], [], []
    for name in ref:
        if kind not in name or name not in qnt:
            continue
        R, Rd = whitened_residual(name)
        if Rd is None:
            continue
        e = (Rd.astype(np.float64) ** 2)
        tot = e.sum()
        flat = np.sort(e.ravel())[::-1]
        n = flat.size
        ent01.append(flat[: max(1, n // 1000)].sum() / tot)
        ent1.append(flat[: max(1, n // 100)].sum() / tot)
        ce = e.sum(axis=0)
        cols = np.sort(ce)[::-1]
        # column patch at the byte cost of rank 64: 64 cols ~ rank-64
        col.append(cols[:64].sum() / tot)
        s = np.linalg.svd(Rd, compute_uv=False)
        svd.append((s[:64].astype(np.float64) ** 2).sum() /
                   (s.astype(np.float64) ** 2).sum())
    print(f"{kind:10s} {np.mean(ent01):9.3f} {np.mean(ent1):7.3f} "
          f"{np.mean(col):10.3f} {np.mean(svd):8.3f}")

# ---- P2: shared basis across layers ------------------------------------
print("\n== P2: shared left-basis capture vs own basis (rank 64) ==")
print(f"{'kind':10s} {'own@64':>7s} {'shared@64':>9s} {'shared@128':>10s}")
for kind in KINDS:
    Rds, owns = [], []
    for name in sorted(n for n in ref if kind in n and n in qnt):
        R, Rd = whitened_residual(name)
        if Rd is None:
            continue
        Rds.append(Rd)
        s = np.linalg.svd(Rd, compute_uv=False)
        e = s.astype(np.float64) ** 2
        owns.append(e[:64].sum() / e.sum())
    if not Rds:
        continue
    cat = np.concatenate(Rds, axis=1)          # (n_out, L*n_in)
    Uc, sc, _ = np.linalg.svd(cat, full_matrices=False)
    for rs, label in [(64, "shared@64"), (128, "shared@128")]:
        caps = []
        for Rd in Rds:
            proj = Uc[:, :rs].T @ Rd
            caps.append(float((proj ** 2).sum() /
                              (Rd.astype(np.float64) ** 2).sum()))
        if label == "shared@64":
            s64 = np.mean(caps)
        else:
            s128 = np.mean(caps)
    print(f"{kind:10s} {np.mean(owns):7.2f} {s64:9.2f} {s128:10.2f}")
