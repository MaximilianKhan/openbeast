#!/usr/bin/env python3
"""P2 v2 (rsvd, cheap): cross-layer shared-basis viability on 0.6B.

For each kind: per-layer whitened capture at r=64 of the layer's OWN
rsvd basis vs ONE shared basis (rsvd of the column-concatenated whitened
residuals) at r=64 and r=128. If shared@64 ≈ own@64, one B serves all
layers and per-layer bytes shrink to A-only → the ~12× amplification
lane at 27B is open. (v1 used exact SVD on the concat and ate the CPU —
killed; this is the honorable replacement.)
"""
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "04-served-v0"))
from extract_adapter import deq, index_2d, load_imatrix  # noqa: E402

D = Path("/home/max/Documents/openbeast")
ref, _ = index_2d(str(D / "weights/Qwen3-0.6B-Q8_0.gguf"))
qnt, _ = index_2d(str(D / "research/lowrank/experiments/04-served-v0/"
                       "qwen3-0.6b-Q2_K-imat.gguf"))
imat = load_imatrix(str(D / "research/lowrank/data/"
                            "qwen3-0.6b.imatrix.gguf"))


def rsvd_basis(M, k, iters=3, seed=1):
    rng = np.random.default_rng(seed)
    k = min(k, min(M.shape))
    Y = M @ rng.standard_normal((M.shape[1], k), dtype=np.float32)
    for _ in range(iters):
        Y = M @ (M.T @ Y)
        Y, _ = np.linalg.qr(Y)
    Q, _ = np.linalg.qr(Y)
    return Q


print(f"{'kind':10s} {'own@64':>7s} {'shared@64':>9s} {'shared@128':>10s}")
for kind in ["attn_q", "attn_k", "attn_v", "attn_o",
             "ffn_gate", "ffn_up", "ffn_down"]:
    Rds = []
    for name in sorted(n for n in ref if kind in n and n in qnt):
        R = deq(ref[name]) - deq(qnt[name])
        m2 = imat.get(name)
        if m2 is None or len(m2) != R.shape[1]:
            continue
        d = np.sqrt(np.maximum(m2, 1e-8 * m2.max())).astype(np.float32)
        Rds.append((R * d[None, :]).astype(np.float32))
    if not Rds:
        continue
    own = []
    for Rd in Rds:
        Q = rsvd_basis(Rd, 64 + 16)[:, :64]
        own.append(float(((Q.T @ Rd) ** 2).sum() / (Rd ** 2).sum()))
    cat = np.concatenate(Rds, axis=1)
    caps = {}
    for rs in (64, 128):
        Qs = rsvd_basis(cat, rs + 16)[:, :rs]
        caps[rs] = np.mean([float(((Qs.T @ Rd) ** 2).sum() /
                                  (Rd ** 2).sum()) for Rd in Rds])
    print(f"{kind:10s} {np.mean(own):7.2f} {caps[64]:9.2f} "
          f"{caps[128]:10.2f}", flush=True)
